#!/usr/bin/env python3
"""R11 selective learned draw gate for the retained V5.1 R8 chain.

R10 proved that a learned even-total draw specialist repairs 1-1 and 2-2 but harms
non-draw outcomes when applied everywhere. R11 keeps the same frozen specialist catalog
and activates it only when a policy-fitted disagreement signal exceeds a policy-fitted
threshold. Inactive rows remain exactly equal to R8. Historical development only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import evaluate_v510_learned_draw_gate_r10 as r10
from v510_historical_structure_features_r1 import ResearchError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_selective_draw_gate_r11.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_selective_draw_gate_r11_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_selective_draw_gate_r11_stability.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def disagreement_signal(mode: str, learned: np.ndarray, base: np.ndarray) -> np.ndarray:
    learned = np.asarray(learned, dtype=float)
    base = np.asarray(base, dtype=float)
    if mode == "positive_uplift":
        return np.maximum(learned - base, 0.0)
    if mode == "absolute_disagreement":
        return np.abs(learned - base)
    raise ResearchError(f"unknown selective draw mode: {mode}")


def fit_thresholds(
    even_frame: pd.DataFrame,
    design: pd.DataFrame,
    model: Any,
    features: list[str],
    mode: str,
    quantile: float,
    config: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    eligible = [int(value) for value in config["draw_gate_contract"]["eligible_totals"]]
    thresholds: dict[str, float] = {}
    receipt: dict[str, Any] = {}
    for total in eligible:
        rows = even_frame[even_frame.total_goals_exact == total]
        if rows.empty:
            raise ResearchError(f"no threshold-fit rows for T={total}")
        learned = r10.predict_positive(model, design.loc[rows.index], features)
        base = design.loc[rows.index, "base_draw_probability"].to_numpy(float)
        signal = disagreement_signal(mode, learned, base)
        threshold = float(np.quantile(signal, quantile))
        thresholds[str(total)] = threshold
        receipt[str(total)] = {
            "rows": len(rows),
            "threshold": threshold,
            "signal_mean": float(signal.mean()),
            "signal_max": float(signal.max()),
            "positive_signal_rate": float((signal > 0).mean()),
        }
    return thresholds, receipt


def actual_activation(
    frame: pd.DataFrame,
    design: pd.DataFrame,
    wrapper: dict[str, Any],
    features: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    eligible = [int(value) for value in config["draw_gate_contract"]["eligible_totals"]]
    active = pd.Series(False, index=frame.index)
    per_total: dict[str, Any] = {}
    for total in eligible:
        rows = frame[frame.total_goals_exact == total]
        if rows.empty:
            per_total[str(total)] = {"rows": 0, "active_rows": 0, "active_rate": 0.0}
            continue
        learned = r10.predict_positive(wrapper["model"], design.loc[rows.index], features)
        base = design.loc[rows.index, "base_draw_probability"].to_numpy(float)
        signal = disagreement_signal(wrapper["mode"], learned, base)
        mask = signal > float(wrapper["thresholds"][str(total)])
        active.loc[rows.index] = mask
        per_total[str(total)] = {
            "rows": len(rows),
            "active_rows": int(mask.sum()),
            "active_rate": float(mask.mean()),
            "mean_base_draw_probability": float(base.mean()),
            "mean_learned_draw_probability": float(learned.mean()),
        }
    eligible_rows = frame.total_goals_exact.isin(eligible)
    denominator = int(eligible_rows.sum())
    return {
        "active_mask": active,
        "eligible_rows": denominator,
        "active_rows": int(active.sum()),
        "active_rate": float(active.sum() / denominator) if denominator else 0.0,
        "per_total": per_total,
    }


def apply_gate_all_totals(
    frame: pd.DataFrame,
    base_probabilities: dict[int, np.ndarray],
    model_wrapper: dict[str, Any] | None,
    features: list[str],
    alpha: float,
    config: dict[str, Any],
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    output = {
        total: np.asarray(probability, dtype=float).copy()
        for total, probability in base_probabilities.items()
    }
    if model_wrapper is None:
        return output, {
            "identity": True,
            "eligible_rows": 0,
            "active_rows": 0,
            "active_rate": 0.0,
            "probability_sum_max_residual": 0.0,
        }
    minimum = float(config["draw_gate_contract"]["minimum_probability"])
    eligible = [int(value) for value in config["draw_gate_contract"]["eligible_totals"]]
    maximum = 0.0
    total_active = 0
    total_eligible = 0
    per_total: dict[str, Any] = {}
    for total in eligible:
        base_matrix = np.asarray(base_probabilities[total], dtype=float)
        design = r10.gate_design_counterfactual(frame, total, base_matrix, minimum)
        learned = r10.predict_positive(model_wrapper["model"], design, features)
        base_draw = base_matrix[:, total // 2]
        signal = disagreement_signal(model_wrapper["mode"], learned, base_draw)
        threshold = float(model_wrapper["thresholds"][str(total)])
        active = signal > threshold
        target = np.where(active, learned, base_draw)
        output[total] = r10.adjust_draw_matrix(
            base_matrix, total, target, alpha, minimum
        )
        residual = float(np.max(np.abs(output[total].sum(axis=1) - 1.0)))
        maximum = max(maximum, residual)
        total_active += int(active.sum())
        total_eligible += len(active)
        per_total[str(total)] = {
            "threshold": threshold,
            "rows": len(active),
            "active_rows": int(active.sum()),
            "active_rate": float(active.mean()),
            "mean_base_draw_probability": float(base_draw.mean()),
            "mean_learned_draw_probability": float(learned.mean()),
            "mean_final_draw_probability": float(output[total][:, total // 2].mean()),
            "probability_sum_max_residual": residual,
        }
    return output, {
        "identity": False,
        "mode": model_wrapper["mode"],
        "activation_quantile": model_wrapper["activation_quantile"],
        "alpha": alpha,
        "thresholds": model_wrapper["thresholds"],
        "threshold_receipt": model_wrapper.get("threshold_receipt"),
        "eligible_rows": total_eligible,
        "active_rows": total_active,
        "active_rate": float(total_active / total_eligible) if total_eligible else 0.0,
        "per_total": per_total,
        "probability_sum_max_residual": maximum,
    }


def select_gate_candidate(
    policy: pd.DataFrame,
    policy_base_probabilities: dict[int, np.ndarray],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    fraction = float(config["split_contract"]["policy_gate_fit_fraction_by_date"])
    gate_fit, gate_select, split_receipt = r10.policy_date_split(policy, fraction)
    minimum = float(config["draw_gate_contract"]["minimum_probability"])
    eligible_totals = {
        int(value) for value in config["draw_gate_contract"]["eligible_totals"]
    }
    full_design = r10.gate_design_actual(policy, policy_base_probabilities, minimum)
    feature_sets = r10.gate_feature_sets(config, full_design)
    position = {int(index): pos for pos, index in enumerate(policy.index)}

    fit_even = gate_fit[gate_fit.total_goals_exact.isin(eligible_totals)]
    select_even = gate_select[gate_select.total_goals_exact.isin(eligible_totals)]
    select_core = gate_select[gate_select.total_goals_exact <= 6].copy()
    if min(len(fit_even), len(select_even), len(select_core)) <= 0:
        raise ResearchError("empty selective draw-gate policy segment")
    target_fit = (fit_even.home_goals_exact == fit_even.away_goals_exact).astype(int)
    if target_fit.nunique() != 2:
        raise ResearchError("selective gate fit lacks draw and non-draw classes")

    select_positions = [position[int(index)] for index in select_core.index]
    select_base = {
        total: probability[select_positions]
        for total, probability in policy_base_probabilities.items()
    }
    base_realised = r10.realised_probability_list(select_core, select_base)
    base_score = r10.score_components(select_core, base_realised)
    base_result = r10.result_components(select_core, base_realised)
    non_draw_mask = (
        select_core.home_goals_exact.to_numpy(int)
        != select_core.away_goals_exact.to_numpy(int)
    )
    draw_mask = ~non_draw_mask
    receipts: list[dict[str, Any]] = [{
        "name": "identity",
        "feature_set": "identity",
        "feature_count": 0,
        "C": None,
        "alpha": 0.0,
        "mode": "identity",
        "activation_quantile": None,
        "thresholds": {},
        "active_rate": 0.0,
        "eligible": True,
        "policy_selection_score_metrics": r10.metric_summary(base_score),
        "policy_selection_result_metrics": r10.metric_summary(base_result),
        "policy_selection_draw_score_logloss": float(base_score.loc[draw_mask, "logloss"].mean()),
        "policy_selection_non_draw_logloss_delta": 0.0,
        "policy_selection_non_draw_brier_delta": 0.0,
    }]

    ll_margin = float(config["draw_gate_contract"]["policy_non_draw_logloss_margin"])
    brier_margin = float(config["draw_gate_contract"]["policy_non_draw_brier_margin"])
    for feature_set_name, features in feature_sets.items():
        for C in config["draw_gate_contract"]["regularization_C_grid"]:
            model = r10.make_model(float(C), config)
            model.fit(full_design.loc[fit_even.index, features], target_fit)
            for mode in config["draw_gate_contract"]["selection_modes"]:
                for quantile in config["draw_gate_contract"]["activation_quantile_grid"]:
                    thresholds, threshold_receipt = fit_thresholds(
                        fit_even,
                        full_design,
                        model,
                        features,
                        str(mode),
                        float(quantile),
                        config,
                    )
                    wrapper = {
                        "model": model,
                        "mode": str(mode),
                        "activation_quantile": float(quantile),
                        "thresholds": thresholds,
                        "threshold_receipt": threshold_receipt,
                    }
                    activation = actual_activation(
                        select_core,
                        full_design.loc[select_core.index],
                        wrapper,
                        features,
                        config,
                    )
                    for alpha in config["draw_gate_contract"]["learned_blend_alpha_grid"]:
                        candidate_realised = r10.apply_candidate_to_realised(
                            select_core,
                            select_base,
                            wrapper,
                            features,
                            float(alpha),
                            config,
                        )
                        candidate_score = r10.score_components(
                            select_core, candidate_realised
                        )
                        candidate_result = r10.result_components(
                            select_core, candidate_realised
                        )
                        non_draw_ll_delta = float(
                            candidate_score.loc[non_draw_mask, "logloss"].mean()
                            - base_score.loc[non_draw_mask, "logloss"].mean()
                        )
                        non_draw_brier_delta = float(
                            candidate_score.loc[non_draw_mask, "brier"].mean()
                            - base_score.loc[non_draw_mask, "brier"].mean()
                        )
                        eligible = (
                            non_draw_ll_delta <= ll_margin
                            and non_draw_brier_delta <= brier_margin
                        )
                        receipts.append({
                            "name": (
                                f"{feature_set_name}_C{float(C):g}_A{float(alpha):g}_"
                                f"{mode}_Q{float(quantile):g}"
                            ),
                            "feature_set": feature_set_name,
                            "features": features,
                            "feature_count": len(features),
                            "C": float(C),
                            "alpha": float(alpha),
                            "mode": str(mode),
                            "activation_quantile": float(quantile),
                            "thresholds": thresholds,
                            "threshold_receipt": threshold_receipt,
                            "active_rate": activation["active_rate"],
                            "active_rows": activation["active_rows"],
                            "eligible_rows": activation["eligible_rows"],
                            "actual_activation": {
                                key: value for key, value in activation.items()
                                if key != "active_mask"
                            },
                            "eligible": eligible,
                            "policy_selection_score_metrics": r10.metric_summary(candidate_score),
                            "policy_selection_result_metrics": r10.metric_summary(candidate_result),
                            "policy_selection_draw_score_logloss": float(
                                candidate_score.loc[draw_mask, "logloss"].mean()
                            ),
                            "policy_selection_non_draw_logloss_delta": non_draw_ll_delta,
                            "policy_selection_non_draw_brier_delta": non_draw_brier_delta,
                            "max_solver_iterations": int(
                                np.max(model.named_steps["model"].n_iter_)
                            ),
                        })

    eligible_receipts = [row for row in receipts if row["eligible"]]
    if not eligible_receipts:
        raise ResearchError("selective draw catalog has no policy-eligible candidate")
    winner = min(
        eligible_receipts,
        key=lambda row: (
            row["policy_selection_score_metrics"]["logloss"],
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
    return winner, receipts, {
        **split_receipt,
        "gate_fit_rows": len(gate_fit),
        "gate_selection_rows": len(gate_select),
        "gate_selection_core_rows": len(select_core),
        "gate_fit_even_rows": len(fit_even),
        "gate_selection_even_rows": len(select_even),
        "gate_fit_draw_rate": float(target_fit.mean()),
        "catalog_rows": len(receipts),
        "policy_eligible_candidates": len(eligible_receipts),
    }


def fit_final_gate(
    policy: pd.DataFrame,
    policy_base_probabilities: dict[int, np.ndarray],
    selected: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str], float, dict[str, Any]]:
    if selected["feature_set"] == "identity":
        return None, [], 0.0, {"identity": True}
    minimum = float(config["draw_gate_contract"]["minimum_probability"])
    eligible = {
        int(value) for value in config["draw_gate_contract"]["eligible_totals"]
    }
    design = r10.gate_design_actual(policy, policy_base_probabilities, minimum)
    features = [str(value) for value in selected["features"]]
    even = policy[policy.total_goals_exact.isin(eligible)]
    target = (even.home_goals_exact == even.away_goals_exact).astype(int)
    if target.nunique() != 2:
        raise ResearchError("full policy selective gate lacks both classes")
    model = r10.make_model(float(selected["C"]), config)
    model.fit(design.loc[even.index, features], target)
    thresholds, threshold_receipt = fit_thresholds(
        even,
        design,
        model,
        features,
        str(selected["mode"]),
        float(selected["activation_quantile"]),
        config,
    )
    wrapper = {
        "model": model,
        "mode": str(selected["mode"]),
        "activation_quantile": float(selected["activation_quantile"]),
        "thresholds": thresholds,
        "threshold_receipt": threshold_receipt,
    }
    activation = actual_activation(policy, design, wrapper, features, config)
    return wrapper, features, float(selected["alpha"]), {
        "identity": False,
        "rows": len(even),
        "draw_rate": float(target.mean()),
        "mode": wrapper["mode"],
        "activation_quantile": wrapper["activation_quantile"],
        "thresholds": thresholds,
        "threshold_receipt": threshold_receipt,
        "policy_activation": {
            key: value for key, value in activation.items() if key != "active_mask"
        },
        "max_solver_iterations": int(np.max(model.named_steps["model"].n_iter_)),
    }


def run(config: dict[str, Any], out_path: Path, stability_path: Path) -> dict[str, Any]:
    # The retained R10 outer evaluation is reused unchanged; only its three gate hooks
    # are replaced by the frozen R11 selective policy.
    original_select = r10.select_gate_candidate
    original_fit = r10.fit_final_gate
    original_apply = r10.apply_gate_all_totals
    r10.select_gate_candidate = select_gate_candidate
    r10.fit_final_gate = fit_final_gate
    r10.apply_gate_all_totals = apply_gate_all_totals
    try:
        result = r10.run(config, out_path, stability_path)
    finally:
        r10.select_gate_candidate = original_select
        r10.fit_final_gate = original_fit
        r10.apply_gate_all_totals = original_apply

    status_map = {
        "PASS_R10_LEARNED_DRAW_GATE_REPAIRS_DRAW_DOMAIN":
            "PASS_R11_SELECTIVE_DRAW_GATE_REPAIRS_DRAW_WITH_NON_DRAW_SAFETY",
        "PARTIAL_PASS_R10_DRAW_SIGNAL_WITH_GATE_FAILURES":
            "PARTIAL_PASS_R11_SELECTIVE_DRAW_SIGNAL_WITH_GATE_FAILURES",
        "FAIL_R10_LEARNED_DRAW_GATE_NO_SAFE_REPAIR":
            "FAIL_R11_SELECTIVE_DRAW_GATE_NO_SAFE_REPAIR",
    }
    result["schema_version"] = config["schema_version"]
    result["status"] = status_map.get(result["status"], result["status"])
    result["algorithm_contract"] = {
        "base": "R7 three-expert direct-total plus R4 shared Beta-Binomial H|T,X",
        "specialist": "binary logistic conditional draw mass for T=2,4,6",
        "activation": (
            "policy-fitted per-total threshold on either positive draw uplift or "
            "absolute specialist/base disagreement"
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
    result["ruling"].pop("learned_draw_gate_retained", None)
    result["ruling"]["selective_draw_gate_retained"] = retained
    result["ruling"]["r8_base_retained_if_gate_fails"] = not retained
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def self_test() -> None:
    learned = np.asarray([0.2, 0.6, 0.5])
    base = np.asarray([0.3, 0.4, 0.5])
    assert np.allclose(disagreement_signal("positive_uplift", learned, base), [0.0, 0.2, 0.0])
    assert np.allclose(disagreement_signal("absolute_disagreement", learned, base), [0.1, 0.2, 0.0])
    matrix = np.asarray([[0.2, 0.6, 0.2], [0.3, 0.4, 0.3]], dtype=float)
    target = np.asarray([0.6, 0.7])
    adjusted = r10.adjust_draw_matrix(matrix, 2, target, 0.5, 1e-6)
    assert np.max(np.abs(adjusted.sum(axis=1) - 1.0)) < 1e-12


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
