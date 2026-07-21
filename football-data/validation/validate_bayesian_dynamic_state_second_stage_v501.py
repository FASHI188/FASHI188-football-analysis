#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import validate_bayesian_dynamic_state_second_stage_v500 as legacy
from bayesian_dynamic_state_oof_v501_same_day_safe import simulate_season_same_day_safe
from platform_core import atomic_write_json, load_json

ROOT = Path(__file__).resolve().parents[1]
ADJUDICATION = ROOT / "manifests" / "bayesian_dynamic_state_adjudication_v501_status.json"
FIRST_STAGE_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v501"
OUT = ROOT / "manifests" / "bayesian_dynamic_state_second_stage_v501_status.json"
REPORT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_second_stage_v501"


def main() -> int:
    adjudication = load_json(ADJUDICATION)
    if adjudication.get("same_day_outcomes_withheld") is not True:
        raise RuntimeError("adjudication is not same-day-safe")

    legacy.ADJUDICATION = ADJUDICATION
    legacy.FIRST_STAGE_DIR = FIRST_STAGE_DIR
    legacy.OUT = OUT
    legacy.REPORT_DIR = REPORT_DIR
    legacy._simulate_season = simulate_season_same_day_safe
    rc = legacy.main()

    aggregate = load_json(OUT)
    aggregate["schema_version"] = "V5.0.1-bayesian-dynamic-state-second-stage-aggregate-r2"
    aggregate["same_day_outcomes_withheld"] = True
    aggregate["replaces_invalidated_v500_evidence"] = True
    aggregate["invalidation_receipt"] = "manifests/bayesian_dynamic_state_v500_invalidation_status.json"
    aggregate["formal_weight_change"] = False
    aggregate["probability_change"] = False
    aggregate["automatic_promotion"] = False
    atomic_write_json(OUT, aggregate)

    for competition_id in aggregate.get("completed_domains") or []:
        path = REPORT_DIR / f"{competition_id}.json"
        report = load_json(path)
        report["schema_version"] = "V5.0.1-bayesian-dynamic-state-second-stage-domain-r2"
        report["same_day_outcomes_withheld"] = True
        report["replaces_invalidated_v500_evidence"] = True
        report["formal_weight"] = 0
        report["probability_change"] = False
        report["automatic_promotion"] = False
        atomic_write_json(path, report)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
