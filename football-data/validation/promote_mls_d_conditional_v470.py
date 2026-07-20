#!/usr/bin/env python3
"""Independent governance receipt builder for USA_MLS V4.7 D|T promotion.

This module performs no fitting, no parameter search and no score transformation.
It only reads frozen validation/audit artifacts and code/source hashes.  It signs a
competition-specific promotion receipt when every V4.7 gate represented by those
artifacts passes.  The receipt activates the exact fully validated D|T transform for
USA_MLS 2026 only; all other competition/module weights remain unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, atomic_write_json, load_json, sha256_file

CID = "USA_MLS"
MODULE = "conditional_allocation_v470"
TARGET_SEASON = "2026"
FINAL_REVIEW = ROOT / "manifests" / "mls_d_conditional_final_chain_v470_status.json"
PRIORITY_ARTIFACT = ROOT / "models" / "challengers_v470" / CID / "priority_v470.json"
CALIBRATOR_ARTIFACT = ROOT / "models" / "formal_core_v460" / CID / "oof_matrix_calibrator.json"
CONDITIONAL_CODE = ROOT / "engine" / "conditional_allocation_challenger_v470.py"
FINAL_REVIEW_CODE = ROOT / "validation" / "review_mls_d_conditional_final_chain_v470.py"
TRAINING_CODE = ROOT / "validation" / "train_priority_challengers_v470.py"
COMPETITION_CONFIG = ROOT / "config" / "competition_independent_v470.json"
RUNTIME_MAINTENANCE = ROOT / "manifests" / "runtime_maintenance_v473_status.json"
FINAL_CHAIN_REPLAY = ROOT / "manifests" / "final_chain_replay_v463_status.json"
OUT = ROOT / "manifests" / "promotions" / "USA_MLS_d_conditional_v470.json"


def _require(condition: bool, name: str, checks: dict[str, bool]) -> None:
    checks[name] = bool(condition)


def main() -> int:
    final = load_json(FINAL_REVIEW)
    priority = load_json(PRIORITY_ARTIFACT)
    calibrator = load_json(CALIBRATOR_ARTIFACT)
    config = load_json(COMPETITION_CONFIG)
    maintenance = load_json(RUNTIME_MAINTENANCE)
    replay = load_json(FINAL_CHAIN_REPLAY)

    ci = final.get("confidence_intervals") or {}
    audits = final.get("audits") or {}
    current_cal = final.get("current_final_chain_calibration") or {}
    candidate_cal = final.get("candidate_final_chain_calibration") or {}
    structural = final.get("structural_absolute_error_nonworse") or {}
    default_policy = config.get("default_competition_policy") or {}
    competitions = set(config.get("competitions") or [])

    checks: dict[str, bool] = {}
    _require(final.get("competition_id") == CID, "final_review_competition_match", checks)
    _require(final.get("status") == "FORMAL_PROMOTION_REVIEW_READY", "final_review_ready", checks)
    _require(int(final.get("outer_predictions") or 0) == 1954, "outer_prediction_count_frozen", checks)
    _require(int(final.get("outer_folds") or 0) == 5, "outer_fold_count_frozen", checks)
    _require(float((ci.get("joint_log") or {}).get("ci95_upper", 1.0)) < 0.0, "joint_log_95ci_improves", checks)
    _require(float((ci.get("one_x_two_brier") or {}).get("ci95_upper", 1.0)) <= 0.002, "one_x_two_brier_noninferior", checks)
    _require(float((ci.get("one_x_two_rps") or {}).get("ci95_upper", 1.0)) <= 0.002, "one_x_two_rps_noninferior", checks)
    _require(abs(float((ci.get("total_rps") or {}).get("mean_difference", 1.0))) <= 1e-12, "total_goals_rps_preserved", checks)
    _require(all(bool(structural.get(name)) for name in ("btts", "home_zero", "away_zero", "margin2plus")), "all_structural_errors_nonworse", checks)
    _require(float(candidate_cal.get("top1_hit_rate", 0.0)) >= float(current_cal.get("top1_hit_rate", 1.0)), "top1_not_worse", checks)
    _require(float(candidate_cal.get("top3_hit_rate", 0.0)) >= float(current_cal.get("top3_hit_rate", 1.0)), "top3_not_worse", checks)
    _require(float(candidate_cal.get("top5_hit_rate", 0.0)) >= float(current_cal.get("top5_hit_rate", 1.0)), "top5_not_worse", checks)
    _require(0.76 <= float(candidate_cal.get("score_set_80_coverage", -1.0)) <= 0.84, "score_set_80_calibrated", checks)
    _require(0.86 <= float(candidate_cal.get("score_set_90_coverage", -1.0)) <= 0.94, "score_set_90_calibrated", checks)
    _require(bool(audits.get("primary_ci_pass")), "primary_ci_gate", checks)
    _require(bool(audits.get("calibration_pass")), "calibration_gate", checks)
    _require(bool(audits.get("probability_conservation_pass")), "probability_conservation", checks)
    _require(bool(audits.get("point_in_time_calibration_safe")), "point_in_time_calibration_safe", checks)
    _require(float(audits.get("max_final_total_marginal_residual", 1.0)) <= 1e-10, "final_total_marginal_preserved", checks)
    _require(priority.get("competition_id") == CID, "priority_artifact_competition_match", checks)
    _require(str(priority.get("target_live_season")) == TARGET_SEASON, "target_live_season_match", checks)
    _require(priority.get("formal_weight") == 0, "source_challenger_still_unpromoted", checks)
    _require((priority.get("conditional_allocation") or {}).get("status") == "REVIEW_CANDIDATE", "conditional_training_review_candidate", checks)
    _require(calibrator.get("competition_id") == CID and calibrator.get("enabled") is True, "oof_calibrator_operational", checks)
    _require(maintenance.get("status") == "PASS" and int(maintenance.get("hard_error_count") or 0) == 0, "runtime_maintenance_pass", checks)
    _require(((replay.get("reports") or {}).get(CID) or {}).get("status") == "通过", "current_core_final_chain_replay_pass", checks)
    _require(CID in competitions, "competition_registered_in_independence_config", checks)
    _require(default_policy.get("allow_cross_competition_training_rows") is False, "no_cross_competition_training_rows", checks)
    _require(default_policy.get("allow_cross_competition_calibrator") is False, "no_cross_competition_calibrator", checks)
    _require(default_policy.get("allow_cross_competition_challenger_weight") is False, "no_cross_competition_challenger_weight", checks)

    passed = all(checks.values())
    parameters = dict((priority.get("conditional_allocation") or {}).get("parameters") or {})
    if set(parameters) != {"btts", "home_zero", "away_zero", "margin2plus"}:
        passed = False
        checks["validated_parameter_schema"] = False
    else:
        checks["validated_parameter_schema"] = True

    receipt: dict[str, Any] = {
        "schema_version": "V4.7.0-competition-module-promotion-receipt-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "promotion_status": "PROMOTED" if passed else "REJECTED_KEEP_FORMAL_WEIGHT_ZERO",
        "competition_id": CID,
        "target_season": TARGET_SEASON,
        "module": MODULE,
        "formal_weight": 1.0 if passed else 0.0,
        "activation_mode": "full_validated_transform" if passed else "disabled",
        "activation_order": "post_oof_matrix_calibration",
        "parameters": parameters if passed else {},
        "automatic_cross_competition_promotion": False,
        "checks": checks,
        "evidence": {
            "final_chain_review_path": str(FINAL_REVIEW.relative_to(ROOT)),
            "final_chain_review_sha256": sha256_file(FINAL_REVIEW),
            "priority_artifact_path": str(PRIORITY_ARTIFACT.relative_to(ROOT)),
            "priority_artifact_sha256": sha256_file(PRIORITY_ARTIFACT),
            "oof_calibrator_path": str(CALIBRATOR_ARTIFACT.relative_to(ROOT)),
            "oof_calibrator_sha256": sha256_file(CALIBRATOR_ARTIFACT),
            "conditional_code_path": str(CONDITIONAL_CODE.relative_to(ROOT)),
            "conditional_code_sha256": sha256_file(CONDITIONAL_CODE),
            "training_code_sha256": sha256_file(TRAINING_CODE),
            "final_review_code_sha256": sha256_file(FINAL_REVIEW_CODE),
            "competition_independence_config_sha256": sha256_file(COMPETITION_CONFIG),
            "runtime_maintenance_sha256": sha256_file(RUNTIME_MAINTENANCE),
            "final_chain_replay_sha256": sha256_file(FINAL_CHAIN_REPLAY),
        },
        "validated_metrics": {
            "joint_log_ci95_upper": (ci.get("joint_log") or {}).get("ci95_upper"),
            "one_x_two_brier_ci95_upper": (ci.get("one_x_two_brier") or {}).get("ci95_upper"),
            "one_x_two_rps_ci95_upper": (ci.get("one_x_two_rps") or {}).get("ci95_upper"),
            "total_rps_mean_difference": (ci.get("total_rps") or {}).get("mean_difference"),
            "current_top1": current_cal.get("top1_hit_rate"),
            "candidate_top1": candidate_cal.get("top1_hit_rate"),
            "current_top3": current_cal.get("top3_hit_rate"),
            "candidate_top3": candidate_cal.get("top3_hit_rate"),
            "current_top5": current_cal.get("top5_hit_rate"),
            "candidate_top5": candidate_cal.get("top5_hit_rate"),
            "current_score80": current_cal.get("score_set_80_coverage"),
            "candidate_score80": candidate_cal.get("score_set_80_coverage"),
            "current_score90": current_cal.get("score_set_90_coverage"),
            "candidate_score90": candidate_cal.get("score_set_90_coverage"),
        },
        "invalidation_policy": [
            "competition_id or target_season mismatch",
            "any bound code/artifact hash mismatch",
            "runtime maintenance hard error",
            "competition-independence policy violation",
            "new target season without a new competition-specific receipt",
            "subsequent drift audit suspends the module",
        ],
        "governance_note": (
            "This receipt promotes only USA_MLS D|T conditional structural correction for target season 2026. "
            "It does not promote total_tail_v470, dynamic strength, lineup numeric effects, xG residuals, "
            "structured calibration, market coordination, EV, or any other competition."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OUT, receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
