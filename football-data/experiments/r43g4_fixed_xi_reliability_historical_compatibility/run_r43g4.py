#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
G1_DIR = ROOT / "football-data" / "experiments" / "r43g1_coverage_reliability_shrink"
if str(G1_DIR) not in sys.path:
    sys.path.insert(0, str(G1_DIR))
import run_r43g1 as g1  # noqa: E402

f5 = g1.f5

SOURCE_FORWARD_RUN = 33157684431
SOURCE_FORWARD_HEAD = "d450690a845b464297ef286e55f06be8879f3f32"
SOURCE_FORWARD_ADAPTATION_BLOB = "3b65b9c1c63d69ad9c346c20035626da8eeede05"
SOURCE_FORWARD_LOCKED_AT_UTC = "2026-08-28T09:03:12.031771+00:00"
FIXED_XI_FORMULA = (
    "r_ctx=min(home_role_known_share,away_role_known_share); "
    "r_tech=min(r_ctx,tech_known_share_min)"
)

# Pre-registered historical compatibility blocks. All are disjoint from the
# R43G1 architecture block valid_sorted_[-40000:-20000] and from each other.
# They were consumed by earlier football3 research, so formal_weight remains 0.
# The fixed-XI formula was frozen on the true-forward branch before target outcomes.
BLOCKS = [
    {"name": "older20k_a", "skip_newest_n": 40000, "selection": "valid_sorted_[-60000:-40000]"},
    {"name": "older20k_b", "skip_newest_n": 60000, "selection": "valid_sorted_[-80000:-60000]"},
    {"name": "older20k_c", "skip_newest_n": 80000, "selection": "valid_sorted_[-100000:-80000]"},
]


def fixed_xi_reliability(x: dict) -> tuple[float, float]:
    cf = x["base_cf"]
    vals = []
    for k in ("home_role_known_share", "away_role_known_share"):
        v = float(cf.get(k, 0.0))
        if not np.isfinite(v):
            v = 0.0
        vals.append(min(1.0, max(0.0, v)))
    r_ctx = float(min(vals))
    t = float(x.get("context_weighted_known", 0.0))
    t = min(1.0, max(0.0, t)) if np.isfinite(t) else 0.0
    r_tech = float(min(r_ctx, t))
    return r_ctx, r_tech


def weighted_delta(results: list[dict], metric: str) -> float:
    den = sum(int(r["split"]["test_n"]) for r in results)
    if den <= 0:
        raise RuntimeError("empty compatibility test population")
    return sum(
        int(r["split"]["test_n"]) * float(r["test"]["candidate_minus_baseline"][metric])
        for r in results
    ) / den


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    old_skip = f5.SKIP_NEWEST_N
    old_selection = f5.SELECTION
    old_out = g1.OUT
    old_reliability = g1.reliability
    results = []
    try:
        g1.reliability = fixed_xi_reliability
        for block in BLOCKS:
            f5.SKIP_NEWEST_N = int(block["skip_newest_n"])
            f5.SELECTION = str(block["selection"])
            block_out = OUT / block["name"]
            block_out.mkdir(parents=True, exist_ok=True)
            g1.OUT = block_out
            d = g1.run()
            if d["governance"]["source_slice"] != block["selection"]:
                raise RuntimeError(f"source selection metadata drift for {block['name']}")
            results.append(d)
    finally:
        g1.reliability = old_reliability
        f5.SKIP_NEWEST_N = old_skip
        f5.SELECTION = old_selection
        g1.OUT = old_out

    blocks = []
    for spec, d in zip(BLOCKS, results):
        blocks.append({
            "name": spec["name"],
            "selection": spec["selection"],
            "skip_newest_n": spec["skip_newest_n"],
            "first_date": d["source"]["first_date"],
            "last_date": d["source"]["last_date"],
            "train_n": d["split"]["train_n"],
            "validation_n": d["split"]["val_n"],
            "test_n": d["split"]["test_n"],
            "validation_delta": d["validation"]["candidate_minus_baseline"],
            "test_delta": d["test"]["candidate_minus_baseline"],
            "positive_logloss_blocks": d["test"]["positive_logloss_blocks"],
            "negative_logloss_blocks": d["test"]["negative_logloss_blocks"],
            "internal_gate_passed": bool(d["gate"]["passed"]),
        })

    aggregate = {
        "test_n": sum(int(d["split"]["test_n"]) for d in results),
        "delta_hits": sum(int(d["test"]["candidate_minus_baseline"]["hits"]) for d in results),
        "delta_logloss": weighted_delta(results, "logloss"),
        "delta_brier": weighted_delta(results, "brier"),
        "delta_rps": weighted_delta(results, "rps"),
        "delta_draw_binary_logloss": weighted_delta(results, "draw_binary_logloss"),
        "delta_draw_binary_brier": weighted_delta(results, "draw_binary_brier"),
        "positive_logloss_blocks": sum(int(d["test"]["positive_logloss_blocks"]) for d in results),
        "negative_logloss_blocks": sum(int(d["test"]["negative_logloss_blocks"]) for d in results),
    }
    all_internal_pass = all(bool(d["gate"]["passed"]) for d in results)
    aggregate_proper_pass = bool(
        aggregate["delta_hits"] >= 0
        and aggregate["delta_logloss"] < 0
        and aggregate["delta_brier"] < 0
        and aggregate["delta_rps"] < 0
    )
    passed = bool(all_internal_pass and aggregate_proper_pass)

    out = {
        "schema_version": "football3-r43g4-fixed-xi-reliability-historical-compatibility-v1",
        "status": "COMPLETE",
        "formal_weight": 0,
        "classification": "PRELABEL_FIXED_XI_COMPATIBILITY_HISTORICAL_TEST_ON_PREVIOUSLY_CONSUMED_BLOCKS",
        "question": "Does the already prelabel-frozen fixed-XI reliability adaptation preserve the historical benefit of coverage-scaled residual transport without retuning?",
        "governance": {
            "source_forward_run": SOURCE_FORWARD_RUN,
            "source_forward_head": SOURCE_FORWARD_HEAD,
            "source_forward_adaptation_blob": SOURCE_FORWARD_ADAPTATION_BLOB,
            "source_forward_locked_at_utc": SOURCE_FORWARD_LOCKED_AT_UTC,
            "formula_reused_exact_in_historical_schema": FIXED_XI_FORMULA,
            "historical_tech_known_share_mapping": "context_weighted_known == tech_known_share_min",
            "formula_frozen_before_true_forward_target_outcomes": True,
            "true_forward_target_results_accessed": False,
            "blocks_disjoint_from_r43g1": True,
            "blocks_disjoint_from_each_other": True,
            "blocks_previously_consumed_by_other_research": True,
            "parameter_search": False,
            "threshold_search": False,
            "feature_search": False,
            "formula_change": False,
            "gate_change": False,
            "tech_alpha_change": False,
            "no_manual_outcome_override": True,
            "no_manual_draw_override": True,
            "no_draw_threshold": True,
            "no_draw_class_weight": True,
            "r42l_lock_modified": False,
        },
        "blocks": blocks,
        "aggregate": aggregate,
        "gate": {
            "all_block_internal_gates_passed": all_internal_pass,
            "aggregate_proper_pass": aggregate_proper_pass,
            "passed": passed,
            "action": (
                "FIXED_XI_ADAPTATION_HISTORICALLY_COMPATIBLE_KEEP_FORWARD_LOCK_UNCHANGED"
                if passed
                else "FIXED_XI_ADAPTATION_NOT_HISTORICALLY_COMPATIBLE_KEEP_FORWARD_LOCK_ONLY_AS_FALSIFICATION_TEST"
            ),
        },
        "limitations": [
            "All historical blocks were previously consumed by other research; formal_weight is therefore zero.",
            "This test cannot promote the model. It only checks whether the prelabel fixed-XI adaptation is historically compatible before the three forward outcomes are known.",
            "The forward three-match lock remains append-only and is not rewritten by this experiment.",
        ],
    }
    p = OUT / "summary_r43g4_fixed_xi_reliability_historical_compatibility.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "blocks": blocks, "aggregate": aggregate, "gate": out["gate"]}, indent=2))
    return out


if __name__ == "__main__":
    run()
