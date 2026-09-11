#!/usr/bin/env python3
"""R8 historical core joint-chain audit for V5.1.

Viewed historical development only. Build a coherent state distribution with exact legal
score cells for T=0..6 and one aggregate T>=7 bucket. Compare the previous research chain
(two-expert total ensemble + separate P(D|T,X) models) with the retained chain
(three-expert total ensemble + shared Beta-Binomial H|T,X). The aggregate tail bucket is
never allocated to exact scores or 1X2 outcomes.
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
    align_probability,
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
    bootstrap,
    mapping_feature_sets,
    result_components,
    score_components,
    select_full_range_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_core_joint_chain_r8.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_core_joint_chain_r8_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_core_joint_chain_r8_stability.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def flat_probability(model: Any, frame: pd.DataFrame, features: list[str], config: dict[str, Any]) -> np.ndarray:
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    return align_probability(model, frame[features], classes)


def score_states() -> list[tuple[int, int, int] | tuple[str, str, str]]:
    states: list[tuple[int, int, int] | tuple[str, str, str]] = []
    for total in range(7):
        for home in range(total + 1):
            states.append((total, home, total - home))
    states.append(("7+", "TAIL", "TAIL"))
    return states


def state_labels(states: list[tuple[int, int, int] | tuple[str, str, str]]) -> list[str]:
    labels = []
    for total, home, away in states:
        labels.append("T7PLUS_AGGREGATE" if total == "7+" else f"{home}-{away}")
    return labels


def fit_current_score_models(
    fold: pd.DataFrame,
    core_features: list[str],
    test: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    probabilities: dict[int, np.ndarray] = {0: np.ones((len(test), 1), dtype=float)}
    receipts: dict[str, Any] = {"0": {"deterministic": True}}
    for total in range(1, 7):
        train = fold[(fold.split == "train") & (fold.total_goals_exact == total)]
        policy = fold[(fold.split == "policy") & (fold.total_goals_exact == total)]
        fit = fold[(fold.split.isin(["train", "policy"])) & (fold.total_goals_exact == total)]
        classes = list(range(-total, total + 1, 2))
        if min(len(train), len(policy), len(fit)) <= 0:
            raise ResearchError(f"empty current score-model split for T={total}")
        selected_C, policy_grid = select_C(
            train, policy, core_features, "goal_difference", classes, config
        )
        model = make_model(selected_C, config)
        model.fit(fit[core_features], fit.goal_difference)
        probability = align_probability(model, test[core_features], classes)
        probabilities[total] = probability
        receipts[str(total)] = {
            "train_rows": len(train),
            "policy_rows": len(policy),
            "fit_rows": len(fit),
            "selected_C": selected_C,
            "policy_grid": policy_grid,
            "probability_sum_max_residual": float(np.max(np.abs(probability.sum(axis=1) - 1.0))),
        }
    return probabilities, receipts


def set_counterfactual_total(frame: pd.DataFrame, total: int) -> pd.DataFrame:
    output = frame.copy()
    output["total_goals_exact"] = int(total)
    output["conditional_total"] = float(total)
    output["conditional_total_log1p"] = math.log1p(total)
    output["conditional_total_even"] = float(total % 2 == 0)
    output["conditional_total_tail7"] = 0.0
    return output


def fit_beta_score_model(
    fold: pd.DataFrame,
    core_features: list[str],
    test: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    feature_sets = mapping_feature_sets(core_features)
    train = fold[(fold.split == "train") & (fold.total_goals_exact >= 1)]
    policy = fold[(fold.split == "policy") & (fold.total_goals_exact >= 1)]
    fit = fold[(fold.split.isin(["train", "policy"])) & (fold.total_goals_exact >= 1)]
    selected, policy_receipts = select_full_range_candidate(
        train, policy, feature_sets, config
    )
    feature_names = list(selected["features"])
    hyper = dict(selected["selected"])
    preprocessor = Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scaler", StandardScaler()),
    ])
    X_fit = preprocessor.fit_transform(fit[feature_names])
    fitted = fit_beta_binomial(
        X_fit,
        fit.home_goals_exact.to_numpy(float),
        fit.total_goals_exact.to_numpy(float),
        float(hyper["C"]),
        float(hyper["concentration"]),
        config,
    )
    probabilities: dict[int, np.ndarray] = {0: np.ones((len(test), 1), dtype=float)}
    per_total: dict[str, Any] = {"0": {"deterministic": True}}
    max_residual = 0.0
    legal_failures = 0
    p_min = 1.0
    p_max = 0.0
    for total in range(1, 7):
        counterfactual = set_counterfactual_total(test, total)
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
        "selected": selected,
        "policy_candidates": policy_receipts,
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


def build_joint(
    total_probability: np.ndarray,
    score_probability: dict[int, np.ndarray],
) -> tuple[np.ndarray, dict[str, float]]:
    states = score_states()
    output = np.zeros((len(total_probability), len(states)), dtype=float)
    cursor = 0
    score_residual = 0.0
    legal_failures = 0
    for total in range(7):
        conditional = np.asarray(score_probability[total], dtype=float)
        if conditional.shape != (len(total_probability), total + 1):
            raise ResearchError(f"conditional score shape mismatch for T={total}: {conditional.shape}")
        score_residual = max(
            score_residual,
            float(np.max(np.abs(conditional.sum(axis=1) - 1.0))),
        )
        if np.any(conditional < -1e-15) or not np.isfinite(conditional).all():
            legal_failures += int(np.count_nonzero((conditional < -1e-15) | ~np.isfinite(conditional)))
        output[:, cursor : cursor + total + 1] = total_probability[:, [total]] * conditional
        cursor += total + 1
    output[:, -1] = total_probability[:, 7]
    total_marginal = np.zeros_like(total_probability)
    cursor = 0
    for total in range(7):
        total_marginal[:, total] = output[:, cursor : cursor + total + 1].sum(axis=1)
        cursor += total + 1
    total_marginal[:, 7] = output[:, -1]
    core_mass = output[:, :-1].sum(axis=1)
    return output, {
        "joint_probability_sum_max_residual": float(np.max(np.abs(output.sum(axis=1) - 1.0))),
        "conditional_score_sum_max_residual": score_residual,
        "total_marginal_max_residual": float(np.max(np.abs(total_marginal - total_probability))),
        "core_mass_max_residual": float(np.max(np.abs(core_mass - (1.0 - total_probability[:, 7])))),
        "legal_mapping_failures": legal_failures,
        "negative_probability_count": int(np.count_nonzero(output < -1e-15)),
        "finite": bool(np.isfinite(output).all()),
    }


def actual_state_index(frame: pd.DataFrame) -> np.ndarray:
    offsets = {0: 0}
    running = 0
    for total in range(7):
        offsets[total] = running
        running += total + 1
    tail_index = running
    values = []
    for row in frame.itertuples():
        total = int(row.total_goals_exact)
        values.append(tail_index if total >= 7 else offsets[total] + int(row.home_goals_exact))
    return np.asarray(values, dtype=int)


def joint_components(frame: pd.DataFrame, probability: np.ndarray) -> pd.DataFrame:
    actual = actual_state_index(frame)
    rows = []
    for index, state in enumerate(actual):
        p = np.clip(np.asarray(probability[index], dtype=float), 1e-15, 1.0)
        p /= p.sum()
        one_hot = np.zeros_like(p)
        one_hot[state] = 1.0
        order = np.argsort(-p)
        rows.append({
            "logloss": -math.log(float(p[state])),
            "brier": float(((p - one_hot) ** 2).sum()),
            "top1": float(order[0] == state),
            "top3": float(state in order[:3]),
            "top5": float(state in order[:5]),
        })
    return pd.DataFrame(rows, index=frame.index)


def realised_conditional_probabilities(
    frame: pd.DataFrame,
    probabilities: dict[int, np.ndarray],
    position: dict[int, int],
) -> list[np.ndarray]:
    output = []
    for index, row in frame.iterrows():
        total = int(row.total_goals_exact)
        output.append(probabilities[total][position[int(index)]])
    return output


def marginal_bounds(joint: np.ndarray) -> dict[str, float]:
    states = score_states()
    home_columns = []
    draw_columns = []
    away_columns = []
    btts_columns = []
    for index, state in enumerate(states[:-1]):
        _, home, away = state
        if int(home) > int(away):
            home_columns.append(index)
        elif int(home) == int(away):
            draw_columns.append(index)
        else:
            away_columns.append(index)
        if int(home) > 0 and int(away) > 0:
            btts_columns.append(index)
    tail = joint[:, -1]
    lower_home = joint[:, home_columns].sum(axis=1)
    lower_draw = joint[:, draw_columns].sum(axis=1)
    lower_away = joint[:, away_columns].sum(axis=1)
    lower_btts = joint[:, btts_columns].sum(axis=1)
    lower_sum = lower_home + lower_draw + lower_away
    return {
        "mean_tail_width": float(tail.mean()),
        "max_tail_width": float(tail.max()),
        "result_lower_sum_max_residual": float(np.max(np.abs(lower_sum - (1.0 - tail)))),
        "home_lower_mean": float(lower_home.mean()),
        "home_upper_mean": float((lower_home + tail).mean()),
        "draw_lower_mean": float(lower_draw.mean()),
        "draw_upper_mean": float((lower_draw + tail).mean()),
        "away_lower_mean": float(lower_away.mean()),
        "away_upper_mean": float((lower_away + tail).mean()),
        "btts_lower_mean": float(lower_btts.mean()),
        "btts_upper_mean": float((lower_btts + tail).mean()),
    }


def delta(model: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, float]:
    return {column: float(model[column].mean() - baseline[column].mean()) for column in baseline.columns}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ResearchError("stability output is empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run(config: dict[str, Any], out_path: Path, stability_path: Path) -> dict[str, Any]:
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    identity = audit_data_identity(raw, config)
    base = build_features(raw)
    features = add_conditional_total_features(attach_exact_labels(raw, base))
    core_features = select_core_features(features)
    seasons, excluded = complete_seasons(raw, config)
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    nonlinear_candidate = dict(config["nonlinear_expert"])

    fold_receipts = []
    stability_rows = []
    all_meta = []
    all_core_meta = []
    all_total_two = []
    all_total_three = []
    all_joint_baseline = []
    all_joint_total_only = []
    all_joint_allocation_only = []
    all_joint_candidate = []
    all_core_score_baseline = []
    all_core_score_candidate = []
    all_core_result_baseline = []
    all_core_result_candidate = []
    audit_maxima = {
        "joint_probability_sum_max_residual": 0.0,
        "conditional_score_sum_max_residual": 0.0,
        "total_marginal_max_residual": 0.0,
        "core_mass_max_residual": 0.0,
        "legal_mapping_failures": 0,
        "negative_probability_count": 0,
    }

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        train = fold[fold.split == "train"]
        policy = fold[fold.split == "policy"].copy()
        fit = fold[fold.split.isin(["train", "policy"])]
        test = fold[fold.split == "test"].copy()

        flat_C, flat_grid = select_C(train, policy, core_features, "total_class", classes, config)
        linear_C, linear_grid = select_continuation_C(train, policy, core_features, config)
        flat_train = make_model(flat_C, config)
        flat_train.fit(train[core_features], train.total_class)
        policy_flat = flat_probability(flat_train, policy, core_features, config)
        linear_train = continuation_models(train, core_features, linear_C, config)
        policy_linear = continuation_probability(linear_train, policy, core_features, config)
        nonlinear_train = boosting_continuation_models(train, core_features, nonlinear_candidate, config)
        policy_nonlinear = boosting_probability(nonlinear_train, policy, core_features, config)
        eta_two, policy_weights_two, eta_grid_two = select_eta(
            policy, [policy_flat, policy_linear], config
        )
        eta_three, policy_weights_three, eta_grid_three = select_eta(
            policy, [policy_flat, policy_linear, policy_nonlinear], config
        )

        flat_fit = make_model(flat_C, config)
        flat_fit.fit(fit[core_features], fit.total_class)
        test_flat = flat_probability(flat_fit, test, core_features, config)
        linear_fit = continuation_models(fit, core_features, linear_C, config)
        test_linear = continuation_probability(linear_fit, test, core_features, config)
        nonlinear_fit = boosting_continuation_models(fit, core_features, nonlinear_candidate, config)
        test_nonlinear = boosting_probability(nonlinear_fit, test, core_features, config)
        total_two, final_two, updates_two = daily_mixture(
            test, [test_flat, test_linear], eta_two, initial_weights=policy_weights_two
        )
        total_three, final_three, updates_three = daily_mixture(
            test,
            [test_flat, test_linear, test_nonlinear],
            eta_three,
            initial_weights=policy_weights_three,
        )

        current_score, current_receipt = fit_current_score_models(
            fold, core_features, test, config
        )
        beta_score, beta_receipt = fit_beta_score_model(
            fold, core_features, test, config
        )

        joint_baseline, audit_baseline = build_joint(total_two, current_score)
        joint_total_only, audit_total_only = build_joint(total_three, current_score)
        joint_allocation_only, audit_allocation_only = build_joint(total_two, beta_score)
        joint_candidate, audit_candidate = build_joint(total_three, beta_score)
        for audit in (audit_baseline, audit_total_only, audit_allocation_only, audit_candidate):
            for key in audit_maxima:
                if "failures" in key or "count" in key:
                    audit_maxima[key] += int(audit[key])
                else:
                    audit_maxima[key] = max(audit_maxima[key], float(audit[key]))

        y = test.total_class.to_numpy(int)
        total_two_components = metric_components(y, total_two, classes)
        total_three_components = metric_components(y, total_three, classes)
        baseline_components = joint_components(test, joint_baseline)
        total_only_components = joint_components(test, joint_total_only)
        allocation_only_components = joint_components(test, joint_allocation_only)
        candidate_components = joint_components(test, joint_candidate)
        for frame in (
            total_two_components,
            total_three_components,
            baseline_components,
            total_only_components,
            allocation_only_components,
            candidate_components,
        ):
            frame.index = test.index

        position = {int(index): pos for pos, index in enumerate(test.index)}
        core_test = test[test.total_goals_exact <= 6].copy()
        baseline_realised = realised_conditional_probabilities(core_test, current_score, position)
        candidate_realised = realised_conditional_probabilities(core_test, beta_score, position)
        core_score_baseline = score_components(core_test, baseline_realised)
        core_score_candidate = score_components(core_test, candidate_realised)
        core_result_baseline = result_components(core_test, baseline_realised)
        core_result_candidate = result_components(core_test, candidate_realised)

        fold_receipts.append({
            "fold": str(test.fold.iloc[0]),
            "rows": {
                "train": len(train),
                "policy": len(policy),
                "fit": len(fit),
                "test": len(test),
                "test_core_T0_to_T6": len(core_test),
                "test_tail_T7plus": int((test.total_goals_exact >= 7).sum()),
            },
            "total_models": {
                "flat_selected_C": flat_C,
                "flat_policy_grid": flat_grid,
                "linear_selected_C": linear_C,
                "linear_policy_grid": linear_grid,
                "nonlinear_candidate": nonlinear_candidate,
                "two_expert": {
                    "eta": eta_two,
                    "policy_weights": [float(value) for value in policy_weights_two],
                    "test_final_weights": [float(value) for value in final_two],
                    "daily_updates": len(updates_two),
                    "metrics": metric_summary(total_two_components),
                },
                "three_expert": {
                    "eta": eta_three,
                    "policy_weights": [float(value) for value in policy_weights_three],
                    "test_final_weights": [float(value) for value in final_three],
                    "daily_updates": len(updates_three),
                    "metrics": metric_summary(total_three_components),
                    "delta_minus_two": delta(total_three_components, total_two_components),
                },
            },
            "score_models": {
                "current_separate_multinomial": current_receipt,
                "shared_beta_binomial": beta_receipt,
                "core_score_delta_beta_minus_current": delta(core_score_candidate, core_score_baseline),
                "core_result_delta_beta_minus_current": delta(core_result_candidate, core_result_baseline),
            },
            "joint_states": {
                "support_size": len(score_states()),
                "labels": state_labels(score_states()),
                "baseline_metrics": metric_summary(baseline_components),
                "total_only_metrics": metric_summary(total_only_components),
                "allocation_only_metrics": metric_summary(allocation_only_components),
                "candidate_metrics": metric_summary(candidate_components),
                "candidate_delta_minus_baseline": delta(candidate_components, baseline_components),
                "candidate_delta_minus_total_only": delta(candidate_components, total_only_components),
                "candidate_delta_minus_allocation_only": delta(candidate_components, allocation_only_components),
                "candidate_bounds": marginal_bounds(joint_candidate),
            },
            "audits": {
                "baseline": audit_baseline,
                "total_only": audit_total_only,
                "allocation_only": audit_allocation_only,
                "candidate": audit_candidate,
            },
            "same_day_predictions_frozen": True,
            "tail_exact_allocation_performed": False,
        })

        all_meta.append(test[["competition_id", "season", "fold"]])
        all_core_meta.append(core_test[["competition_id", "season", "fold"]])
        all_total_two.append(total_two_components)
        all_total_three.append(total_three_components)
        all_joint_baseline.append(baseline_components)
        all_joint_total_only.append(total_only_components)
        all_joint_allocation_only.append(allocation_only_components)
        all_joint_candidate.append(candidate_components)
        all_core_score_baseline.append(core_score_baseline)
        all_core_score_candidate.append(core_score_candidate)
        all_core_result_baseline.append(core_result_baseline)
        all_core_result_candidate.append(core_result_candidate)

        for competition, indexes in test.groupby("competition_id").groups.items():
            indexes = list(indexes)
            baseline_summary = metric_summary(baseline_components.loc[indexes])
            candidate_summary = metric_summary(candidate_components.loc[indexes])
            total_two_summary = metric_summary(total_two_components.loc[indexes])
            total_three_summary = metric_summary(total_three_components.loc[indexes])
            stability_rows.append({
                "competition_id": competition,
                "fold": str(test.fold.iloc[0]),
                "rows": len(indexes),
                **{f"baseline_joint_{key}": value for key, value in baseline_summary.items()},
                **{f"candidate_joint_{key}": value for key, value in candidate_summary.items()},
                **{f"candidate_delta_{key}": candidate_summary[key] - baseline_summary[key] for key in baseline_summary},
                **{f"two_total_{key}": value for key, value in total_two_summary.items()},
                **{f"three_total_{key}": value for key, value in total_three_summary.items()},
                **{f"three_total_delta_{key}": total_three_summary[key] - total_two_summary[key] for key in total_two_summary},
            })

    meta = pd.concat(all_meta)
    core_meta = pd.concat(all_core_meta)
    total_two = pd.concat(all_total_two)
    total_three = pd.concat(all_total_three)
    joint_baseline = pd.concat(all_joint_baseline)
    joint_total_only = pd.concat(all_joint_total_only)
    joint_allocation_only = pd.concat(all_joint_allocation_only)
    joint_candidate = pd.concat(all_joint_candidate)
    core_score_baseline = pd.concat(all_core_score_baseline)
    core_score_candidate = pd.concat(all_core_score_candidate)
    core_result_baseline = pd.concat(all_core_result_baseline)
    core_result_candidate = pd.concat(all_core_result_candidate)

    total_boot = bootstrap(meta, total_three, total_two, config)
    joint_boot = bootstrap(meta, joint_candidate, joint_baseline, config)
    joint_vs_total_only_boot = bootstrap(meta, joint_candidate, joint_total_only, config)
    joint_vs_allocation_only_boot = bootstrap(meta, joint_candidate, joint_allocation_only, config)
    core_score_boot = bootstrap(core_meta, core_score_candidate, core_score_baseline, config)
    core_result_boot = bootstrap(core_meta, core_result_candidate, core_result_baseline, config)

    tolerance = float(config["joint_contract"]["probability_sum_tolerance"])
    audits_pass = (
        audit_maxima["joint_probability_sum_max_residual"] <= tolerance
        and audit_maxima["conditional_score_sum_max_residual"] <= tolerance
        and audit_maxima["total_marginal_max_residual"] <= tolerance
        and audit_maxima["core_mass_max_residual"] <= tolerance
        and audit_maxima["legal_mapping_failures"] == 0
        and audit_maxima["negative_probability_count"] == 0
    )
    total_robust = all(total_boot[metric]["p95"] < 0 for metric in ("logloss", "brier", "rps"))
    joint_robust = all(joint_boot[metric]["p95"] < 0 for metric in ("logloss", "brier"))
    allocation_increment_robust = all(
        joint_vs_total_only_boot[metric]["p95"] < 0 for metric in ("logloss", "brier")
    )
    total_increment_robust = all(
        joint_vs_allocation_only_boot[metric]["p95"] < 0 for metric in ("logloss", "brier")
    )
    core_score_robust = all(
        core_score_boot[metric]["p95"] < 0 for metric in ("logloss", "brier", "rps")
    )
    if audits_pass and joint_robust and allocation_increment_robust and total_increment_robust and core_score_robust:
        status = "PASS_R8_CORE_PLUS_TAIL_BUCKET_JOINT_CHAIN_RESEARCH_SIGNAL"
    elif audits_pass and joint_robust:
        status = "PARTIAL_PASS_R8_JOINT_GAIN_WITH_COMPONENT_INCREMENT_UNCERTAINTY"
    else:
        status = "FAIL_R8_CORE_JOINT_CHAIN_NO_ROBUST_END_TO_END_SIGNAL"

    result = {
        "schema_version": config["schema_version"],
        "status": status,
        "evidence_class": config["evidence_class"],
        "data_identity": identity,
        "split_contract": {
            "complete_seasons": seasons,
            "excluded_incomplete_latest_seasons": excluded,
            "rolling_windows": len(fold_receipts),
            "same_day_freeze_before_update": True,
            "test_outcomes_used_for_model_or_weight_selection": False,
            "repeated_historical_replay_allowed": True,
            "independent_confirmation_claim_allowed": False,
        },
        "state_contract": {
            "exact_score_cells": "all legal H-A cells for T=0..6",
            "tail_state": "one aggregate T>=7 bucket",
            "support_size": len(score_states()),
            "tail_exact_score_allocation": False,
            "full_unified_score_matrix_claim": False,
            "unconditional_1X2_point_probability_available": False,
            "1X2_and_BTTS_bounds_available": True,
        },
        "architecture": {
            "baseline": "R3 two-expert direct-total ensemble plus separate multinomial P(D|T,X) for T=1..6",
            "total_only_ablation": "R7 three-expert total ensemble plus previous separate multinomial score allocation",
            "allocation_only_ablation": "R3 two-expert total ensemble plus R4 shared Beta-Binomial H|T,X",
            "candidate": "R7 three-expert total ensemble plus R4 shared Beta-Binomial H|T,X",
        },
        "folds": fold_receipts,
        "pooled": {
            "test_rows": len(meta),
            "core_test_rows_T0_to_T6": len(core_meta),
            "tail_test_rows_T7plus": int(len(meta) - len(core_meta)),
            "total_two_metrics": metric_summary(total_two),
            "total_three_metrics": metric_summary(total_three),
            "total_three_delta_minus_two": delta(total_three, total_two),
            "total_three_bootstrap_vs_two_90": total_boot,
            "total_three_robust_all_proper_scores": total_robust,
            "joint_baseline_metrics": metric_summary(joint_baseline),
            "joint_total_only_metrics": metric_summary(joint_total_only),
            "joint_allocation_only_metrics": metric_summary(joint_allocation_only),
            "joint_candidate_metrics": metric_summary(joint_candidate),
            "joint_candidate_delta_minus_baseline": delta(joint_candidate, joint_baseline),
            "joint_candidate_bootstrap_vs_baseline_90": joint_boot,
            "joint_candidate_bootstrap_vs_total_only_90": joint_vs_total_only_boot,
            "joint_candidate_bootstrap_vs_allocation_only_90": joint_vs_allocation_only_boot,
            "joint_candidate_robust_vs_baseline": joint_robust,
            "allocation_increment_robust_inside_joint": allocation_increment_robust,
            "total_increment_robust_inside_joint": total_increment_robust,
            "core_conditional_score_baseline_metrics": metric_summary(core_score_baseline),
            "core_conditional_score_candidate_metrics": metric_summary(core_score_candidate),
            "core_conditional_score_delta": delta(core_score_candidate, core_score_baseline),
            "core_conditional_score_bootstrap_90": core_score_boot,
            "core_conditional_score_robust": core_score_robust,
            "core_conditional_result_baseline_metrics": metric_summary(core_result_baseline),
            "core_conditional_result_candidate_metrics": metric_summary(core_result_candidate),
            "core_conditional_result_delta": delta(core_result_candidate, core_result_baseline),
            "core_conditional_result_bootstrap_90": core_result_boot,
        },
        "audits": {
            **audit_maxima,
            "probability_sum_tolerance": tolerance,
            "audits_pass": audits_pass,
            "T_D_parity_failures": 0,
            "nonnegative_integer_score_support": True,
            "tail_exact_allocation_performed": False,
            "market_constraint_fitting_performed": False,
            "optimization_convergence_claim_for_market_coordination": False,
        },
        "stability": {
            "competition_window_count": len(stability_rows),
            "joint_logloss_wins": int(sum(row["candidate_delta_logloss"] < 0 for row in stability_rows)),
            "joint_brier_wins": int(sum(row["candidate_delta_brier"] < 0 for row in stability_rows)),
            "joint_top1_wins": int(sum(row["candidate_delta_top1"] > 0 for row in stability_rows)),
            "total_logloss_wins": int(sum(row["three_total_delta_logloss"] < 0 for row in stability_rows)),
        },
        "ruling": {
            "core_joint_research_component_retained": status.startswith("PASS_"),
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
    total = np.asarray([
        [0.2, 0.2, 0.2, 0.1, 0.1, 0.05, 0.05, 0.1],
        [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.3],
    ])
    score = {total_value: np.full((2, total_value + 1), 1.0 / (total_value + 1)) for total_value in range(7)}
    joint, audit = build_joint(total, score)
    assert joint.shape == (2, 29)
    assert audit["joint_probability_sum_max_residual"] < 1e-12
    assert audit["total_marginal_max_residual"] < 1e-12
    assert score_states()[-1][0] == "7+"


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
        "evidence_class": result["evidence_class"],
        "state_contract": result["state_contract"],
        "pooled": result["pooled"],
        "audits": result["audits"],
        "stability": result["stability"],
        "ruling": result["ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
