#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import permanent_team_identity_bridge_v1 as legacy
import runtime as rt

SCHEMA = "football3-cross-season-team-state-binding-v1"
MANIFEST_DIR = "football-data/manifests/lineup_match_identity_v502"
CURRENT_REGISTRY = "football-data/config/v6_full17_capture_identity_v6484.json"
CURRENT_ALIASES = "football-data/config/v6_full17_provider_aliases_v6483.json"
ALLOWED_MANIFEST_METHODS = {"exact_normalized_unique", "mutual_schedule_fingerprint"}
RETURNING = {"CONTINUING_CLUB", "RENAMED_OR_REKEYED_CLUB"}
_INSTALLED = False
_GUARD_PATCHED = False


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj)).hexdigest()


def _previous_season(season: str) -> str | None:
    return legacy._previous_season(season)


def _current_from_hint(hint: str) -> str | None:
    a, b = rt._season_years(hint)
    if a is None:
        return None
    if a == b:
        return str(a + 1)
    return f"{a + 1}/{str((b or a) + 1)[-2:]}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError(f"cross-season identity source unreadable: {path}") from exc
    if type(obj) is not dict:
        raise rt.RuntimeGateError(f"cross-season identity source object required: {path}")
    return obj


def _registry_rows(repo_root: Path, comp: str, season: str) -> tuple[list[dict[str, Any]], list[str]]:
    path = repo_root / CURRENT_REGISTRY
    if not path.is_file():
        return [], []
    obj = _load_json(path)
    c = (obj.get("competitions") or {}).get(comp)
    if type(c) is not dict or c.get("status") != "PASS" or type(c.get("teams")) is not list:
        return [], []
    hint = str(c.get("processed_latest_season_hint") or "")
    current = _current_from_hint(hint)
    if season not in {current, hint}:
        return [], []
    aliases_path = repo_root / CURRENT_ALIASES
    amap: dict[str, str] = {}
    if aliases_path.is_file():
        raw = (_load_json(aliases_path).get("aliases") or {}).get(comp)
        if type(raw) is dict:
            amap = {str(k): str(v) for k, v in raw.items()}
    rows: list[dict[str, Any]] = []
    for raw in c["teams"]:
        if type(raw) is not dict:
            continue
        canonical = str(raw.get("canonical_name") or "").strip()
        if not canonical:
            continue
        names = [canonical]
        names += [str(x).strip() for x in (raw.get("provider_alias_tokens") or []) if str(x or "").strip()]
        names += [a for a, target in amap.items() if rt._normalize_team(target) == rt._normalize_team(canonical)]
        rows.append({
            "canonical_name": canonical,
            "accepted_names": list(dict.fromkeys(names)),
            "source_path": str(raw.get("source_path") or CURRENT_REGISTRY),
            "registry_path": CURRENT_REGISTRY,
            "registry_sha256": rt._sha_file(path),
            "processed_latest_season_hint": hint,
        })
    return rows, [CURRENT_REGISTRY] + ([CURRENT_ALIASES] if amap else [])


def _current_row(repo_root: Path, comp: str, season: str, requested: str) -> tuple[dict[str, Any] | None, list[str]]:
    rows, sources = _registry_rows(repo_root, comp, season)
    if not rows:
        return None, sources
    token = rt._normalize_team(requested)
    matches = [r for r in rows if token in {rt._normalize_team(x) for x in r["accepted_names"]}]
    if len(matches) != 1:
        raise rt.RuntimeGateError(f"cross-season current identity not unique: {comp} {season} {requested}")
    return matches[0], sources


def _processed_roster(repo_root: Path, comp: str, season: str | None) -> set[str]:
    return legacy._processed_roster(repo_root, comp, season)


def _history_profile(repo_root: Path, comp: str, historical_name: str) -> tuple[list[str], int, dict[str, int]]:
    return legacy._history_profile(repo_root, comp, [historical_name])


def _manifest_crosswalk(repo_root: Path, comp: str) -> tuple[dict[str, Any], str | None]:
    path = repo_root / MANIFEST_DIR / f"{comp}.json"
    if not path.is_file():
        return {}, None
    obj = _load_json(path)
    if obj.get("status") != "IDENTITY_BRIDGE_PASS":
        raise rt.RuntimeGateError(f"authoritative identity manifest not PASS: {comp}")
    if obj.get("ambiguous_fixture_count") != 0 or obj.get("score_conflict_count") != 0:
        raise rt.RuntimeGateError(f"authoritative identity manifest conflict: {comp}")
    cross = obj.get("crosswalk")
    if type(cross) is not dict:
        raise rt.RuntimeGateError(f"authoritative identity crosswalk missing: {comp}")
    return cross, rt._sha_file(path)


def _manifest_resolution(repo_root: Path, comp: str, names: list[str]) -> dict[str, Any] | None:
    cross, manifest_sha = _manifest_crosswalk(repo_root, comp)
    if not cross:
        return None
    tokens = {rt._normalize_team(x) for x in names if str(x or "").strip()}
    hits: list[tuple[str, dict[str, Any]]] = []
    for provider_id, raw in cross.items():
        if type(raw) is not dict:
            continue
        method = str(raw.get("method") or "")
        if method not in ALLOWED_MANIFEST_METHODS or float(raw.get("confidence") or 0) != 1.0:
            continue
        accepted = [str(raw.get("processed_team") or "").strip()]
        accepted += [str(x).strip() for x in (raw.get("source_names") or []) if str(x or "").strip()]
        if tokens & {rt._normalize_team(x) for x in accepted if x}:
            hits.append((str(provider_id), raw))
    if len(hits) > 1:
        raise rt.RuntimeGateError(f"cross-season authoritative provider identity ambiguous: {comp} {names[0]}")
    if not hits:
        return None
    provider_id, raw = hits[0]
    processed = str(raw.get("processed_team") or "").strip()
    if not processed:
        raise rt.RuntimeGateError(f"cross-season authoritative mapping lacks processed team: {comp} {provider_id}")
    provenance = {
        "source": f"{MANIFEST_DIR}/{comp}.json",
        "source_sha256": manifest_sha,
        "method": str(raw.get("method")),
        "confidence": float(raw.get("confidence")),
        "provider_identity": provider_id,
        "source_names": list(raw.get("source_names") or []),
        "processed_team": processed,
        "selection_fields": ["provider_identity", "source_names", "processed_team", "schedule_fingerprint_if_declared"],
        "result_or_xg_values_used_for_identity": False,
        "fuzzy_matching_used": False,
    }
    return {"provider_identity": provider_id, "historical_name": processed, "provenance": provenance}


def _exact_previous_resolution(repo_root: Path, comp: str, previous: str | None, names: list[str]) -> dict[str, Any] | None:
    roster = sorted(_processed_roster(repo_root, comp, previous))
    tokens = {rt._normalize_team(x) for x in names if str(x or "").strip()}
    hits = [x for x in roster if rt._normalize_team(x) in tokens]
    if len(hits) > 1:
        raise rt.RuntimeGateError(f"cross-season previous roster identity ambiguous: {comp} {previous} {names[0]}")
    if not hits:
        return None
    historical = hits[0]
    provenance = {
        "source": f"football-data/processed/{comp}/*.csv",
        "source_sha256": "MULTI_FILE_SET_BOUND_BY_RUNTIME_HISTORY_LOADER",
        "method": "EXACT_NORMALIZED_PREVIOUS_SEASON_PARTICIPATION",
        "season": previous,
        "processed_team": historical,
        "selection_fields": ["competition", "season", "home_team_name", "away_team_name"],
        "result_or_xg_values_used_for_identity": False,
        "fuzzy_matching_used": False,
    }
    return {"provider_identity": None, "historical_name": historical, "provenance": provenance}


def _stable_global_id(comp: str, canonical: str, provider_identity: str | None) -> str:
    key = f"provider:{provider_identity}" if provider_identity else f"canonical:{comp}:{rt._normalize_team(canonical)}"
    return "club_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _state_hit(state: Any, comp: str, team_id: str) -> tuple[bool, bool]:
    base = getattr(state, "base", None)
    return ((comp, team_id) in getattr(base, "teams_local", {}), team_id in getattr(base, "teams_global", {}))


def resolve_team(repo_root: Path, state: Any, comp: str, season: str, requested: str) -> dict[str, Any]:
    requested = str(requested or "").strip()
    if not requested:
        raise rt.RuntimeGateError("empty requested team identity")
    row, registry_sources = _current_row(repo_root, comp, season, requested)
    if row is None:
        return _ORIGINAL_RESOLVE_TEAM(repo_root, state, comp, season, requested)

    canonical = row["canonical_name"]
    names = list(dict.fromkeys([requested, canonical, *row["accepted_names"]]))
    previous = _previous_season(season)
    prev_roster = _processed_roster(repo_root, comp, previous)
    mapped = _manifest_resolution(repo_root, comp, names)
    if mapped is None:
        mapped = _exact_previous_resolution(repo_root, comp, previous, names)

    if mapped is not None:
        historical_name = mapped["historical_name"]
        provider_identity = mapped["provider_identity"]
        mapping_provenance = mapped["provenance"]
    else:
        historical_name = canonical
        provider_identity = None
        mapping_provenance = {
            "source": row["registry_path"],
            "source_sha256": row["registry_sha256"],
            "method": "EXACT_CURRENT_CANONICAL_NO_PREVIOUS_IDENTITY_MATCH",
            "season": season,
            "processed_team": canonical,
            "result_or_xg_values_used_for_identity": False,
            "fuzzy_matching_used": False,
        }

    prev_tokens = {rt._normalize_team(x) for x in prev_roster}
    returning = rt._normalize_team(historical_name) in prev_tokens
    historical_id = rt._global_team_id(historical_name)
    local_hit, global_hit = _state_hit(state, comp, historical_id)
    historical_seasons, historical_match_count, historical_by_season = _history_profile(repo_root, comp, historical_name)

    if returning:
        classification = "CONTINUING_CLUB" if rt._normalize_team(canonical) == rt._normalize_team(historical_name) else "RENAMED_OR_REKEYED_CLUB"
        relation = "CONTINUING_TOP_FLIGHT"
    elif historical_match_count > 0 or local_hit or global_hit:
        classification = "PROMOTED_OR_ENTERING_CLUB"
        relation = "PROMOTED_OR_RETURNING_WITH_FROZEN_HISTORY"
    elif row is not None:
        classification = "PROMOTED_OR_ENTERING_CLUB"
        relation = "PROMOTED_OR_NEW_TO_FORMAL_HISTORY"
    else:
        classification = "TRUE_NEW_OR_UNAVAILABLE_HISTORY"
        relation = "PROMOTED_OR_NEW_TO_FORMAL_HISTORY"

    canonical_global_id = _stable_global_id(comp, canonical, provider_identity)
    provenance = {
        "schema_version": SCHEMA,
        "competition": comp,
        "current_season": season,
        "previous_season": previous,
        "registry_sources": registry_sources,
        "registry_source_path": row["source_path"],
        "current_registry_sha256": row["registry_sha256"],
        "identity_mapping": mapping_provenance,
        "canonical_global_id": canonical_global_id,
        "historical_state_team_id": historical_id,
        "provenance_sha256": None,
    }
    provenance["provenance_sha256"] = _sha({k: v for k, v in provenance.items() if k != "provenance_sha256"})

    return {
        "schema_version": SCHEMA,
        "requested_name": requested,
        "current_name": canonical,
        "current_team_id": rt._global_team_id(canonical),
        "provider_names": row["accepted_names"],
        "canonical_name": canonical,
        "canonical_team_id": historical_id,
        "canonical_global_id": canonical_global_id,
        "strength_team_id": historical_id,
        "historical_state_team_id": historical_id,
        "previous_season_identity": historical_name if returning else None,
        "strength_alias": historical_name,
        "aliases": list(dict.fromkeys([*names, historical_name])),
        "registry_match": True,
        "registry_sources": registry_sources,
        "state_evidence": bool(local_hit or global_hit),
        "state_local_evidence": local_hit,
        "state_global_evidence": global_hit,
        "resolution": "CROSS_SEASON_CANONICAL_TO_HISTORICAL_STATE_ID",
        "season_relation": relation,
        "participation_classification": classification,
        "previous_season": previous,
        "historical_seasons": historical_seasons,
        "historical_match_count": historical_match_count,
        "historical_matches_by_season": historical_by_season,
        "mapping_provenance": provenance,
        "legal_xg_coverage_available": (repo_root / "football-data" / "evidence" / "xg" / "understat_2025_26_linked" / f"{comp}.jsonl").is_file(),
        "identity_selection_uses_result_or_xg": False,
        "fuzzy_cross_club_substitution": False,
    }


def resolve_fixture(repo_root: Path, state: Any, comp: str, season: str, home: str, away: str, kickoff):
    h = resolve_team(repo_root, state, comp, season, home)
    a = resolve_team(repo_root, state, comp, season, away)
    if h["strength_team_id"] == a["strength_team_id"]:
        raise rt.RuntimeGateError("resolved home/away strength identity collision")
    fixture = {
        "fixture_id": rt._fixture_id(comp, season, kickoff, home, away),
        "competition_id": comp,
        "season": season,
        "kickoff": kickoff.isoformat(),
        "home_team_id": h["strength_team_id"],
        "away_team_id": a["strength_team_id"],
        "home_team_name": home,
        "away_team_name": away,
    }
    audit = {
        "schema_version": SCHEMA,
        "competition_id": comp,
        "season": season,
        "fixture_id": fixture["fixture_id"],
        "home": h,
        "away": a,
        "identity_selection_uses_result_or_xg": False,
        "fuzzy_cross_club_substitution": False,
    }
    audit["mapping_sha256"] = _sha(audit)
    return fixture, audit


def patch_guard(guard_module) -> dict[str, Any]:
    global _GUARD_PATCHED
    if _GUARD_PATCHED:
        return {"schema_version": "football3-returning-club-state-integrity-guard-v1", "installed": True, "idempotent_reuse": True, "identity_contract": SCHEMA, "zero_history_fail_closed": True, "evidence_threshold_changed": False, "model_parameters_or_weights_changed": False}
    original = guard_module.classify_state

    def classify_state(loaded, fixture, identity_audit, trigger, receipt):
        out = original(loaded, fixture, identity_audit, trigger, receipt)
        reasons = list(out.get("anomaly_reasons") or [])
        returning_failure = False
        for side in ("home", "away"):
            ident = identity_audit[side]
            cls = str(ident.get("participation_classification") or "")
            if cls in RETURNING:
                missing = int(ident.get("historical_match_count") or 0) <= 0 or not bool(ident.get("state_evidence")) or not bool(ident.get("state_local_evidence")) or not bool(ident.get("state_global_evidence"))
                linked_key = f"{side}_linked_2025_26_matches"
                linked_xg = int((out.get("historical_xg") or {}).get(linked_key) or 0)
                if bool(ident.get("legal_xg_coverage_available")) and linked_xg <= 0:
                    missing = True
                if missing:
                    returning_failure = True
                    code = f"{side.upper()}_RETURNING_CLUB_HISTORICAL_STATE_MISSING"
                    if code not in reasons:
                        reasons.append(code)
        if reasons != list(out.get("anomaly_reasons") or []):
            out["anomaly_reasons"] = reasons
            out["status"] = "DATA_STATE_ANOMALY"
            out["fallback_class"] = "DATA_STATE_ANOMALY"
            out["fallback_reason"] = "RETURNING_CLUB_HISTORICAL_STATE_MISSING;" + ";".join(reasons)
        out["returning_club_guard"] = {
            "status": "FAIL_CLOSED" if returning_failure else "PASS",
            "reason_code": "RETURNING_CLUB_HISTORICAL_STATE_MISSING" if returning_failure else None,
            "identity_contract": SCHEMA,
        }
        return out

    guard_module.classify_state = classify_state
    _GUARD_PATCHED = True
    return {
        "schema_version": "football3-returning-club-state-integrity-guard-v1",
        "installed": True,
        "identity_contract": SCHEMA,
        "returning_classifications": sorted(RETURNING),
        "zero_history_fail_closed": True,
        "normal_fallback_from_lookup_zero_prohibited": True,
        "evidence_threshold_changed": False,
        "model_parameters_or_weights_changed": False,
    }


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"schema_version": SCHEMA, "installed": True, "idempotent_reuse": True, "canonical_global_identity_separate_from_historical_state_key": True, "fuzzy_matching": False, "result_or_xg_values_used_for_identity": False, "model_parameters_or_weights_changed": False, "formal_current_or_production_pointer_changed": False}
    legacy.resolve_team = resolve_team
    legacy.resolve_fixture = resolve_fixture
    import formal_state_integrity_guard_v1 as guard_module
    guard_contract = patch_guard(guard_module)
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "canonical_global_identity_separate_from_historical_state_key": True,
        "authoritative_manifest_methods": sorted(ALLOWED_MANIFEST_METHODS),
        "previous_season_derived_from_request_season": True,
        "calendar_year_leagues_supported": True,
        "fuzzy_matching": False,
        "result_or_xg_values_used_for_identity": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
        "returning_club_guard": guard_contract,
    }


_ORIGINAL_RESOLVE_TEAM = legacy.resolve_team
_ORIGINAL_RESOLVE_FIXTURE = legacy.resolve_fixture
