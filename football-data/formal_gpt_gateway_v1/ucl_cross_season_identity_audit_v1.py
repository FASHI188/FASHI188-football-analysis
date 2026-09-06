#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import runtime as rt
import cross_season_team_state_binding_v1 as domestic_bridge
import formal_state_integrity_guard_v1 as guard

from platform_core import ROOT as FOOTBALL_ROOT, load_json, normalize_team_token, sha256_file
import ucl_league_phase_identity_bridge_v1 as ucl_bridge

SCHEMA = "football3-ucl-2026-27-cross-season-identity-audit-v1"
UCL = "UEFA_ChampionsLeague"


def _current_domestic_season(repo: Path, comp: str) -> str:
    registry = load_json(repo / domestic_bridge.CURRENT_REGISTRY)
    c = (registry.get("competitions") or {}).get(comp)
    if not isinstance(c, dict):
        raise rt.RuntimeGateError(f"domestic current registry missing: {comp}")
    hint = str(c.get("processed_latest_season_hint") or "")
    current = domestic_bridge._current_from_hint(hint)
    if not current:
        raise rt.RuntimeGateError(f"domestic current season unresolved: {comp} hint={hint}")
    return current


def _historical_base_name(value: str) -> str:
    text = str(value or "").strip()
    m = re.fullmatch(r"(.+?)\s+\([A-Z]{3}\)", text)
    return m.group(1).strip() if m else text


def _domestic_audit(repo: Path, loaded: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    comp = row.get("domestic_formal_competition_id")
    if not comp:
        return {
            "status": "DATA_COVERAGE_LIMITATION",
            "competition": None,
            "reason": "DOMESTIC_COMPETITION_OUTSIDE_FORMAL_16_SCOPE",
            "historical_match_count": None,
            "linked_xg_count": None,
            "legal_xg_coverage_available": False,
            "identity": None,
        }
    season = _current_domestic_season(repo, str(comp))
    domestic = ucl_bridge._domestic_registry_row(row)
    if domestic is None:
        raise rt.RuntimeGateError(f"UCL domestic identity missing despite formal competition: {row['uefa_name']}")
    ident = domestic_bridge.resolve_team(repo, loaded["state"], str(comp), season, domestic["canonical_name"])
    linked = guard._linked_count(loaded, str(comp), ident["historical_state_team_id"])
    returning = ident["participation_classification"] in domestic_bridge.RETURNING
    missing = returning and (
        int(ident.get("historical_match_count") or 0) <= 0
        or not bool(ident.get("state_local_evidence"))
        or not bool(ident.get("state_global_evidence"))
        or (bool(ident.get("legal_xg_coverage_available")) and linked <= 0)
    )
    status = "IDENTITY_STATE_LINKAGE_FAILURE" if missing else "PASS"
    evidence = (
        "LEGAL_XG_LINKED" if bool(ident.get("legal_xg_coverage_available")) and linked > 0
        else "DATA_COVERAGE_LIMITATION" if not bool(ident.get("legal_xg_coverage_available"))
        else "LEGAL_XG_NOT_REQUIRED_FOR_ENTERING_CLUB" if not returning
        else "IDENTITY_STATE_LINKAGE_FAILURE"
    )
    return {
        "status": status,
        "competition": comp,
        "current_season": season,
        "previous_season": ident.get("previous_season"),
        "domestic_current_name": domestic["canonical_name"],
        "canonical_global_id": ident.get("canonical_global_id"),
        "historical_state_team_id": ident.get("historical_state_team_id"),
        "participation_classification": ident.get("participation_classification"),
        "previous_season_identity": ident.get("previous_season_identity"),
        "historical_match_count": int(ident.get("historical_match_count") or 0),
        "strength_state_local": bool(ident.get("state_local_evidence")),
        "strength_state_global": bool(ident.get("state_global_evidence")),
        "linked_xg_count": int(linked),
        "legal_xg_coverage_available": bool(ident.get("legal_xg_coverage_available")),
        "evidence_status": evidence,
        "mapping_provenance": ident.get("mapping_provenance"),
    }


def _team(repo: Path, loaded: dict[str, Any], snapshot: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    legacy_team, ucl_identity = ucl_bridge.resolve_snapshot_team(snapshot, row["uefa_name"])
    seasons = [str(x) for x in (legacy_team.get("seasons_observed") or [])]
    historical_name = str(legacy_team.get("team_name") or "")
    historical_base = _historical_base_name(historical_name)
    previous_ucl = "2025/26" in seasons
    ucl_matches = int(((legacy_team.get("overall") or {}).get("matches") or 0))
    if previous_ucl and ucl_matches <= 0:
        ucl_state_status = "IDENTITY_STATE_LINKAGE_FAILURE"
    else:
        ucl_state_status = "PASS"
    if previous_ucl and normalize_team_token(historical_base) != normalize_team_token(row["uefa_name"]):
        ucl_class = "RENAMED_OR_REKEYED_CLUB"
    elif previous_ucl:
        ucl_class = "CONTINUING_CLUB"
    else:
        ucl_class = "PROMOTED_OR_ENTERING_CLUB"
    domestic = _domestic_audit(repo, loaded, row)
    global_id = domestic.get("canonical_global_id") or ucl_identity["canonical_global_id"]
    state_status = "PASS"
    reasons = []
    if ucl_state_status != "PASS":
        state_status = "DATA_STATE_ANOMALY"
        reasons.append("RETURNING_CLUB_HISTORICAL_STATE_MISSING")
    if domestic["status"] == "IDENTITY_STATE_LINKAGE_FAILURE":
        state_status = "DATA_STATE_ANOMALY"
        reasons.append("DOMESTIC_HISTORICAL_STATE_LINKAGE_FAILURE")
    evidence_status = domestic.get("evidence_status") or domestic["status"]
    return {
        "current_name": row["uefa_name"],
        "current_team_id": ucl_identity["current_team_id"],
        "canonical_global_id": global_id,
        "association_code": row["association_code"],
        "domestic_formal_competition_id": row.get("domestic_formal_competition_id"),
        "previous_season_identity": historical_name if previous_ucl else None,
        "participation_classification": ucl_class,
        "ucl_historical_match_count": ucl_matches,
        "ucl_historical_seasons": seasons,
        "ucl_strength_state_team_id": legacy_team.get("team_id"),
        "ucl_strength_state_status": ucl_state_status,
        "domestic_historical_match_count": domestic.get("historical_match_count"),
        "linked_xg_count": domestic.get("linked_xg_count"),
        "state_status": state_status,
        "evidence_status": evidence_status,
        "data_coverage_status": domestic["status"] if domestic["status"] == "DATA_COVERAGE_LIMITATION" else "AVAILABLE_OR_NOT_REQUIRED",
        "mapping_provenance": {
            "ucl": ucl_identity,
            "domestic": domestic.get("mapping_provenance"),
        },
        "domestic_state": domestic,
        "anomaly_reasons": reasons,
    }


def _markdown(report: dict[str, Any]) -> str:
    out = [
        "# UEFA Champions League 2026/27 league-phase identity audit", "",
        f"- authoritative roster: **{report['roster_count']}/36**", 
        f"- Fusion V2 formal scope unchanged: **{report['fusion_v2_scope_unchanged']}**",
        f"- final guard: **{report['final_guard_status']}**", "",
        "|UEFA team|current ID|canonical global ID|association|domestic formal comp|class|UCL hist|domestic hist|linked xG|state|evidence|",
        "|---|---|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for x in report["teams"]:
        out.append(
            f"|{x['current_name']}|`{x['current_team_id']}`|`{x['canonical_global_id']}`|{x['association_code']}|{x['domestic_formal_competition_id'] or '-'}|{x['participation_classification']}|{x['ucl_historical_match_count']}|{x['domestic_historical_match_count'] if x['domestic_historical_match_count'] is not None else '-'}|{x['linked_xg_count'] if x['linked_xg_count'] is not None else '-'}|{x['state_status']}|{x['evidence_status']}|"
        )
    out += ["", "## Coverage limitations", "", json.dumps(report["data_coverage_limitations"], ensure_ascii=False, indent=2)]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    repo = Path(args.repo_root).resolve()
    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    domestic_bridge.install()
    registry = ucl_bridge._registry()
    snapshot_path = repo / "football-data" / "team_strengths" / UCL / "latest.json"
    snapshot = load_json(snapshot_path)
    loaded = rt.validate_bundle(Path(args.bundle).resolve())
    if UCL in rt.FORMAL_SCOPE:
        raise rt.RuntimeGateError("UEFA_ChampionsLeague must remain outside Fusion V2 formal 16 scope")
    teams = []
    missing = []
    ambiguous = []
    for row in registry["teams"]:
        try:
            teams.append(_team(repo, loaded, snapshot, row))
        except Exception as exc:
            missing.append({"current_name": row.get("uefa_name"), "reason": str(exc)})
    gids = [x["canonical_global_id"] for x in teams]
    dup = sorted({gid for gid in gids if gids.count(gid) > 1})
    anomalies = [x for x in teams if x["state_status"] != "PASS"]
    coverage = [
        {"team": x["current_name"], "reason": x["domestic_state"]["reason"]}
        for x in teams if x["domestic_state"]["status"] == "DATA_COVERAGE_LIMITATION"
    ]
    guard_status = "PASS_WITH_DATA_COVERAGE_LIMITATIONS" if not missing and not ambiguous and not dup and not anomalies else "FAIL_CLOSED"
    report = {
        "schema_version": SCHEMA,
        "competition": UCL,
        "season": "2026/27",
        "phase": "league_phase",
        "roster_count": len(registry["teams"]),
        "roster_sha256": registry["roster_sha256"],
        "registry_sha256": sha256_file(repo / "football-data" / "config" / "ucl_2026_27_league_phase_identity_v1.json"),
        "authority": registry["authority"],
        "qualification_roster_used": False,
        "fusion_v2_scope_unchanged": tuple(rt.FORMAL_SCOPE) == (
            "ARG_Primera","BRA_SerieA","ENG_PremierLeague","ESP_LaLiga","FRA_Ligue1",
            "GER_Bundesliga","ITA_SerieA","JPN_J1","KOR_KLeague1","NED_Eredivisie",
            "NOR_Eliteserien","POR_PrimeiraLiga","SCO_Premiership","SUI_SuperLeague",
            "SWE_Allsvenskan","USA_MLS",
        ),
        "ucl_historical_snapshot_sha256": sha256_file(snapshot_path),
        "teams": teams,
        "missing": missing,
        "extra": [],
        "ambiguous": ambiguous,
        "duplicate_canonical_identity": dup,
        "zero_history_anomalies": [x["current_name"] for x in anomalies],
        "data_coverage_limitations": coverage,
        "data_coverage_limitation_count": len(coverage),
        "final_guard_status": guard_status,
        "fuzzy_matching_used": False,
        "result_or_xg_values_used_for_identity": False,
        "model_current_or_weights_changed": False,
    }
    (out / "ucl_2026_27_identity_audit.json").write_bytes(rt._canon_bytes(report))
    (out / "ucl_2026_27_identity_audit.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": guard_status, "roster": len(teams), "coverage_limitations": len(coverage)}, sort_keys=True))
    return 0 if guard_status != "FAIL_CLOSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
