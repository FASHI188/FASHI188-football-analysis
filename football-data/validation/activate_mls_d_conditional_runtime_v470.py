#!/usr/bin/env python3
"""Build the hash-bound runtime activation manifest for promoted USA_MLS D|T."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, atomic_write_json, load_json, sha256_file

PROMOTION_RECEIPT = ROOT / "manifests" / "promotions" / "USA_MLS_d_conditional_v470.json"
RUNTIME_MODULE = ROOT / "engine" / "promoted_challenger_runtime_v470.py"
RUNTIME_GATE = ROOT / "engine" / "promoted_challenger_runtime_gate_v470.py"
ACTIONABLE_RUNNER = ROOT / "engine" / "run_formal_prediction_actionable.py"
OUT = ROOT / "manifests" / "promotions" / "USA_MLS_d_conditional_v470_runtime_activation.json"


def main() -> int:
    receipt = load_json(PROMOTION_RECEIPT)
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
    }
    active = all(checks.values())
    payload = {
        "schema_version": "V4.7.0-runtime-activation-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "activation_status": "ACTIVE" if active else "INACTIVE_FAIL_CLOSED",
        "competition_id": "USA_MLS",
        "target_season": "2026",
        "module": "conditional_allocation_v470",
        "activation_order": "post_oof_matrix_calibration",
        "formal_weight": 1.0 if active else 0.0,
        "checks": checks,
        "bound_sha256": {
            "promotion_receipt": sha256_file(PROMOTION_RECEIPT),
            "runtime_module": sha256_file(RUNTIME_MODULE),
            "runtime_gate": sha256_file(RUNTIME_GATE),
            "actionable_runner": sha256_file(ACTIONABLE_RUNNER),
        } if active else {},
        "policy": "Fail closed on any bound hash mismatch. Activation is USA_MLS 2026 only and does not alter any other competition or challenger.",
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if active else 2


if __name__ == "__main__":
    raise SystemExit(main())
