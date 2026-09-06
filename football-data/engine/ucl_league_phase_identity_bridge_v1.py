#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platform_core import (
    ROOT,
    PlatformError,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    sha256_file,
    stable_team_id,
)

SCHEMA = "football3-ucl-league-phase-identity-bridge-v1"
REGISTRY_PATH = ROOT / "config" / "ucl_2026_27_league_phase_identity_v1.json"
FULL17_REGISTRY = ROOT / "config" / "v6_full17_capture_identity_v6484.json"
FULL17_ALIASES = ROOT / "config" / "v6_full17_provider_aliases_v6483.json"
LINEUP_MANIFEST_DIR = ROOT / "manifests" / "lineup_match_identity_v502"
ALLOWED_MANIFEST_METHODS = {"exact_normalized_unique", "mutual_schedule_fingerprint"}
MIN_SCHEDULE_FINGERPRINT_F1 = 0.99
MIN_SCHEDULE_FINGERPRINT_MARGIN = 0.75
_INSTALLED = False


def _canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _global_id_from_key(key: str) -> str:
    return "club_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _exact_key(value: str) -> str:
    """Exact-name comparison key; deliberately keeps club tokens such as FC/CF/SC."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"[\u2010-\u2015'’`.]+", " ", text)
    text = re.sub(r"[^\w\sáàâäãåæçéèêëíìîïñóòôöõøœúùûüýÿšžčćđğışł]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _registry() -> dict[str, Any]:
    data = load_json(REGISTRY_PATH)
    if data.get("schema_version") != "football3-ucl-2026-27-league-phase-identity-v1":
        raise PlatformError("UCL league-phase identity registry schema mismatch")
    if data.get("competition_id") != "UEFA_ChampionsLeague" or data.get("season") != "2026/27" or data.get("phase") != "league_phase":
        raise PlatformError("UCL league-phase identity registry scope mismatch")
    teams = data.get("teams")
    if not isinstance(teams, list) or len(teams) != 36 or data.get("team_count") != 36:
        raise PlatformError("UCL league-phase registry must contain exactly 36 teams")
    names = [str(row.get("uefa_name") or "").strip() for row in teams if isinstance(row, dict)]
    if len(names) != 36 or len(set(names)) != 36 or any(not name for name in names):
        raise PlatformError("UCL league-phase registry team names are incomplete or duplicate")
    roster_sha = _sha_bytes(json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if roster_sha != data.get("roster_sha256"):
        raise PlatformError("UCL league-phase registry roster SHA mismatch")
    alias_owner: dict[str, str] = {}
    for row in teams:
        assoc = str(row.get("association_code") or "").strip()
        aliases = row.get("accepted_exact_names")
        if not re.fullmatch(r"[A-Z]{3}", assoc) or not isinstance(aliases, list) or not aliases:
            raise PlatformError(f"UCL registry identity row invalid: {row.get('uefa_name')}")
        for alias in aliases:
            token = _exact_key(str(alias))
            if not token:
                raise PlatformError(f"UCL registry empty exact alias: {row.get('uefa_name')}")
            owner = alias_owner.setdefault(token, row["uefa_name"])
            if owner != row["uefa_name"]:
                raise PlatformError(f"UCL exact alias collision: {alias!r} -> {owner!r}/{row['uefa_name']!r}")
    authority = data.get("authority") or {}
    if authority.get("qualification_roster_is_authoritative_for_league_phase") is not False:
        raise PlatformError("UCL qualifying roster isolation contract missing")
    return data


def registry_snapshot() -> dict[str, Any]:
    data = _registry()
    return {
        "schema_version": SCHEMA,
        "registry_path": str(REGISTRY_PATH.relative_to(ROOT)),
        "registry_sha256": sha256_file(REGISTRY_PATH),
        "season": data["season"],
        "phase": data["phase"],
        "team_count": data["team_count"],
        "roster_sha256": data["roster_sha256"],
        "authority": data["authority"],
        "qualification_roster_used": False,
        "fuzzy_matching_used": False,
        "result_or_score_or_xg_values_used_for_identity": False,
    }


def _in_valid_window(kickoff: datetime) -> bool:
    data = _registry()
    window = data.get("identity_valid_window") or {}
    start = parse_iso_datetime(window.get("start_utc"), "UCL identity start")
    end = parse_iso_datetime(window.get("end_utc"), "UCL identity end")
    moment = kickoff.astimezone(timezone.utc)
    return start <= moment < end


def resolve_current(name: str) -> dict[str, Any]:
    raw = str(name or "").strip()
    if not raw:
        raise PlatformError("empty UCL current team identity")
    data = _registry()
    token = _exact_key(raw)
    matches = []
    for row in data["teams"]:
        aliases = [str(x).strip() for x in row["accepted_exact_names"]]
        if token in {_exact_key(x) for x in aliases}:
            matches.append(row)
    if len(matches) != 1:
        raise PlatformError(f"UCL current league-phase identity not unique: {raw!r}")
    row = dict(matches[0])
    row["requested_name"] = raw
    row["current_team_id"] = stable_team_id("UEFA_ChampionsLeague", row["uefa_name"])
    return row


def _domestic_registry_row(row: dict[str, Any]) -> dict[str, Any] | None:
    comp = row.get("domestic_formal_competition_id")
    if not comp:
        return None
    registry = load_json(FULL17_REGISTRY)
    c = (registry.get("competitions") or {}).get(comp)
    if not isinstance(c, dict) or c.get("status") != "PASS" or not isinstance(c.get("teams"), list):
        raise PlatformError(f"UCL domestic formal registry unavailable: {comp}")
    amap: dict[str, str] = {}
    if FULL17_ALIASES.is_file():
        raw_aliases = (load_json(FULL17_ALIASES).get("aliases") or {}).get(comp)
        if isinstance(raw_aliases, dict):
            amap = {str(k): str(v) for k, v in raw_aliases.items()}
    requested_keys = {_exact_key(x) for x in row["accepted_exact_names"]}
    matches = []
    for team in c["teams"]:
        if not isinstance(team, dict):
            continue
        canonical = str(team.get("canonical_name") or "").strip()
        names = [canonical] + [str(x) for x in (team.get("provider_alias_tokens") or [])]
        names += [alias for alias, target in amap.items() if _exact_key(target) == _exact_key(canonical)]
        if requested_keys & {_exact_key(x) for x in names if x}:
            matches.append({
                "canonical_name": canonical,
                "accepted_names": list(dict.fromkeys(names)),
                "source_path": team.get("source_path"),
                "registry_path": str(FULL17_REGISTRY.relative_to(ROOT)),
                "registry_sha256": sha256_file(FULL17_REGISTRY),
                "alias_registry_path": str(FULL17_ALIASES.relative_to(ROOT)) if FULL17_ALIASES.is_file() else None,
                "alias_registry_sha256": sha256_file(FULL17_ALIASES) if FULL17_ALIASES.is_file() else None,
                "matching_method": "EXACT_REGISTERED_NAME_OR_ALIAS",
            })
    if len(matches) != 1:
        raise PlatformError(f"UCL -> domestic current identity not unique: {row['uefa_name']} -> {comp}; hits={len(matches)}")
    return matches[0]


def _manifest_row_eligible(mapped: dict[str, Any]) -> bool:
    method = str(mapped.get("method") or "")
    if method == "exact_normalized_unique":
        return float(mapped.get("confidence") or 0.0) == 1.0
    if method == "mutual_schedule_fingerprint":
        return (
            bool(mapped.get("mutual_best"))
            and float(mapped.get("fingerprint_f1") or 0.0) >= MIN_SCHEDULE_FINGERPRINT_F1
            and float(mapped.get("best_margin") or 0.0) >= MIN_SCHEDULE_FINGERPRINT_MARGIN
        )
    return False


def _provider_identity(comp: str, names: list[str]) -> dict[str, Any] | None:
    path = LINEUP_MANIFEST_DIR / f"{comp}.json"
    if not path.is_file():
        return None
    manifest = load_json(path)
    if manifest.get("status") != "IDENTITY_BRIDGE_PASS" or int(manifest.get("ambiguous_fixture_count") or 0) != 0:
        raise PlatformError(f"UCL domestic provider identity manifest not authoritative: {comp}")
    crosswalk = manifest.get("crosswalk")
    if not isinstance(crosswalk, dict):
        raise PlatformError(f"UCL domestic provider crosswalk missing: {comp}")
    keys = {_exact_key(x) for x in names if str(x or "").strip()}
    hits = []
    for provider_id, mapped in crosswalk.items():
        if not isinstance(mapped, dict) or not _manifest_row_eligible(mapped):
            continue
        accepted = [str(mapped.get("processed_team") or "")] + [str(x) for x in (mapped.get("source_names") or [])]
        if keys & {_exact_key(x) for x in accepted if x}:
            hits.append((str(provider_id), mapped))
    if len(hits) > 1:
        raise PlatformError(f"UCL domestic provider identity ambiguous: {comp} {names[0]}")
    if not hits:
        return None
    provider_id, mapped = hits[0]
    return {
        "provider_identity": provider_id,
        "method": mapped["method"],
        "source": str(path.relative_to(ROOT)),
        "source_sha256": sha256_file(path),
        "processed_team": mapped.get("processed_team"),
        "mutual_best": mapped.get("mutual_best"),
        "fingerprint_f1": mapped.get("fingerprint_f1"),
        "best_margin": mapped.get("best_margin"),
        "selection_uses_result_or_score_or_xg": False,
    }


def canonical_global_identity(row: dict[str, Any]) -> dict[str, Any]:
    domestic = _domestic_registry_row(row)
    if domestic is not None:
        comp = str(row["domestic_formal_competition_id"])
        names = list(dict.fromkeys([row["uefa_name"], *row["accepted_exact_names"], domestic["canonical_name"], *domestic["accepted_names"]]))
        provider = _provider_identity(comp, names)
        if provider is not None:
            key = f"provider:{provider['provider_identity']}"
            method = "AUTHORITATIVE_DOMESTIC_PROVIDER_ID"
            provenance = provider
        else:
            key = f"canonical:{comp}:{normalize_team_token(domestic['canonical_name'])}"
            method = "EXACT_DOMESTIC_CURRENT_CANONICAL"
            provenance = {
                "source": domestic["registry_path"],
                "source_sha256": domestic["registry_sha256"],
                "processed_team": domestic["canonical_name"],
                "matching_method": domestic["matching_method"],
            }
    else:
        key = f"association:{row['association_code']}:{normalize_team_token(row['uefa_name'])}"
        method = "UEFA_OFFICIAL_ASSOCIATION_AND_EXACT_NAME"
        provenance = {
            "source": str(REGISTRY_PATH.relative_to(ROOT)),
            "source_sha256": sha256_file(REGISTRY_PATH),
            "association_code": row["association_code"],
        }
    return {
        "canonical_global_id": _global_id_from_key(key),
        "global_identity_key_sha256": hashlib.sha256(key.encode("utf-8")).hexdigest(),
        "method": method,
        "provenance": provenance,
    }


def _legacy_name_parts(value: str) -> tuple[str, str | None]:
    text = str(value or "").strip()
    m = re.fullmatch(r"(.+?)\s+\(([A-Z]{3})\)", text)
    if not m:
        return text, None
    return m.group(1).strip(), m.group(2)


def _previous_ucl_season() -> str:
    season = str(_registry().get("season") or "")
    m = re.fullmatch(r"(\d{4})/(\d{2})", season)
    if not m:
        raise PlatformError(f"UCL season format unsupported for historical identity: {season!r}")
    start = int(m.group(1))
    return f"{start - 1}/{str(start)[-2:]}"


def resolve_snapshot_team(snapshot: dict[str, Any], requested: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = resolve_current(requested)
    accepted = {_exact_key(x) for x in row["accepted_exact_names"]}
    hits = []
    for team in snapshot.get("teams", []):
        if not isinstance(team, dict):
            continue
        base, assoc = _legacy_name_parts(str(team.get("team_name") or ""))
        if assoc != row["association_code"]:
            continue
        if _exact_key(base) in accepted:
            hits.append(team)
    initial_hit_count = len(hits)
    mapping_method = "UEFA_OFFICIAL_EXACT_ALIAS_PLUS_ASSOCIATION_CODE"
    participation_season = None
    if len(hits) > 1:
        participation_season = _previous_ucl_season()
        active = [
            team for team in hits
            if participation_season in {str(x) for x in (team.get("seasons_observed") or [])}
        ]
        if len(active) != 1:
            raise PlatformError(
                f"UCL league-phase -> historical strength identity not unique after previous-season participation: "
                f"{requested!r}; exact_hits={len(hits)}; participation_hits={len(active)}; season={participation_season}"
            )
        hits = active
        mapping_method = "UEFA_OFFICIAL_EXACT_ALIAS_PLUS_ASSOCIATION_CODE_PLUS_PREVIOUS_SEASON_PARTICIPATION"
    if len(hits) != 1:
        raise PlatformError(f"UCL league-phase -> historical strength identity not unique: {requested!r}; hits={len(hits)}")
    global_identity = canonical_global_identity(row)
    audit = {
        "schema_version": SCHEMA,
        "season": "2026/27",
        "phase": "league_phase",
        "requested_name": requested,
        "current_uefa_name": row["uefa_name"],
        "current_team_id": row["current_team_id"],
        "association_code": row["association_code"],
        "domestic_formal_competition_id": row.get("domestic_formal_competition_id"),
        "canonical_global_id": global_identity["canonical_global_id"],
        "canonical_global_method": global_identity["method"],
        "canonical_global_provenance": global_identity["provenance"],
        "historical_strength_team_id": hits[0].get("team_id"),
        "historical_strength_team_name": hits[0].get("team_name"),
        "historical_strength_matches": int(((hits[0].get("overall") or {}).get("matches") or 0)),
        "historical_strength_initial_exact_hit_count": initial_hit_count,
        "historical_strength_previous_season_participation_filter": participation_season,
        "registry_sha256": sha256_file(REGISTRY_PATH),
        "roster_sha256": _registry()["roster_sha256"],
        "mapping_method": mapping_method,
        "identity_selection_fields": ["accepted_exact_names", "association_code", "seasons_observed_if_exact_alias_is_rekeyed"],
        "qualification_roster_used": False,
        "fuzzy_matching_used": False,
        "result_or_score_or_xg_values_used_for_identity": False,
    }
    return hits[0], audit


def install(match_pipeline_module) -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"schema_version": SCHEMA, "installed": True, "idempotent_reuse": True}
    original = match_pipeline_module._team_assessment

    def _team_assessment(competition: dict[str, Any], home_team: str, away_team: str, kickoff):
        if competition.get("competition_id") != "UEFA_ChampionsLeague" or not _in_valid_window(kickoff):
            return original(competition, home_team, away_team, kickoff)
        path = match_pipeline_module.TEAM_STRENGTH_ROOT / "UEFA_ChampionsLeague" / "latest.json"
        if not path.is_file():
            return {"status": "不可用", "error_codes": ["D01"], "reason": "UCL historical strength snapshot missing"}
        snapshot = load_json(path)
        try:
            home, home_audit = resolve_snapshot_team(snapshot, home_team)
            away, away_audit = resolve_snapshot_team(snapshot, away_team)
        except PlatformError as exc:
            return {
                "status": "不可用",
                "error_codes": ["D01"],
                "reason": f"DATA_STATE_ANOMALY:{exc}",
                "identity_contract": registry_snapshot(),
            }
        return {
            "status": "通过",
            "error_codes": [],
            "data_as_of": snapshot.get("data_as_of"),
            "feature_status": snapshot.get("feature_status"),
            "home": home,
            "away": away,
            "special_current_competition_unavailable": False,
            "snapshot_sha256": sha256_file(path),
            "identity_bridge": {
                "schema_version": SCHEMA,
                "home": home_audit,
                "away": away_audit,
                "registry": registry_snapshot(),
                "status": "PASS",
            },
        }

    match_pipeline_module._team_assessment = _team_assessment
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "scope": "UEFA_ChampionsLeague_2026_27_league_phase_identity_only",
        "registry_sha256": sha256_file(REGISTRY_PATH),
        "team_count": 36,
        "historical_rekey_disambiguation": "EXACT_ALIAS_ASSOCIATION_THEN_UNIQUE_PREVIOUS_SEASON_PARTICIPATION",
        "qualification_roster_used": False,
        "fuzzy_matching_used": False,
        "result_or_score_or_xg_values_used_for_identity": False,
        "model_or_weight_changed": False,
        "fusion_v2_scope_changed": False,
    }