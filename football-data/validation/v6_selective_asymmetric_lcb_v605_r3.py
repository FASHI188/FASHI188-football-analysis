#!/usr/bin/env python3
"""Fail-closed launcher for V6.0.5.

Preserves all V6.0.5 policy parameters and gates. Any runtime failure, including an
empty validation-qualified execution pool, is persisted as an auditable failure receipt
instead of leaving no manifest.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import v6_selective_asymmetric_lcb_v605 as core
from platform_core import atomic_write_json

core.BOOTSTRAP_REPS = 1000


def main() -> int:
    try:
        return core.main()
    except Exception as exc:
        payload = {
            "schema_version": "V6.0.5-selective-asymmetric-lcb-r3-fail-closed",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "FAIL_RUNTIME",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "governance": {
                "research_challenge_only": True,
                "formal_weight_change": False,
                "runtime_probability_change": False,
                "current_rule_change": False,
                "automatic_promotion": False,
            },
        }
        atomic_write_json(core.OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
