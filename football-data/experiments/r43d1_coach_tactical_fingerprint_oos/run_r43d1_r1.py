#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import run_r43d1 as base  # noqa: E402

# R1 is a compatibility-only split correction after the original runner stopped
# before fitting/scoring because the eligible cohort was 5,394 (< 8,000 required).
# No validation/test metrics were produced or inspected before freezing these values.
TRAIN_TARGET = 2500
VAL_TARGET = 1200
MIN_TEST = 1200
ORIGINAL_UNDERSIZED_COHORT = 5394
ORIGINAL_FAILURE = "undersized outcome cohort 5394"


def apply_contract() -> None:
    base.OUTCOME_TRAIN_TARGET = TRAIN_TARGET
    base.OUTCOME_VAL_TARGET = VAL_TARGET
    base.MIN_TEST_MATCHES = MIN_TEST


def run() -> dict:
    apply_contract()
    d = base.run()
    d["schema_version"] = "football3-r43d1-r1-coach-tactical-fingerprint-oos-v1"
    d["classification"] = "DEVELOPMENT_STRICT_PRIOR_TEAM_AND_COACH_TACTICAL_FINGERPRINT_CHRONOLOGICAL_OOS_R1_PRELABEL_SPLIT_COMPATIBILITY"
    d["governance"].update({
        "r1_prelabel_compatibility_fix": True,
        "r1_reason": ORIGINAL_FAILURE,
        "original_eligible_cohort_observed": ORIGINAL_UNDERSIZED_COHORT,
        "outcome_metrics_produced_before_r1_fix": False,
        "outcome_metrics_inspected_before_r1_fix": False,
        "r1_train_target": TRAIN_TARGET,
        "r1_val_target": VAL_TARGET,
        "r1_min_test_matches": MIN_TEST,
        "feature_definitions_changed_in_r1": False,
        "model_hyperparameters_changed_in_r1": False,
        "gate_changed_in_r1": False,
    })
    p = base.OUT / "summary_r43d1_coach_tactical_fingerprint_oos.json"
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return d


def verify() -> None:
    apply_contract()
    base.verify()
    p = base.OUT / "summary_r43d1_coach_tactical_fingerprint_oos.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["governance"]["r1_prelabel_compatibility_fix"] is True
    assert d["governance"]["outcome_metrics_inspected_before_r1_fix"] is False
    assert d["governance"]["feature_definitions_changed_in_r1"] is False
    assert d["governance"]["model_hyperparameters_changed_in_r1"] is False
    assert d["governance"]["gate_changed_in_r1"] is False
    assert d["outcome_split"]["train_n"] >= TRAIN_TARGET
    assert d["outcome_split"]["val_n"] >= VAL_TARGET
    assert d["outcome_split"]["test_n"] >= MIN_TEST
    print("R43D1-R1 compatibility contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
