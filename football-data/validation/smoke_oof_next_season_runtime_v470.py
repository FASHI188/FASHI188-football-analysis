#!/usr/bin/env python3
"""Smoke the next-season OOF bridge through the unchanged calibration function."""
from __future__ import annotations

import json
from pathlib import Path

import oof_matrix_calibration as calibration
from oof_next_season_runtime_v470 import load_rollforward_calibrator
from platform_core import ROOT

OUT = ROOT / "manifests" / "oof_next_season_runtime_v470_smoke.json"


def calculation(engine_sha: str) -> dict:
    return {
        "module_states": {"unified_score_matrix": "通过"},
        "probabilities": {
            "score_matrix": [
                {"home_goals": 0, "away_goals": 0, "probability": 0.20},
                {"home_goals": 1, "away_goals": 0, "probability": 0.30},
                {"home_goals": 0, "away_goals": 1, "probability": 0.20},
                {"home_goals": 1, "away_goals": 1, "probability": 0.30},
            ]
        },
        "derived_markets": {},
        "model_audit": {"season": "2026/27", "audit": {"engine_sha256": engine_sha}},
        "conclusions": {"confidence_grade": "D", "price_status": "No Bet"},
    }


def main() -> int:
    original_loader = calibration.load_oof_matrix_calibrator
    checks = {}
    reports = {}
    for cid in ("ESP_LaLiga", "NED_Eredivisie"):
        base = original_loader(cid)
        canonical_has_2026 = bool(base and isinstance(base[1].get("season_calibrators"), dict) and "2026/27" in base[1]["season_calibrators"])
        path, augmented, audit = load_rollforward_calibrator(cid, "2026/27", "2026-07-20T12:00:00Z")
        engine_sha = augmented["engine_sha256"]

        def temporary_loader(requested):
            return (path, augmented) if requested == cid else original_loader(requested)

        calibration.load_oof_matrix_calibrator = temporary_loader
        try:
            output = calibration.apply_oof_matrix_calibration(
                {"match_identity": {"competition_id": cid, "freeze_time_utc": "2026-07-20T12:00:00Z"}},
                calculation(engine_sha),
            )
        finally:
            calibration.load_oof_matrix_calibrator = original_loader
        checks[f"{cid}_canonical_has_no_2026_27"] = not canonical_has_2026
        checks[f"{cid}_rollforward_audit_passes"] = audit.get("status") == "通过"
        checks[f"{cid}_unchanged_calibration_function_passes"] = output.get("module_states", {}).get("oof_matrix_calibration") == "通过"
        checks[f"{cid}_target_season_is_2026_27"] = output.get("calibration_audit", {}).get("target_season") == "2026/27"
        checks[f"{cid}_temperature_matches_rollforward"] = abs(float(output.get("calibration_audit", {}).get("temperature")) - float(audit["temperature"])) <= 1e-12
        checks[f"{cid}_probability_conserved"] = abs(float(output.get("calibration_audit", {}).get("calibrated_probability_sum", 0.0)) - 1.0) <= 1e-10
        reports[cid] = {"rollforward_audit": audit, "calibration_audit": output.get("calibration_audit")}
    result = {
        "schema_version": "V4.7.0-oof-next-season-runtime-smoke-r1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "reports": reports,
        "canonical_calibration_code_sha256": calibration.sha256_file(calibration.CALIBRATION_MODULE_PATH),
        "probability_change": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
