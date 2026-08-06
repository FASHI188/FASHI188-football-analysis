#!/usr/bin/env python3
"""Validate the zero-label Betfair draw-trajectory pilot preregistration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


class PreregistrationError(RuntimeError):
    pass


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreregistrationError("preregistration root must be an object")
    return value


def validate(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != "BETFAIR-DRAW-TRAJECTORY-PILOT-PREREG-R1":
        raise PreregistrationError("schema version mismatch")
    if value.get("status") != "PRE_REGISTERED_NOT_AUTHORIZED_NOT_RUN":
        raise PreregistrationError("preregistration status is not frozen pre-run")

    upstream = value["upstream_evidence"]
    expected_upstream = {
        "source_commit": "90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff",
        "inventory_head": "c44dcfca5f94c016788fefda88740130bc8ece13",
        "inventory_run": 31090041292,
        "inventory_artifact": 8963032490,
        "inventory_artifact_sha256": "6f97e9db294ab55c83b20a959da0702809a46d7ba298a1ba2ab18db6ae09993a",
        "complete_core_trajectory_markets": 65,
    }
    for key, expected in expected_upstream.items():
        if upstream.get(key) != expected:
            raise PreregistrationError(f"upstream identity mismatch: {key}")
    if upstream.get("exploratory_pilot_ready") is not True or upstream.get("model_screen_ready") is not False:
        raise PreregistrationError("upstream readiness boundary mismatch")

    eligibility = value["eligibility_contract"]
    if eligibility.get("required_cutoffs_minutes_before_kickoff") != [1440, 360, 90, 15]:
        raise PreregistrationError("core cutoff contract changed")
    if eligibility.get("expected_eligible_market_count_from_inventory") != 65:
        raise PreregistrationError("eligible market count changed")
    if eligibility.get("eligibility_must_be_computed_without_winner_or_settlement_status") is not True:
        raise PreregistrationError("eligibility may access labels")
    if eligibility.get("fail_if_eligible_count_differs") is not True:
        raise PreregistrationError("eligible count fail-closed disabled")

    split = value["identity_and_split_contract"]
    required_split_flags = (
        "split_must_be_persisted_before_label_access",
        "split_hash_must_be_persisted_before_label_access",
        "same_market_must_not_cross_splits",
    )
    if any(split.get(key) is not True for key in required_split_flags):
        raise PreregistrationError("split leakage gate disabled")
    if split.get("ordering") != "ascending_sha256_of_canonical_identity":
        raise PreregistrationError("split ordering is not deterministic")

    probability = value["probability_contract"]
    candidates = probability.get("fixed_candidates")
    if not isinstance(candidates, list) or [row.get("id") for row in candidates] != [
        "DRAW_T15_PLUS_HALF_T90_MOVE",
        "DRAW_T15_PLUS_HALF_T6H_MOVE",
        "DRAW_T15_PLUS_HALF_T24H_MOVE",
    ]:
        raise PreregistrationError("candidate catalog changed")
    if probability["baseline"].get("id") != "DRAW_FAIR_T15":
        raise PreregistrationError("baseline changed")
    forbidden_tuning = (
        "additional_features_or_models_allowed",
        "coefficient_tuning_allowed",
        "threshold_selection_allowed",
        "class_weighting_allowed",
        "model_fit_allowed",
    )
    if any(probability.get(key) is not False for key in forbidden_tuning):
        raise PreregistrationError("post-hoc tuning remains enabled")

    authorization = value["authorization"]
    if authorization != {
        "run_authorized": False,
        "external_raw_data_access_authorized": False,
        "winner_label_access_authorized": False,
        "one_time_run_only": True,
    }:
        raise PreregistrationError("authorization must remain absent")

    boundary = value["evidence_boundary"]
    if boundary.get("source_has_explicit_reusable_data_license") is not False:
        raise PreregistrationError("source license boundary changed")
    if boundary.get("raw_files_may_be_committed_or_uploaded") is not False:
        raise PreregistrationError("raw redistribution enabled")
    if boundary.get("derived_per_market_identity_or_price_rows_may_be_uploaded") is not False:
        raise PreregistrationError("per-market derived rows may be uploaded")
    if boundary.get("basic_ltp_is_executable_price") is not False or boundary.get("formal_ev_allowed") is not False:
        raise PreregistrationError("LTP or EV boundary changed")

    limits = value["hard_limits"]
    required_false = (
        "formal_promotion_allowed",
        "current_match_probability_allowed",
        "current_match_direction_allowed",
        "unified_score_matrix_allowed",
        "exact_score_allowed",
        "ev_allowed",
        "provider_account_or_credentials_access",
        "CURRENT_mutation_allowed",
        "formal_model_data_config_mutation_allowed",
    )
    if limits.get("formal_weight") != 0 or any(limits.get(key) is not False for key in required_false):
        raise PreregistrationError("hard limits changed")

    return {
        "status": "PASS_ZERO_LABEL_PREREGISTRATION_FROZEN",
        "preregistration_sha256": canonical_sha256(value),
        "external_data_accessed": False,
        "winner_labels_read": 0,
        "model_fits": 0,
        "thresholds_selected": 0,
        "candidate_count": len(candidates),
        "expected_eligible_market_count": eligibility["expected_eligible_market_count_from_inventory"],
        "run_authorized": False,
        "formal_weight": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    receipt = validate(load_json(args.prereg))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
