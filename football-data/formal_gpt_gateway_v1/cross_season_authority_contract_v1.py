#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cross_season_team_state_binding_v1 as binding
import cross_season_participation_authority_v1 as participation
import runtime as rt

SCHEMA = "football3-cross-season-authority-contract-v1"
CROSSWALK = "football-data/manifests/cross_season_authoritative_entity_crosswalk_v1.json"


def _load(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError(f"cross-season authority source unreadable: {path}") from exc
    if type(obj) is not dict:
        raise rt.RuntimeGateError(f"cross-season authority object required: {path}")
    return obj


def _norm(value: Any) -> str:
    return rt._normalize_team(str(value or "").strip())


def _crosswalk_row(repo_root: Path, ident: dict[str, Any]) -> tuple[dict[str, Any], str]:
    path = repo_root / CROSSWALK
    obj = _load(path)
    if obj.get("schema_version") != "football3-cross-season-authoritative-entity-crosswalk-v1":
        raise rt.RuntimeGateError("cross-season authoritative crosswalk schema mismatch")
    if obj.get("status") != "PASS" or obj.get("fuzzy_matching") is not False or obj.get("result_or_score_or_xg_assisted_identity") is not False:
        raise rt.RuntimeGateError("cross-season authoritative crosswalk governance mismatch")
    rows = obj.get("rows")
    if type(rows) is not list:
        raise rt.RuntimeGateError("cross-season authoritative crosswalk rows missing")
    expected = {
        "competition_id": str(ident.get("mapping_provenance", {}).get("competition") or ""),
        "current_season": str(ident.get("mapping_provenance", {}).get("current_season") or ""),
        "current_canonical_name": str(ident.get("current_name") or ""),
        "previous_season": str(ident.get("previous_season") or ""),
        "historical_entity": str(ident.get("strength_alias") or ""),
    }
    hits = []
    for candidate in rows:
        if type(candidate) is not dict:
            continue
        if str(candidate.get("competition_id") or "") != expected["competition_id"]:
            continue
        if str(candidate.get("current_season") or "") != expected["current_season"]:
            continue
        if _norm(candidate.get("current_canonical_name")) != _norm(expected["current_canonical_name"]):
            continue
        if str(candidate.get("previous_season") or "") != expected["previous_season"]:
            continue
        if _norm(candidate.get("historical_entity")) != _norm(expected["historical_entity"]):
            continue
        hits.append(candidate)
    if not hits:
        required = {
            (str(x.get("competition_id") or ""), _norm(x.get("current_canonical_name")))
            for x in (obj.get("required_current_endpoints") or []) if type(x) is dict
        }
        if (expected["competition_id"], _norm(expected["current_canonical_name"])) not in required:
            return {}, rt._sha_file(path)
    if len(hits) != 1:
        raise rt.RuntimeGateError(
            f"authority crosswalk endpoint not unique: {expected['competition_id']} "
            f"{expected['current_season']} {expected['current_canonical_name']}->{expected['historical_entity']}; hits={len(hits)}"
        )
    row = hits[0]
    if not str(row.get("id") or ""):
        raise rt.RuntimeGateError("authority crosswalk id missing")
    return row, rt._sha_file(path)


def _check_current_snapshot(repo_root: Path, comp: str, check: dict[str, Any]) -> dict[str, Any]:
    path = repo_root / str(check["path"])
    obj = _load(path)
    if str(obj.get("competition_id") or "") != comp:
        raise rt.RuntimeGateError(f"current snapshot competition mismatch: {path}")
    exact = str(check["team_name"])
    if _norm(obj.get("team_name")) != _norm(exact):
        raise rt.RuntimeGateError(f"current snapshot team mismatch: {path}")
    provider = check.get("provider_id")
    if provider:
        ids = obj.get("provider_ids")
        if type(ids) is not dict or str(ids.get(str(provider["field"])) or "") != str(provider["value"]):
            raise rt.RuntimeGateError(f"current snapshot provider id mismatch: {path}")
    return {"type": "CURRENT_SNAPSHOT_EXACT", "path": str(check["path"]), "sha256": rt._sha_file(path), "team_name": exact}


def _check_current_registry(repo_root: Path, comp: str, season: str, check: dict[str, Any]) -> dict[str, Any]:
    exact = str(check["team_name"])
    rows, sources = binding._registry_rows(repo_root, comp, season)
    hits = [row for row in rows if _norm(row.get("canonical_name")) == _norm(exact)]
    if len(hits) != 1:
        raise rt.RuntimeGateError(f"authority current registry identity not unique: {comp} {season} {exact}; hits={len(hits)}")
    return {"type": "CURRENT_REGISTRY_EXACT", "team_name": str(hits[0]["canonical_name"]), "season": season, "sources": sources}


def _check_previous_roster(repo_root: Path, comp: str, previous: str, check: dict[str, Any]) -> dict[str, Any]:
    exact = str(check["team_name"])
    roster = binding._processed_roster(repo_root, comp, previous)
    hits = [name for name in roster if _norm(name) == _norm(exact)]
    if len(hits) != 1:
        raise rt.RuntimeGateError(f"authority previous roster identity not unique: {comp} {previous} {exact}; hits={len(hits)}")
    return {"type": "PREVIOUS_ROSTER_EXACT", "team_name": hits[0], "season": previous}


def _check_json_alias(repo_root: Path, comp: str, check: dict[str, Any]) -> dict[str, Any]:
    path = repo_root / str(check["path"])
    obj = _load(path)
    aliases = (obj.get("aliases") or {}).get(comp)
    if type(aliases) is not dict:
        raise rt.RuntimeGateError(f"authority alias competition missing: {path} {comp}")
    source = str(check["source"])
    target = str(check["target"])
    exact_hits = [(str(k), str(v)) for k, v in aliases.items() if _norm(k) == _norm(source)]
    if len(exact_hits) != 1 or _norm(exact_hits[0][1]) != _norm(target):
        raise rt.RuntimeGateError(f"authority exact alias mismatch: {path} {source}->{target}")
    return {"type": "JSON_ALIAS_EXACT", "path": str(check["path"]), "sha256": rt._sha_file(path), "source": exact_hits[0][0], "target": exact_hits[0][1]}


def _check_lineup_manifest(repo_root: Path, comp: str, check: dict[str, Any]) -> dict[str, Any]:
    path = repo_root / str(check["path"])
    obj = _load(path)
    if obj.get("status") != "IDENTITY_BRIDGE_PASS" or int(obj.get("ambiguous_fixture_count") or 0) != 0:
        raise rt.RuntimeGateError(f"authority lineup manifest not PASS: {path}")
    cross = obj.get("crosswalk")
    provider = str(check["provider_identity"])
    if type(cross) is not dict or type(cross.get(provider)) is not dict:
        raise rt.RuntimeGateError(f"authority lineup provider missing: {path} {provider}")
    raw = cross[provider]
    if not binding._manifest_row_eligible(raw):
        raise rt.RuntimeGateError(f"authority lineup provider not eligible: {path} {provider}")
    processed = str(check["processed_team"])
    source_name = str(check["source_name"])
    if _norm(raw.get("processed_team")) != _norm(processed):
        raise rt.RuntimeGateError(f"authority lineup processed identity mismatch: {path} {provider}")
    names = [str(x) for x in (raw.get("source_names") or [])]
    if _norm(source_name) not in {_norm(x) for x in names}:
        raise rt.RuntimeGateError(f"authority lineup source identity mismatch: {path} {provider}")
    return {"type": "LINEUP_MANIFEST_EXACT", "path": str(check["path"]), "sha256": rt._sha_file(path), "provider_identity": provider, "source_name": source_name, "processed_team": processed, "method": raw.get("method")}


def _validate_continuity(repo_root: Path, ident: dict[str, Any]) -> dict[str, Any]:
    mapping = ((ident.get("mapping_provenance") or {}).get("identity_mapping") or {})
    if str(mapping.get("method") or "") != "AUDITED_EXACT_CROSS_SEASON_CONTINUITY_REGISTRY":
        return {"status": "NOT_APPLICABLE", "method": mapping.get("method")}
    row, crosswalk_sha = _crosswalk_row(repo_root, ident)
    if not row:
        return {"status": "NOT_IN_CURRENT_REPAIR_SCOPE", "method": mapping.get("method"), "authority_crosswalk_sha256": crosswalk_sha}
    checks = row.get("checks")
    if type(checks) is not list or not checks:
        raise rt.RuntimeGateError(f"authority crosswalk checks missing: {row.get('id')}")
    comp = str(row["competition_id"])
    previous = str(row["previous_season"])
    outcomes = []
    roles = set()
    for check in checks:
        if type(check) is not dict:
            raise rt.RuntimeGateError(f"authority crosswalk check object required: {row.get('id')}")
        kind = str(check.get("type") or "")
        raw_role = check.get("role")
        if type(raw_role) is list:
            roles.update(str(x) for x in raw_role if str(x or ""))
        elif str(raw_role or ""):
            roles.add(str(raw_role))
        if kind == "CURRENT_SNAPSHOT_EXACT":
            outcomes.append(_check_current_snapshot(repo_root, comp, check))
        elif kind == "CURRENT_REGISTRY_EXACT":
            outcomes.append(_check_current_registry(repo_root, comp, str(row["current_season"]), check))
        elif kind == "PREVIOUS_ROSTER_EXACT":
            outcomes.append(_check_previous_roster(repo_root, comp, previous, check))
        elif kind == "JSON_ALIAS_EXACT":
            outcomes.append(_check_json_alias(repo_root, comp, check))
        elif kind == "LINEUP_MANIFEST_EXACT":
            outcomes.append(_check_lineup_manifest(repo_root, comp, check))
        else:
            raise rt.RuntimeGateError(f"unsupported authority crosswalk check: {row.get('id')} {kind}")
    if "current" not in roles or "historical" not in roles:
        raise rt.RuntimeGateError(f"authority crosswalk endpoint roles incomplete: {row.get('id')}")
    return {"status": "PASS", "authority_crosswalk_id": row["id"], "authority_crosswalk_sha256": crosswalk_sha, "checks": outcomes, "fuzzy_matching_used": False, "result_or_score_or_xg_values_used_for_identity": False}


def validate_team(repo_root: Path, ident: dict[str, Any]) -> dict[str, Any]:
    participation_result = participation.validate_team(repo_root, ident)
    continuity_result = _validate_continuity(repo_root, ident)
    return {
        "schema_version": SCHEMA,
        "status": "PASS",
        "participation": participation_result,
        "identity_continuity": continuity_result,
        "fuzzy_matching_used": False,
        "history_lookup_zero_used_as_promotion_evidence": False,
        "result_or_score_or_xg_values_used_for_identity": False,
    }


def validate_fixture(repo_root: Path, audit: dict[str, Any]) -> dict[str, Any]:
    result = {"schema_version": SCHEMA, "status": "PASS", "home": validate_team(repo_root, audit["home"]), "away": validate_team(repo_root, audit["away"])}
    result["validated_continuity_sides"] = sum(1 for side in ("home", "away") if result[side]["identity_continuity"]["status"] == "PASS")
    result["validated_participation_sides"] = sum(1 for side in ("home", "away") if result[side]["participation"]["status"] == "PASS")
    return result
