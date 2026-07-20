#!/usr/bin/env python3
"""Smoke audit for final V4.7 governance-version normalization."""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from formal_governance_runtime_v470 import apply_formal_governance_runtime
from platform_core import ROOT, atomic_write_json, load_json

OUT = ROOT / "manifests" / "formal_governance_runtime_v470_smoke.json"
GOVERNANCE = ROOT / "manifests" / "v470_upgrade_status.json"


def main() -> int:
    governance = load_json(GOVERNANCE)
    calculation = {
        "rule_version": "V4.6.1",
        "engine_version": "V4.6.x-validated-core",
        "probabilities": {
            "one_x_two": {"home": 0.4, "draw": 0.3, "away": 0.3},
            "total_goals": {"0": 0.05, "1": 0.15, "2": 0.25, "3": 0.25, "4": 0.15, "5": 0.08, "6": 0.04, "7+": 0.03},
        },
    }
    before = copy.deepcopy(calculation["probabilities"])
    output = apply_formal_governance_runtime(calculation)
    checks = {
        "formal_rule_version_matches_active_governance": output["rule_version"] == governance["formal_rule_version"],
        "underlying_implementation_version_preserved": output["implementation_rule_version"] == "V4.6.1",
        "probabilities_unchanged": output["probabilities"] == before,
        "audit_passed": output["formal_governance_audit"]["status"] == "通过",
        "audit_declares_no_mutation": output["formal_governance_audit"]["probability_mutation"] is False and output["formal_governance_audit"]["market_mutation"] is False and output["formal_governance_audit"]["price_mutation"] is False,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "schema_version": "V4.7.0-formal-governance-runtime-smoke-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "checks": checks,
        "runtime_audit": output["formal_governance_audit"],
        "reported_rule_version": output["rule_version"],
        "underlying_implementation_rule_version": output["implementation_rule_version"],
        "probability_change": False,
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
