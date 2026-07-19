#!/usr/bin/env python3
"""Independent deterministic replay verifier for frozen football predictions.

A replay is accepted only when the frozen context hashes are intact and a fresh
execution of the formal base core plus the eligible OOF calibration reproduces
the frozen final probability objects within the configured tolerance.
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

from football_v460_engine import calculation_from_context  # noqa: E402
from match_pipeline import validate_calculation_output  # noqa: E402
from oof_matrix_calibration import apply_oof_matrix_calibration  # noqa: E402
from platform_core import PlatformError, load_json, sha256_json, utc_now  # noqa: E402

TOLERANCE = 1e-10


def _score_map(matrix: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    return {
        (int(cell["home_goals"]), int(cell["away_goals"])): float(cell["probability"])
        for cell in matrix
    }


def _max_probability_difference(left: dict[str, Any], right: dict[str, Any]) -> float:
    differences: list[float] = []
    for key in ("home", "draw", "away"):
        differences.append(abs(float(left["one_x_two"][key]) - float(right["one_x_two"][key])))
    for key in ("0", "1", "2", "3", "4", "5", "6", "7+"):
        differences.append(abs(float(left["total_goals"][key]) - float(right["total_goals"][key])))
    differences.append(abs(float(left.get("btts_yes", 0.0)) - float(right.get("btts_yes", 0.0))))
    lm, rm = _score_map(left["score_matrix"]), _score_map(right["score_matrix"])
    for key in set(lm) | set(rm):
        differences.append(abs(lm.get(key, 0.0) - rm.get(key, 0.0)))
    return max(differences or [0.0])


def verify_freeze(freeze: dict[str, Any]) -> dict[str, Any]:
    hashes = freeze.get("hashes") or {}
    context = freeze.get("match_context")
    frozen_calculation = freeze.get("calculation_output")
    frozen_validation = freeze.get("validation_report")
    if not isinstance(context, dict) or not isinstance(frozen_calculation, dict) or not isinstance(frozen_validation, dict):
        raise PlatformError("freeze is missing context/calculation/validation objects")

    payload_without_hashes = {key: value for key, value in freeze.items() if key != "hashes"}
    integrity = {
        "match_context": hashes.get("match_context_sha256") == sha256_json(context),
        "calculation_output": hashes.get("calculation_output_sha256") == sha256_json(frozen_calculation),
        "validation_report": hashes.get("validation_report_sha256") == sha256_json(frozen_validation),
        "freeze_payload": hashes.get("freeze_payload_sha256") == sha256_json(payload_without_hashes),
    }
    if not all(integrity.values()):
        raise PlatformError(f"freeze hash integrity failed: {integrity}")

    replay = calculation_from_context(context)
    replay = apply_oof_matrix_calibration(context, replay)
    validation = validate_calculation_output(context, replay)
    if validation.get("status") != "通过":
        raise PlatformError(f"replayed calculation failed formal validation: {validation.get('errors')}")

    max_diff = _max_probability_difference(
        frozen_calculation.get("probabilities") or {}, replay.get("probabilities") or {}
    )
    passed = max_diff <= TOLERANCE
    return {
        "schema_version": "V4.6.2",
        "verified_at_utc": utc_now(),
        "freeze_id": freeze.get("freeze_id"),
        "status": "通过" if passed else "失败",
        "tolerance": TOLERANCE,
        "max_probability_difference": max_diff,
        "freeze_integrity": integrity,
        "replayed_validation_status": validation.get("status"),
        "replayed_engine_sha256": replay.get("model_audit", {}).get("audit", {}).get("engine_sha256"),
        "replayed_oof_status": replay.get("module_states", {}).get("oof_matrix_calibration"),
        "independent_replay": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        receipt = verify_freeze(load_json(Path(args.freeze)))
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] == "通过" else 2


if __name__ == "__main__":
    raise SystemExit(main())
