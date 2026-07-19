#!/usr/bin/env python3
"""Fail-closed A-grade auditor using the corrected V4.6.3 evidence chain.

This auditor consumes true rolling outer folds, complete-final-chain replay,
point-in-time probable-lineup validation, calibration/subgroup/source/drift
diagnostics and OOF calibration evidence.  Missing market evidence remains a
hard failure.  The auditor never signs a receipt.
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

from platform_core import ROOT, PlatformError, atomic_write_json, load_json, sha256_file, utc_now  # noqa: E402

POLICY_PATH = ROOT / "validation" / "promotion_policy.json"
CORE_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
OOF_ROOT = ROOT / "validation" / "reports" / "oof_matrix_calibration_v461"
ROLLING_ROOT = ROOT / "validation" / "reports" / "rolling_outer_v463"
REPLAY_ROOT = ROOT / "validation" / "reports" / "final_chain_replay_v463"
LINEUP_ROOT = ROOT / "validation" / "reports" / "probable_lineup_v462"
DIAG_ROOT = ROOT / "validation" / "reports" / "a_grade_diagnostics_v463"
MARKET_ROOT = ROOT / "validation" / "reports" / "market_baseline_v463"
MODEL_ROOT = ROOT / "models" / "formal_core_v460"
REPORT_ROOT = ROOT / "validation" / "reports" / "a_grade_eligibility_v463"


def _load_required(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise PlatformError(f"missing {label}: {path.relative_to(ROOT)}")
    return load_json(path)


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


def audit_competition(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    policy = _load_required(POLICY_PATH, "promotion policy")
    core_path = CORE_ROOT / f"{competition_id}.json"
    oof_path = OOF_ROOT / f"{competition_id}.json"
    rolling_path = ROLLING_ROOT / f"{competition_id}.json"
    replay_path = REPLAY_ROOT / f"{competition_id}.json"
    lineup_path = LINEUP_ROOT / f"{competition_id}.json"
    diag_path = DIAG_ROOT / f"{competition_id}.json"
    market_path = MARKET_ROOT / f"{competition_id}.json"
    model_path = MODEL_ROOT / competition_id / "model.json"

    core = _load_required(core_path, "formal-core report")
    oof = _load_required(oof_path, "OOF report")
    rolling = _load_required(rolling_path, "rolling outer report")
    replay = _load_required(replay_path, "final-chain replay receipt")
    lineup = _load_required(lineup_path, "probable-lineup report")
    diag = _load_required(diag_path, "A-grade diagnostics")
    model = _load_required(model_path, "model artifact")
    market = load_json(market_path) if market_path.exists() else None

    t = policy["a_grade_thresholds"]
    rolling_checks = rolling.get("checks") or {}
    cal = diag.get("calibration_diagnostics") or {}
    subgroup = diag.get("subgroup_calibration") or {}
    core_metrics = core.get("model_metrics") or {}
    oof_rolling = oof.get("rolling_validation") or {}

    market_pass = bool(
        market
        and market.get("status") == "MARKET_BASELINE_VALIDATED"
        and market.get("timestamped_synchronized") is True
        and int(market.get("prediction_count", 0)) >= int(t["minimum_outer_predictions"])
        and _lte(market.get("model_minus_market_log_loss_ci95_upper"), t["market_log_loss_difference_ci_upper_lte"])
    )

    checks = {
        "minimum_outer_predictions": int(rolling.get("outer_predictions", 0)) >= int(t["minimum_outer_predictions"]),
        "minimum_outer_time_folds": int(rolling.get("outer_folds", 0)) >= int(t["minimum_outer_time_folds"]),
        "rolling_disjoint_and_prior": bool(rolling_checks.get("disjoint_test_windows") and rolling_checks.get("strictly_prior_selection")),
        "joint_log_score_ci": bool(rolling_checks.get("joint_log_score_ci")),
        "one_x_two_brier_rps_ci": bool(rolling_checks.get("one_x_two_brier_rps_ci")),
        "total_goals_rps_ci": bool(rolling_checks.get("total_goals_rps_ci")),
        "market_baseline": market_pass,
        "lineup_route": bool(lineup.get("validated_for_a_grade") is True),
        "independent_final_chain_replay": bool(
            replay.get("status") == "通过"
            and replay.get("independent_process") is True
            and int(replay.get("fixture_count", 0)) >= 12
            and float(replay.get("max_probability_or_settlement_difference", 1.0)) <= 1e-10
            and replay.get("engine_sha256") == core.get("engine_sha256") == model.get("engine_sha256")
        ),
        "oof_calibrator_available": bool(oof.get("operational_status") == "OOF_MATRIX_CALIBRATOR_AVAILABLE" and oof.get("enabled") is True),
        "oof_no_unsupported_scores": int(oof.get("unsupported_actual_scores_excluded", 1)) == 0,
        "calibration_intercepts": all(
            _lte(abs(cal.get(key)) if cal.get(key) is not None else None, t["calibration_intercept_abs_lte"])
            for key in ("home_intercept", "draw_intercept", "away_intercept")
        ),
        "calibration_slopes": all(
            _between(cal.get(key), t["calibration_slope_min"], t["calibration_slope_max"])
            for key in ("home_slope", "draw_slope", "away_slope")
        ),
        "calibration_ece": _lte(cal.get("maximum_ece"), t["ece_lte"]),
        "tail4_error": _lte(core_metrics.get("tail4_absolute_error"), t["tail_absolute_error_lte"]),
        "tail5_error": _lte(core_metrics.get("tail5_absolute_error"), t["tail_absolute_error_lte"]),
        "score_set_80_coverage": _between(
            oof_rolling.get("candidate_score_set_80_coverage"),
            t["score_set_80_coverage_min"], t["score_set_80_coverage_max"],
        ),
        "score_set_90_coverage": _between(
            oof_rolling.get("candidate_score_set_90_coverage"),
            t["score_set_90_coverage_min"], t["score_set_90_coverage_max"],
        ),
        "important_subgroup_ece": bool(
            subgroup.get("all_important_groups_meet_minimum_sample")
            and _lte(subgroup.get("maximum_ece"), 0.07)
        ),
        "source_manifest_complete": bool(diag.get("source_manifest_complete")),
        "no_unresolved_drift": bool(diag.get("no_unresolved_drift")),
        "engine_hash_bound": core.get("engine_sha256") == model.get("engine_sha256"),
    }
    eligible = all(checks.values())
    failed = [key for key, passed in checks.items() if not passed]
    result = {
        "schema_version": "V4.6.3-evidence",
        "audited_at_utc": utc_now(),
        "competition_id": competition_id,
        "checks": checks,
        "failed_checks": failed,
        "eligible_for_receipt_signing": eligible,
        "promotion_status": "ELIGIBLE_FOR_SIGNING" if eligible else "NOT_A",
        "a_grade_receipt_issued": False,
        "evidence_sha256": {
            "core": sha256_file(core_path),
            "oof": sha256_file(oof_path),
            "rolling_outer": sha256_file(rolling_path),
            "final_chain_replay": sha256_file(replay_path),
            "lineup": sha256_file(lineup_path),
            "diagnostics": sha256_file(diag_path),
            "market": sha256_file(market_path) if market_path.exists() else None,
            "model": sha256_file(model_path),
        },
        "governance_note": "Fail closed. No market file, no validated lineup history, insufficient drift audits, failed OOS statistics, or any hash/replay failure blocks A. This auditor does not issue or sign an A receipt.",
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = audit_competition(args.competition, write=True)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.output:
        atomic_write_json(Path(args.output), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["eligible_for_receipt_signing"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
