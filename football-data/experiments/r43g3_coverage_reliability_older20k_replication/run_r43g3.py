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
SKIP_NEWEST_N = 80000
SELECTION = "valid_sorted_[-100000:-80000]"


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    old_skip = f5.SKIP_NEWEST_N
    old_selection = f5.SELECTION
    old_out = g1.OUT
    try:
        f5.SKIP_NEWEST_N = SKIP_NEWEST_N
        f5.SELECTION = SELECTION
        g1.OUT = OUT / "inner"
        d = g1.run()
    finally:
        f5.SKIP_NEWEST_N = old_skip
        f5.SELECTION = old_selection
        g1.OUT = old_out

    if d["governance"]["source_slice"] != SELECTION:
        raise RuntimeError("source selection metadata drift")

    out = {
        "schema_version": "football3-r43g3-coverage-reliability-older20k-replication-v1",
        "status": "COMPLETE",
        "formal_weight": 0,
        "classification": "FROZEN_R43G1_FORMULA_OLDER20K_HISTORICAL_REPLICATION_PREVIOUSLY_CONSUMED",
        "question": "Does the exact frozen R43G1 coverage-reliability residual shrink remain beneficial on the next older disjoint 20k block without retuning?",
        "governance": {
            "source_r43g1_run": SOURCE_R43G1_RUN,
            "source_r43g1_execution_head": SOURCE_R43G1_EXECUTION_HEAD,
            "source_r43g1_runner_blob": SOURCE_R43G1_RUNNER_BLOB,
            "formula_reused_exact": SOURCE_R43G1_FORMULA,
            "selection": SELECTION,
            "skip_newest_n": SKIP_NEWEST_N,
            "disjoint_from_r43g1_and_r43g2": True,
            "block_previously_consumed_by_other_research": True,
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
        "source": d["source"],
        "split": d["split"],
        "validation_delta": d["validation"]["candidate_minus_baseline"],
        "test_delta": d["test"]["candidate_minus_baseline"],
        "positive_logloss_blocks": d["test"]["positive_logloss_blocks"],
        "negative_logloss_blocks": d["test"]["negative_logloss_blocks"],
        "internal_gate_passed": bool(d["gate"]["passed"]),
        "gate": {
            "passed": bool(d["gate"]["passed"]),
            "action": "KEEP_FROZEN_FOR_TRUE_FORWARD_CONFIRMATION" if d["gate"]["passed"] else "DO_NOT_PROMOTE_AND_DO_NOT_RETUNE_ON_R43G3",
        },
        "limitations": [
            "This block is disjoint from R43G1/R43G2 but was consumed in earlier football3 research, so formal_weight remains zero.",
            "The R43G1 formula and gate are reused exactly with no label-driven change.",
            "Formal promotion still requires pristine or true-forward evidence.",
        ],
    }
    p = OUT / "summary_r43g3_coverage_reliability_older20k_replication.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "validation_delta": out["validation_delta"], "test_delta": out["test_delta"], "gate": out["gate"]}, indent=2))
    return out


if __name__ == "__main__":
    run()
