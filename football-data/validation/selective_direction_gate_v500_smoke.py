#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import atomic_write_json, load_json, sha256_file  # noqa: E402
from selective_direction_gate_v500 import (  # noqa: E402
    PROMOTION,
    V500_STATUS,
    apply_selective_direction_gate,
    evaluate_gate,
)

OUT = ROOT / "manifests" / "selective_direction_gate_v500_smoke.json"
RUNTIME = ROOT / "engine" / "selective_direction_gate_v500.py"


def main() -> int:
    promotion = load_json(PROMOTION)
    context = {"match_identity": {"competition_id": "ESP_LaLiga", "season": "2026/27"}}

    strong = {"probabilities": {"1x2": {"home": 0.70, "draw": 0.20, "away": 0.10}}}
    strong_before = copy.deepcopy(strong)
    strong_audit = evaluate_gate(context, strong, promotion)

    weak = {"probabilities": {"1x2": {"home": 0.45, "draw": 0.30, "away": 0.25}}}
    weak_before = copy.deepcopy(weak)
    weak_audit = evaluate_gate(context, weak, promotion)

    staged = apply_selective_direction_gate(context, strong)
    staged_audit = staged.get("selective_direction_gate_v500_audit") or {}

    checks = {
        "strong_gap_allowed": strong_audit.get("status") == "通过" and strong_audit.get("formal_direction_allowed") is True,
        "weak_gap_abstains": weak_audit.get("status") == "弃权" and weak_audit.get("formal_direction_allowed") is False,
        "threshold_is_0_30": abs(float(strong_audit.get("threshold", -1)) - 0.30) < 1e-12,
        "strong_probabilities_unchanged": strong == strong_before,
        "weak_probabilities_unchanged": weak == weak_before,
        "staged_v5_does_not_activate_early": staged_audit.get("status") == "未启用",
        "staged_probability_mutation_false": staged_audit.get("probability_mutation") is False,
    }
    payload = {
        "schema_version": "V5.0.0-selective-direction-gate-smoke-r1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "runtime_sha256": sha256_file(RUNTIME),
        "promotion_sha256": sha256_file(PROMOTION),
        "v500_status_sha256": sha256_file(V500_STATUS),
        "strong_case": strong_audit,
        "weak_case": weak_audit,
        "staged_activation_case": staged_audit,
        "policy": "Smoke verifies gate semantics and fail-closed staging without activating V5 or changing probabilities."
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
