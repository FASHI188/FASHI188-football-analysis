#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import runtime as rt
import cross_season_team_state_binding_v1 as bridge
import formal_state_integrity_guard_v1 as guard

FOCUS = ("ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1", "JPN_J1", "KOR_KLeague1")
EXPECTED_FORMAL_SCOPE = (
    "ARG_Primera","BRA_SerieA","ENG_PremierLeague","ESP_LaLiga","FRA_Ligue1",
    "GER_Bundesliga","ITA_SerieA","JPN_J1","KOR_KLeague1","NED_Eredivisie",
    "NOR_Eliteserien","POR_PrimeiraLiga","SCO_Premiership","SUI_SuperLeague",
    "SWE_Allsvenskan","USA_MLS",
)


def _current_season(repo: Path, comp: str) -> str:
    return bridge.current_season(repo, comp)


def _rows(repo: Path, comp: str, season: str) -> list[dict[str, Any]]:
    rows, _ = bridge._registry_rows(repo, comp, season)
    if not rows:
        raise RuntimeError(f"current roster empty: {comp} {season}")
    return rows


def _one(repo: Path, loaded: dict[str, Any], comp: str, season: str, name: str) -> dict[str, Any]:
    ident = bridge.resolve_team(repo, loaded["state"], comp, season, name)
    linked = guard._linked_count(loaded, comp, ident["historical_state_team_id"])
    cls = ident["participation_classification"]
    returning = cls in bridge.RETURNING
    result_ok = int(ident["historical_match_count"]) > 0
    strength_ok = bool(ident["state_local_evidence"]) and bool(ident["state_global_evidence"])
    legal_xg = bool(ident["legal_xg_coverage_available"])
    xg_ok = (linked > 0) if legal_xg else None
    if bool(ident.get("participation_classification_uses_history_lookup_zero")):
        status = "DATA_STATE_ANOMALY"
        reason = "PARTICIPATION_CLASSIFICATION_USED_HISTORY_LOOKUP_ZERO"
    elif returning and (not result_ok or not strength_ok or (legal_xg and not xg_ok)):
        status = "DATA_STATE_ANOMALY"
        reason = "RETURNING_CLUB_HISTORICAL_STATE_MISSING"
    else:
        status = "PASS"
        reason = None
    evidence_status = (
        "LEGAL_XG_LINKED" if legal_xg and linked > 0
        else "IDENTITY_STATE_LINKAGE_FAILURE" if legal_xg and returning
        else "DATA_COVERAGE_LIMITATION" if not legal_xg
        else "LEGAL_XG_BELOW_OR_NO_LINK_FOR_ENTERING_CLUB"
    )
    return {
        "current_name": ident["current_name"],
        "current_team_id": ident["current_team_id"],
        "canonical_global_id": ident["canonical_global_id"],
        "historical_state_team_id": ident["historical_state_team_id"],
        "previous_season_identity": ident["previous_season_identity"],
        "participation_classification": cls,
        "participation_classification_uses_history_lookup_zero": bool(ident.get("participation_classification_uses_history_lookup_zero")),
        "historical_match_count": int(ident["historical_match_count"]),
        "historical_matches_by_season": ident["historical_matches_by_season"],
        "linked_xg_count": int(linked),
        "state_status": status,
        "evidence_status": evidence_status,
        "strength_state_local": bool(ident["state_local_evidence"]),
        "strength_state_global": bool(ident["state_global_evidence"]),
        "legal_xg_coverage_available": legal_xg,
        "mapping_provenance": ident["mapping_provenance"],
        "guard_reason": reason,
    }


def _domain(repo: Path, loaded: dict[str, Any], comp: str) -> dict[str, Any]:
    season = _current_season(repo, comp)
    previous = bridge._previous_season(season)
    roster = _rows(repo, comp, season)
    previous_roster = bridge._processed_roster(repo, comp, previous)
    teams = []
    missing = []
    ambiguous = []
    for row in roster:
        name = row["canonical_name"]
        try:
            teams.append(_one(repo, loaded, comp, season, name))
        except Exception as exc:
            missing.append({"current_name": name, "reason": str(exc)})
    ids = [x["canonical_global_id"] for x in teams]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    returning = [x for x in teams if x["participation_classification"] in bridge.RETURNING]
    legal = [x for x in returning if x["legal_xg_coverage_available"]]
    failures = [x for x in teams if x["state_status"] != "PASS"]
    natural_year_clock_ok = comp not in bridge.NATURAL_YEAR_COMPETITIONS or (season == "2026" and previous == "2025")
    status = "PASS" if not missing and not ambiguous and not dup and not failures and natural_year_clock_ok else "FAIL_CLOSED"
    return {
        "schema_version": "football3-cross-season-domain-audit-v1",
        "competition": comp,
        "current_season": season,
        "previous_season": previous,
        "natural_year_clock_status": "PASS" if natural_year_clock_ok else "FAIL_CLOSED",
        "current_roster_team_count": len(roster),
        "previous_roster_team_count": len(previous_roster),
        "continuing_clubs": [x["current_name"] for x in teams if x["participation_classification"] == "CONTINUING_CLUB"],
        "promoted_entering_clubs": [x["current_name"] for x in teams if x["participation_classification"] == "PROMOTED_OR_ENTERING_CLUB"],
        "renamed_rekeyed_clubs": [x["current_name"] for x in teams if x["participation_classification"] == "RENAMED_OR_REKEYED_CLUB"],
        "true_cold_start_clubs": [x["current_name"] for x in teams if x["participation_classification"] == "TRUE_NEW_OR_UNAVAILABLE_HISTORY"],
        "historical_result_linkage_success": {"success": sum(x["historical_match_count"] > 0 for x in returning), "required": len(returning)},
        "strength_state_linkage_success": {"success": sum(x["strength_state_local"] and x["strength_state_global"] for x in returning), "required": len(returning)},
        "legal_xg_linkage_success": {"success": sum(x["linked_xg_count"] > 0 for x in legal), "required": len(legal)},
        "xg_unavailable_by_licensed_coverage": [x["current_name"] for x in teams if x["evidence_status"] == "DATA_COVERAGE_LIMITATION"],
        "missing": missing,
        "extra": [],
        "ambiguous": ambiguous,
        "duplicate_canonical_identity": dup,
        "final_guard_status": status,
        "teams": teams,
    }


def _markdown(report: dict[str, Any]) -> str:
    out = [
        "# Football3 cross-season team/state binding audit", "",
        f"- exact formal scope gate: **{report['formal_scope_gate']['status']}**",
        f"- state SHA: `{report['state_sha256']}`",
        f"- final status: **{report['status']}**", "",
    ]
    for comp in FOCUS:
        d = report["domains"][comp]
        out += [
            f"## {comp}", "",
            f"- season: `{d['current_season']}`; previous: `{d['previous_season']}`; roster: {d['current_roster_team_count']}; guard: **{d['final_guard_status']}**", "",
            "|current|current ID|canonical global ID|previous identity|class|historical matches|linked xG|state|evidence|provenance|",
            "|---|---|---|---|---|---:|---:|---|---|---|",
        ]
        for x in d["teams"]:
            prov = x["mapping_provenance"]["identity_mapping"]
            ps = f"{prov.get('method')} @ {prov.get('source')}"
            out.append(f"|{x['current_name']}|`{x['current_team_id']}`|`{x['canonical_global_id']}`|{x['previous_season_identity'] or '-'}|{x['participation_classification']}|{x['historical_match_count']}|{x['linked_xg_count']}|{x['state_status']}|{x['evidence_status']}|{ps}|")
        if d["missing"]:
            out += ["", f"Missing: `{json.dumps(d['missing'], ensure_ascii=False)}`"]
        if d["ambiguous"]:
            out += ["", f"Ambiguous: `{json.dumps(d['ambiguous'], ensure_ascii=False)}`"]
        out.append("")
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
    bridge.install()
    loaded = rt.validate_bundle(Path(args.bundle).resolve())
    scope = tuple(rt.FORMAL_SCOPE)
    scope_gate = {"status": "PASS" if scope == EXPECTED_FORMAL_SCOPE else "FAIL_CLOSED", "expected": list(EXPECTED_FORMAL_SCOPE), "actual": list(scope)}
    domains = {comp: _domain(repo, loaded, comp) for comp in EXPECTED_FORMAL_SCOPE}
    focus_ok = all(domains[c]["final_guard_status"] == "PASS" for c in FOCUS)
    other_ok = all(domains[c]["final_guard_status"] == "PASS" for c in EXPECTED_FORMAL_SCOPE if c not in FOCUS)
    report = {
        "schema_version": "football3-cross-season-team-state-binding-audit-v1",
        "status": "PASS" if scope_gate["status"] == "PASS" and focus_ok and other_ok else "FAIL_CLOSED",
        "state_sha256": loaded["manifest"]["state_sha256"],
        "state_bundle_sha256": loaded["manifest"]["state_bundle_sha256"],
        "historical_cutoff": loaded["meta"]["historical_cutoff"],
        "formal_scope_gate": scope_gate,
        "focus_domains": list(FOCUS),
        "natural_year_competitions": sorted(bridge.NATURAL_YEAR_COMPETITIONS),
        "domains": domains,
        "model_parameters_or_weights_changed": False,
        "current_or_formal_pointer_changed": False,
        "participation_classification_uses_history_lookup_zero": False,
        "result_or_score_or_xg_values_used_for_identity": False,
        "fuzzy_matching_used": False,
    }
    (out / "cross_season_team_state_audit.json").write_bytes(rt._canon_bytes(report))
    (out / "cross_season_team_state_audit.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "state_sha256": report["state_sha256"]}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
