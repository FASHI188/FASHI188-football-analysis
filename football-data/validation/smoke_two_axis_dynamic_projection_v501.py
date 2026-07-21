#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import validate_two_axis_dynamic_projection_v501 as validation
from platform_core import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "two_axis_dynamic_projection_v501_smoke.json"


def main() -> int:
    baseline = [
        {"home_goals": 0, "away_goals": 0, "probability": 0.20},
        {"home_goals": 1, "away_goals": 0, "probability": 0.30},
        {"home_goals": 0, "away_goals": 1, "probability": 0.20},
        {"home_goals": 1, "away_goals": 1, "probability": 0.30},
    ]
    profile = validation.common._profile("slow_share")
    candidate, audit = validation._project(
        baseline,
        dynamic_home=1.6,
        dynamic_away=1.0,
        profile=profile,
        total_scale=0.5,
        share_scale=0.25,
    )
    probability_sum = sum(float(cell["probability"]) for cell in candidate)
    checks = {
        "configuration_count_25": len(validation.CONFIGS) == 25,
        "baseline_configuration_present": validation._config_id(0.0, 0.0) in {
            validation._config_id(total_scale, share_scale)
            for total_scale, share_scale in validation.CONFIGS
        },
        "probability_sum_one": abs(probability_sum - 1.0) <= 1e-12,
        "projection_audit_residual_small": abs(float(audit["probability_sum_residual"])) <= 1e-12,
        "two_axes_distinct": float(audit["effective_total_weight"]) != float(audit["effective_share_weight"]),
    }
    payload = {
        "schema_version": "V5.0.1-two-axis-dynamic-projection-smoke-r1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "probability_sum": probability_sum,
        "audit": audit,
        "formal_weight_change": False,
        "probability_change": False,
    }
    atomic_write_json(OUT, payload)
    print(payload)
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
