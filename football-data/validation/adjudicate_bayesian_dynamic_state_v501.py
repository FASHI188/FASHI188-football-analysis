#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import adjudicate_bayesian_dynamic_state_v500 as legacy
from platform_core import atomic_write_json, load_json

ROOT = Path(__file__).resolve().parents[1]
AGGREGATE = ROOT / "manifests" / "bayesian_dynamic_state_oof_v501_status.json"
REPORT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v501"
OUT = ROOT / "manifests" / "bayesian_dynamic_state_adjudication_v501_status.json"
SHADOW = ROOT / "config" / "bayesian_dynamic_state_shadow_registry_v501.json"


def main() -> int:
    aggregate = load_json(AGGREGATE)
    if aggregate.get("same_day_outcomes_withheld") is not True:
        raise RuntimeError("aggregate is not same-day-safe")

    legacy.AGGREGATE = AGGREGATE
    legacy.REPORT_DIR = REPORT_DIR
    legacy.OUT = OUT
    legacy.SHADOW = SHADOW
    rc = legacy.main()

    adjudication = load_json(OUT)
    adjudication["schema_version"] = "V5.0.1-bayesian-dynamic-state-adjudication-r2"
    adjudication["same_day_outcomes_withheld"] = True
    adjudication["replaces_invalidated_v500_evidence"] = True
    adjudication["invalidation_receipt"] = "manifests/bayesian_dynamic_state_v500_invalidation_status.json"
    adjudication["formal_weight_change"] = False
    adjudication["probability_change"] = False
    adjudication["automatic_promotion"] = False
    atomic_write_json(OUT, adjudication)

    shadow = load_json(SHADOW)
    shadow["schema_version"] = "V5.0.1-bayesian-dynamic-state-shadow-registry-r3"
    shadow["same_day_outcomes_withheld"] = True
    shadow["replaces_invalidated_v500_evidence"] = True
    shadow["formal_weight"] = 0
    shadow["probability_mutation"] = False
    shadow["status"] = "SHADOW_ONLY_PENDING_SECOND_STAGE"
    atomic_write_json(SHADOW, shadow)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
