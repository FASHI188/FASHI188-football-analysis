#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import runtime as rt
import cross_season_team_state_binding_v1 as bridge
import cross_season_participation_authority_v1 as participation
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
    participation_receipt = participation.validate_team(repo, ident)
    linked = guard._linked_count(loaded, comp, ident["historical_state_team_id"])
    cls = ident["participation_classification"]
    expected_returning = participation_receipt.get("expected_class") == "AUTHORITATIVE_RETURNING_EXPECTED"
    result_ok = int(ident["historical_match_count"]) > 0
    strength_ok = bool(ident["state_local_evidence"]) and bool(ident["state_global_evidence"])
    legal_xg = bool(ident["legal_xg_coverage_available"])
    xg_ok = (linked > 0) if legal_xg else None
    if bool(ident.get("participation_classification_uses_history_lookup_zero")):
        status, reason = "DATA_STATE_ANOMALY", "PARTICIPATION_CLASSIFICATION_USED_HISTORY_LOOKUP_ZERO"
    elif expected_returning and (not result_ok or not strength_ok or (legal_xg and not xg_ok)):
        status, reason = "DATA_STATE_ANOMALY", "RETURNING_EXPECTED_HISTORICAL_STATE_MISSING"
    else:
        status, reason = "PASS", None
    evidence_status = (
        "LEGAL_XG_LINKED" if legal_xg and linked > 0
        else "IDENTITY_STATE_LINKAGE_FAILURE" if legal_xg and expected_returning
        else "DATA_COVERAGE_LIMITATION" if not legal_xg
        else "LEGAL_XG_BELOW_OR_NO_LINK_FOR_AUTHORITATIVE_ENTERING_CLUB"
    )
    return {
        "current_name": ident["current_name"], "current_team_id": ident["current_team_id"],
        "canonical_global_id": ident["canonical_global_id"], "historical_state_team_id": ident["historical_state_team_id"],
        "previous_season_identity": ident["previous_season_identity"], "participation_classification": cls,
        "authoritative_expected_participation": participation_receipt.get("expected_class"),
        "authoritative_promotion_evidence": participation_receipt.get("promotion_evidence"),
        "participation_authority_status": participation_receipt.get("status"),
        "participation_classification_uses_history_lookup_zero": bool(ident.get("participation_classification_uses_history_lookup_zero")),
        "historical_match_count": int(ident["historical_match_count"]), "historical_matches_by_season": ident["historical_matches_by_season"],
        "linked_xg_count": int(linked), "state_status": status, "evidence_status": evidence_status,
        "strength_state_local": bool(ident["state_local_evidence"]), "strength_state_global": bool(ident["state_global_evidence"]),
        "legal_xg_coverage_available": legal_xg, "mapping_provenance": ident["mapping_provenance"], "guard_reason": reason,
    }


def _legacy_one(repo: Path, loaded: dict[str, Any], comp: str, season: str, name: str) -> dict[str, Any]:
    ident = bridge.resolve_team(repo, loaded["state"], comp, season, name)
    linked = guard._linked_count(loaded, comp, ident["historical_state_team_id"])
    cls = ident["participation_classification"]
    returning = cls in bridge.RETURNING
    result_ok = int(ident["historical_match_count"]) > 0
    strength_ok = bool(ident["state_local_evidence"]) and bool(ident["state_global_evidence"])
    legal_xg = bool(ident["legal_xg_coverage_available"])
    xg_ok = (linked > 0) if legal_xg else None
    status, reason = "PASS", None
    if bool(ident.get("participation_classification_uses_history_lookup_zero")):
        status, reason = "DATA_STATE_ANOMALY", "PARTICIPATION_CLASSIFICATION_USED_HISTORY_LOOKUP_ZERO"
    elif returning and (not result_ok or not strength_ok or (legal_xg and not xg_ok)):
        status, reason = "DATA_STATE_ANOMALY", "RETURNING_CLUB_HISTORICAL_STATE_MISSING"
    evidence_status = (
        "LEGAL_XG_LINKED" if legal_xg and linked > 0
        else "IDENTITY_STATE_LINKAGE_FAILURE" if legal_xg and returning
        else "DATA_COVERAGE_LIMITATION" if not legal_xg
        else "LEGAL_XG_BELOW_OR_NO_LINK_FOR_ENTERING_CLUB"
    )
    return {
        "current_name": ident["current_name"], "current_team_id": ident["current_team_id"],
        "canonical_global_id": ident["canonical_global_id"], "historical_state_team_id": ident["historical_state_team_id"],
        "previous_season_identity": ident["previous_season_identity"], "participation_classification": cls,
        "participation_classification_uses_history_lookup_zero": bool(ident.get("participation_classification_uses_history_lookup_zero")),
        "historical_match_count": int(ident["historical_match_count"]), "historical_matches_by_season": ident["historical_matches_by_season"],
        "linked_xg_count": int(linked), "state_status": status, "evidence_status": evidence_status,
        "strength_state_local": bool(ident["state_local_evidence"]), "strength_state_global": bool(ident["state_global_evidence"]),
        "legal_xg_coverage_available": legal_xg, "mapping_provenance": ident["mapping_provenance"], "guard_reason": reason,
    }


def _domain(repo: Path, loaded: dict[str, Any], comp: str) -> dict[str, Any]:
    season = _current_season(repo, comp)
    contract = participation.domain_contract(repo, comp, season, required=False)
    previous = str(contract["frozen_history_reference_season"]) if contract else bridge._previous_season(season)
    roster = _rows(repo, comp, season)
    previous_roster = bridge._processed_roster(repo, comp, previous)
    teams: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for row in roster:
        name = row["canonical_name"]
        try:
            teams.append(_one(repo, loaded, comp, season, name) if contract else _legacy_one(repo, loaded, comp, season, name))
        except Exception as exc:
            item = {"current_name": name, "reason": str(exc)}
            if "ambiguous" in str(exc).lower():
                ambiguous.append(item)
            else:
                missing.append(item)
    ids = [x["canonical_global_id"] for x in teams]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    failures = [x for x in teams if x["state_status"] != "PASS"]
    extra: list[Any] = []

    if contract:
        promoted_authority = {rt._normalize_team(x["current_name"]): x["current_name"] for x in contract["authoritative_promoted_teams"]}
        returning_expected_count = sum(rt._normalize_team(row["canonical_name"]) not in promoted_authority for row in roster)
        returning_rows = [x for x in teams if x.get("authoritative_expected_participation") == "AUTHORITATIVE_RETURNING_EXPECTED"]
        returning_linked = [x for x in returning_rows if x["historical_match_count"] > 0 and x["strength_state_local"] and x["strength_state_global"]]
        zero_history = [x for x in returning_rows if x["historical_match_count"] <= 0]
        observed_promoted = {rt._normalize_team(x["current_name"]): x["current_name"] for x in teams if x["participation_classification"] == "PROMOTED_OR_ENTERING_CLUB"}
        promoted_exact = set(observed_promoted) == set(promoted_authority)
        season_clock_ok = (
            str(contract["formal_current_season"]) == season
            and str(contract["frozen_history_reference_season"]) == str(previous)
            and (comp != "JPN_J1" or (
                contract["season_model"] == "J1_2026_TRANSITION_TO_AUTUMN_SPRING"
                and contract["official_current_season"] == "2026/27"
                and contract["transition_predecessor"] == "2026_MEIJI_YASUDA_J1_100_YEAR_VISION_LEAGUE"
                and previous == "2025"
            ))
            and (comp != "KOR_KLeague1" or (
                contract["season_model"] == "NATURAL_YEAR" and season == "2026" and previous == "2025"
            ))
        )
        legal = [x for x in returning_rows if x["legal_xg_coverage_available"]]
        legal_xg_ok = sum(x["linked_xg_count"] > 0 for x in legal) == len(legal)
        resolved_team_count = len(teams)
        returning_history_linked_count = len(returning_linked)
        zero_history_anomaly_count = len(zero_history)
        authoritative_promoted_team_count = len(promoted_authority)
        hard_gate = (
            resolved_team_count == len(roster)
            and returning_history_linked_count == returning_expected_count
            and zero_history_anomaly_count == 0
            and not missing and not extra and not ambiguous and not dup
            and promoted_exact and season_clock_ok and legal_xg_ok and not failures
        )
        status = "PASS" if hard_gate else "FAIL_CLOSED"
        natural_status = "PASS" if season_clock_ok else "FAIL_CLOSED"
        classification_authority_status = "PASS" if promoted_exact else "FAIL_CLOSED"
        returning_for_metrics = returning_rows
    else:
        returning_for_metrics = [x for x in teams if x["participation_classification"] in bridge.RETURNING]
        legal = [x for x in returning_for_metrics if x["legal_xg_coverage_available"]]
        natural_year_clock_ok = comp not in bridge.NATURAL_YEAR_COMPETITIONS or (season == "2026" and previous == "2025")
        status = "PASS" if not missing and not ambiguous and not dup and not failures and natural_year_clock_ok else "FAIL_CLOSED"
        resolved_team_count = len(teams)
        returning_expected_count = len(returning_for_metrics)
        returning_history_linked_count = sum(x["historical_match_count"] > 0 and x["strength_state_local"] and x["strength_state_global"] for x in returning_for_metrics)
        zero_history_anomaly_count = sum(x["historical_match_count"] <= 0 for x in returning_for_metrics)
        authoritative_promoted_team_count = None
        natural_status = "PASS" if natural_year_clock_ok else "FAIL_CLOSED"
        classification_authority_status = "NOT_APPLICABLE"

    legal = [x for x in returning_for_metrics if x["legal_xg_coverage_available"]]
    return {
        "schema_version": "football3-cross-season-domain-audit-v2",
        "competition": comp,
        "current_season": season,
        "official_current_season": contract["official_current_season"] if contract else season,
        "previous_season": previous,
        "season_model": contract["season_model"] if contract else ("NATURAL_YEAR" if comp in bridge.NATURAL_YEAR_COMPETITIONS else "AUTUMN_SPRING"),
        "transition_predecessor": contract["transition_predecessor"] if contract else None,
        "natural_year_clock_status": natural_status,
        "current_roster_team_count": len(roster),
        "resolved_team_count": resolved_team_count,
        "previous_roster_team_count": len(previous_roster),
        "returning_expected_count": returning_expected_count,
        "returning_history_linked_count": returning_history_linked_count,
        "authoritative_promoted_team_count": authoritative_promoted_team_count,
        "zero_history_anomaly_count": zero_history_anomaly_count,
        "classification_authority_status": classification_authority_status,
        "continuing_clubs": [x["current_name"] for x in teams if x["participation_classification"] == "CONTINUING_CLUB"],
        "promoted_entering_clubs": [x["current_name"] for x in teams if x["participation_classification"] == "PROMOTED_OR_ENTERING_CLUB"],
        "renamed_rekeyed_clubs": [x["current_name"] for x in teams if x["participation_classification"] == "RENAMED_OR_REKEYED_CLUB"],
        "true_cold_start_clubs": [x["current_name"] for x in teams if x["participation_classification"] == "TRUE_NEW_OR_UNAVAILABLE_HISTORY"],
        "historical_result_linkage_success": {"success": sum(x["historical_match_count"] > 0 for x in returning_for_metrics), "required": len(returning_for_metrics)},
        "strength_state_linkage_success": {"success": sum(x["strength_state_local"] and x["strength_state_global"] for x in returning_for_metrics), "required": len(returning_for_metrics)},
        "legal_xg_linkage_success": {"success": sum(x["linked_xg_count"] > 0 for x in legal), "required": len(legal)},
        "xg_unavailable_by_licensed_coverage": [x["current_name"] for x in teams if x["evidence_status"] == "DATA_COVERAGE_LIMITATION"],
        "participation_authority": contract,
        "missing": missing,
        "extra": extra,
        "ambiguous": ambiguous,
        "duplicate_canonical_identity": dup,
        "final_guard_status": status,
        "teams": teams,
    }


def _markdown(report: dict[str, Any]) -> str:
    out = ["# Football3 cross-season team/state binding audit", "", f"- exact formal scope gate: **{report['formal_scope_gate']['status']}**", f"- state SHA: `{report['state_sha256']}`", f"- final status: **{report['status']}**", ""]
    for comp in FOCUS:
        d = report["domains"][comp]
        out += [
            f"## {comp}", "",
            f"- season: `{d['current_season']}`; official: `{d['official_current_season']}`; history ref: `{d['previous_season']}`; roster/resolved: {d['current_roster_team_count']}/{d['resolved_team_count']}; returning linked/expected: {d['returning_history_linked_count']}/{d['returning_expected_count']}; promoted authority: {d['authoritative_promoted_team_count']}; zero-history anomalies: {d['zero_history_anomaly_count']}; guard: **{d['final_guard_status']}**", "",
            "|current|current ID|previous identity|class|authority expected|historical matches|linked xG|state|provenance|",
            "|---|---|---|---|---|---:|---:|---|---|",
        ]
        for x in d["teams"]:
            prov = x["mapping_provenance"]["identity_mapping"]
            ps = f"{prov.get('method')} @ {prov.get('source')}"
            out.append(f"|{x['current_name']}|`{x['current_team_id']}`|{x['previous_season_identity'] or '-'}|{x['participation_classification']}|{x.get('authoritative_expected_participation') or '-'}|{x['historical_match_count']}|{x['linked_xg_count']}|{x['state_status']}|{ps}|")
        if d["missing"]:
            out += ["", f"Missing: `{json.dumps(d['missing'], ensure_ascii=False)}`"]
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
        "schema_version": "football3-cross-season-team-state-binding-audit-v2",
        "status": "PASS" if scope_gate["status"] == "PASS" and focus_ok and other_ok else "FAIL_CLOSED",
        "state_sha256": loaded["manifest"]["state_sha256"], "state_bundle_sha256": loaded["manifest"]["state_bundle_sha256"],
        "historical_cutoff": loaded["meta"]["historical_cutoff"], "formal_scope_gate": scope_gate,
        "focus_domains": list(FOCUS), "natural_year_competitions": sorted(bridge.NATURAL_YEAR_COMPETITIONS), "domains": domains,
        "model_parameters_or_weights_changed": False, "current_or_formal_pointer_changed": False,
        "participation_classification_uses_history_lookup_zero": False,
        "promotion_requires_authoritative_participation_evidence": True,
        "result_or_score_or_xg_values_used_for_identity": False, "fuzzy_matching_used": False,
    }
    (out / "cross_season_team_state_audit.json").write_bytes(rt._canon_bytes(report))
    (out / "cross_season_team_state_audit.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "state_sha256": report["state_sha256"]}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
