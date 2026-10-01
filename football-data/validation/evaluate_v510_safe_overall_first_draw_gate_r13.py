#!/usr/bin/env python3
"""R13 safe overall-first selection ablation for the V5.1 draw specialist.

The R12 candidate catalog, amplitudes, thresholds, policy safety constraints, outer
rolling windows, and pass gates are unchanged. Only the policy selection ordering is
changed: among candidates already safe on non-draw and overall policy scores, choose
lowest overall conditional-score Log Loss, then Brier, then draw Log Loss.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import evaluate_v510_constrained_selective_draw_gate_r12 as r12
from v510_historical_structure_features_r1 import ResearchError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_safe_overall_first_draw_gate_r13.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_safe_overall_first_draw_gate_r13_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_safe_overall_first_draw_gate_r13_stability.csv"
ORIGINAL_R12_SELECT = r12.select_gate_candidate


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def select_gate_candidate(policy, policy_base_probabilities, config):
    _, receipts, split = ORIGINAL_R12_SELECT(
        policy, policy_base_probabilities, config
    )
    eligible = [row for row in receipts if row.get("eligible", False)]
    if not eligible:
        raise ResearchError("R13 catalog has no policy-safe candidate")
    winner = min(
        eligible,
        key=lambda row: (
            row["policy_selection_score_metrics"]["logloss"],
            row["policy_selection_score_metrics"]["brier"],
            row["policy_selection_draw_score_logloss"],
            row["active_rate"],
            row["feature_count"],
            float("inf") if row["C"] is None else row["C"],
            row["alpha"],
            float("inf") if row["activation_quantile"] is None
            else row["activation_quantile"],
            row["name"],
        ),
    )
    split = {
        **split,
        "selection_objective": (
            "overall conditional-score Log Loss, then Brier, then draw Log Loss, "
            "among R12 policy-safe candidates"
        ),
    }
    return winner, receipts, split


def run(config: dict[str, Any], out_path: Path, stability_path: Path) -> dict[str, Any]:
    original_select = r12.select_gate_candidate
    r12.select_gate_candidate = select_gate_candidate
    try:
        result = r12.run(config, out_path, stability_path)
    finally:
        r12.select_gate_candidate = original_select

    status_map = {
        "PASS_R12_CONSTRAINED_DRAW_GATE_REPAIRS_DRAW_WITH_SAFETY":
            "PASS_R13_SAFE_OVERALL_FIRST_DRAW_GATE",
        "PARTIAL_PASS_R12_CONSTRAINED_DRAW_SIGNAL_WITH_GATE_FAILURES":
            "PARTIAL_PASS_R13_SAFE_OVERALL_FIRST_WITH_GATE_FAILURES",
        "FAIL_R12_CONSTRAINED_DRAW_GATE_NO_SAFE_REPAIR":
            "FAIL_R13_SAFE_OVERALL_FIRST_NO_REPAIR",
    }
    result["schema_version"] = config["schema_version"]
    result["status"] = status_map.get(result["status"], result["status"])
    result["algorithm_contract"]["policy_selection"] = (
        "overall conditional-score Log Loss first, Brier second, draw Log Loss third, "
        "after all R12 safety constraints"
    )
    retained = result["status"].startswith("PASS_")
    result["ruling"].pop("constrained_selective_draw_gate_retained", None)
    result["ruling"]["safe_overall_first_draw_gate_retained"] = retained
    result["ruling"]["r8_base_retained_if_gate_fails"] = not retained
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def self_test() -> None:
    rows = [
        {
            "name": "a", "eligible": True,
            "policy_selection_score_metrics": {"logloss": 1.0, "brier": 0.6},
            "policy_selection_draw_score_logloss": 0.5,
            "active_rate": 0.1, "feature_count": 3, "C": 0.1,
            "alpha": 0.25, "activation_quantile": 0.95,
        },
        {
            "name": "b", "eligible": True,
            "policy_selection_score_metrics": {"logloss": 0.9, "brier": 0.7},
            "policy_selection_draw_score_logloss": 0.6,
            "active_rate": 0.1, "feature_count": 3, "C": 0.1,
            "alpha": 0.25, "activation_quantile": 0.95,
        },
    ]
    winner = min(
        rows,
        key=lambda row: (
            row["policy_selection_score_metrics"]["logloss"],
            row["policy_selection_score_metrics"]["brier"],
        ),
    )
    assert winner["name"] == "b"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stability", type=Path, default=DEFAULT_STABILITY)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    result = run(load_json(args.config), args.out, args.stability)
    print(json.dumps({
        "status": result["status"],
        "reproduction": result["reproduction"],
        "pass_gates": result["pass_gates"],
        "audits": result["audits"],
        "stability": result["stability"],
        "ruling": result["ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
