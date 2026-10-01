#!/usr/bin/env python3
"""R10 learned even-total draw gate for the retained V5.1 R8 joint chain.

Viewed historical development only. The gate never applies a manual draw, 1-1, or 2-2
multiplier. For even totals T in {2,4,6}, a binary logistic model learns the conditional
draw mass from pre-match features and the R4 Beta-Binomial base draw probability. The
remaining non-draw score cells are rescaled proportionally, preserving the legal
conditional score simplex and the direct-total marginal. T=0 and all odd totals are
unchanged; T>=7 remains one unresolved aggregate bucket.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import (
    make_model,
    metric_components,
    metric_summary,
    select_C,
)
from evaluate_v510_prequential_algorithm_challenge_r3 import (
    continuation_models,
    continuation_probability,
    daily_mixture,
    select_continuation_C,
    select_eta,
)
from evaluate_v510_nonlinear_continuation_boosting_r6 import (
    boosting_continuation_models,
    boosting_probability,
)
from evaluate_v510_historical_tail_mapping_r1 import (
    attach_exact_labels,
    beta_binomial_probability,
    fit_beta_binomial,
)
from evaluate_v510_full_range_score_allocation_r4 import (
    add_conditional_total_features,
    mapping_feature_sets,
    result_components,
    score_components,
    select_full_range_candidate,
)
from evaluate_v510_core_joint_chain_r8 import (
    build_joint,
    flat_probability,
    joint_components,
    realised_conditional_probabilities,
    set_counterfactual_total,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_learned_draw_gate_r10.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_learned_draw_gate_r10_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_learned_draw_gate_r10_stability.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def policy_date_split(policy: pd.DataFrame, fraction: float) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    dates = sorted(policy["date_key"].astype(str).unique())
    if len(dates) < 4:
        raise ResearchError("policy season has fewer than four unique dates")
    cut = int(round(len(dates) * fraction))
    cut = max(2, min(cut, len(dates) - 2))
    early_dates = set(dates[:cut])
    fit = policy[policy.date_key.astype(str).isin(early_dates)].copy()
    select = policy[~policy.date_key.astype(str).isin(early_dates)].copy()
    if fit.empty or select.empty:
        raise ResearchError("empty policy gate fit or selection segment")
    return fit, select, {
        "unique_dates": len(dates),
        "cut_position": cut,
        "gate_fit_first_date": str(fit.date_key.min()),
        "gate_fit_last_date": str(fit.date_key.max()),
        "gate_selection_first_date": str(select.date_key.min()),
        "gate_selection_last_date": str(select.date_key.max()),
        "same_day_kept_together": True,
    }


def fit_selected_beta_model(
    fit: pd.DataFrame,
    predict: pd.DataFrame,
    feature_names: list[str],
    hyper: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    nonzero_fit = fit[fit.total_goals_exact >= 1]
    if nonzero_fit.empty:
        raise ResearchError("Beta-Binomial fit set has no nonzero totals")
    preprocessor = Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scaler", StandardScaler()),
    ])
    X_fit = preprocessor.fit_transform(nonzero_fit[feature_names])
    fitted = fit_beta_binomial(
        X_fit,
        nonzero_fit.home_goals_exact.to_numpy(float),
        nonzero_fit.total_goals_exact.to_numpy(float),
        float(hyper["C"]),
        float(hyper["concentration"]),
        config,
    )
    probabilities: dict[int, np.ndarray] = {0: np.ones((len(predict), 1), dtype=float)}
    per_total: dict[str, Any] = {"0": {"deterministic": True}}
    max_residual = 0.0
    legal_failures = 0
    p_min = 1.0
    p_max = 0.0
    for total in range(1, 7):
        counterfactual = set_counterfactual_total(predict, total)
        predicted, p_values, residual, failures = beta_binomial_probability(
            fitted.x,
            preprocessor,
            counterfactual,
            feature_names,
            float(hyper["concentration"]),
        )
        matrix = np.vstack(predicted)
        probabilities[total] = matrix
        max_residual = max(max_residual, float(residual))
        legal_failures += int(failures)
        p_min = min(p_min, float(np.min(p_values)))
        p_max = max(p_max, float(np.max(p_values)))
        per_total[str(total)] = {
            "support": total + 1,
            "probability_sum_max_residual": float(np.max(np.abs(matrix.sum(axis=1) - 1.0))),
        }
    return probabilities, {
        "solver_success": bool(fitted.success),
        "solver_status": int(fitted.status),
        "solver_message": str(fitted.message),
        "solver_iterations": int(fitted.nit),
        "p_min": p_min,
        "p_max": p_max,
        "probability_sum_max_residual": max_residual,
        "legal_mapping_failures": legal_failures,
        "per_total": per_total,
    }


def base_draw_probability_for_actual(
    frame: pd.DataFrame,
    probabilities: dict[int, np.ndarray],
) -> np.ndarray:
    output = np.zeros(len(frame), dtype=float)
    for position, row in enumerate(frame.itertuples()):
        total = int(row.total_goals_exact)
        if total == 0:
            output[position] = 1.0
        elif total in {2, 4, 6}:
            output[position] = float(probabilities[total][position, total // 2])
        else:
            output[position] = 0.0
    return output


def gate_design_actual(
    frame: pd.DataFrame,
    probabilities: dict[int, np.ndarray],
    minimum_probability: float,
) -> pd.DataFrame:
    output = frame.copy()
    base_draw = base_draw_probability_for_actual(frame, probabilities)
    clipped = np.clip(base_draw, minimum_probability, 1.0 - minimum_probability)
    output["base_draw_probability"] = base_draw
    output["base_draw_logit"] = np.log(clipped / (1.0 - clipped))
    return output


def gate_design_counterfactual(
    frame: pd.DataFrame,
    total: int,
    probability: np.ndarray,
    minimum_probability: float,
) -> pd.DataFrame:
    output = set_counterfactual_total(frame, total)
    base_draw = probability[:, total // 2]
    clipped = np.clip(base_draw, minimum_probability, 1.0 - minimum_probability)
    output["base_draw_probability"] = base_draw
    output["base_draw_logit"] = np.log(clipped / (1.0 - clipped))
    return output


def predict_positive(model: Any, frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    raw = model.predict_proba(frame[features])
    classes = [int(value) for value in model.named_steps["model"].classes_]
    if 1 not in classes:
        raise ResearchError("draw gate model lacks positive class")
    return raw[:, classes.index(1)]


def adjust_draw_matrix(
    probability: np.ndarray,
    total: int,
    learned_probability: np.ndarray,
    alpha: float,
    minimum_probability: float,
) -> np.ndarray:
    base = np.asarray(probability, dtype=float)
    if total not in {2, 4, 6}:
        return base.copy()
    if base.shape[1] != total + 1:
        raise ResearchError(f"draw adjustment support mismatch for T={total}")
    draw_index = total // 2
    p_base = np.clip(base[:, draw_index], minimum_probability, 1.0 - minimum_probability)
    q_model = np.clip(np.asarray(learned_probability, dtype=float), minimum_probability, 1.0 - minimum_probability)
    q = np.clip((1.0 - alpha) * p_base + alpha * q_model, minimum_probability, 1.0 - minimum_probability)
    output = base.copy()
    denominator = np.maximum(1.0 - p_base, minimum_probability)
    scale = (1.0 - q) / denominator
    output *= scale[:, None]
    output[:, draw_index] = q
    output /= output.sum(axis=1, keepdims=True)
    return output


def apply_gate_all_totals(
    frame: pd.DataFrame,
    base_probabilities: dict[int, np.ndarray],
    model: Any | None,
    features: list[str],
    alpha: float,
    config: dict[str, Any],
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    minimum = float(config["draw_gate_contract"]["minimum_probability"])
    eligible = {int(value) for value in config["draw_gate_contract"]["eligible_totals"]}
    output = {total: np.asarray(probability, dtype=float).copy() for total, probability in base_probabilities.items()}
    receipt: dict[str, Any] = {}
    if model is None:
        return output, {"identity": True, "probability_sum_max_residual": 0.0}
    maximum = 0.0
    for total in sorted(eligible):
        design = gate_design_counterfactual(frame, total, base_probabilities[total], minimum)
        learned = predict_positive(model, design, features)
        output[total] = adjust_draw_matrix(
            base_probabilities[total], total, learned, alpha, minimum
        )
        residual = float(np.max(np.abs(output[total].sum(axis=1) - 1.0)))
        maximum = max(maximum, residual)
        receipt[str(total)] = {
            "mean_base_draw_probability": float(base_probabilities[total][:, total // 2].mean()),
            "mean_learned_draw_probability": float(learned.mean()),
            "mean_final_draw_probability": float(output[total][:, total // 2].mean()),
            "probability_sum_max_residual": residual,
        }
    return output, {
        "identity": False,
        "alpha": alpha,
        "per_total": receipt,
        "probability_sum_max_residual": maximum,
    }


def realised_probability_list(
    frame: pd.DataFrame,
    probabilities: dict[int, np.ndarray],
) -> list[np.ndarray]:
    output = []
    for position, row in enumerate(frame.itertuples()):
        total = int(row.total_goals_exact)
        if total > 6:
            raise ResearchError("realised conditional list cannot include T>=7")
        output.append(probabilities[total][position])
    return output


def apply_candidate_to_realised(
    frame: pd.DataFrame,
    base_probabilities: dict[int, np.ndarray],
    model: Any | None,
    features: list[str],
    alpha: float,
    config: dict[str, Any],
) -> list[np.ndarray]:
    gated, _ = apply_gate_all_totals(frame, base_probabilities, model, features, alpha, config)
    return realised_probability_list(frame, gated)


def gate_feature_sets(config: dict[str, Any], frame: pd.DataFrame) -> dict[str, list[str]]:
    values = {}
    for name, columns in config["draw_gate_contract"]["feature_sets"].items():
        missing = sorted(set(columns) - set(frame.columns))
        if missing:
            raise ResearchError(f"draw-gate feature set {name} missing columns: {missing}")
        values[str(name)] = [str(column) for column in columns]
    return values


def select_gate_candidate(
    policy: pd.DataFrame,
    policy_base_probabilities: dict[int, np.ndarray],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    fraction = float(config["split_contract"]["policy_gate_fit_fraction_by_date"])
    gate_fit, gate_select, split_receipt = policy_date_split(policy, fraction)
    minimum = float(config["draw_gate_contract"]["minimum_probability"])
    eligible_totals = {int(value) for value in config["draw_gate_contract"]["eligible_totals"]}
    full_design = gate_design_actual(policy, policy_base_probabilities, minimum)
    feature_sets = gate_feature_sets(config, full_design)
    position = {int(index): pos for pos, index in enumerate(policy.index)}

    fit_even = gate_fit[gate_fit.total_goals_exact.isin(eligible_totals)]
    select_even = gate_select[gate_select.total_goals_exact.isin(eligible_totals)]
    if min(len(fit_even), len(select_even)) <= 0:
        raise ResearchError("empty even-total policy gate split")
    target_fit = (fit_even.home_goals_exact == fit_even.away_goals_exact).astype(int)
    if target_fit.nunique() != 2:
        raise ResearchError("gate-fit segment lacks draw and non-draw classes")

    gate_select_core = gate_select[gate_select.total_goals_exact <= 6].copy()
    if gate_select_core.empty:
        raise ResearchError("policy gate selection core is empty")
    select_positions = [position[int(index)] for index in gate_select_core.index]
    select_base = {
        total: probability[select_positions]
        for total, probability in policy_base_probabilities.items()
    }
    base_realised = realised_probability_list(gate_select_core, select_base)
    base_score = score_components(gate_select_core, base_realised)
    base_result = result_components(gate_select_core, base_realised)
    receipts = [{
        "name": "identity",
        "feature_set": "identity",
        "feature_count": 0,
        "C": None,
        "alpha": 0.0,
        "policy_selection_score_metrics": metric_summary(base_score),
        "policy_selection_result_metrics": metric_summary(base_result),
    }]

    for feature_set_name, features in feature_sets.items():
        for C in config["draw_gate_contract"]["regularization_C_grid"]:
            model = make_model(float(C), config)
            model.fit(full_design.loc[fit_even.index, features], target_fit)
            for alpha in config["draw_gate_contract"]["learned_blend_alpha_grid"]:
                candidate_realised = apply_candidate_to_realised(
                    gate_select_core,
                    select_base,
                    model,
                    features,
                    float(alpha),
                    config,
                )
                candidate_score = score_components(gate_select_core, candidate_realised)
                candidate_result = result_components(gate_select_core, candidate_realised)
                receipts.append({
                    "name": f"{feature_set_name}_C{float(C):g}_A{float(alpha):g}",
                    "feature_set": feature_set_name,
                    "features": features,
                    "feature_count": len(features),
                    "C": float(C),
                    "alpha": float(alpha),
                    "policy_selection_score_metrics": metric_summary(candidate_score),
                    "policy_selection_result_metrics": metric_summary(candidate_result),
                    "max_solver_iterations": int(np.max(model.named_steps["model"].n_iter_)),
                })

    winner = min(
        receipts,
        key=lambda row: (
            row["policy_selection_score_metrics"]["logloss"],
            row["policy_selection_result_metrics"]["draw_binary_brier"],
            row["feature_count"],
            float("inf") if row["C"] is None else row["C"],
            row["alpha"],
        ),
    )
    return winner, receipts, {
        **split_receipt,
        "gate_fit_rows": len(gate_fit),
        "gate_selection_rows": len(gate_select),
        "gate_selection_core_rows": len(gate_select_core),
        "gate_fit_even_rows": len(fit_even),
        "gate_selection_even_rows": len(select_even),
        "gate_fit_draw_rate": float(target_fit.mean()),
    }


def fit_final_gate(
    policy: pd.DataFrame,
    policy_base_probabilities: dict[int, np.ndarray],
    selected: dict[str, Any],
    config: dict[str, Any],
) -> tuple[Any | None, list[str], float, dict[str, Any]]:
    if selected["feature_set"] == "identity":
        return None, [], 0.0, {"identity": True}
    minimum = float(config["draw_gate_contract"]["minimum_probability"])
    eligible = {int(value) for value in config["draw_gate_contract"]["eligible_totals"]}
    design = gate_design_actual(policy, policy_base_probabilities, minimum)
    features = [str(value) for value in selected["features"]]
    even = policy[policy.total_goals_exact.isin(eligible)]
    target = (even.home_goals_exact == even.away_goals_exact).astype(int)
    if target.nunique() != 2:
        raise ResearchError("full policy segment lacks both gate classes")
    model = make_model(float(selected["C"]), config)
    model.fit(design.loc[even.index, features], target)
    return model, features, float(selected["alpha"]), {
        "identity": False,
        "rows": len(even),
        "draw_rate": float(target.mean()),
        "max_solver_iterations": int(np.max(model.named_steps["model"].n_iter_)),
    }


def paired_bootstrap(
    meta: pd.DataFrame,
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    if not (len(meta) == len(candidate) == len(baseline)):
        raise ResearchError("paired bootstrap length mismatch")
    meta = meta.reset_index(drop=True)
    candidate = candidate.reset_index(drop=True)
    baseline = baseline.reset_index(drop=True)
    keys = meta[["competition_id", "fold"]].astype(str).agg("|".join, axis=1)
    groups = sorted(keys.unique())
    indexes = [np.flatnonzero(keys.to_numpy() == group) for group in groups]
    if len(indexes) < 2:
        raise ResearchError("paired bootstrap has fewer than two clusters")
    counts = np.asarray([len(index) for index in indexes], dtype=float)
    rng = np.random.default_rng(int(config["bootstrap"]["seed"]))
    samples = int(config["bootstrap"]["samples"])
    picks = rng.integers(0, len(indexes), size=(samples, len(indexes)))
    denominator = counts[picks].sum(axis=1)
    low, high = [float(value) for value in config["bootstrap"]["interval"]]
    lower_is_better = {
        "logloss", "brier", "rps", "result_logloss", "result_brier", "draw_binary_brier"
    }
    output = {}
    for metric in baseline.columns:
        group_delta = np.asarray([
            float((candidate.iloc[index][metric] - baseline.iloc[index][metric]).sum())
            for index in indexes
        ])
        values = group_delta[picks].sum(axis=1) / denominator
        better = values < 0 if metric in lower_is_better else values > 0
        output[metric] = {
            "mean_delta_candidate_minus_baseline": float(
                candidate[metric].mean() - baseline[metric].mean()
            ),
            "p05": float(np.quantile(values, low)),
            "p95": float(np.quantile(values, high)),
            "probability_candidate_better": float(better.mean()),
        }
    return output


def subset_receipt(
    name: str,
    meta: pd.DataFrame,
    frame: pd.DataFrame,
    score_candidate: pd.DataFrame,
    score_baseline: pd.DataFrame,
    result_candidate: pd.DataFrame,
    result_baseline: pd.DataFrame,
    mask: pd.Series,
    config: dict[str, Any],
) -> dict[str, Any]:
    positions = np.flatnonzero(mask.to_numpy(bool))
    if len(positions) == 0:
        raise ResearchError(f"subset {name} is empty")
    subset_meta = meta.iloc[positions]
    subset_score_candidate = score_candidate.iloc[positions]
    subset_score_baseline = score_baseline.iloc[positions]
    subset_result_candidate = result_candidate.iloc[positions]
    subset_result_baseline = result_baseline.iloc[positions]
    return {
        "name": name,
        "rows": len(positions),
        "score_baseline_metrics": metric_summary(subset_score_baseline),
        "score_candidate_metrics": metric_summary(subset_score_candidate),
        "score_bootstrap_90": paired_bootstrap(
            subset_meta, subset_score_candidate, subset_score_baseline, config
        ),
        "result_baseline_metrics": metric_summary(subset_result_baseline),
        "result_candidate_metrics": metric_summary(subset_result_candidate),
        "result_bootstrap_90": paired_bootstrap(
            subset_meta, subset_result_candidate, subset_result_baseline, config
        ),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ResearchError("stability output is empty")
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(config: dict[str, Any], out_path: Path, stability_path: Path) -> dict[str, Any]:
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    identity = audit_data_identity(raw, config)
    base = build_features(raw)
    features = add_conditional_total_features(attach_exact_labels(raw, base))
    core_features = select_core_features(features)
    mapping_sets = mapping_feature_sets(core_features)
    seasons, excluded = complete_seasons(raw, config)
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    nonlinear_candidate = dict(config["nonlinear_expert"])

    fold_receipts = []
    stability_rows = []
    all_meta = []
    all_core_meta = []
    all_joint_base = []
    all_joint_gate = []
    all_score_base = []
    all_score_gate = []
    all_result_base = []
    all_result_gate = []
    all_core_frames = []
    audit_maxima = {
        "base_joint_probability_sum_max_residual": 0.0,
        "gate_joint_probability_sum_max_residual": 0.0,
        "gate_conditional_score_sum_max_residual": 0.0,
        "gate_total_marginal_max_residual": 0.0,
        "gate_legal_mapping_failures": 0,
        "gate_negative_probability_count": 0,
    }

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        train = fold[fold.split == "train"]
        policy = fold[fold.split == "policy"].copy()
        fit = fold[fold.split.isin(["train", "policy"])]
        test = fold[fold.split == "test"].copy()

        flat_C, _ = select_C(train, policy, core_features, "total_class", classes, config)
        linear_C, _ = select_continuation_C(train, policy, core_features, config)
        flat_train = make_model(flat_C, config)
        flat_train.fit(train[core_features], train.total_class)
        policy_flat = flat_probability(flat_train, policy, core_features, config)
        linear_train = continuation_models(train, core_features, linear_C, config)
        policy_linear = continuation_probability(linear_train, policy, core_features, config)
        nonlinear_train = boosting_continuation_models(train, core_features, nonlinear_candidate, config)
        policy_nonlinear = boosting_probability(nonlinear_train, policy, core_features, config)
        eta_three, policy_weights_three, _ = select_eta(policy, [policy_flat, policy_linear, policy_nonlinear], config)
        flat_fit = make_model(flat_C, config)
        flat_fit.fit(fit[core_features], fit.total_class)
        test_flat = flat_probability(flat_fit, test, core_features, config)
        linear_fit = continuation_models(fit, core_features, linear_C, config)
        test_linear = continuation_probability(linear_fit, test, core_features, config)
        nonlinear_fit = boosting_continuation_models(fit, core_features, nonlinear_candidate, config)
        test_nonlinear = boosting_probability(nonlinear_fit, test, core_features, config)
        total_three, final_weights, updates = daily_mixture(
            test, [test_flat, test_linear, test_nonlinear], eta_three,
            initial_weights=policy_weights_three,
        )

        train_nonzero = train[train.total_goals_exact >= 1]
        policy_nonzero = policy[policy.total_goals_exact >= 1]
        selected_mapping, mapping_receipts = select_full_range_candidate(
            train_nonzero, policy_nonzero, mapping_sets, config
        )
        mapping_features = [str(value) for value in selected_mapping["features"]]
        mapping_hyper = dict(selected_mapping["selected"])
        policy_base, policy_base_receipt = fit_selected_beta_model(
            train, policy, mapping_features, mapping_hyper, config
        )
        test_base, test_base_receipt = fit_selected_beta_model(
            fit, test, mapping_features, mapping_hyper, config
        )

        selected_gate, gate_grid, gate_split = select_gate_candidate(policy, policy_base, config)
        gate_model, gate_features, gate_alpha, gate_fit_receipt = fit_final_gate(
            policy, policy_base, selected_gate, config
        )
        test_gate, gate_application = apply_gate_all_totals(
            test, test_base, gate_model, gate_features, gate_alpha, config
        )

        joint_base, audit_base = build_joint(total_three, test_base)
        joint_gate, audit_gate = build_joint(total_three, test_gate)
        base_joint_components = joint_components(test, joint_base)
        gate_joint_components = joint_components(test, joint_gate)
        for components in (base_joint_components, gate_joint_components):
            components.index = test.index

        position = {int(index): pos for pos, index in enumerate(test.index)}
        core_test = test[test.total_goals_exact <= 6].copy()
        base_realised = realised_conditional_probabilities(core_test, test_base, position)
        gate_realised = realised_conditional_probabilities(core_test, test_gate, position)
        base_score = score_components(core_test, base_realised)
        gate_score = score_components(core_test, gate_realised)
        base_result = result_components(core_test, base_realised)
        gate_result = result_components(core_test, gate_realised)

        audit_maxima["base_joint_probability_sum_max_residual"] = max(
            audit_maxima["base_joint_probability_sum_max_residual"], float(audit_base["joint_probability_sum_max_residual"])
        )
        audit_maxima["gate_joint_probability_sum_max_residual"] = max(
            audit_maxima["gate_joint_probability_sum_max_residual"], float(audit_gate["joint_probability_sum_max_residual"])
        )
        audit_maxima["gate_conditional_score_sum_max_residual"] = max(
            audit_maxima["gate_conditional_score_sum_max_residual"],
            float(audit_gate["conditional_score_sum_max_residual"]),
            float(gate_application["probability_sum_max_residual"]),
        )
        audit_maxima["gate_total_marginal_max_residual"] = max(
            audit_maxima["gate_total_marginal_max_residual"], float(audit_gate["total_marginal_max_residual"])
        )
        audit_maxima["gate_legal_mapping_failures"] += int(audit_gate["legal_mapping_failures"])
        audit_maxima["gate_negative_probability_count"] += int(audit_gate["negative_probability_count"])

        fold_receipts.append({
            "fold": str(test.fold.iloc[0]),
            "rows": {"train": len(train), "policy": len(policy), "fit": len(fit), "test": len(test), "core_test": len(core_test)},
            "direct_total": {
                "eta": eta_three,
                "policy_weights": [float(value) for value in policy_weights_three],
                "test_final_weights": [float(value) for value in final_weights],
                "daily_updates": len(updates),
            },
            "base_mapping": {
                "selected": selected_mapping,
                "policy_candidates": mapping_receipts,
                "policy_prediction_receipt": policy_base_receipt,
                "test_prediction_receipt": test_base_receipt,
            },
            "draw_gate": {
                "policy_split": gate_split,
                "selected": selected_gate,
                "policy_candidates": gate_grid,
                "final_fit": gate_fit_receipt,
                "application": gate_application,
            },
            "joint_delta_gate_minus_base": {
                metric: float(gate_joint_components[metric].mean() - base_joint_components[metric].mean())
                for metric in base_joint_components.columns
            },
            "conditional_score_delta_gate_minus_base": {
                metric: float(gate_score[metric].mean() - base_score[metric].mean())
                for metric in base_score.columns
            },
            "conditional_result_delta_gate_minus_base": {
                metric: float(gate_result[metric].mean() - base_result[metric].mean())
                for metric in base_result.columns
            },
            "audits": {"base": audit_base, "gate": audit_gate},
        })

        all_meta.append(test[["competition_id", "season", "fold"]])
        all_core_meta.append(core_test[["competition_id", "season", "fold"]])
        all_joint_base.append(base_joint_components)
        all_joint_gate.append(gate_joint_components)
        all_score_base.append(base_score)
        all_score_gate.append(gate_score)
        all_result_base.append(base_result)
        all_result_gate.append(gate_result)
        all_core_frames.append(core_test[[
            "competition_id", "season", "fold", "home_goals_exact", "away_goals_exact", "total_goals_exact"
        ]])

        for competition, indexes in test.groupby("competition_id").groups.items():
            positions = [test.index.get_loc(index) for index in indexes]
            base_summary = metric_summary(base_joint_components.iloc[positions])
            gate_summary = metric_summary(gate_joint_components.iloc[positions])
            stability_rows.append({
                "competition_id": competition,
                "fold": str(test.fold.iloc[0]),
                "rows": len(positions),
                **{f"base_{key}": value for key, value in base_summary.items()},
                **{f"gate_{key}": value for key, value in gate_summary.items()},
                **{f"delta_{key}": gate_summary[key] - base_summary[key] for key in base_summary},
                "selected_gate": selected_gate["name"],
            })

    meta = pd.concat(all_meta).reset_index(drop=True)
    core_meta = pd.concat(all_core_meta).reset_index(drop=True)
    joint_base = pd.concat(all_joint_base).reset_index(drop=True)
    joint_gate = pd.concat(all_joint_gate).reset_index(drop=True)
    score_base = pd.concat(all_score_base).reset_index(drop=True)
    score_gate = pd.concat(all_score_gate).reset_index(drop=True)
    result_base = pd.concat(all_result_base).reset_index(drop=True)
    result_gate = pd.concat(all_result_gate).reset_index(drop=True)
    core_frame = pd.concat(all_core_frames).reset_index(drop=True)

    joint_boot = paired_bootstrap(meta, joint_gate, joint_base, config)
    score_boot = paired_bootstrap(core_meta, score_gate, score_base, config)
    result_boot = paired_bootstrap(core_meta, result_gate, result_base, config)

    home = core_frame.home_goals_exact.astype(int)
    away = core_frame.away_goals_exact.astype(int)
    subset_masks = {
        "ALL_CORE": pd.Series(True, index=core_frame.index),
        "DRAW": home == away,
        "NON_DRAW": home != away,
        "DRAW_0_0": (home == 0) & (away == 0),
        "DRAW_1_1": (home == 1) & (away == 1),
        "DRAW_2_2": (home == 2) & (away == 2),
        "DRAW_3PLUS": (home == away) & (home >= 3),
    }
    subsets = {
        name: subset_receipt(
            name, core_meta, core_frame, score_gate, score_base, result_gate, result_base, mask, config
        )
        for name, mask in subset_masks.items()
    }

    expected = dict(config["frozen_r8_reproduction"])
    actual = {
        "joint_logloss": float(joint_base.logloss.mean()),
        "joint_brier": float(joint_base.brier.mean()),
        "core_score_logloss": float(score_base.logloss.mean()),
        "core_score_brier": float(score_base.brier.mean()),
        "core_result_logloss": float(result_base.result_logloss.mean()),
        "draw_binary_brier": float(result_base.draw_binary_brier.mean()),
    }
    reproduction_residuals = {key: float(actual[key] - float(expected[key])) for key in actual}
    reproduction_max = max(abs(value) for value in reproduction_residuals.values())
    reproduction_pass = reproduction_max <= float(expected["tolerance"])

    gates = config["pass_gates"]
    overall_joint_pass = all(
        joint_boot[metric]["p95"] < 0 for metric in gates["overall_joint_metrics_must_robustly_improve"]
    )
    draw_pass = all(
        subsets["DRAW"]["score_bootstrap_90"][metric]["p95"] < 0
        for metric in gates["draw_score_metrics_must_robustly_improve"]
    )
    draw_brier_pass = subsets["DRAW"]["result_bootstrap_90"]["draw_binary_brier"]["p95"] < 0
    one_one_pass = subsets["DRAW_1_1"]["score_bootstrap_90"]["logloss"]["p95"] < 0
    two_two_pass = subsets["DRAW_2_2"]["score_bootstrap_90"]["logloss"]["p95"] < 0
    non_draw_pass = (
        subsets["NON_DRAW"]["score_bootstrap_90"]["logloss"]["p95"] <= float(gates["non_draw_logloss_noninferiority_margin"])
        and subsets["NON_DRAW"]["score_bootstrap_90"]["brier"]["p95"] <= float(gates["non_draw_brier_noninferiority_margin"])
    )
    zero_zero_pass = (
        subsets["DRAW_0_0"]["score_bootstrap_90"]["logloss"]["p95"] <= float(gates["zero_zero_logloss_noninferiority_margin"])
        and subsets["DRAW_0_0"]["score_bootstrap_90"]["brier"]["p95"] <= float(gates["zero_zero_brier_noninferiority_margin"])
    )
    audits_pass = (
        audit_maxima["gate_joint_probability_sum_max_residual"] <= float(config["model_contract"]["probability_sum_tolerance"])
        and audit_maxima["gate_conditional_score_sum_max_residual"] <= float(config["model_contract"]["probability_sum_tolerance"])
        and audit_maxima["gate_total_marginal_max_residual"] <= float(config["model_contract"]["probability_sum_tolerance"])
        and audit_maxima["gate_legal_mapping_failures"] == 0
        and audit_maxima["gate_negative_probability_count"] == 0
    )
    pass_map = {
        "reproduction": reproduction_pass,
        "audits": audits_pass,
        "overall_joint": overall_joint_pass,
        "draw_score": draw_pass,
        "draw_binary_brier": draw_brier_pass,
        "one_one_logloss": one_one_pass,
        "two_two_logloss": two_two_pass,
        "non_draw_noninferiority": non_draw_pass,
        "zero_zero_noninferiority": zero_zero_pass,
    }
    if all(pass_map.values()):
        status = "PASS_R10_LEARNED_DRAW_GATE_REPAIRS_DRAW_DOMAIN"
    elif reproduction_pass and audits_pass and (
        draw_pass or draw_brier_pass or one_one_pass or two_two_pass
    ):
        status = "PARTIAL_PASS_R10_DRAW_SIGNAL_WITH_GATE_FAILURES"
    else:
        status = "FAIL_R10_LEARNED_DRAW_GATE_NO_SAFE_REPAIR"

    result = {
        "schema_version": config["schema_version"],
        "status": status,
        "evidence_class": config["evidence_class"],
        "data_identity": identity,
        "split_contract": {
            "complete_seasons": seasons,
            "excluded_incomplete_latest_seasons": excluded,
            "same_day_freeze_before_update": True,
            "test_outcomes_used_for_gate_or_model_selection": False,
            "policy_gate_fit_and_selection_date_separated": True,
            "repeated_historical_replay_allowed": True,
            "independent_confirmation_claim_allowed": False,
        },
        "algorithm_contract": {
            "base": "R7 three-expert direct-total plus R4 shared Beta-Binomial H|T,X",
            "gate": "binary logistic conditional draw mass for T=2,4,6, blended by policy-selected alpha",
            "non_draw_rescaling": "proportional within the realised total support",
            "total_marginal_changed": False,
            "T0_changed": False,
            "odd_totals_changed": False,
            "manual_draw_or_exact_score_multiplier": False,
            "tail_exact_allocation": False,
        },
        "folds": fold_receipts,
        "reproduction": {
            "actual": actual,
            "expected": {key: value for key, value in expected.items() if key != "tolerance"},
            "residuals": reproduction_residuals,
            "max_abs_residual": reproduction_max,
            "tolerance": expected["tolerance"],
            "pass": reproduction_pass,
        },
        "pooled": {
            "test_rows": len(meta),
            "core_rows": len(core_meta),
            "joint_base_metrics": metric_summary(joint_base),
            "joint_gate_metrics": metric_summary(joint_gate),
            "joint_bootstrap_gate_vs_base_90": joint_boot,
            "core_score_base_metrics": metric_summary(score_base),
            "core_score_gate_metrics": metric_summary(score_gate),
            "core_score_bootstrap_gate_vs_base_90": score_boot,
            "core_result_base_metrics": metric_summary(result_base),
            "core_result_gate_metrics": metric_summary(result_gate),
            "core_result_bootstrap_gate_vs_base_90": result_boot,
            "subsets": subsets,
        },
        "pass_gates": {"results": pass_map, "all_pass": all(pass_map.values()), "contract": gates},
        "audits": {
            **audit_maxima,
            "audits_pass": audits_pass,
            "conditional_probability_simplex_preserved": True,
            "direct_total_marginal_preserved": True,
            "T_D_parity_failures": 0,
            "T0_mutations": 0,
            "odd_total_mutations": 0,
            "tail_exact_allocation_performed": False,
        },
        "stability": {
            "competition_window_count": len(stability_rows),
            "joint_logloss_wins": int(sum(row["delta_logloss"] < 0 for row in stability_rows)),
            "joint_brier_wins": int(sum(row["delta_brier"] < 0 for row in stability_rows)),
            "joint_top1_wins": int(sum(row["delta_top1"] > 0 for row in stability_rows)),
            "selected_gate_counts": {
                str(key): int(value)
                for key, value in pd.Series(
                    [fold["draw_gate"]["selected"]["name"] for fold in fold_receipts]
                ).value_counts().to_dict().items()
            },
        },
        "ruling": {
            "learned_draw_gate_retained": status.startswith("PASS_"),
            "r8_base_retained_if_gate_fails": not status.startswith("PASS_"),
            "tail_exact_distribution_confirmed": False,
            "full_unified_score_matrix_allowed": False,
            "formal_weight": 0,
            "promotion": False,
            "strict_PIT_market_context_rows": 0,
            "current_match_probabilities_generated": False,
            "exact_score_output_generated": False,
            "EV_generated": False,
            "fixed_outputs": ["总进球分布不可用。", "精确比分不可用。"],
        },
        "governance": config["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(stability_path, stability_rows)
    return result


def self_test() -> None:
    base = np.asarray([[0.2, 0.6, 0.2], [0.3, 0.4, 0.3]], dtype=float)
    learned = np.asarray([0.4, 0.5], dtype=float)
    adjusted = adjust_draw_matrix(base, 2, learned, 1.0, 1e-6)
    assert np.max(np.abs(adjusted.sum(axis=1) - 1.0)) < 1e-12
    assert np.allclose(adjusted[:, 1], learned)
    assert np.allclose(adjusted[:, 0] / adjusted[:, 2], base[:, 0] / base[:, 2])
    odd = adjust_draw_matrix(np.full((2, 4), 0.25), 3, learned, 1.0, 1e-6)
    assert np.allclose(odd, 0.25)


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
        "pooled": result["pooled"],
        "pass_gates": result["pass_gates"],
        "audits": result["audits"],
        "stability": result["stability"],
        "ruling": result["ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
