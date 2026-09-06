#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import runtime as rt

SCHEMA = "football3-cross-season-participation-authority-v1"
MANIFEST = "football-data/config/cross_season_participation_authority_v1.json"
RETURNING = {"CONTINUING_CLUB", "RENAMED_OR_REKEYED_CLUB"}
PROMOTED = "PROMOTED_OR_ENTERING_CLUB"


def _load(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError(f"participation authority unreadable: {path}") from exc
    if type(obj) is not dict:
        raise rt.RuntimeGateError(f"participation authority object required: {path}")
    return obj


def _norm(value: Any) -> str:
    return rt._normalize_team(str(value or "").strip())


def _manifest(repo_root: Path) -> tuple[dict[str, Any], str]:
    path = repo_root / MANIFEST
    obj = _load(path)
    gov = obj.get("governance") or {}
    if obj.get("schema_version") != SCHEMA or obj.get("status") != "PASS":
        raise rt.RuntimeGateError("participation authority schema/status mismatch")
    if gov.get("promotion_requires_authoritative_evidence") is not True:
        raise rt.RuntimeGateError("participation authority promotion proof gate disabled")
    if gov.get("history_lookup_zero_is_not_promotion_evidence") is not True:
        raise rt.RuntimeGateError("participation authority zero-history policy mismatch")
    if any(gov.get(k) is not False for k in ("fuzzy_matching", "score_result_xg_assisted_participation", "club_specific_runtime_branching")):
        raise rt.RuntimeGateError("participation authority governance mismatch")
    domains = obj.get("domains")
    if type(domains) is not dict:
        raise rt.RuntimeGateError("participation authority domains missing")
    return obj, rt._sha_file(path)


def domain_contract(repo_root: Path, comp: str, season: str, *, required: bool = True) -> dict[str, Any] | None:
    obj, manifest_sha = _manifest(repo_root)
    raw = (obj.get("domains") or {}).get(comp)
    if type(raw) is not dict:
        if required:
            raise rt.RuntimeGateError(f"participation authority domain missing: {comp}")
        return None
    if str(raw.get("formal_current_season") or "") != str(season):
        raise rt.RuntimeGateError(
            f"participation authority season mismatch: {comp} formal={season} "
            f"authority={raw.get('formal_current_season')}"
        )
    prev = str(raw.get("frozen_history_reference_season") or "").strip()
    official = str(raw.get("official_current_season") or "").strip()
    model = str(raw.get("season_model") or "").strip()
    promoted = raw.get("authoritative_promoted_teams")
    sources = raw.get("official_sources")
    if not prev or not official or not model or type(promoted) is not list or type(sources) is not list or not sources:
        raise rt.RuntimeGateError(f"participation authority domain contract incomplete: {comp}")
    names = []
    for row in promoted:
        if type(row) is not dict:
            raise rt.RuntimeGateError(f"participation authority promoted row invalid: {comp}")
        name = str(row.get("current_name") or "").strip()
        if not name:
            raise rt.RuntimeGateError(f"participation authority promoted current name missing: {comp}")
        names.append(name)
    tokens = [_norm(x) for x in names]
    if len(tokens) != len(set(tokens)):
        raise rt.RuntimeGateError(f"participation authority duplicate promoted identity: {comp}")
    return {
        "schema_version": SCHEMA,
        "competition_id": comp,
        "formal_current_season": str(season),
        "official_current_season": official,
        "frozen_history_reference_season": prev,
        "season_model": model,
        "transition_predecessor": raw.get("transition_predecessor"),
        "authoritative_promoted_teams": promoted,
        "authoritative_promoted_team_count": len(promoted),
        "official_sources": sources,
        "manifest_path": MANIFEST,
        "manifest_sha256": manifest_sha,
        "history_lookup_zero_is_not_promotion_evidence": True,
        "fuzzy_matching_used": False,
    }


def authoritative_promoted_names(repo_root: Path, comp: str, season: str) -> list[str]:
    contract = domain_contract(repo_root, comp, season)
    assert contract is not None
    return [str(x["current_name"]) for x in contract["authoritative_promoted_teams"]]


def expected_participation(repo_root: Path, comp: str, season: str, current_name: str) -> dict[str, Any]:
    contract = domain_contract(repo_root, comp, season)
    assert contract is not None
    token = _norm(current_name)
    hits = [row for row in contract["authoritative_promoted_teams"] if _norm(row.get("current_name")) == token]
    if len(hits) > 1:
        raise rt.RuntimeGateError(f"participation authority promoted identity ambiguous: {comp} {current_name}")
    return {
        "expected_class": "AUTHORITATIVE_PROMOTED_OR_ENTERING" if hits else "AUTHORITATIVE_RETURNING_EXPECTED",
        "promotion_evidence": hits[0] if hits else None,
        "contract": contract,
    }


def validate_team(repo_root: Path, ident: dict[str, Any]) -> dict[str, Any]:
    prov = ident.get("mapping_provenance") or {}
    comp = str(prov.get("competition") or "")
    season = str(prov.get("current_season") or "")
    current = str(ident.get("current_name") or "")
    if not comp or not season or not current:
        raise rt.RuntimeGateError("participation authority identity endpoint incomplete")
    contract = domain_contract(repo_root, comp, season, required=False)
    if contract is None:
        return {"schema_version": SCHEMA, "status": "NOT_APPLICABLE", "competition_id": comp}
    exp = expected_participation(repo_root, comp, season, current)
    actual = str(ident.get("participation_classification") or "")
    if exp["expected_class"] == "AUTHORITATIVE_PROMOTED_OR_ENTERING":
        if actual != PROMOTED:
            raise rt.RuntimeGateError(
                f"AUTHORITATIVE_PROMOTED_CLUB_MISCLASSIFIED_AS_RETURNING: {comp} {season} {current}; actual={actual}"
            )
    else:
        if actual == PROMOTED:
            raise rt.RuntimeGateError(
                f"PROMOTED_CLASSIFICATION_LACKS_AUTHORITATIVE_PARTICIPATION_EVIDENCE: "
                f"{comp} {season} {current}; history_lookup_zero_is_not_promotion_evidence"
            )
        if actual not in RETURNING:
            raise rt.RuntimeGateError(f"RETURNING_EXPECTED_CLASSIFICATION_INVALID: {comp} {season} {current}; actual={actual}")
        hist = int(ident.get("historical_match_count") or 0)
        local = bool(ident.get("state_local_evidence"))
        global_ = bool(ident.get("state_global_evidence"))
        if hist <= 0 or not local or not global_:
            raise rt.RuntimeGateError(
                f"RETURNING_EXPECTED_HISTORICAL_STATE_MISSING: {comp} {season} {current}; "
                f"historical_match_count={hist},local={int(local)},global={int(global_)}"
            )
    return {
        "schema_version": SCHEMA,
        "status": "PASS",
        "competition_id": comp,
        "formal_current_season": season,
        "official_current_season": contract["official_current_season"],
        "frozen_history_reference_season": contract["frozen_history_reference_season"],
        "season_model": contract["season_model"],
        "transition_predecessor": contract["transition_predecessor"],
        "current_name": current,
        "expected_class": exp["expected_class"],
        "actual_class": actual,
        "promotion_evidence": exp["promotion_evidence"],
        "manifest_path": contract["manifest_path"],
        "manifest_sha256": contract["manifest_sha256"],
        "history_lookup_zero_is_not_promotion_evidence": True,
        "fuzzy_matching_used": False,
        "result_or_score_or_xg_values_used_for_participation": False,
    }
