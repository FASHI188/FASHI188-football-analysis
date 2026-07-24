#!/usr/bin/env python3
"""V6.18.3 r2 is invalidated before activation.

Reason: its training rows were built by the pre-V6.18.1c formal-row builder, which
could append final results from one match into the history used for another match on
the same calendar date even though only date-level timestamps were available.

This file intentionally fails closed. A new prospective freeze may only be created
from a PASS V6.18.1c strict-daily-PIT receipt (or a later stricter successor).
No formal probability/runtime/CURRENT was ever changed by V6.18.3 r2.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_total_shot_prospective_freeze_v6183r2_status.json"


def main() -> int:
    payload = {
        "schema_version": "V6.18.3-prospective-shot-total-freeze-r2-invalidated",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "FAIL_CLOSED_PIT_LEAK_SUPERSEDED",
        "formal_current_version": "V5.0.1",
        "activation_allowed": False,
        "reason": "training rows used same-date sequential formal history without verified kickoff timestamps",
        "required_successor": "PASS V6.18.1c strict-daily-PIT evidence followed by a fresh pre-forward freeze",
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "no_backfill": True,
            "fail_closed": True
        }
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
