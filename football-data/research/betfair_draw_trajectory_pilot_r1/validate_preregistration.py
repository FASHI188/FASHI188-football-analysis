#!/usr/bin/env python3
"""Validate the zero-label, single-candidate Betfair draw-trajectory preregistration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_CANONICAL_SHA256 = "033e345348f3f809dd0a5a9e5811e8608655dbeefef6e4f2725f945263dc969b"
EXPECTED_CHANGED_FILES = {
    ".github/workflows/football-betfair-draw-trajectory-pilot-prereg-r1.yml",
    "football-data/research/betfair_draw_trajectory_pilot_r1/preregistration.json",
    "football-data/research/betfair_draw_trajectory_pilot_r1/test_validate_preregistration.py",
    "football-data/research/betfair_draw_trajectory_pilot_r1/validate_preregistration.py",
}


class PreregistrationError(RuntimeError):
    pass


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreregistrationError("preregistration root must be an object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PreregistrationError(message)


def require_exact(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise PreregistrationError(message)


def validate(value: dict[str, Any]) -> dict[str, Any]:
    require_exact(value.get("schema_version"), "BETFAIR-DRAW-TRAJECTORY-PILOT-PREREG-R2", "schema version mismatch")
    require_exact(value.get("status"), "PRE_REGISTERED_NOT_AUTHORIZED_NOT_RUN", "preregistration status is not frozen pre-run")

    amendment = value.get("amendment_scope", {})
    require_exact(amendment.get("supersedes_design_at_head"), "34f8ffdb9d1de39cc62e52b7e0886823c99224e4", "superseded head mismatch")
    require(
        amendment.get("remove_policy_confirmation_split") is True
        and amendment.get("remove_candidate_selection") is True
        and amendment.get("fixed_candidate_count") == 1
        and amendment.get("external_data_or_winner_labels_accessed_for_amendment") is False,
        "amendment scope boundary mismatch",
    )

    upstream = value.get("upstream_evidence", {})
    expected_upstream = {
        "source_repository": "marcosf63/bet",
        "source_commit": "90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff",
        "inventory_pr": 102,
        "inventory_head": "c44dcfca5f94c016788fefda88740130bc8ece13",
        "inventory_run": 31090041292,
        "inventory_artifact": 8963032490,
        "inventory_artifact_sha256": "6f97e9db294ab55c83b20a959da0702809a46d7ba298a1ba2ab18db6ae09993a",
        "legacy_complete_core_trajectory_market_upper_bound": 65,
        "legacy_count_not_reused_as_synchronized_eligible_count": True,
        "external_source_not_reaccessed_by_this_amendment": True,
        "winner_or_settlement_labels_not_reaccessed_by_this_amendment": True,
    }
    require_exact(upstream, expected_upstream, "upstream evidence identity or zero-access boundary mismatch")

    eligibility = value.get("eligibility_contract", {})
    require_exact(eligibility.get("required_cutoffs_minutes_before_kickoff"), [90, 15], "eligible cutoff contract changed")
    require(
        eligibility.get("winner_or_settlement_fields_may_be_parsed_during_eligibility") is False
        and eligibility.get("inplay_observations_allowed") is False
        and eligibility.get("messages_at_or_after_kickoff_allowed") is False,
        "eligibility label or in-play exclusion gate changed",
    )
    require(
        eligibility.get("minimum_synchronized_eligible_before_any_label_access") == 60
        and eligibility.get("legacy_inventory_upper_bound") == 65
        and eligibility.get("eligible_count_preknown") is False
        and eligibility.get("fail_if_count_below_minimum") is True
        and eligibility.get("fail_if_count_exceeds_legacy_upper_bound") is True
        and eligibility.get("fail_on_duplicate_canonical_identity") is True
        and eligibility.get("fail_on_parse_or_identity_error") is True,
        "pre-label eligibility fail-closed gate changed",
    )

    sync = value.get("synchronization_and_staleness_contract", {})
    require_exact(sync.get("observation_timestamp_source"), "publish_time_pt_of_message_containing_explicit_valid_ltp_for_that_runner", "observation timestamp source changed")
    require_exact(sync.get("observation_timestamp_refresh_rule"), "carry_forward_without_an_explicit_runner_ltp_field_does_not_refresh_observed_at", "observation timestamp refresh rule changed")
    require_exact(sync.get("selection_rule_per_runner"), "latest_explicit_valid_ltp_observation_at_or_before_target_time", "runner observation selection rule changed")
    require_exact(
        sync.get("cutoffs"),
        {
            "T90": {"minutes_before_kickoff": 90, "maximum_single_runner_staleness_seconds": 900, "maximum_home_draw_away_observation_span_seconds": 300},
            "T15": {"minutes_before_kickoff": 15, "maximum_single_runner_staleness_seconds": 300, "maximum_home_draw_away_observation_span_seconds": 120},
        },
        "synchronization or staleness thresholds changed",
    )
    required_sync_true = (
        "selected_runner_observation_must_be_pre_match",
        "all_three_runner_observed_at_timestamps_required",
        "no_future_observation_relative_to_target",
        "no_synthetic_or_imputed_timestamp",
        "no_cross_market_or_cross_event_price_join",
        "no_stitching_outside_registered_span",
    )
    require(all(sync.get(key) is True for key in required_sync_true), "synchronization fail-closed flag changed")
    require_exact(
        sync.get("fail_closed_conditions"),
        [
            "missing_ltp",
            "missing_observed_at",
            "non_finite_ltp",
            "ltp_below_1.01",
            "observation_after_target",
            "single_runner_staleness_exceeded",
            "cross_runner_observation_span_exceeded",
            "in_play_observation",
            "message_at_or_after_kickoff",
            "market_or_event_identity_mismatch",
        ],
        "synchronization fail-closed conditions changed",
    )
    require(sync.get("threshold_change_after_any_external_or_label_access_allowed") is False, "post-access synchronization threshold change enabled")

    lock = value.get("identity_lock_contract", {})
    require_exact(
        lock.get("persist_before_any_label_access"),
        [
            "ordered_eligible_identity_hashes",
            "eligible_count",
            "ordered_identity_hashes_sha256",
            "preregistration_canonical_sha256",
            "synchronization_gate_parameters",
        ],
        "pre-label identity lock contents changed",
    )
    require(
        lock.get("lock_must_be_written_and_reloaded_successfully_before_labels") is True
        and lock.get("lock_mutation_after_label_access_allowed") is False
        and lock.get("raw_names_prices_or_stream_messages_in_lock_allowed") is False
        and lock.get("label_fields_in_lock_allowed") is False
        and lock.get("fail_before_label_access_if_lock_missing_or_hash_mismatch") is True,
        "pre-label identity lock ordering or content gate changed",
    )

    probability = value.get("probability_contract", {})
    require_exact(probability.get("baseline"), {"id": "DRAW_FAIR_T15", "formula": "qD_T15"}, "baseline formula changed")
    require_exact(
        probability.get("fixed_candidate"),
        {
            "id": "DRAW_T15_PLUS_HALF_T90_MOVE",
            "source_cutoffs_minutes_before_kickoff": [90, 15],
            "coefficient": 0.5,
            "formula": "clip(qD_T15 + 0.5 * (qD_T15 - qD_T90))",
        },
        "unique candidate formula or coefficient changed",
    )
    require(probability.get("candidate_count") == 1, "candidate count changed")
    forbidden_probability_true = (
        "additional_candidates_allowed",
        "additional_features_or_models_allowed",
        "coefficient_tuning_allowed",
        "threshold_selection_allowed",
        "class_weighting_allowed",
        "model_fit_allowed",
        "policy_or_candidate_selection_allowed",
    )
    require(all(probability.get(key) is False for key in forbidden_probability_true), "candidate selection, tuning, weighting, thresholding, or model fitting enabled")

    comparison = value.get("comparison_contract", {})
    require(
        comparison.get("sample") == "all_synchronization_qualified_and_identity_locked_markets"
        and comparison.get("policy_split_allowed") is False
        and comparison.get("confirmation_split_allowed") is False
        and comparison.get("candidate_selection_step_allowed") is False
        and comparison.get("single_frozen_comparison_only") is True
        and comparison.get("baseline_id") == "DRAW_FAIR_T15"
        and comparison.get("candidate_id") == "DRAW_T15_PLUS_HALF_T90_MOVE",
        "single frozen comparison contract changed",
    )
    require_exact(
        comparison.get("candidate_minus_baseline_sign_convention"),
        {
            "average_precision_delta": "candidate_minus_baseline_higher_is_better",
            "roc_auc_delta": "candidate_minus_baseline_higher_is_better",
            "brier_delta": "candidate_minus_baseline_lower_is_better",
            "log_loss_delta": "candidate_minus_baseline_lower_is_better",
        },
        "candidate-minus-baseline sign convention changed",
    )

    metrics = value.get("metric_definitions", {})
    require_exact(
        metrics.get("average_precision"),
        {
            "id": "average_precision_draw_vs_non_draw",
            "implementation": "non_interpolated_step_area_under_precision_recall_curve",
            "algorithm": "sort_scores_descending_group_equal_scores;at_each_tie_group_end_compute_precision=tp/(tp+fp),recall=tp/total_positives;AP=sum((recall-recall_previous)*precision)",
            "tie_handling": "all_equal_scores_are_one_atomic_threshold_group",
            "no_positive_full_sample": "INVALID_SAMPLE_GATE_FAIL",
            "all_positive_full_sample": "valid_AP_equals_1",
        },
        "Average Precision definition changed",
    )
    require_exact(
        metrics.get("roc_auc"),
        {
            "id": "roc_auc_draw_vs_non_draw",
            "implementation": "mann_whitney_pair_probability",
            "formula": "(count(score_positive>score_negative)+0.5*count(score_positive==score_negative))/(n_positive*n_negative)",
            "tie_handling": "equal_positive_negative_score_pair_contributes_0.5",
            "single_class_full_sample": "INVALID_SAMPLE_GATE_FAIL",
        },
        "ROC AUC definition changed",
    )
    require_exact(metrics.get("binary_brier"), {"id": "brier_draw_binary", "formula": "mean((p-y)^2)", "lower_is_better": True}, "Binary Brier definition changed")
    require_exact(
        metrics.get("binary_log_loss"),
        {
            "id": "logloss_draw_binary",
            "formula": "mean(-(y*ln(p)+(1-y)*ln(1-p)))",
            "natural_log": True,
            "probability_domain": "[0.000001,0.999999]",
            "lower_is_better": True,
        },
        "Binary Log Loss definition changed",
    )
    require_exact(
        metrics.get("input_validation"),
        {
            "labels_allowed": [0, 1],
            "scores_must_be_finite": True,
            "scores_must_be_within_registered_clip_bounds": True,
            "missing_value_policy": "FAIL_CLOSED_NO_IMPUTATION_NO_ROW_DROP",
            "invalid_probability_policy": "FAIL_CLOSED_NO_RECLIP_DURING_SCORING",
        },
        "metric input validation or missing-value policy changed",
    )

    bootstrap = value.get("bootstrap_contract", {})
    expected_bootstrap = {
        "target_statistic": "average_precision_candidate_minus_baseline",
        "resampling_unit": "market",
        "paired_resampling": True,
        "sample_size_each_replicate": "n_eligible_markets",
        "replacement": True,
        "repetitions": 5000,
        "seed": 51103,
        "rng": "python_random_Random_seeded_once_then_randrange_n",
        "interval_quantiles": [0.05, 0.95],
        "quantile_algorithm": "R7_linear_interpolation_sorted_values_h=(m-1)*q",
        "no_positive_replicate_handling": "retain_replicate;set_baseline_AP=0_and_candidate_AP=0_so_delta=0;increment_no_positive_replicate_count",
        "all_positive_replicate_handling": "retain_replicate;both_AP_values_follow_definition_and_equal_1_so_delta=0;increment_all_positive_replicate_count",
        "invalid_replicates_discarded_or_redrawn": False,
        "receipt_must_report": ["repetitions", "seed", "no_positive_replicate_count", "all_positive_replicate_count", "p05", "median", "p95"],
    }
    require_exact(bootstrap, expected_bootstrap, "bootstrap rules changed")

    gates = value.get("sample_and_result_gates", {})
    require_exact(
        gates.get("research_pass_gate_all_required"),
        {
            "average_precision_delta_strictly_greater_than": 0.0,
            "average_precision_bootstrap_p05_strictly_greater_than": 0.0,
            "roc_auc_delta_greater_than_or_equal_to": 0.0,
            "brier_delta_less_than_or_equal_to": 0.0,
            "log_loss_delta_less_than_or_equal_to": 0.0,
        },
        "research pass conditions changed",
    )
    require(
        gates.get("pass_status") == "PASS_TRAJECTORY_PILOT_INCREMENT_JUSTIFIES_LARGER_LICENSED_DATASET_ONLY"
        and gates.get("candidate_not_above_all_gates_status") == "STOP_TRAJECTORY_PILOT_NO_INCREMENT"
        and gates.get("sample_gate_failure_status") == "STOP_NO_RESULT_SAMPLE_GATE_FAILED"
        and gates.get("no_forced_candidate_selection") is True,
        "pass/failure status or no-forced-selection gate changed",
    )

    label_order = value.get("label_access_order_contract", {})
    require_exact(
        label_order,
        {
            "labels_allowed_only_after_independent_authorization": True,
            "eligibility_must_be_label_blind": True,
            "identity_lock_must_be_persisted_and_verified_before_any_label_access": True,
            "pre_label_sample_gate_must_pass_before_any_label_access": True,
            "winner_or_settlement_fields_must_not_be_parsed_before_lock_and_gate_receipt": True,
            "all_eligible_labels_read_exactly_once_after_lock": True,
            "policy_labels_stage_allowed": False,
            "confirmation_labels_stage_allowed": False,
            "labels_may_not_change_eligibility_or_scores": True,
        },
        "label access order changed",
    )

    one_time = value.get("one_time_execution_contract", {})
    require_exact(
        one_time,
        {
            "manual_workflow_dispatch_only": True,
            "push_trigger_allowed": False,
            "pull_request_trigger_allowed": False,
            "independent_authorization_file_and_sha256_required": True,
            "authorization_nonce_must_be_unique": True,
            "consumed_marker_must_exist_before_external_source_or_label_access": True,
            "workflow_must_fail_before_external_source_or_label_access_if_nonce_already_consumed": True,
            "rerun_allowed": False,
            "retry_after_any_label_access_allowed": False,
            "reuse_same_sample_as_unseen_confirmation_allowed": False,
            "change_formula_coefficient_sync_threshold_metrics_bootstrap_or_gates_after_authorization_allowed": False,
        },
        "one-time-only execution restriction changed",
    )

    require_exact(
        value.get("authorization", {}),
        {
            "run_authorized": False,
            "external_raw_data_access_authorized": False,
            "winner_label_access_authorized": False,
            "authorization_file_present": False,
            "authorization_nonce_present": False,
        },
        "authorization must remain absent",
    )

    boundary = value.get("evidence_boundary", {})
    require(
        boundary.get("source_has_explicit_reusable_data_license") is False
        and boundary.get("raw_files_may_be_committed_or_uploaded") is False
        and boundary.get("raw_market_names_prices_or_stream_messages_may_be_persisted") is False
        and boundary.get("basic_ltp_is_executable_price") is False
        and boundary.get("formal_ev_allowed") is False,
        "external data, raw persistence, LTP, or EV boundary changed",
    )

    limits = value.get("hard_limits", {})
    require_exact(
        limits,
        {
            "research_only": True,
            "formal_weight": 0,
            "formal_promotion_allowed": False,
            "current_match_probability_allowed": False,
            "current_match_direction_allowed": False,
            "unified_score_matrix_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
            "provider_account_or_credentials_access": False,
            "CURRENT_mutation_allowed": False,
            "formal_model_mutation_allowed": False,
            "formal_data_mutation_allowed": False,
            "formal_config_mutation_allowed": False,
        },
        "formal_weight, CURRENT, or formal asset mutation gate changed",
    )

    forbidden_legacy_sections = {"identity_and_split_contract", "policy_selection_contract", "confirmation_metrics", "research_pass_gate", "fixed_candidates"}
    require(not any(key in value for key in forbidden_legacy_sections), "legacy policy/confirmation or multi-candidate section remains")

    actual_sha = canonical_sha256(value)
    require_exact(actual_sha, EXPECTED_CANONICAL_SHA256, "canonical preregistration SHA-256 mismatch")

    return {
        "status": "PASS_ZERO_LABEL_SINGLE_CANDIDATE_PREREGISTRATION_FROZEN",
        "preregistration_sha256": actual_sha,
        "external_data_accessed": False,
        "winner_labels_read": 0,
        "model_fits": 0,
        "thresholds_selected": 0,
        "candidate_count": 1,
        "policy_or_candidate_selection_allowed": False,
        "expected_legacy_upper_bound": 65,
        "minimum_synchronized_eligible_before_labels": 60,
        "run_authorized": False,
        "formal_weight": 0,
        "formal_model_changes": 0,
        "formal_data_changes": 0,
        "formal_config_changes": 0,
        "CURRENT_changes": 0,
        "negative_test_cases_expected": 17,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    receipt = validate(load_json(args.prereg))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
