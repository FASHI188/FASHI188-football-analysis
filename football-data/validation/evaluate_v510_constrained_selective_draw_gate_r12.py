#!/usr/bin/env python3
"""R12 constrained selective draw gate for V5.1 historical development.

R11 came close to the frozen safety gate but still exceeded the non-draw Brier margin.
R12 keeps the learned, policy-separated mechanism and narrows it with lower blend
amplitudes, higher activation thresholds, a stricter policy safety buffer, and a
policy draw-first objective among candidates that do not worsen overall policy scores.
No exact-score multiplier or manual draw uplift is used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

import evaluate_v510_selective_draw_gate_r11 as r11
from v510_historical_structure_features_r1 import ResearchError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_constrained_selective_draw_gate_r12.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_constrained_selective_draw_gate_r12_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_constrained_selective_draw_gate_r12_stability.csv"
ORIGINAL_R11_SELECT = r11.select_gate_candidate


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def constrained_signal(mode: str, learned: np.ndarray, base: np.ndarray) -> np.ndarray:
    learned = np.asarray(learned, dtype=float)
    base = np.asarray(base, dtype=float)
    uplift = np.maximum(learned - base, 0.0)
    if mode == "positive_uplift":
        return uplift
    if mode == "learned_probability":
        return learned
    if mode == "uplift_times_probability":
        return uplift * learned
    if mode == "absolute_disagreement":
        return np.abs(learned - base)
    raise ResearchError(f"unknown R12 selection mode: {mode}")


def select_gate_candidate(
    policy,
    policy_base_probabilities: dict[int, np.ndarray],
    config: dict[str, Any],
):
    _, receipts, split = ORIGINAL_R11_SELECT(
        policy, policy_base_probabilities, config
    )
    identity = next(row for row in receipts if row["name"] == "identity")
    base_logloss = float(identity["policy_selection_score_metrics"]["logloss"])
    base_brier = float(identity["policy_selection_score_metrics"]["brier"])
    ll_margin = float(config["draw_gate_contract"]["policy_overall_logloss_margin"])
    brier_margin = float(config["draw_gate_contract"]["policy_overall_brier_margin"])
    for row in receipts:
        row["policy_selection_overall_logloss_delta"] = float(
            row["policy_selection_score_metrics"]["logloss"] - base_logloss
        )
        row["policy_selection_overall_brier_delta"] = float(
            row["policy_selection_score_metrics"]["brier"] - base_brier
        )
        row["non_draw_eligible"] = bool(row.get("eligible", False))
        row["overall_eligible"] = bool(
            row["policy_selection_overall_logloss_delta"] <= ll_margin
            and row["policy_selection_overall_brier_delta"] <= brier_margin
        )
        row["eligible"] = bool(row["non_draw_eligible"] and row["overall_eligible"])
    eligible = [row for row in receipts if row["eligible"]]
    if not eligible:
        raise ResearchError("R12 constrained catalog has no eligible candidate")
    winner = min(
        eligible,
        key=lambda row: (
            row["policy_selection_draw_score_logloss"],
            row["policy_selection_score_metrics"]["logloss"],
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
        "catalog_rows": len(receipts),
        "non_draw_eligible_candidates": int(
            sum(row["non_draw_eligible"] for row in receipts)
        ),
        "overall_and_non_draw_eligible_candidates": len(eligible),
        "selection_objective": "draw score Log Loss among policy-safe candidates",
    }
    return winner, receipts, split


def run(config: dict[str, Any], out_path: Path, stability_path: Path) -> dict[str, Any]:
    original_signal = r11.disagreement_signal
    original_select = r11.select_gate_candidate
    r11.disagreement_signal = constrained_signal
    r11.select_gate_candidate = select_gate_candidate
    try:
        result = r11.run(config, out_path, stability_path)
    finally:
        r11.disagreement_signal = original_signal
        r11.select_gate_candidate = original_select

    status_map = {
        "PASS_R11_SELECTIVE_DRAW_GATE_REPAIRS_DRAW_WITH_NON_DRAW_SAFETY":
            "PASS_R12_CONSTRAINED_DRAW_GATE_REPAIRS_DRAW_WITH_SAFETY",
        "PARTIAL_PASS_R11_SELECTIVE_DRAW_SIGNAL_WITH_GATE_FAILURES":
            "PARTIAL_PASS_R12_CONSTRAINED_DRAW_SIGNAL_WITH_GATE_FAILURES",
        "FAIL_R11_SELECTIVE_DRAW_GATE_NO_SAFE_REPAIR":
            "FAIL_R12_CONSTRAINED_DRAW_GATE_NO_SAFE_REPAIR",
    }
    result["schema_version"] = config["schema_version"]
    result["status"] = status_map.get(result["status"], result["status"])
    result["algorithm_contract"] = {
        "base": "R7 three-expert direct-total plus R4 shared Beta-Binomial H|T,X",
        "specialist": "binary logistic conditional draw mass for T=2,4,6",
        "activation": (
            "policy-fitted per-total high-quantile threshold on learned draw probability, "
            "positive uplift, or uplift-times-probability"
        ),
        "policy_safety": (
            "candidate must satisfy half-margin non-draw constraints and non-worsening "
            "overall policy Log Loss and Brier before draw-first selection"
        ),
        "inactive_rows": "exact R8 probabilities",
        "active_non_draw_rescaling": "proportional within the realised total support",
        "total_marginal_changed": False,
        "T0_changed": False,
        "odd_totals_changed": False,
        "manual_draw_or_exact_score_multiplier": False,
        "tail_exact_allocation": False,
    }
    retained = result["status"].startswith("PASS_")
    result["ruling"].pop("selective_draw_gate_retained", None)
    result["ruling"]["constrained_selective_draw_gate_retained"] = retained
    result["ruling"]["r8_base_retained_if_gate_fails"] = not retained
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def self_test() -> None:
    learned = np.asarray([0.2, 0.6, 0.5])
    base = np.asarray([0.3, 0.4, 0.5])
    assert np.allclose(constrained_signal("positive_uplift", learned, base), [0.0, 0.2, 0.0])
    assert np.allclose(constrained_signal("learned_probability", learned, base), learned)
    assert np.allclose(constrained_signal("uplift_times_probability", learned, base), [0.0, 0.12, 0.0])


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
