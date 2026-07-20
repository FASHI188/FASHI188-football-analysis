#!/usr/bin/env python3
"""Runtime verifier for next-season OOF calibration rollforward receipts.

The canonical calibration module and its file hash are unchanged. This helper may
augment the in-memory calibrator only for an exact competition/target-season pair
whose rollforward receipt passes all bound hash and point-in-time checks.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from football_v460_engine import ENGINE_PATH
from oof_matrix_calibration import CALIBRATION_MODULE_PATH
from platform_core import ROOT, PlatformError, load_json, parse_iso_datetime, sha256_file, sha256_json

ROLLFORWARD_PATH = ROOT / "manifests" / "oof_next_season_rollforward_v470_status.json"


def load_rollforward_calibrator(
    competition_id: str,
    target_season: str,
    freeze_time_utc: str,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    if not ROLLFORWARD_PATH.exists():
        raise PlatformError("next-season OOF calibration rollforward receipt missing")
    receipt = load_json(ROLLFORWARD_PATH)
    report = (receipt.get("reports") or {}).get(competition_id)
    if not isinstance(report, dict) or report.get("status") != "NEXT_SEASON_OOF_CALIBRATOR_FROZEN":
        raise PlatformError("competition has no valid next-season OOF calibrator")
    if str(report.get("target_season") or "") != target_season:
        raise PlatformError("next-season OOF calibrator target season mismatch")

    source_path = ROOT / str(report.get("source_nested_backtest_report_path") or "")
    base_path = ROOT / str(report.get("base_calibrator_path") or "")
    calibration_report_path = ROOT / str(report.get("base_calibration_report_path") or "")
    if not source_path.exists() or not base_path.exists() or not calibration_report_path.exists():
        raise PlatformError("next-season OOF calibrator bound source artifact missing")
    source = load_json(source_path)
    base = load_json(base_path)
    calibration_report = load_json(calibration_report_path)
    checks = {
        "engine_sha_match": report.get("engine_sha256") == sha256_file(ENGINE_PATH),
        "calibration_code_sha_match": report.get("calibration_code_sha256") == sha256_file(CALIBRATION_MODULE_PATH),
        "source_report_sha_match": report.get("source_nested_backtest_report_sha256") == sha256_file(source_path),
        "base_calibrator_sha_match": report.get("base_calibrator_sha256") == sha256_file(base_path),
        "base_calibration_report_sha_match": report.get("base_calibration_report_sha256") == sha256_file(calibration_report_path),
        "source_engine_binding_match": source.get("engine_sha256") == sha256_file(ENGINE_PATH),
        "base_engine_binding_match": base.get("engine_sha256") == sha256_file(ENGINE_PATH),
        "base_calibration_code_binding_match": base.get("calibration_code_sha256") == sha256_file(CALIBRATION_MODULE_PATH),
        "base_source_report_binding_match": base.get("source_nested_backtest_report_sha256") == sha256_file(source_path),
        "base_calibration_report_binding_match": base.get("calibration_report_sha256") == sha256_json(calibration_report),
        "guardrail_passed": report.get("guardrail_passed") is True,
        "unsupported_actual_scores_zero": int(report.get("unsupported_actual_scores_excluded", -1)) == 0,
    }
    if not all(checks.values()):
        raise PlatformError(f"next-season OOF rollforward hash/invariant failure: {checks}")

    cutoff = parse_iso_datetime(freeze_time_utc, "freeze_time_utc")
    training_max_date = str(report.get("training_max_date") or "").strip()
    if not training_max_date:
        raise PlatformError("next-season OOF rollforward training_max_date missing")
    try:
        training_date = datetime.fromisoformat(training_max_date).date()
    except ValueError as exc:
        raise PlatformError("invalid next-season OOF training_max_date") from exc
    if training_date >= cutoff.date():
        raise PlatformError("next-season OOF training horizon is not strictly before prediction cutoff")

    augmented = copy.deepcopy(base)
    season_map = dict(augmented.get("season_calibrators") or {})
    season_map[target_season] = {
        "target_season": target_season,
        "mode": report["mode"],
        "temperature": float(report["temperature"]),
        "learned_temperature": float(report["learned_temperature"]),
        "guardrail_passed": bool(report["guardrail_passed"]),
        "training_predictions": int(report["training_predictions"]),
        "training_seasons": list(report.get("training_seasons") or []),
        "training_max_date": training_max_date,
        "rolling_validation_predictions": int(report["rolling_validation_predictions"]),
        "rolling_validation": report["rolling_validation"],
    }
    augmented["season_calibrators"] = season_map
    audit = {
        "status": "通过",
        "competition_id": competition_id,
        "source_season": report["source_season"],
        "target_season": target_season,
        "mode": report["mode"],
        "temperature": float(report["temperature"]),
        "training_predictions": int(report["training_predictions"]),
        "training_seasons": list(report.get("training_seasons") or []),
        "training_max_date": training_max_date,
        "rolling_validation_predictions": int(report["rolling_validation_predictions"]),
        "checks": checks,
        "probability_mutation": False,
        "policy": "In-memory target-season route only; canonical base calibrator file and calibration code remain unchanged.",
    }
    return base_path, augmented, audit
