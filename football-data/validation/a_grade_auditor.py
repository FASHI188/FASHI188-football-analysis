#!/usr/bin/env python3
"""Independent A-grade governance auditor.

This module never upgrades a competition by assertion. It reads frozen
validation artifacts and either refuses promotion with explicit failed gates or
emits a machine-readable eligibility result. Receipt signing remains a separate
controlled action; therefore this auditor cannot by itself create an A receipt.
Missing evidence always fails closed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import sys

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, PlatformError, load_json, sha256_file, utc_now  # noqa: E402

POLICY_PATH = ROOT / "validation" / "promotion_policy.json"
CORE_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
OOF_REPORT_ROOT = ROOT / "validation" / "reports" / "oof_matrix_calibration_v461"
MODEL_ROOT = ROOT / "models" / "formal_core_v460"
REPLAY_REPORT_ROOT = ROOT / "validation" / "reports" / "replay_v462"
LINEUP_REPORT_ROOT = ROOT / "validation" / "reports" / "probable_lineup_v462"


def _between(value: Any, low: float, high: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return low <= number <= high


def _lte(value: Any, limit: float) -> bool:
    try:
        return float(value) <= limit
    except (TypeError, ValueError):
        return False


def audit_competition(competition_id: str) -> dict[str, Any]:
    policy = load_json(POLICY_PATH)
    core_path = CORE_REPORT_ROOT / f"{competition_id}.json"
    oof_path = OOF_REPORT_ROOT / f"{competition_id}.json"
    model_path = MODEL_ROOT / competition_id / "model.json"
    replay_path = REPLAY_REPORT_ROOT / f"{competition_id}.json"
    lineup_path = LINEUP_REPORT_ROOT / f"{competition_id}.json"
    if not core_path.exists() or not model_path.exists():
        raise PlatformError(f"required formal-core report/model missing for {competition_id}")
    core = load_json(core_path)
    model = load_json(model_path)
    oof = load_json(oof_path) if oof_path.exists() else None
    replay = load_json(replay_path) if replay_path.exists() else None
    lineup = load_json(lineup_path) if lineup_path.exists() else None
    thresholds = policy["a_grade_thresholds"]
    core_checks = core.get("a_grade_checks", {})
    core_metrics = core.get("model_metrics", {})
    oof_rolling = (oof or {}).get("rolling_validation", {})
    calibration = core.get("calibration_diagnostics", {})
    subgroup = core.get("subgroup_calibration", {})

    checks: dict[str, bool] = {
        "minimum_outer_predictions": int(core.get("outer_predictions", 0)) >= int(thresholds["minimum_outer_predictions"]),
        "minimum_outer_time_folds": int(core.get("outer_folds", 0)) >= int(thresholds["minimum_outer_time_folds"]),
        "core_engine_hash_bound": core.get("engine_sha256") == model.get("engine_sha256"),
        "joint_log_score_ci": bool(core_checks.get("joint_log_score_ci")),
        "one_x_two_brier_rps_ci": bool(core_checks.get("one_x_two_brier_rps_ci", False)),
        "total_goals_rps_ci": bool(core_checks.get("total_goals_rps_ci")),
        "market_baseline": bool(core_checks.get("market_baseline")),
        "lineup_route": bool(
            lineup
            and lineup.get("status") == "PROBABLE_LINEUP_ROUTE_VALIDATED"
            and lineup.get("validated_for_a_grade") is True
            and int(lineup.get("prediction_count", 0)) >= int(lineup.get("minimum_validation_predictions", 10**9))
        ),
        "independent_replay_receipt": bool(
            replay
            and replay.get("status") == "通过"
            and replay.get("independent_process") is True
            and int(replay.get("fixture_count", 0)) >= 12
            and float(replay.get("max_probability_difference", 1.0)) <= 1e-10
            and replay.get("engine_sha256") == core.get("engine_sha256") == model.get("engine_sha256")
            and replay.get("model_artifact_sha256") == sha256_file(model_path)
            and replay.get("core_report_sha256") == sha256_file(core_path)
        ),
        "oof_calibrator_available": bool(oof and oof.get("operational_status") == "OOF_MATRIX_CALIBRATOR_AVAILABLE" and oof.get("enabled") is True),
        "oof_no_unsupported_scores": bool(oof and int(oof.get("unsupported_actual_scores_excluded", 1)) == 0),
        "calibration_intercepts": all(
            _lte(abs(calibration.get(key)) if calibration.get(key) is not None else None, thresholds["calibration_intercept_abs_lte"])
            for key in ("home_intercept", "draw_intercept", "away_intercept")
        ),
        "calibration_slopes": all(
            _between(calibration.get(key), thresholds["calibration_slope_min"], thresholds["calibration_slope_max"])
            for key in ("home_slope", "draw_slope", "away_slope")
        ),
        "calibration_ece": _lte(calibration.get("maximum_ece"), thresholds["ece_lte"]),
        "tail4_error": _lte(core_metrics.get("tail4_absolute_error"), thresholds["tail_absolute_error_lte"]),
        "tail5_error": _lte(core_metrics.get("tail5_absolute_error"), thresholds["tail_absolute_error_lte"]),
        "score_set_80_coverage": _between(
            oof_rolling.get("candidate_score_set_80_coverage"),
            thresholds["score_set_80_coverage_min"],
            thresholds["score_set_80_coverage_max"],
        ),
        "score_set_90_coverage": _between(
            oof_rolling.get("candidate_score_set_90_coverage"),
            thresholds["score_set_90_coverage_min"],
            thresholds["score_set_90_coverage_max"],
        ),
        "important_subgroup_ece": bool(
            subgroup.get("all_important_groups_meet_minimum_sample")
            and _lte(subgroup.get("maximum_ece"), 0.07)
        ),
        "source_manifest_complete": bool(core_checks.get("source_manifest_complete", False)),
        "no_unresolved_drift": bool(core_checks.get("no_unresolved_drift", False)),
    }
    eligible_for_receipt_signing = all(checks.values())
    failed = [key for key, passed in checks.items() if not passed]
    return {
        "schema_version": "V4.6.2",
        "audited_at_utc": utc_now(),
        "competition_id": competition_id,
        "core_report_sha256": sha256_file(core_path),
        "oof_report_sha256": sha256_file(oof_path) if oof_path.exists() else None,
        "model_artifact_sha256": sha256_file(model_path),
        "checks": checks,
        "failed_checks": failed,
        "eligible_for_receipt_signing": eligible_for_receipt_signing,
        "promotion_status": "ELIGIBLE_FOR_SIGNING" if eligible_for_receipt_signing else "NOT_A",
        "a_grade_receipt_issued": False,
        "governance_note": "Fail-closed auditor: missing calibration, market, subgroup, replay, source-manifest or drift evidence blocks A eligibility. This auditor cannot sign or issue an A receipt; a separate signed receipt must bind competition, data route, engine hash and validity window.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = audit_competition(args.competition)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["eligible_for_receipt_signing"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
