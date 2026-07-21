#!/usr/bin/env python3
"""Run one competition for the V5 Bayesian dynamic-state OOF screen."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import bayesian_dynamic_state_oof_v500 as base
from platform_core import atomic_write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    args = parser.parse_args()
    competition_id = str(args.competition).strip()
    output = base.REPORT_DIR / f"{competition_id}.json"
    try:
        report = base._validate_domain(competition_id)
    except Exception as exc:
        report = {
            "schema_version": "V5.0.0-bayesian-dynamic-state-oof-domain-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "competition_id": competition_id,
            "status": "FAILED",
            "formal_weight": 0,
            "automatic_promotion": False,
            "probability_change": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output, report)
    print(json.dumps({
        "competition_id": competition_id,
        "status": report.get("status"),
        "outer_prediction_count": report.get("outer_prediction_count"),
        "reason": report.get("reason"),
    }, ensure_ascii=False, indent=2))
    # Always persist a domain receipt. The aggregate gate decides overall success.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
