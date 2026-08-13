#!/usr/bin/env python3
"""Fail closed if any R45B zero-label stage tries to auto-authorize OOS access."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT / "research" / "r45b_pit_role_availability_gate_status.json"
FORWARD = ROOT / "research" / "r45b_forward_capture_validation_status.json"


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def main() -> int:
    violations: list[str] = []
    first = load(FIRST)
    forward = load(FORWARD)

    ruling = first.get("ruling") if isinstance(first.get("ruling"), dict) else {}
    if ruling.get("independent_oos_authorized") is True:
        violations.append("first_gate_attempted_independent_oos_authorization")
    if first.get("independent_oos_authorized") is True:
        violations.append("first_gate_top_level_independent_oos_authorization")
    if forward.get("automatic_oos_authorization") is True:
        violations.append("forward_validator_attempted_automatic_oos_authorization")
    if forward.get("independent_oos_authorized") is True:
        violations.append("forward_validator_attempted_independent_oos_authorization")

    payload = {
        "schema_version": "R45B-OOS-GOVERNANCE-GUARD-R1",
        "status": "PASS_NO_AUTOMATIC_OOS_AUTHORIZATION" if not violations else "FAIL_AUTOMATIC_OOS_AUTHORIZATION_ATTEMPT",
        "violations": violations,
        "target_match_labels_read": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "independent_oos_authorized": False,
        "ruling": "Data readiness never grants OOS label/training/scoring access. A separate preregistration and explicit authorization are mandatory."
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
