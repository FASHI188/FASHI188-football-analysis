#!/usr/bin/env python3
"""V5.5.37-r2 reduced global-only execution.

The full global/domain blend grid is unnecessary for the first viability decision and
is computationally excessive. This wrapper runs the identical cross-class balance
calibrator and untouched 4,786-match holdout protocol with blend fixed at 0.0. It does
not alter the statistical method, holdout, acceptance gates, CURRENT, or runtime.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import full_result_balance_kl_v5537 as base
from platform_core import atomic_write_json, load_json

OUT = ROOT / "manifests" / "full_result_balance_kl_v5537_r2_status.json"


def main() -> int:
    base.BLEND_GRID = (0.0,)
    base.OUT = OUT
    rc = base.main()
    payload = load_json(OUT)
    payload["schema_version"] = "V5.5.37-full-result-balance-kl-r2-global-only"
    payload["generated_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload["execution_reduction"] = {
        "statistical_method_changed": False,
        "holdout_changed": False,
        "acceptance_gate_changed": False,
        "global_domain_blend_fixed": 0.0,
        "reason": "first viability decision does not require 17 domain-specific refits",
        "supersedes_r1_for_global_viability": True,
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": payload.get("status"),
        "result_status": (payload.get("result") or {}).get("status"),
        "baseline_accuracy": ((payload.get("baseline") or {}).get("untouched_holdout") or {}).get("accuracy"),
        "calibrated_accuracy": (((payload.get("result") or {}).get("holdout") or {}).get("accuracy")),
        "gain_pp": (payload.get("result") or {}).get("accuracy_gain_pp"),
        "draw_predictions": (((payload.get("result") or {}).get("holdout") or {}).get("draw_prediction_count")),
        "challenge_gate_passed": (payload.get("result") or {}).get("challenge_gate_passed"),
    }, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
