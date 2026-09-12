from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

EXPECTED_BASE = "4227ce96b02e586e1ac40f4c709ca1c4c30d9649"
EXPECTED_BRANCH = "football3/nextgen-market-pit-prereg-v1"
EXPECTED_FORMAL_HEAD = "e12f5d1193be5d81f60301cf34ab2140e11712a9"
EXPECTED_CURRENT_SHA256 = "71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_EVIDENCE_BLOBS = {
    "football-data/ingestion/historical_market_snapshot_v475.py": "622738c89aa167b6b0e3404a86798aa6e7cdebc2",
    "football-data/config/evidence_sources_v470.json": "f9241dd14cbb89cc31ada2facfeb6d3620d0afff",
    "football-data/config/global_evidence_routes_v475.json": "397de139df98b7a0b55f19cc0c2d2112bdd58651",
    "football-data/manifests/market_lomo_data_readiness_v470_status.json": "7fb36dbfb4dc18716375c03d3a7a8530034c845a",
    "football-data/manifests/global_evidence_alignment_v475_status.json": "f3db085f400ce635a0d25861366e66d657d3bf55",
    "football-data/research/FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json": "1a4d0c71b15704bddbd2abe023e46bebe833db74",
    "football-data/research/FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json": "776abfbc06b66405aeb13d67848518c64e05d3d2",
}


class ContractError(AssertionError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(contract: dict[str, Any]) -> list[str]:
    checks: list[str] = []

    def ok(condition: bool, name: str) -> None:
        _require(condition, name)
        checks.append(name)

    ok(contract["schema_version"] == "football3-nextgen-market-pit-prereg-contract-v1", "schema")
    ok(contract["project_id"] == "football3", "project")
    ok(contract["phase"] == "CANDIDATE_1_ZERO_LABEL_DATA_FEASIBILITY_AND_PREREGISTRATION", "phase")
    ok(contract["status"] == "STOP_DATA_COVERAGE", "stop_status")
    ok(contract["canonical_integration"]["exact_base"] == EXPECTED_BASE, "exact_base")
    ok(contract["research_branch"] == EXPECTED_BRANCH, "branch")
    ok(contract["formal_baseline"]["head"] == EXPECTED_FORMAL_HEAD, "formal_head")
    ok(contract["formal_baseline"]["xg_weight"] == 0.75, "formal_xg_weight")
    ok(contract["formal_baseline"]["frozen_v1_weight"] == 0.25, "formal_v1_weight")
    ok(contract["current_authority"]["current_sha256"] == EXPECTED_CURRENT_SHA256, "current_sha")

    c = contract["candidate"]
    ok(c["status"] == "NOT_AVAILABLE" and c["weight"] == 0 and c["matrix_delta"] == 0, "inactive_zero_zero")
    ok(not c["activation_allowed"] and not c["training_allowed"] and not c["tuning_allowed"] and not c["target_label_access_allowed"], "candidate_fail_closed")

    z = contract["zero_label_audit"]
    for key in ("target_labels_read", "training_performed", "tuning_performed", "label_based_league_selection", "label_based_time_selection", "label_based_line_selection", "provider_calls_performed_in_this_stage", "sealed_pools_opened"):
        ok(z[key] is False, f"zero_label_{key}")

    ge = contract["repository_evidence"]["global_alignment"]
    ok(ge["market_jsonl_files"] == 0 and ge["market_rows"] == 0, "no_acquired_market_rows")
    ok(ge["timestamped_historical_market_two_season_backfill_complete"] is False, "backfill_incomplete")
    ok(all(v is False for v in ge["credential_status"].values()), "credential_status_false")
    mr = contract["repository_evidence"]["market_readiness"]
    ok(mr["production_lomo_validated_count"] == 0, "no_lomo_validation")
    ok(mr["timestamped_complete_surface_review_count"] == 0, "no_timestamped_complete_surface")
    ok(mr["formal_ev_available_count"] == 0, "no_formal_ev")

    failures = set(contract["coverage_gate"]["current_failures"])
    for required_failure in (
        "PAID_HISTORICAL_CREDENTIAL_OR_EQUIVALENT_LICENSED_ACCESS_NOT_PRESENT",
        "ACQUIRED_TIMESTAMPED_MARKET_ROWS_ZERO",
        "COMPLETE_PIT_MARKET_SURFACES_ZERO",
        "MULTI_LINE_TOTALS_HISTORY_NOT_ACQUIRED_OR_17_DOMAIN_COVERAGE_PROVEN",
        "EXISTING_COLLECTOR_ONLY_NORMALIZES_ONE_FEATURED_TOTAL_LINE_PER_BOOKMAKER",
    ):
        ok(required_failure in failures, f"failure_{required_failure}")

    pit = contract["pit_contract_if_future_data_ready"]
    ok(pit["master_cutoff"] == "T-15m", "master_cutoff")
    ok(pit["reject_timezone_naive"] and pit["reject_post_cutoff_snapshot"] and pit["reject_post_cutoff_market_update"], "pit_fail_closed")
    ok(pit["competition_plus_kickoff_only_identity_forbidden"] is True, "identity_not_competition_kickoff_only")
    ok(pit["dynamic_failures"]["http_503"] == "EXTERNAL_DYNAMIC_DATA_FAILURE", "503_classification")
    ok(pit["dynamic_failures"]["http_429"] == "EXTERNAL_DYNAMIC_DATA_FAILURE", "429_classification")
    ok(pit["dynamic_failures"]["timeout"] == "EXTERNAL_DYNAMIC_DATA_FAILURE", "timeout_classification")

    norm = contract["market_normalization_preregistration"]
    ok(norm["ordinary_featured_totals_single_line_alone_is_sufficient"] is False, "single_line_not_sufficient")
    ok(norm["alternate_totals_or_materially_equivalent_multi_line_surface_required"] is True, "multi_line_required")
    ok(norm["postmatch_or_closing_quote_used_for_earlier_cutoff"] is False, "no_closing_backfill")

    sci = contract["conditional_scientific_preregistration"]
    ok(sci["target"] == "P(T=0,1,2,3,4,5,6,7+)", "target")
    ok(sci["parameter_count"] == 1 and sci["parameter"] == "w" and sci["parameter_range"] == [0.0, 0.35], "low_freedom_parameter")
    ok(sci["distribution_constraint"] == "D(H,A|T) remains exactly current V2; only P(T) may change", "single_matrix_decomposition")
    ok(not sci["league_specific_weights_allowed"] and not sci["post_hoc_line_subset_allowed"] and not sci["post_hoc_cutoff_change_allowed"] and not sci["post_hoc_provider_subset_allowed"], "no_method_shopping")
    ok(sci["random_split"] is False and sci["chronological_oos_required"] is True, "temporal_oos")
    ok(sci["paired_bootstrap"] == {"resamples": 5000, "seed": 72000, "ci": 0.9}, "bootstrap_frozen")
    ok(sci["numeric_success_line_frozen_now"] is False, "no_invented_numeric_gate")
    ok(sci["optional_stopping"] is False, "no_optional_stopping")

    iso = contract["sample_isolation"]
    ok(iso["random_split"] is False, "sample_no_random_split")
    ok(all(x["authorized_access"] is False for x in iso["sealed_pools"]), "sealed_pools_untouched")

    reopen = contract["reopen_contract"]
    ok(all(reopen.values()), "reopen_requires_new_zero_label_contract")
    forbidden = contract["forbidden_changes"]
    ok(all(forbidden.values()), "all_formal_changes_forbidden")
    return checks


def validate_repo_blobs(repo_root: Path) -> list[str]:
    if not (repo_root / ".git").exists():
        raise ContractError("git repository required for exact-base blob validation")
    checks: list[str] = []
    for path, expected in EXPECTED_EVIDENCE_BLOBS.items():
        got = subprocess.check_output(["git", "-C", str(repo_root), "rev-parse", f"{EXPECTED_BASE}:{path}"], text=True).strip()
        _require(got == expected, f"blob mismatch {path}: {got} != {expected}")
        checks.append(path)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="governance/football3/nextgen_market_pit_prereg_contract_v1.json")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--skip-repo-blobs", action="store_true")
    args = parser.parse_args()
    contract_path = Path(args.contract)
    checks = validate(_load(contract_path))
    blob_checks: list[str] = []
    if not args.skip_repo_blobs:
        blob_checks = validate_repo_blobs(Path(args.repo_root))
    print(json.dumps({
        "status": "STOP_DATA_COVERAGE",
        "contract_checks": len(checks),
        "repo_blob_checks": len(blob_checks),
        "target_labels_read": False,
        "training_performed": False,
        "tuning_performed": False,
        "candidate_status": "NOT_AVAILABLE",
        "weight": 0,
        "matrix_delta": 0
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
