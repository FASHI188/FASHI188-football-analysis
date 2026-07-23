#!/usr/bin/env python3
"""Engineering self-test for the V6.8.2 multiline I-projection solver.

Uses a strictly synthetic full-support prior only to verify optimization mechanics against a
real frozen market ladder. It is NOT a football prediction or promotion result.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))
from v6_multiline_market_matrix_projection_v682 import project

LADDERS = ROOT / "evidence" / "market_ladders_v680" / "kambi_full_time_ladders.json"
OUT = ROOT / "manifests" / "v6_multiline_market_matrix_projection_v682_status.json"


def synthetic_prior():
    rows = []
    for h in range(11):
        for a in range(11):
            # full positive support, mild home asymmetry; mechanics only
            weight = math.exp(-0.72 * (h + a)) * (1.06 if h > a else 1.0)
            rows.append({"home_goals": h, "away_goals": a, "probability": weight})
    total = sum(row["probability"] for row in rows)
    for row in rows:
        row["probability"] /= total
    return rows


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not LADDERS.exists():
        payload = {"schema_version": "V6.8.2-multiline-market-matrix-selftest-r1", "generated_at_utc": now, "status": "WAITING_FOR_LADDERS"}
    else:
        source = json.loads(LADDERS.read_text(encoding="utf-8"))
        bundles = [b for b in source.get("bundles") or [] if len([x for x in b.get("total_goal_ladder") or [] if x.get("market_kind") == "total_goals"]) >= 2 and b.get("one_x_two_offers")]
        if not bundles:
            payload = {"schema_version": "V6.8.2-multiline-market-matrix-selftest-r1", "generated_at_utc": now, "status": "NO_ELIGIBLE_REAL_LADDER"}
        else:
            bundle = bundles[0]
            result = project(synthetic_prior(), bundle)
            ready = result.get("status") == "MULTILINE_MARKET_MATRIX_READY"
            payload = {
                "schema_version": "V6.8.2-multiline-market-matrix-selftest-r1",
                "generated_at_utc": now,
                "status": "PASS" if ready else "FAIL",
                "engineering_only": True,
                "real_ladder_event_id": bundle.get("event_id"),
                "real_ladder_observed_at_utc": bundle.get("observed_at_utc"),
                "solver_status": result.get("status"),
                "iterations": result.get("iterations"),
                "constraint_count": 1 + len(result.get("de_vigged_total_targets") or {}),
                "max_constraint_residual": result.get("max_constraint_residual"),
                "probability_sum_residual": result.get("probability_sum_residual"),
                "kl_from_prior": result.get("kl_from_prior"),
                "market_accuracy_claim": False,
                "promotion_claim": False,
                "governance": {"research_only": True, "current_rule_change": False, "formal_probability_change": False}
            }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") in {"PASS", "WAITING_FOR_LADDERS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
