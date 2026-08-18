#!/usr/bin/env python3
"""Build or verify the hash-bound USA_MLS D|T runtime activation artifact.

This operational entrypoint is intentionally located under runtime/activation,
not validation. `--check` is read-only. Running without `--check` materializes
the activation receipt and therefore requires an explicitly authorized use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
ENGINE_DIR = ROOT_DIR / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, atomic_write_json, load_json

PROMOTION_RECEIPT = ROOT / "manifests" / "promotions" / "USA_MLS_d_conditional_v470.json"
RUNTIME_MODULE = ROOT / "engine" / "promoted_challenger_runtime_v470.py"
RUNTIME_GATE = ROOT / "engine" / "promoted_challenger_runtime_gate_v470.py"
ACTIONABLE_RUNNER = ROOT / "engine" / "run_formal_prediction_actionable.py"
OUT = ROOT / "manifests" / "promotions" / "USA_MLS_d_conditional_v470_runtime_activation.json"
SOURCE_REPORT = ROOT / "validation" / "reports" / "formal_core_v460" / "USA_MLS.json"
CALIBRATION_REPORT = ROOT / "validation" / "reports" / "oof_matrix_calibration_v461" / "USA_MLS.json"


def canonical_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build_payload() -> dict:
    receipt = load_json(PROMOTION_RECEIPT)
    evidence = receipt.get("evidence") or {}
    promotion_bound_files = {
        "conditional_code_sha256": ROOT / str(evidence.get("conditional_code_path") or ""),
        "priority_artifact_sha256": ROOT / str(evidence.get("priority_artifact_path") or ""),
        "oof_calibrator_sha256": ROOT / str(evidence.get("oof_calibrator_path") or ""),
        "final_chain_review_sha256": ROOT / str(evidence.get("final_chain_review_path") or ""),
        "competition_independence_config_sha256": ROOT / "config" / "competition_independent_v470.json",
    }
    promotion_files_present = all(path.is_file() for path in promotion_bound_files.values())
    promotion_hashes_match = promotion_files_present and all(
        str(evidence.get(field) or "") == canonical_sha256(path)
        for field, path in promotion_bound_files.items()
    )
    calibrator_path = promotion_bound_files["oof_calibrator_sha256"]
    calibrator = load_json(calibrator_path) if calibrator_path.is_file() else {}
    calibrator_internal_bindings_match = (
        SOURCE_REPORT.is_file()
        and CALIBRATION_REPORT.is_file()
        and calibrator.get("source_nested_backtest_report_sha256") == canonical_sha256(SOURCE_REPORT)
        and calibrator.get("calibration_report_sha256") == canonical_sha256(CALIBRATION_REPORT)
    )
    checks = {
        "promotion_receipt_promoted": receipt.get("promotion_status") == "PROMOTED",
        "competition_match": receipt.get("competition_id") == "USA_MLS",
        "target_season_match": str(receipt.get("target_season")) == "2026",
        "module_match": receipt.get("module") == "conditional_allocation_v470",
        "formal_weight_full_validated_transform": float(receipt.get("formal_weight", 0.0)) == 1.0,
        "activation_order_post_oof": receipt.get("activation_order") == "post_oof_matrix_calibration",
        "runtime_module_present": RUNTIME_MODULE.exists(),
        "runtime_gate_present": RUNTIME_GATE.exists(),
        "actionable_runner_present": ACTIONABLE_RUNNER.exists(),
        "promotion_bound_files_present": promotion_files_present,
        "promotion_bound_artifact_hashes_match": promotion_hashes_match,
        "oof_calibrator_internal_bindings_match": calibrator_internal_bindings_match,
    }
    active = all(checks.values())
    payload = {
        "schema_version": "V4.7.0-runtime-activation-r2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "activation_status": "ACTIVE" if active else "INACTIVE_STALE_BOUND_ARTIFACT",
        "competition_id": "USA_MLS",
        "target_season": "2026",
        "module": "conditional_allocation_v470",
        "activation_order": "post_oof_matrix_calibration",
        "formal_weight": 1.0 if active else 0.0,
        "checks": checks,
        "bound_sha256": {
            "promotion_receipt": canonical_sha256(PROMOTION_RECEIPT),
            "runtime_module": canonical_sha256(RUNTIME_MODULE),
            "runtime_gate": canonical_sha256(RUNTIME_GATE),
            "actionable_runner": canonical_sha256(ACTIONABLE_RUNNER),
        } if active else {},
        "policy": "Fail closed on any bound hash mismatch. This script does not select project tasks or grant activation authority by itself.",
    }
    return payload


def critical_view(payload: dict) -> dict:
    return {key: payload.get(key) for key in (
        "schema_version", "activation_status", "competition_id", "target_season",
        "module", "activation_order", "formal_weight", "checks", "bound_sha256",
    )}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = build_payload()
    active = payload["activation_status"] == "ACTIVE"
    if args.check:
        current = load_json(OUT) if OUT.exists() else {}
        passed = critical_view(current) == critical_view(payload)
        print(json.dumps({
            "status": "PASS" if passed else "FAIL_STALE_ACTIVATION",
            "activation_status": payload["activation_status"],
            "bound_sha256": payload["bound_sha256"],
        }, ensure_ascii=False, indent=2))
        return 0 if passed else 2
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if active else 2


if __name__ == "__main__":
    raise SystemExit(main())
