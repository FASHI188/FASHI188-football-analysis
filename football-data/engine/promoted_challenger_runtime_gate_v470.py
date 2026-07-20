#!/usr/bin/env python3
"""Fail-closed activation gate for promoted V4.7 runtime modules."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from platform_core import ROOT, load_json, sha256_file
from promoted_challenger_runtime_v470 import apply_promoted_v470_post_calibration_challengers

MODULE_PATH = Path(__file__).resolve()
RUNTIME_MODULE = ROOT / "engine" / "promoted_challenger_runtime_v470.py"
ACTIONABLE_RUNNER = ROOT / "engine" / "run_formal_prediction_actionable.py"
ACTIVATION_MANIFEST = ROOT / "manifests" / "promotions" / "USA_MLS_d_conditional_v470_runtime_activation.json"
PROMOTION_RECEIPT = ROOT / "manifests" / "promotions" / "USA_MLS_d_conditional_v470.json"


def _unavailable(calculation: dict[str, Any], reason: str) -> dict[str, Any]:
    output = copy.deepcopy(calculation)
    output.setdefault("module_states", {})["conditional_allocation_v470"] = "不可用"
    output["conditional_allocation_v470_audit"] = {
        "status": "不可用",
        "reason": reason,
        "method": "hash_bound_runtime_activation_gate",
    }
    return output


def apply_hash_bound_promoted_v470_challengers(
    context: dict[str, Any], calculation: dict[str, Any]
) -> dict[str, Any]:
    competition_id = str((context.get("match_identity") or {}).get("competition_id") or "")
    if competition_id != "USA_MLS":
        output = copy.deepcopy(calculation)
        output.setdefault("module_states", {})["conditional_allocation_v470"] = "未启用"
        output["conditional_allocation_v470_audit"] = {
            "status": "未启用",
            "reason": "no promoted D|T runtime activation for this competition",
            "method": "hash_bound_runtime_activation_gate",
        }
        return output

    if not ACTIVATION_MANIFEST.exists():
        return _unavailable(calculation, "runtime activation manifest missing")
    if not PROMOTION_RECEIPT.exists():
        return _unavailable(calculation, "promotion receipt missing")

    activation = load_json(ACTIVATION_MANIFEST)
    if activation.get("activation_status") != "ACTIVE":
        return _unavailable(calculation, f"runtime activation is not ACTIVE: {activation.get('activation_status')}")
    if activation.get("competition_id") != competition_id:
        return _unavailable(calculation, "runtime activation competition mismatch")

    expected = activation.get("bound_sha256") or {}
    actual = {
        "promotion_receipt": sha256_file(PROMOTION_RECEIPT),
        "runtime_module": sha256_file(RUNTIME_MODULE),
        "runtime_gate": sha256_file(MODULE_PATH),
        "actionable_runner": sha256_file(ACTIONABLE_RUNNER),
    }
    for key, value in actual.items():
        if str(expected.get(key) or "") != value:
            return _unavailable(calculation, f"runtime activation hash mismatch: {key}")

    return apply_promoted_v470_post_calibration_challengers(context, calculation)
