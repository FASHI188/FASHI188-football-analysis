#!/usr/bin/env python3
"""R6 nonlinear continuation-ratio boosting challenge for direct total goals.

Research-only viewed historical development replay. Seven binary hazards reconstruct one
coherent distribution for T=0..6,7+. HistGradientBoosting candidates are selected only
on the policy season. Same-day expert weights are frozen until all outcomes in that date
packet are revealed.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

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
    bootstrap,
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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_nonlinear_continuation_boosting_r6.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_nonlinear_continuation_boosting_r6_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_nonlinear_continuation_boosting_r6_stability.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def make_boosting_model(candidate: dict[str, Any], config: dict[str, Any]) -> Pipeline:
    common = config["boosting_common"]
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("model", HistGradientBoostingClassifier(
            learning_rate=float(candidate["learning_rate"]),
            max_leaf_nodes=int(candidate["max_leaf_nodes"]),
            max_iter=int(candidate["max_iter"]),
            l2_regularization=float(candidate["l2_regularization"]),
            min_samples_leaf=int(candidate["min_samples_leaf"]),
            max_bins=int(common["max_bins"]),
            early_stopping=bool(common["early_stopping"]),
            random_state=int(common["random_state"]),
        )),
    ])


def boosting_continuation_models(
    train: pd.DataFrame,
    features: list[str],
    candidate: dict[str, Any],
    config: dict[str, Any],
) -> list[Pipeline]:
    models: list[Pipeline] = []
    for threshold in range(1, 8):
        eligible = train[train.total_class >= threshold - 1]
        target = (eligible.total_class >= threshold).astype(int)
        if target.nunique() != 2:
            raise ResearchError(f"boosting threshold {threshold} lacks both classes")
        model = make_boosting_model(candidate, config)
        model.fit(eligible[features], target)
        models.append(model)
    return models


def boosting_probability(
    models: list[Pipeline],
    frame: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> np.ndarray:
    minimum = float(config["model_contract"]["minimum_probability"])
    q_values: list[np.ndarray] = []
    for threshold, model in enumerate(models, start=1):
        raw = model.predict_proba(frame[features])
        classes = [int(value) for value in model.named_steps["model"].classes_]
        if 1 not in classes:
            raise ResearchError(f"boosting threshold {threshold} missing positive class")
        q_values.append(np.clip(raw[:, classes.index(1)], minimum, 1.0 - minimum))
    q = np.column_stack(q_values)
    output = np.zeros((len(frame), 8), dtype=float)
    survival = np.ones(len(frame), dtype=float)
    for bucket in range(7):
        output[:, bucket] = survival * (1.0 - q[:, bucket])
        survival *= q[:, bucket]
    output[:, 7] = survival
    output /= output.sum(axis=1, keepdims=True)
    residual = float(np.max(np.abs(output.sum(axis=1) - 1.0)))
    if residual > float(config["model_contract"]["probability_sum_tolerance"]):
        raise ResearchError(f"boosting probability sum residual {residual}")
    return output


def select_boosting_candidate(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    receipts: list[dict[str, Any]] = []
    for candidate in config["boosting_candidates"]:
        models = boosting_continuation_models(train, features, candidate, config)
        probability = boosting_probability(models, policy, features, config)
        components = metric_components(policy.total_class.to_numpy(int), probability, classes)
        receipts.append({
            "candidate": dict(candidate),
            "policy_metrics": metric_summary(components),
            "probability_sum_max_residual": float(np.max(np.abs(probability.sum(axis=1) - 1.0))),
        })
    selected = min(
        receipts,
        key=lambda row: (
            row["policy_metrics"]["logloss"],
            row["policy_metrics"]["brier"],
            str(row["candidate"]["name"]),
        ),
    )
    return dict(selected["candidate"]), receipts


def flat_probability(model: Any, frame: pd.DataFrame, features: list[str], config: dict[str, Any]) -> np.ndarray:
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    return align_probability(model, frame[features], classes)


def loss_correlation(
    y: np.ndarray,
    first: np.ndarray,
    second: np.ndarray,
) -> float:
    first_loss = -np.log(np.clip(first[np.arange(len(y)), y], 1e-15, 1.0))
    second_loss = -np.log(np.clip(second[np.arange(len(y)), y], 1e-15, 1.0))
    if np.std(first_loss) <= 0 or np.std(second_loss) <= 0:
        return 1.0
    return float(np.corrcoef(first_loss, second_loss)[0, 1])


def distribution_difference(first: np.ndarray, second: np.ndarray) -> dict[str, float]:
    first = np.clip(first, 1e-15, 1.0)
    second = np.clip(second, 1e-15, 1.0)
    return {
        "mean_absolute_probability_difference": float(np.mean(np.abs(first - second))),
        "mean_row_L1_distance": float(np.mean(np.abs(first - second).sum(axis=1))),
        "mean_KL_first_to_second": float(np.mean(np.sum(first * np.log(first / second), axis=1))),
    }


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
    features = build_features(raw)
    feature_names = select_core_features(features)
    seasons, excluded = complete_seasons(raw, config)
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]

    fold_receipts: list[dict[str, Any]] = []
    all_meta: list[pd.DataFrame] = []
    all_flat: list[pd.DataFrame] = []
    all_linear: list[pd.DataFrame] = []
    all_boosting: list[pd.DataFrame] = []
    all_mixture: list[pd.DataFrame] = []
    stability_rows: list[dict[str, Any]] = []
    selected_counts: Counter[str] = Counter()

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        train = fold[fold.split == "train"]
        policy = fold[fold.split == "policy"].copy()
        fit = fold[fold.split.isin(["train", "policy"])]
        test = fold[fold.split == "test"].copy()

        flat_C, flat_grid = select_C(train, policy, feature_names, "total_class", classes, config)
        linear_C, linear_grid = select_continuation_C(train, policy, feature_names, config)
        boosting_candidate, boosting_grid = select_boosting_candidate(train, policy, feature_names, config)
        selected_counts[str(boosting_candidate["name"])] += 1

        policy_flat_model = make_model(flat_C, config)
        policy_flat_model.fit(train[feature_names], train.total_class)
        policy_flat = flat_probability(policy_flat_model, policy, feature_names, config)
        policy_linear_models = continuation_models(train, feature_names, linear_C, config)
        policy_linear = continuation_probability(policy_linear_models, policy, feature_names, config)
        policy_boosting_models = boosting_continuation_models(train, feature_names, boosting_candidate, config)
        policy_boosting = boosting_probability(policy_boosting_models, policy, feature_names, config)
        eta, policy_final_weights, eta_grid = select_eta(
            policy,
            [policy_flat, policy_linear, policy_boosting],
            config,
        )

        flat_fit_model = make_model(flat_C, config)
        flat_fit_model.fit(fit[feature_names], fit.total_class)
        test_flat = flat_probability(flat_fit_model, test, feature_names, config)
        linear_fit_models = continuation_models(fit, feature_names, linear_C, config)
        test_linear = continuation_probability(linear_fit_models, test, feature_names, config)
        boosting_fit_models = boosting_continuation_models(fit, feature_names, boosting_candidate, config)
        test_boosting = boosting_probability(boosting_fit_models, test, feature_names, config)
        test_mixture, test_final_weights, daily_receipts = daily_mixture(
            test,
            [test_flat, test_linear, test_boosting],
            eta,
            initial_weights=policy_final_weights,
        )

        y = test.total_class.to_numpy(int)
        flat_components = metric_components(y, test_flat, classes)
        linear_components = metric_components(y, test_linear, classes)
        boosting_components = metric_components(y, test_boosting, classes)
        mixture_components = metric_components(y, test_mixture, classes)
        for components in (flat_components, linear_components, boosting_components, mixture_components):
            components.index = test.index

        fold_receipts.append({
            "fold": str(test.fold.iloc[0]),
            "rows": {"train": len(train), "policy": len(policy), "fit": len(fit), "test": len(test)},
            "flat": {"selected_C": flat_C, "policy_grid": flat_grid, "test_metrics": metric_summary(flat_components)},
            "linear_continuation": {
                "selected_C": linear_C,
                "policy_grid": linear_grid,
                "test_metrics": metric_summary(linear_components),
            },
            "nonlinear_boosting": {
                "selected_candidate": boosting_candidate,
                "policy_grid": boosting_grid,
                "test_metrics": metric_summary(boosting_components),
                "delta_minus_flat": {
                    metric: float(boosting_components[metric].mean() - flat_components[metric].mean())
                    for metric in flat_components.columns
                },
                "delta_minus_linear": {
                    metric: float(boosting_components[metric].mean() - linear_components[metric].mean())
                    for metric in flat_components.columns
                },
                "difference_from_linear": distribution_difference(test_boosting, test_linear),
                "row_logloss_correlation_with_linear": loss_correlation(y, test_boosting, test_linear),
            },
            "three_expert_daily_mixture": {
                "selected_eta": eta,
                "policy_eta_grid": eta_grid,
                "policy_final_weights": [float(value) for value in policy_final_weights],
                "test_final_weights": [float(value) for value in test_final_weights],
                "daily_updates": len(daily_receipts),
                "same_day_predictions_frozen": True,
                "test_metrics": metric_summary(mixture_components),
                "delta_minus_flat": {
                    metric: float(mixture_components[metric].mean() - flat_components[metric].mean())
                    for metric in flat_components.columns
                },
            },
        })

        all_meta.append(test[["competition_id", "season", "fold"]])
        all_flat.append(flat_components)
        all_linear.append(linear_components)
        all_boosting.append(boosting_components)
        all_mixture.append(mixture_components)
        for competition, indexes in test.groupby("competition_id").groups.items():
            indexes = list(indexes)
            flat_summary = metric_summary(flat_components.loc[indexes])
            linear_summary = metric_summary(linear_components.loc[indexes])
            boosting_summary = metric_summary(boosting_components.loc[indexes])
            mixture_summary = metric_summary(mixture_components.loc[indexes])
            stability_rows.append({
                "competition_id": competition,
                "fold": str(test.fold.iloc[0]),
                "rows": len(indexes),
                "selected_boosting_candidate": str(boosting_candidate["name"]),
                **{f"flat_{key}": value for key, value in flat_summary.items()},
                **{f"linear_{key}": value for key, value in linear_summary.items()},
                **{f"boosting_{key}": value for key, value in boosting_summary.items()},
                **{f"mixture_{key}": value for key, value in mixture_summary.items()},
                **{f"boosting_delta_flat_{key}": boosting_summary[key] - flat_summary[key] for key in flat_summary},
                **{f"boosting_delta_linear_{key}": boosting_summary[key] - linear_summary[key] for key in flat_summary},
                **{f"mixture_delta_flat_{key}": mixture_summary[key] - flat_summary[key] for key in flat_summary},
            })

    meta = pd.concat(all_meta, ignore_index=True)
    flat = pd.concat(all_flat, ignore_index=True)
    linear = pd.concat(all_linear, ignore_index=True)
    boosting = pd.concat(all_boosting, ignore_index=True)
    mixture = pd.concat(all_mixture, ignore_index=True)

    def delta(model: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, float]:
        return {metric: float(model[metric].mean() - baseline[metric].mean()) for metric in baseline.columns}

    boosting_delta_flat = delta(boosting, flat)
    boosting_delta_linear = delta(boosting, linear)
    mixture_delta_flat = delta(mixture, flat)
    boosting_boot_flat = bootstrap(meta, boosting, flat, ["competition_id", "fold"], config)
    boosting_boot_linear = bootstrap(meta, boosting, linear, ["competition_id", "fold"], config)
    mixture_boot_flat = bootstrap(meta, mixture, flat, ["competition_id", "fold"], config)
    proper = ("logloss", "brier", "rps")
    boosting_robust_flat = all(boosting_boot_flat[metric]["p95"] < 0 for metric in proper)
    boosting_robust_linear = all(boosting_boot_linear[metric]["p95"] < 0 for metric in proper)
    mixture_robust_flat = all(mixture_boot_flat[metric]["p95"] < 0 for metric in proper)
    if boosting_robust_flat and boosting_robust_linear:
        status = "PASS_R6_NONLINEAR_CONTINUATION_ROBUST_OVER_FLAT_AND_LINEAR"
    elif boosting_robust_flat or mixture_robust_flat:
        status = "PARTIAL_PASS_R6_NONLINEAR_OR_MIXTURE_ROBUST_OVER_FLAT"
    else:
        status = "FAIL_R6_NONLINEAR_CONTINUATION_NO_ROBUST_GAIN"

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
            "test_outcomes_used_for_model_selection": False,
            "repeated_historical_replay_allowed": True,
            "independent_confirmation_claim_allowed": False,
        },
        "feature_contract": {
            "feature_count": len(feature_names),
            "features": feature_names,
            "market_features_used": False,
            "web_context_features_used": False,
            "current_match_result_used": False,
        },
        "algorithm_contract": {
            "flat": "multinomial logistic P(T=0..6,7+|X)",
            "linear_continuation": "seven linear binary hazards",
            "nonlinear_continuation": "seven HistGradientBoosting binary hazards selected on policy Log Loss",
            "mixture": "date-frozen exponential Log Loss weighting of flat, linear continuation and nonlinear continuation experts",
            "probability_simplex_preserved": True,
        },
        "folds": fold_receipts,
        "pooled": {
            "test_rows": len(meta),
            "flat_metrics": metric_summary(flat),
            "linear_metrics": metric_summary(linear),
            "boosting_metrics": metric_summary(boosting),
            "boosting_delta_minus_flat": boosting_delta_flat,
            "boosting_delta_minus_linear": boosting_delta_linear,
            "boosting_bootstrap_vs_flat_90": boosting_boot_flat,
            "boosting_bootstrap_vs_linear_90": boosting_boot_linear,
            "mixture_metrics": metric_summary(mixture),
            "mixture_delta_minus_flat": mixture_delta_flat,
            "mixture_bootstrap_vs_flat_90": mixture_boot_flat,
            "selected_boosting_candidate_counts": dict(selected_counts),
            "boosting_robust_over_flat": boosting_robust_flat,
            "boosting_robust_over_linear": boosting_robust_linear,
            "mixture_robust_over_flat": mixture_robust_flat,
        },
        "stability": {
            "competition_window_count": len(stability_rows),
            "boosting_logloss_wins_vs_flat": int(sum(row["boosting_delta_flat_logloss"] < 0 for row in stability_rows)),
            "boosting_logloss_wins_vs_linear": int(sum(row["boosting_delta_linear_logloss"] < 0 for row in stability_rows)),
            "boosting_top1_wins_vs_flat": int(sum(row["boosting_delta_flat_top1"] > 0 for row in stability_rows)),
            "mixture_logloss_wins_vs_flat": int(sum(row["mixture_delta_flat_logloss"] < 0 for row in stability_rows)),
        },
        "ruling": {
            "historical_nonlinear_component_retained": status.startswith("PASS_") or status.startswith("PARTIAL_PASS_"),
            "formal_weight": 0,
            "promotion": False,
            "strict_PIT_market_context_rows": 0,
            "unified_score_matrix_allowed": False,
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
    first = np.asarray([[0.6, 0.4], [0.2, 0.8]])
    second = np.asarray([[0.5, 0.5], [0.3, 0.7]])
    difference = distribution_difference(first, second)
    assert difference["mean_row_L1_distance"] > 0
    y = np.asarray([0, 1])
    correlation = loss_correlation(y, first, second)
    assert np.isfinite(correlation)


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
        "pooled": result["pooled"],
        "stability": result["stability"],
        "ruling": result["ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
