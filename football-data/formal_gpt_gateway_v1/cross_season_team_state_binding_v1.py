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
CONTINUITY_REGISTRY = "football-data/config/cross_season_identity_continuity_v1.json"
EXACT_CURRENT_REGISTRY = legacy.CURRENT_REGISTRY
FULL17_REGISTRY = legacy.FULL17_REGISTRY
FULL17_ALIASES = legacy.FULL17_ALIASES
ACTIVE_REGISTRY = legacy.ACTIVE_REGISTRY
ACTIVE_ALIASES = legacy.ACTIVE_ALIASES
ALLOWED_MANIFEST_METHODS = {"exact_normalized_unique", "mutual_schedule_fingerprint"}
RETURNING = {"CONTINUING_CLUB", "RENAMED_OR_REKEYED_CLUB"}
NATURAL_YEAR_COMPETITIONS = {
    "ARG_Primera", "BRA_SerieA", "JPN_J1", "KOR_KLeague1",
    "NOR_Eliteserien", "SWE_Allsvenskan", "USA_MLS",
}
MIN_SCHEDULE_FINGERPRINT_F1 = 0.99
MIN_SCHEDULE_FINGERPRINT_MARGIN = 0.75
_INSTALLED = False
_GUARD_PATCHED = False


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj)).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError(f"cross-season identity source unreadable: {path}") from exc
    if type(obj) is not dict:
        raise rt.RuntimeGateError(f"cross-season identity source object required: {path}")
    return obj


def _previous_season(season: str) -> str | None:
    return legacy._previous_season(season)


def _season_from_observation(comp: str, observed: str) -> str:
    dt = rt._parse_dt(observed, "current-roster observed_at")
    year = dt.year
    if comp in NATURAL_YEAR_COMPETITIONS:
        return str(year)
    start = year if dt.month >= 7 else year - 1
    return f"{start}/{str(start + 1)[-2:]}"


def _full17_meta(repo_root: Path, comp: str) -> dict[str, Any]:
    path = repo_root / FULL17_REGISTRY
    if not path.is_file():
        return {}
    obj = _load_json(path)
    c = (obj.get("competitions") or {}).get(comp)
    if type(c) is not dict:
        return {}
    return {
        "registry_path": FULL17_REGISTRY,
        "registry_sha256": rt._sha_file(path),
        "processed_latest_season_hint": str(c.get("processed_latest_season_hint") or ""),
        "registry_generated_at_utc": obj.get("generated_at_utc"),
    }


def current_season(repo_root: Path, comp: str) -> str:
    exact = repo_root / EXACT_CURRENT_REGISTRY
    if exact.is_file():
        obj = _load_json(exact)
        c = (obj.get("competitions") or {}).get(comp)
        season = str(obj.get("season") or "").strip()
        if type(c) is dict and season:
            return season
    full = repo_root / FULL17_REGISTRY
    if full.is_file():
        obj = _load_json(full)
        c = (obj.get("competitions") or {}).get(comp)
        observed = str(obj.get("generated_at_utc") or "").strip()
        if type(c) is dict and c.get("status") == "PASS" and observed:
            return _season_from_observation(comp, observed)
    raise rt.RuntimeGateError(f"current season unresolved from current-roster authority: {comp}")


def _finalize(rows: list[dict[str, Any]], sources: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        token = rt._normalize_team(str(row.get("canonical_name") or ""))
        if not token:
            continue
        dst = merged.setdefault(token, {"canonical_name": row["canonical_name"], "provider_names": [], "aliases": [], "sources": []})
        for key in ("provider_names", "aliases"):
            for x in row.get(key, []):
                text = str(x or "").strip()
                if text and text not in dst[key]:
                    dst[key].append(text)
        for src in row.get("sources", []):
            if src and src not in dst["sources"]:
                dst["sources"].append(src)
    return list(merged.values()), sorted(set(sources))


def _registry_rows(repo_root: Path, comp: str, season: str) -> tuple[list[dict[str, Any]], list[str]]:
    expected = current_season(repo_root, comp)
    if season != expected:
        return legacy._registry_rows(repo_root, comp, season)
    exact = repo_root / EXACT_CURRENT_REGISTRY
    if exact.is_file():
        obj = _load_json(exact)
        c = (obj.get("competitions") or {}).get(comp)
        if str(obj.get("season") or "") == season and type(c) is dict and type(c.get("teams")) is list:
            rows = []
            for r in c["teams"]:
                if type(r) is not dict:
                    continue
                canonical = str(r.get("canonical_name") or r.get("official_name") or "").strip()
                official = str(r.get("official_name") or "").strip()
                if canonical:
                    names = [canonical, official, *(r.get("aliases") or [])]
                    rows.append({"canonical_name": canonical, "provider_names": [official] if official else [], "aliases": names, "sources": [EXACT_CURRENT_REGISTRY]})
            if rows:
                return _finalize(rows, [EXACT_CURRENT_REGISTRY])
    full = repo_root / FULL17_REGISTRY
    aliases_path = repo_root / FULL17_ALIASES
    if full.is_file():
        obj = _load_json(full)
        c = (obj.get("competitions") or {}).get(comp)
        if type(c) is dict and c.get("status") == "PASS" and type(c.get("teams")) is list:
            amap: dict[str, str] = {}
            if aliases_path.is_file():
                raw = (_load_json(aliases_path).get("aliases") or {}).get(comp)
                if type(raw) is dict:
                    amap = {str(k): str(v) for k, v in raw.items()}
            rows = []
            for r in c["teams"]:
                if type(r) is not dict:
                    continue
                canonical = str(r.get("canonical_name") or "").strip()
                if not canonical:
                    continue
                provider = [str(x).strip() for x in (r.get("provider_alias_tokens") or []) if str(x or "").strip()]
                provider += [alias for alias, target in amap.items() if rt._normalize_team(target) == rt._normalize_team(canonical)]
                rows.append({"canonical_name": canonical, "provider_names": provider, "aliases": [canonical, *provider], "sources": [FULL17_REGISTRY] + ([FULL17_ALIASES] if amap else [])})
            if rows:
                return _finalize(rows, [FULL17_REGISTRY] + ([FULL17_ALIASES] if amap else []))
    active = repo_root / ACTIVE_REGISTRY
    active_alias = repo_root / ACTIVE_ALIASES
    if active.is_file():
        obj = _load_json(active)
        c = (obj.get("competitions") or {}).get(comp)
        amap: dict[str, str] = {}
        if active_alias.is_file():
            raw = (_load_json(active_alias).get("aliases") or {}).get(comp)
            if type(raw) is dict:
                amap = {str(k): str(v) for k, v in raw.items()}
        if type(c) is dict and type(c.get("teams")) is list:
            rows = []
            for r in c["teams"]:
                if type(r) is not dict:
                    continue
                canonical = str(r.get("canonical_name") or "").strip()
                if not canonical:
                    continue
                names = [canonical, *(r.get("observed_variants") or []), *(r.get("provider_aliases") or [])]
                names += [alias for alias, target in amap.items() if rt._normalize_team(target) == rt._normalize_team(canonical)]
                rows.append({"canonical_name": canonical, "provider_names": list(r.get("provider_aliases") or []), "aliases": names, "sources": [ACTIVE_REGISTRY] + ([ACTIVE_ALIASES] if amap else [])})
            if rows:
                return _finalize(rows, [ACTIVE_REGISTRY] + ([ACTIVE_ALIASES] if amap else []))
    roster = legacy._processed_roster(repo_root, comp, season)
    if roster:
        amap = legacy._alias_config(repo_root).get(comp, {})
        rows = []
        for canonical in sorted(roster):
            names = [canonical, *[str(alias) for alias, target in amap.items() if rt._normalize_team(str(target)) == rt._normalize_team(canonical)]]
            rows.append({"canonical_name": canonical, "provider_names": [], "aliases": names, "sources": ["processed_current_season_roster_team_names_only", "team_aliases.json"]})
        return _finalize(rows, ["processed_current_season_roster_team_names_only", "team_aliases.json"])
    return [], []


def _current_row(repo_root: Path, comp: str, season: str, requested: str) -> tuple[dict[str, Any] | None, list[str]]:
    raw_rows, sources = _registry_rows(repo_root, comp, season)
    if not raw_rows:
        return None, sources
    meta = _full17_meta(repo_root, comp)
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        canonical = str(raw.get("canonical_name") or "").strip()
        names = [canonical, *(raw.get("provider_names") or []), *(raw.get("aliases") or [])]
        rows.append({
            "canonical_name": canonical,
            "accepted_names": list(dict.fromkeys(str(x).strip() for x in names if str(x or "").strip())),
            "source_path": (raw.get("sources") or sources or [meta.get("registry_path") or FULL17_REGISTRY])[0],
            "registry_sources": list(dict.fromkeys([*(raw.get("sources") or []), *sources])),
            "registry_path": meta.get("registry_path") or (sources[0] if sources else FULL17_REGISTRY),
            "registry_sha256": meta.get("registry_sha256"),
            "processed_latest_season_hint": meta.get("processed_latest_season_hint"),
            "registry_generated_at_utc": meta.get("registry_generated_at_utc"),
        })
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
    if obj.get("status") != "IDENTITY_BRIDGE_PASS" or int(obj.get("ambiguous_fixture_count") or 0) != 0:
        raise rt.RuntimeGateError(f"authoritative identity manifest not PASS/unambiguous: {comp}")
    cross = obj.get("crosswalk")
    if type(cross) is not dict:
        raise rt.RuntimeGateError(f"authoritative identity crosswalk missing: {comp}")
    return cross, rt._sha_file(path)


def _manifest_row_eligible(raw: dict[str, Any]) -> bool:
    method = str(raw.get("method") or "")
    if method == "exact_normalized_unique":
        return float(raw.get("confidence") or 0.0) == 1.0
    if method == "mutual_schedule_fingerprint":
        return bool(raw.get("mutual_best")) and float(raw.get("fingerprint_f1") or 0.0) >= MIN_SCHEDULE_FINGERPRINT_F1 and float(raw.get("best_margin") or 0.0) >= MIN_SCHEDULE_FINGERPRINT_MARGIN
    return False


def _manifest_resolution(repo_root: Path, comp: str, names: list[str]) -> dict[str, Any] | None:
    cross, manifest_sha = _manifest_crosswalk(repo_root, comp)
    if not cross:
        return None
    tokens = {rt._normalize_team(x) for x in names if str(x or "").strip()}
    hits: list[tuple[str, dict[str, Any]]] = []
    for provider_id, raw in cross.items():
        if type(raw) is not dict or not _manifest_row_eligible(raw):
            continue
        accepted = [str(raw.get("processed_team") or "").strip(), *[str(x).strip() for x in (raw.get("source_names") or []) if str(x or "").strip()]]
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
    return {"provider_identity": provider_id, "historical_name": processed, "provenance": {
        "source": f"{MANIFEST_DIR}/{comp}.json", "source_sha256": manifest_sha,
        "method": str(raw.get("method")), "confidence": float(raw.get("confidence") or 0.0),
        "provider_identity": provider_id, "source_names": list(raw.get("source_names") or []),
        "processed_team": processed, "mutual_best": raw.get("mutual_best"),
        "fingerprint_f1": raw.get("fingerprint_f1"), "best_margin": raw.get("best_margin"),
        "selection_fields": ["provider_identity", "source_names", "processed_team", "schedule_fingerprint_if_declared"],
        "result_or_score_or_xg_values_used_for_identity": False, "fuzzy_matching_used": False,
    }}


def _exact_previous_resolution(repo_root: Path, comp: str, previous: str | None, names: list[str]) -> dict[str, Any] | None:
    roster = sorted(_processed_roster(repo_root, comp, previous))
    tokens = {rt._normalize_team(x) for x in names if str(x or "").strip()}
    hits = [x for x in roster if rt._normalize_team(x) in tokens]
    if len(hits) > 1:
        raise rt.RuntimeGateError(f"cross-season previous roster identity ambiguous: {comp} {previous} {names[0]}")
    if not hits:
        return None
    historical = hits[0]
    return {"provider_identity": None, "historical_name": historical, "provenance": {
        "source": f"football-data/processed/{comp}/*.csv", "source_sha256": "MULTI_FILE_SET_BOUND_BY_RUNTIME_HISTORY_LOADER",
        "method": "EXACT_NORMALIZED_PREVIOUS_SEASON_PARTICIPATION", "season": previous,
        "processed_team": historical, "selection_fields": ["competition", "season", "home_team_name", "away_team_name"],
        "result_or_score_or_xg_values_used_for_identity": False, "fuzzy_matching_used": False,
    }}


def _continuity_resolution(repo_root: Path, comp: str, season: str, previous: str | None,
                           names: list[str], previous_roster: set[str]) -> dict[str, Any] | None:
    path = repo_root / CONTINUITY_REGISTRY
    if not path.is_file():
        return None
    obj = _load_json(path)
    if obj.get("schema_version") != "football3-cross-season-identity-continuity-v1":
        raise rt.RuntimeGateError("cross-season continuity registry schema mismatch")
    rows = obj.get("rows")
    if type(rows) is not list:
        raise rt.RuntimeGateError("cross-season continuity registry rows missing")
    requested_tokens = {rt._normalize_team(x) for x in names if str(x or "").strip()}
    hits: list[dict[str, Any]] = []
    for raw in rows:
        if type(raw) is not dict:
            continue
        if str(raw.get("competition_id") or "") != comp or str(raw.get("current_season") or "") != season or str(raw.get("previous_season") or "") != str(previous or ""):
            continue
        accepted = [str(raw.get("current_canonical_name") or ""), *[str(x) for x in (raw.get("current_exact_names") or [])]]
        if requested_tokens & {rt._normalize_team(x) for x in accepted if x}:
            hits.append(raw)
    if len(hits) > 1:
        raise rt.RuntimeGateError(f"cross-season continuity identity ambiguous: {comp} {season} {names[0]}")
    if not hits:
        return None
    raw = hits[0]
    historical = str(raw.get("previous_processed_name") or "").strip()
    roster_hits = [x for x in previous_roster if rt._normalize_team(x) == rt._normalize_team(historical)]
    if len(roster_hits) != 1:
        raise rt.RuntimeGateError(f"cross-season continuity previous roster identity not unique: {comp} {previous} {historical}; hits={len(roster_hits)}")
    historical = roster_hits[0]
    return {"provider_identity": None, "historical_name": historical, "provenance": {
        "source": CONTINUITY_REGISTRY,
        "source_sha256": rt._sha_file(path),
        "method": "AUDITED_EXACT_CROSS_SEASON_CONTINUITY_REGISTRY",
        "season": season,
        "previous_season": previous,
        "processed_team": historical,
        "evidence": list(raw.get("evidence") or []),
        "selection_fields": ["competition_id", "current_season", "current_exact_names", "previous_season", "previous_processed_name"],
        "result_or_score_or_xg_values_used_for_identity": False,
        "fuzzy_matching_used": False,
    }}


def _state_hit(state: Any, comp: str, team_id: str) -> tuple[bool, bool]:
    base = getattr(state, "base", None)
    return ((comp, team_id) in getattr(base, "teams_local", {}), team_id in getattr(base, "teams_global", {}))


def _explicit_alias_state_resolution(state: Any, comp: str, names: list[str]) -> dict[str, Any] | None:
    hits: dict[str, list[str]] = {}
    for name in names:
        text = str(name or "").strip()
        if not text:
            continue
        tid = rt._global_team_id(text)
        local, global_ = _state_hit(state, comp, tid)
        if local or global_:
            hits.setdefault(tid, []).append(text)
    if len(hits) > 1:
        raise rt.RuntimeGateError(f"explicit current aliases map to multiple frozen state ids: {comp} {names[0]}")
    if not hits:
        return None
    tid, aliases = next(iter(hits.items()))
    return {"historical_name": aliases[0], "historical_state_team_id": tid, "provenance": {
        "source": "current-authority-exact-aliases + frozen-state-key-presence",
        "method": "UNIQUE_POSITIVE_STATE_KEY_FROM_EXPLICIT_ALIAS_SET", "accepted_aliases": aliases,
        "result_or_score_or_xg_values_used_for_identity": False, "fuzzy_matching_used": False,
    }}


def _stable_global_id(comp: str, canonical: str, provider_identity: str | None, historical_name: str | None) -> str:
    if provider_identity:
        key = f"provider:{provider_identity}"
    elif historical_name:
        key = f"historical:{comp}:{rt._normalize_team(historical_name)}"
    else:
        key = f"canonical:{comp}:{rt._normalize_team(canonical)}"
    return "club_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def resolve_team(repo_root: Path, state: Any, comp: str, season: str, requested: str) -> dict[str, Any]:
    requested = str(requested or "").strip()
    if not requested:
        raise rt.RuntimeGateError("empty requested team identity")
    expected_current = current_season(repo_root, comp)
    if season != expected_current:
        return _ORIGINAL_RESOLVE_TEAM(repo_root, state, comp, season, requested)
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
    if mapped is None:
        mapped = _continuity_resolution(repo_root, comp, season, previous, names, prev_roster)
    alias_state = _explicit_alias_state_resolution(state, comp, names)
    provider_identity: str | None = None
    if mapped is not None:
        historical_name = mapped["historical_name"]; provider_identity = mapped.get("provider_identity"); mapping_provenance = mapped["provenance"]
    elif alias_state is not None:
        historical_name = alias_state["historical_name"]; mapping_provenance = alias_state["provenance"]
    else:
        historical_name = canonical
        mapping_provenance = {"source": row["registry_path"], "source_sha256": row.get("registry_sha256"), "method": "CURRENT_IDENTITY_ONLY_NO_PREVIOUS_PARTICIPATION_MAPPING", "season": season, "processed_team": canonical, "result_or_score_or_xg_values_used_for_identity": False, "fuzzy_matching_used": False}
    prev_tokens = {rt._normalize_team(x) for x in prev_roster}
    authority_tokens = {rt._normalize_team(x) for x in names}
    previous_identity_tokens = {rt._normalize_team(historical_name)} | authority_tokens
    previous_matches = sorted(x for x in prev_roster if rt._normalize_team(x) in previous_identity_tokens)
    if len(previous_matches) > 1:
        raise rt.RuntimeGateError(f"previous-season participation maps current club to multiple identities: {comp} {canonical}")
    returning = len(previous_matches) == 1
    previous_identity = previous_matches[0] if returning else None
    if returning and (mapped is None or rt._normalize_team(historical_name) not in prev_tokens):
        historical_name = previous_identity or historical_name
    historical_id = rt._global_team_id(historical_name)
    local_hit, global_hit = _state_hit(state, comp, historical_id)
    historical_seasons, historical_match_count, historical_by_season = _history_profile(repo_root, comp, historical_name)
    if returning:
        classification = "CONTINUING_CLUB" if rt._normalize_team(canonical) == rt._normalize_team(previous_identity or "") else "RENAMED_OR_REKEYED_CLUB"; relation = "CONTINUING_TOP_FLIGHT"
    else:
        classification = "PROMOTED_OR_ENTERING_CLUB"; relation = "PROMOTED_OR_RETURNING_WITH_FROZEN_HISTORY" if (historical_match_count > 0 or local_hit or global_hit) else "PROMOTED_OR_NEW_TO_FORMAL_HISTORY"
    canonical_global_id = _stable_global_id(comp, canonical, provider_identity, historical_name if returning else None)
    provenance = {
        "schema_version": SCHEMA, "competition": comp, "current_season": season, "previous_season": previous,
        "previous_season_roster_count": len(prev_roster), "previous_season_participation_matches": previous_matches,
        "participation_classification_source": f"football-data/processed/{comp}/*.csv team-name roster only",
        "participation_classification_uses_history_lookup_zero": False, "registry_sources": registry_sources,
        "registry_source_path": row["source_path"], "current_registry_sha256": row.get("registry_sha256"),
        "current_registry_generated_at_utc": row.get("registry_generated_at_utc"), "processed_latest_season_hint": row.get("processed_latest_season_hint"),
        "identity_mapping": mapping_provenance, "canonical_global_id": canonical_global_id, "historical_state_team_id": historical_id, "provenance_sha256": None,
    }
    provenance["provenance_sha256"] = _sha({k: v for k, v in provenance.items() if k != "provenance_sha256"})
    return {
        "schema_version": SCHEMA, "requested_name": requested, "current_name": canonical, "current_team_id": rt._global_team_id(canonical),
        "provider_names": row["accepted_names"], "canonical_name": canonical, "canonical_team_id": historical_id, "canonical_global_id": canonical_global_id,
        "strength_team_id": historical_id, "historical_state_team_id": historical_id, "previous_season_identity": previous_identity,
        "strength_alias": historical_name, "aliases": list(dict.fromkeys([*names, historical_name, *(previous_matches or [])])),
        "registry_match": True, "registry_sources": registry_sources, "state_evidence": bool(local_hit or global_hit), "state_local_evidence": local_hit,
        "state_global_evidence": global_hit, "resolution": "CROSS_SEASON_CANONICAL_TO_HISTORICAL_STATE_ID", "season_relation": relation,
        "participation_classification": classification, "participation_classification_uses_history_lookup_zero": False, "previous_season": previous,
        "historical_seasons": historical_seasons, "historical_match_count": historical_match_count, "historical_matches_by_season": historical_by_season,
        "mapping_provenance": provenance, "legal_xg_coverage_available": (repo_root / "football-data" / "evidence" / "xg" / "understat_2025_26_linked" / f"{comp}.jsonl").is_file(),
        "identity_selection_uses_result_or_xg": False, "fuzzy_cross_club_substitution": False,
    }


def resolve_fixture(repo_root: Path, state: Any, comp: str, season: str, home: str, away: str, kickoff):
    h = resolve_team(repo_root, state, comp, season, home); a = resolve_team(repo_root, state, comp, season, away)
    if h["strength_team_id"] == a["strength_team_id"]:
        raise rt.RuntimeGateError("resolved home/away strength identity collision")
    fixture = {"fixture_id": rt._fixture_id(comp, season, kickoff, home, away), "competition_id": comp, "season": season, "kickoff": kickoff.isoformat(), "home_team_id": h["strength_team_id"], "away_team_id": a["strength_team_id"], "home_team_name": home, "away_team_name": away}
    audit = {"schema_version": SCHEMA, "competition_id": comp, "season": season, "fixture_id": fixture["fixture_id"], "home": h, "away": a, "identity_selection_uses_result_or_xg": False, "fuzzy_cross_club_substitution": False}
    audit["mapping_sha256"] = _sha(audit)
    return fixture, audit


def patch_guard(guard_module) -> dict[str, Any]:
    global _GUARD_PATCHED
    if _GUARD_PATCHED:
        return {"schema_version": "football3-returning-club-state-integrity-guard-v1", "installed": True, "idempotent_reuse": True, "identity_contract": SCHEMA, "zero_history_fail_closed": True, "evidence_threshold_changed": False, "model_parameters_or_weights_changed": False}
    original = guard_module.classify_state
    def classify_state(loaded, fixture, identity_audit, trigger, receipt):
        out = original(loaded, fixture, identity_audit, trigger, receipt); reasons = list(out.get("anomaly_reasons") or []); returning_failure = False
        for side in ("home", "away"):
            ident = identity_audit[side]
            if str(ident.get("participation_classification") or "") in RETURNING:
                missing = int(ident.get("historical_match_count") or 0) <= 0 or not bool(ident.get("state_evidence")) or not bool(ident.get("state_local_evidence")) or not bool(ident.get("state_global_evidence"))
                linked_xg = int((out.get("historical_xg") or {}).get(f"{side}_linked_2025_26_matches") or 0)
                if bool(ident.get("legal_xg_coverage_available")) and linked_xg <= 0: missing = True
                if missing:
                    returning_failure = True; code = f"{side.upper()}_RETURNING_CLUB_HISTORICAL_STATE_MISSING"
                    if code not in reasons: reasons.append(code)
        if reasons != list(out.get("anomaly_reasons") or []):
            out["anomaly_reasons"] = reasons; out["status"] = "DATA_STATE_ANOMALY"; out["fallback_class"] = "DATA_STATE_ANOMALY"; out["fallback_reason"] = "RETURNING_CLUB_HISTORICAL_STATE_MISSING;" + ";".join(reasons)
        out["returning_club_guard"] = {"status": "FAIL_CLOSED" if returning_failure else "PASS", "reason_code": "RETURNING_CLUB_HISTORICAL_STATE_MISSING" if returning_failure else None, "identity_contract": SCHEMA}
        return out
    guard_module.classify_state = classify_state; _GUARD_PATCHED = True
    return {"schema_version": "football3-returning-club-state-integrity-guard-v1", "installed": True, "identity_contract": SCHEMA, "returning_classifications": sorted(RETURNING), "zero_history_fail_closed": True, "normal_fallback_from_lookup_zero_prohibited": True, "evidence_threshold_changed": False, "model_parameters_or_weights_changed": False}


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"schema_version": SCHEMA, "installed": True, "idempotent_reuse": True, "canonical_global_identity_separate_from_historical_state_key": True, "fuzzy_matching": False, "result_or_xg_values_used_for_identity": False, "model_parameters_or_weights_changed": False, "formal_current_or_production_pointer_changed": False}
    legacy.resolve_team = resolve_team; legacy.resolve_fixture = resolve_fixture
    import formal_state_integrity_guard_v1 as guard_module
    guard_contract = patch_guard(guard_module); _INSTALLED = True
    return {"schema_version": SCHEMA, "installed": True, "canonical_global_identity_separate_from_historical_state_key": True, "authoritative_manifest_methods": sorted(ALLOWED_MANIFEST_METHODS), "schedule_fingerprint_min_f1": MIN_SCHEDULE_FINGERPRINT_F1, "schedule_fingerprint_min_margin": MIN_SCHEDULE_FINGERPRINT_MARGIN, "season_clock": "CURRENT_ROSTER_AUTHORITY_OBSERVED_AT_NOT_MODEL_TRAINING_SEASONS", "calendar_year_leagues_supported": True, "natural_year_competitions": sorted(NATURAL_YEAR_COMPETITIONS), "continuity_registry": CONTINUITY_REGISTRY, "participation_classification_uses_history_lookup_zero": False, "fuzzy_matching": False, "result_or_xg_values_used_for_identity": False, "model_parameters_or_weights_changed": False, "formal_current_or_production_pointer_changed": False, "returning_club_guard": guard_contract}


_ORIGINAL_RESOLVE_TEAM = legacy.resolve_team
_ORIGINAL_RESOLVE_FIXTURE = legacy.resolve_fixture