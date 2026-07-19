#!/usr/bin/env python3
"""Time-ordered validation framework for LOMO market-value probabilities.

The validator uses the same timestamped synchronized historical market contract as
market_baseline_v463.  Target-market prices are excluded from the KL projection:
1X2 is forecast from AH+OU, AH from 1X2+OU, and OU from 1X2+AH.  This prevents the
same price from being both an input and the evidence used to claim value.

Until a valid historical dataset exists and the validation passes, formal EV must
remain No Bet even though question-time fair-price comparisons may be displayed.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

import sys
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
VALIDATION_DIR = Path(__file__).resolve().parent
for path in (ENGINE_DIR, VALIDATION_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from football_v460_engine import calculation_from_context  # noqa: E402
from market_kl_projection_v463 import lomo_projections  # noqa: E402
from market_baseline_v463 import _context, _load_rows, _validate_snapshot  # noqa: E402
from oof_matrix_calibration import apply_oof_matrix_calibration  # noqa: E402
from platform_core import (  # noqa: E402
    ROOT,
    PlatformError,
    atomic_write_json,
    derive_score_marginals,
    load_registry,
    settle_home_handicap,
    settle_over_total,
    sha256_file,
    utc_now,
)

REPORT_ROOT = ROOT / "validation" / "reports" / "lomo_value_v463"
MANIFEST_PATH = ROOT / "manifests" / "lomo_value_v463_status.json"
MIN_PREDICTIONS = 200
MAX_MEAN_DEGRADATION = 0.005


def _settlement_probability(matrix: list[dict[str, Any]], line: float, fn) -> dict[str, float]:
    result = {"win": 0.0, "push": 0.0, "loss": 0.0}
    for cell in matrix:
        settlement = fn(int(cell["home_goals"]), int(cell["away_goals"]), line)
        probability = float(cell["probability"])
        for key in result:
            result[key] += probability * settlement[key]
    return result


def _fractional_binary_log(prob_win: float, actual: dict[str, float]) -> float | None:
    decided = float(actual["win"]) + float(actual["loss"])
    if decided <= 1e-12:
        return None
    p = min(1.0 - 1e-12, max(1e-12, float(prob_win)))
    return -(
        float(actual["win"]) * math.log(p)
        + float(actual["loss"]) * math.log(1.0 - p)
    ) / decided


def validate_competition(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    path, rows = _load_rows(competition_id)
    if not rows:
        report = {
            "schema_version": "V4.6.3-evidence",
            "generated_at_utc": utc_now(),
            "competition_id": competition_id,
            "status": "LOMO_DATA_UNAVAILABLE",
            "validated_for_formal_ev": False,
            "prediction_count": 0,
            "data_path": str(path.relative_to(ROOT)),
            "reason": "No timestamped synchronized historical market dataset is installed.",
        }
        if write:
            atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
        return report

    one_logs = []
    one_base_logs = []
    ah_logs = []
    ou_logs = []
    errors = []
    for index, row in enumerate(rows):
        try:
            checked = _validate_snapshot(row)
            context = _context(row, checked)
            calculation = apply_oof_matrix_calibration(context, calculation_from_context(context))
            if calculation.get("module_states", {}).get("oof_matrix_calibration") != "通过":
                raise PlatformError("OOF final matrix unavailable")
            prior = calculation["probabilities"]["score_matrix"]
            lomo = lomo_projections(prior, checked["snapshot"])
            if any((lomo[key] or {}).get("status") == "不可用" for key in ("1x2", "ah", "ou")):
                raise PlatformError(f"LOMO projection unavailable: {lomo}")

            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            outcome = "home" if hg > ag else "draw" if hg == ag else "away"
            one = derive_score_marginals(lomo["1x2"]["matrix"])["1x2"]
            base_one = calculation["probabilities"]["one_x_two"]
            one_logs.append(-math.log(max(1e-15, float(one[outcome]))))
            one_base_logs.append(-math.log(max(1e-15, float(base_one[outcome]))))

            ah_line = float(checked["snapshot"]["asian_handicap"]["line"])
            ah_prob = _settlement_probability(lomo["ah"]["matrix"], ah_line, settle_home_handicap)
            ah_decided = ah_prob["win"] + ah_prob["loss"]
            if ah_decided > 1e-12:
                ah_conditional_win = ah_prob["win"] / ah_decided
                ah_actual = settle_home_handicap(hg, ag, ah_line)
                score = _fractional_binary_log(ah_conditional_win, ah_actual)
                if score is not None:
                    ah_logs.append(score)

            ou_line = float(checked["snapshot"]["total_goals"]["line"])
            ou_prob = _settlement_probability(lomo["ou"]["matrix"], ou_line, settle_over_total)
            ou_decided = ou_prob["win"] + ou_prob["loss"]
            if ou_decided > 1e-12:
                ou_conditional_over = ou_prob["win"] / ou_decided
                ou_actual = settle_over_total(hg, ag, ou_line)
                score = _fractional_binary_log(ou_conditional_over, ou_actual)
                if score is not None:
                    ou_logs.append(score)
        except Exception as exc:
            errors.append({"row": index + 1, "error": str(exc)})

    count = len(one_logs)
    one_delta = (mean(one_logs) - mean(one_base_logs)) if one_logs and one_base_logs else None
    sufficient = count >= MIN_PREDICTIONS and len(ah_logs) >= MIN_PREDICTIONS and len(ou_logs) >= MIN_PREDICTIONS
    # LOMO must at minimum avoid meaningful degradation versus the independently
    # generated non-market prior on 1X2.  AH/OU absolute scores are recorded for
    # calibration research; formal betting edge still requires separate price-edge
    # stability analysis after enough observations accumulate.
    passed = bool(
        sufficient
        and one_delta is not None
        and one_delta <= MAX_MEAN_DEGRADATION
        and not errors
    )
    report = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "LOMO_VALUE_MODEL_VALIDATED" if passed else "LOMO_VALUE_MODEL_NOT_VALIDATED",
        "validated_for_formal_ev": passed,
        "prediction_count": count,
        "ah_decided_prediction_count": len(ah_logs),
        "ou_decided_prediction_count": len(ou_logs),
        "minimum_predictions": MIN_PREDICTIONS,
        "mean_1x2_lomo_log_loss": mean(one_logs) if one_logs else None,
        "mean_1x2_base_log_loss": mean(one_base_logs) if one_base_logs else None,
        "mean_1x2_lomo_minus_base_log_loss": one_delta,
        "maximum_allowed_mean_degradation": MAX_MEAN_DEGRADATION,
        "mean_ah_lomo_conditional_log_loss": mean(ah_logs) if ah_logs else None,
        "mean_ou_lomo_conditional_log_loss": mean(ou_logs) if ou_logs else None,
        "data_path": str(path.relative_to(ROOT)),
        "data_sha256": sha256_file(path),
        "invalid_rows": errors,
        "governance_note": "Even a passing LOMO forecast-quality report does not authorize stake sizing or ROI claims. It only clears circularity-safe probability use for later formal EV qualification.",
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_all(*, write: bool = True) -> dict[str, Any]:
    reports = {}
    failures = []
    for item in load_registry()["competitions"]:
        cid = item["competition_id"]
        try:
            report = validate_competition(cid, write=write)
            reports[cid] = {
                "status": report["status"],
                "validated_for_formal_ev": report["validated_for_formal_ev"],
                "prediction_count": report["prediction_count"],
            }
        except Exception as exc:
            failures.append({"competition_id": cid, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "reports": reports,
        "failures": failures,
    }
    if write:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"LOMO validation failed: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    result = validate_competition(args.competition, write=not args.check_only) if args.competition else run_all(write=not args.check_only)
    if args.print_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
