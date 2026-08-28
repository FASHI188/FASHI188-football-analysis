#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_r43g3 as g3  # noqa: E402

OUT = HERE / "results"
RESULT = OUT / "r43g3_r1_forward_coverage_reliability_transport_lock.json"
PRELABEL_FAILURE_RUN_ID = 33157210337
PRELABEL_FAILURE_REASON = (
    "The fixed current-season bridge context exposes home/away role-known-share but does not expose "
    "R43G1 historical expected-XI certainty fields. The failed run stopped before writing any target lock "
    "and did not access target outcomes."
)


def fixed_xi_reliability(bridge_cf: dict, tech_cf: dict):
    required = ("home_role_known_share", "away_role_known_share")
    missing = [k for k in required if k not in bridge_cf]
    if missing:
        raise RuntimeError(f"R43G3-R1 missing fixed-XI reliability fields {missing}; keys={sorted(bridge_cf)}")
    vals = []
    for k in required:
        x = float(bridge_cf[k])
        if not np.isfinite(x):
            x = 0.0
        vals.append(min(1.0, max(0.0, x)))
    r_ctx = float(min(vals))
    tk = float(tech_cf.get("tech_known_share_min", 0.0))
    tk = min(1.0, max(0.0, tk)) if np.isfinite(tk) else 0.0
    r_tech = float(min(r_ctx, tk))
    return r_ctx, r_tech, {
        "home_role_known_share": float(bridge_cf["home_role_known_share"]),
        "away_role_known_share": float(bridge_cf["away_role_known_share"]),
        "tech_known_share_min": tk,
        "xi_certainty_available_in_fixed_bridge_schema": False,
    }


def run():
    # Compatibility adaptation is frozen here before any target outcome. No target label is read by g3.run().
    g3.reliability = fixed_xi_reliability
    d = g3.run()
    d["schema_version"] = "football3-r43g3-r1-forward-fixed-xi-coverage-reliability-transport-lock-v1"
    d["classification"] = "TRUE_FORWARD_PRELABEL_COMPATIBILITY_ADAPTED_FIXED_XI_COVERAGE_RELIABILITY_TRANSPORT_ON_R42L_MW2_TARGETS"
    d["question"] = "Does a prelabel fixed-XI-compatible transport of the frozen R43G1 uncertainty principle improve the existing R42L true-forward targets without tuning?"
    gov = d["governance"]
    gov.pop("r43g1_reliability_formula_reused_exact", None)
    gov.update({
        "prelabel_compatibility_failure_run_id": PRELABEL_FAILURE_RUN_ID,
        "prelabel_compatibility_failure_reason": PRELABEL_FAILURE_REASON,
        "r43g1_historical_exact_formula_runtime_compatible": False,
        "r43g1_principle_preserved": "coverage only scales residual magnitude toward lower-information parent and never selects outcome direction",
        "adapted_fixed_xi_reliability_formula": "r_ctx=min(home_role_known_share,away_role_known_share); r_tech=min(r_ctx,tech_known_share_min)",
        "adaptation_frozen_before_target_outcomes": True,
        "adaptation_uses_target_labels": False,
        "adaptation_parameter_search": False,
        "adaptation_threshold_search": False,
    })
    for x in d["targets"]:
        integ = x["integration"]
        integ["historical_R43G1_formula_changed"] = True
        integ["fixed_xi_compatibility_adaptation"] = "drop unavailable expected-XI-certainty terms; retain minimum role/technical coverage shrink only"
        integ["prelabel_failure_run_id"] = PRELABEL_FAILURE_RUN_ID
    d["limitations"] = [
        "Only three true-forward targets; this can falsify the transport but cannot establish statistical significance.",
        "R43G1 historical expected-XI certainty is not defined by the fixed current-season bridge schema. R43G3-R1 therefore uses the pre-registered fixed-XI-compatible role/technical coverage minimum; it is a transport test, not an exact formula replication.",
        "The adaptation was made after a schema-compatibility failure but before any target outcome and without parameter/threshold search.",
        "R42L remains immutable and is retained as the reference prediction.",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": d["status"], "classification": d["classification"], "locked_at_utc": d["locked_at_utc"],
        "targets": [{"target": x["target"], "reliability": x["reliability"], "candidate": x["probabilities"]["R43G3_forward_reliability_transport_candidate"]} for x in d["targets"]]
    }, indent=2, ensure_ascii=False))
    return d


def verify():
    d = json.loads(RESULT.read_text(encoding="utf-8"))
    assert d["status"] == "LOCKED_PREMATCH" and d["formal_weight"] == 0
    assert d["classification"] == "TRUE_FORWARD_PRELABEL_COMPATIBILITY_ADAPTED_FIXED_XI_COVERAGE_RELIABILITY_TRANSPORT_ON_R42L_MW2_TARGETS"
    g = d["governance"]
    assert g["prelabel_compatibility_failure_run_id"] == PRELABEL_FAILURE_RUN_ID
    assert g["adaptation_frozen_before_target_outcomes"] is True and g["adaptation_uses_target_labels"] is False
    assert g["adaptation_parameter_search"] is False and g["adaptation_threshold_search"] is False
    assert g["target_results_accessed"] is False and g["target_confirmed_xi_accessed"] is False and g["target_postmatch_data_accessed"] is False
    assert g["r42l_existing_lock_modified"] is False
    assert len(d["targets"]) == 3
    for x in d["targets"]:
        assert x["actual_result"] is None and x["actual_score"] is None and x["result_label_accessed"] is False
        r = x["reliability"]
        assert r["xi_certainty_available_in_fixed_bridge_schema"] is False
        assert 0 <= float(r["r_tech"]) <= float(r["r_ctx"]) <= 1
        p = x["probabilities"]["R43G3_forward_reliability_transport_candidate"]
        assert abs(sum(float(p[k]) for k in ("home", "draw", "away")) - 1.0) < 1e-10
        assert all(math.isfinite(float(p[k])) and float(p[k]) > 0 for k in ("home", "draw", "away"))
    print("R43G3-R1 forward lock contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(f"unknown command {cmd}")
