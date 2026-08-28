#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
G1_DIR = ROOT / "football-data" / "experiments" / "r43g1_coverage_reliability_shrink"
if str(G1_DIR) not in sys.path:
    sys.path.insert(0, str(G1_DIR))
import run_r43g1 as g1  # noqa: E402

f5 = g1.f5

SOURCE_R43G1_RUN = 33154229040
SOURCE_R43G1_EXECUTION_HEAD = "2995202e71a5938f6d0afc7a1c56c90a90677e56"
SOURCE_R43G1_RUNNER_BLOB = "a6de1a6cff17a6101fc78bbca6cdb4d19568ef51"
SOURCE_R43G1_FORMULA = (
    "r_ctx=min(home_xi_certainty,away_xi_certainty,home_role_known_share,away_role_known_share); "
    "r_tech=min(r_ctx,context_weighted_known_share)"
)

# Both blocks are disjoint from the R43G1 architecture block [-40000:-20000].
# They were consumed by earlier football3 research, therefore formal_weight remains zero.
# No labels from either block are used to alter the already-frozen R43G1 formula or gate.
BLOCKS = [
    {
        "name": "preceding_20k_a",
        "skip_newest_n": 40000,
        "selection": "valid_sorted_[-60000:-40000]",
    },
    {
        "name": "preceding_20k_b",
        "skip_newest_n": 60000,
        "selection": "valid_sorted_[-80000:-60000]",
    },
]


def weighted_delta(results: list[dict], metric: str) -> float:
    den = sum(int(r["split"]["test_n"]) for r in results)
    if den <= 0:
        raise RuntimeError("empty replication test population")
    return sum(
        int(r["split"]["test_n"]) * float(r["test"]["candidate_minus_baseline"][metric])
        for r in results
    ) / den


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    old_skip = f5.SKIP_NEWEST_N
    old_selection = f5.SELECTION
    old_out = g1.OUT
    results = []
    try:
        for block in BLOCKS:
            f5.SKIP_NEWEST_N = int(block["skip_newest_n"])
            f5.SELECTION = str(block["selection"])
            block_out = OUT / block["name"]
            block_out.mkdir(parents=True, exist_ok=True)
            g1.OUT = block_out
            d = g1.run()
            # Defensive source identity checks: the actual dates must differ from the R43G1 source block.
            if d["governance"]["source_slice"] != block["selection"]:
                raise RuntimeError(f"source selection metadata drift for {block['name']}")
            results.append(d)
    finally:
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
    aggregate_proper_pass = (
        aggregate["delta_hits"] >= 0
        and aggregate["delta_logloss"] < 0
        and aggregate["delta_brier"] < 0
        and aggregate["delta_rps"] < 0
    )
    passed = bool(all_internal_pass and aggregate_proper_pass)

    out = {
        "schema_version": "football3-r43g2-coverage-reliability-multiblock-replication-v1",
        "status": "COMPLETE",
        "formal_weight": 0,
        "classification": "FROZEN_R43G1_FORMULA_DISJOINT_MULTIBLOCK_HISTORICAL_REPLICATION_PREVIOUSLY_CONSUMED",
        "question": "Does the fixed R43G1 coverage-reliability residual shrink replicate without retuning on two disjoint older 20k source blocks?",
        "governance": {
            "source_r43g1_run": SOURCE_R43G1_RUN,
            "source_r43g1_execution_head": SOURCE_R43G1_EXECUTION_HEAD,
            "source_r43g1_runner_blob": SOURCE_R43G1_RUNNER_BLOB,
            "formula_reused_exact": SOURCE_R43G1_FORMULA,
            "r43g1_source_slice": "valid_sorted_[-40000:-20000]",
            "replication_slices_disjoint_from_r43g1": True,
            "replication_slices_disjoint_from_each_other": True,
            "replication_blocks_previously_consumed_by_other_research": True,
            "parameter_search": False,
            "threshold_search": False,
            "feature_search": False,
            "formula_change": False,
            "gate_change": False,
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
                "KEEP_R43G1_FROZEN_FOR_TRUE_FORWARD_CONFIRMATION"
                if passed
                else "DO_NOT_PROMOTE_AND_DO_NOT_RETUNE_ON_R43G2_BLOCKS"
            ),
        },
        "limitations": [
            "Both replication blocks are disjoint from the R43G1 formula-development block but were seen in earlier football3 research, so this is not pristine confirmatory evidence.",
            "Formal promotion still requires true-forward or otherwise untouched evidence.",
            "The exact R43G1 formula and its internal gate are reused without any label-driven changes.",
        ],
    }
    p = OUT / "summary_r43g2_coverage_reliability_multiblock_replication.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "blocks": blocks, "aggregate": aggregate, "gate": out["gate"]}, indent=2))
    return out


if __name__ == "__main__":
    run()
