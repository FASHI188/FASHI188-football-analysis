#!/usr/bin/env python3
"""Build replay-safe next-season OOF full-matrix calibration rollforward receipts.

For a future target season, all training matrices and every activation guardrail
come strictly from completed earlier seasons. No target-season result is required.
The canonical OOF artifact is not modified; runtime may use this receipt only via a
separate fail-closed question-time bridge.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from football_v460_engine import ENGINE_PATH
from oof_matrix_calibration import CALIBRATION_MODULE_PATH
from oof_matrix_calibration_v461 import (
    _passes_guardrails,
    _rolling_summary,
    evaluate_outer_season,
    evaluate_records,
    fit_temperature,
)
from platform_core import ROOT, MatchRow, load_json, read_processed_matches, sha256_file, sha256_json

OUT = ROOT / "manifests" / "oof_next_season_rollforward_v470_status.json"
TARGETS = {
    "ESP_LaLiga": {"source_season": "2025/26", "target_season": "2026/27"},
    "NED_Eredivisie": {"source_season": "2025/26", "target_season": "2026/27"},
}


def build_one(competition_id: str, spec: dict[str, str]) -> dict[str, Any]:
    source_path = ROOT / "validation" / "reports" / "formal_core_v460" / f"{competition_id}.json"
    calibration_report_path = ROOT / "validation" / "reports" / "oof_matrix_calibration_v461" / f"{competition_id}.json"
    base_artifact_path = ROOT / "models" / "formal_core_v460" / competition_id / "oof_matrix_calibrator.json"
    source = load_json(source_path)
    calibration_report = load_json(calibration_report_path)
    base_artifact = load_json(base_artifact_path)
    engine_sha = sha256_file(ENGINE_PATH)
    calibration_sha = sha256_file(CALIBRATION_MODULE_PATH)
    if source.get("engine_sha256") != engine_sha:
        raise RuntimeError("source nested-backtest engine hash mismatch")
    if base_artifact.get("operational_status") != "OOF_MATRIX_CALIBRATOR_AVAILABLE" or base_artifact.get("enabled") is not True:
        raise RuntimeError("base OOF calibrator is not operational")
    if base_artifact.get("engine_sha256") != engine_sha:
        raise RuntimeError("base calibrator engine hash mismatch")
    if base_artifact.get("calibration_code_sha256") != calibration_sha:
        raise RuntimeError("base calibrator code hash mismatch")
    if base_artifact.get("source_nested_backtest_report_sha256") != sha256_file(source_path):
        raise RuntimeError("base calibrator source report hash mismatch")
    if base_artifact.get("calibration_report_sha256") != sha256_json(calibration_report):
        raise RuntimeError("base calibrator report hash mismatch")

    by_season: dict[str, list[MatchRow]] = defaultdict(list)
    for match in read_processed_matches(competition_id):
        by_season[match.season].append(match)

    folds = []
    seen = set()
    unsupported = 0
    for fold in source.get("folds", []):
        season = str(fold.get("outer_season") or "")
        if not season or season in seen:
            continue
        seen.add(season)
        records, missing = evaluate_outer_season(
            competition_id,
            sorted(by_season[season], key=lambda x: (x.date, x.home_team, x.away_team)),
            fold["selected_parameters"],
        )
        folds.append({"season": season, "records": records})
        unsupported += missing
    if not folds or folds[-1]["season"] != spec["source_season"]:
        raise RuntimeError("latest completed OOF fold does not match declared source season")
    all_records = [record for fold in folds for record in fold["records"]]
    if len(all_records) < 100:
        raise RuntimeError(f"insufficient OOF matrices for next-season calibration: {len(all_records)}")

    rolling_steps = []
    for index in range(1, len(folds)):
        train = [record for fold in folds[:index] for record in fold["records"]]
        test = folds[index]["records"]
        if len(train) < 100 or not test:
            continue
        temperature = fit_temperature(train)
        rolling_steps.append({
            "index": index,
            "validation_season": folds[index]["season"],
            "training_predictions": len(train),
            "validation_predictions": len(test),
            "temperature": temperature,
            "raw_metrics": evaluate_records(test, 1.0),
            "calibrated_metrics": evaluate_records(test, temperature),
        })
    summary = _rolling_summary(rolling_steps)
    learned_temperature = fit_temperature(all_records)
    guardrail_passed = _passes_guardrails(summary)
    live_temperature = learned_temperature if guardrail_passed else 1.0
    mode = "temperature" if guardrail_passed else "identity_guardrail"
    dates = [record["date"] for record in all_records if record.get("date")]
    return {
        "competition_id": competition_id,
        "status": "NEXT_SEASON_OOF_CALIBRATOR_FROZEN",
        "source_season": spec["source_season"],
        "target_season": spec["target_season"],
        "mode": mode,
        "temperature": live_temperature,
        "learned_temperature": learned_temperature,
        "guardrail_passed": guardrail_passed,
        "training_predictions": len(all_records),
        "training_seasons": [fold["season"] for fold in folds],
        "training_max_date": max(dates) if dates else None,
        "rolling_validation_predictions": int(summary.get("count", 0)),
        "rolling_validation": summary,
        "unsupported_actual_scores_excluded": unsupported,
        "engine_sha256": engine_sha,
        "calibration_code_sha256": calibration_sha,
        "source_nested_backtest_report_path": str(source_path.relative_to(ROOT)),
        "source_nested_backtest_report_sha256": sha256_file(source_path),
        "base_calibrator_path": str(base_artifact_path.relative_to(ROOT)),
        "base_calibrator_sha256": sha256_file(base_artifact_path),
        "base_calibration_report_path": str(calibration_report_path.relative_to(ROOT)),
        "base_calibration_report_sha256": sha256_file(calibration_report_path),
        "formal_weight_change": False,
        "probability_change": False,
        "policy": (
            "Target-season calibrator is trained only on completed prior-season OOF matrices; "
            "activation guardrails use only rolling validations completed before the target season."
        ),
    }


def main() -> int:
    reports = {}
    failures = []
    for cid, spec in TARGETS.items():
        try:
            reports[cid] = build_one(cid, spec)
        except Exception as exc:
            failures.append(cid)
            reports[cid] = {
                "competition_id": cid,
                "status": "FAILED",
                "reason": str(exc),
                "formal_weight_change": False,
                "probability_change": False,
            }
    out = {
        "schema_version": "V4.7.0-oof-next-season-rollforward-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "failed_competitions": failures,
        "formal_weight_change": False,
        "probability_change": False,
        "reports": reports,
        "policy": "Fail closed on any engine, calibration-code, source-report or base-calibrator hash mismatch.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
