#!/usr/bin/env python3
"""Viewed historical prequential algorithm challenge for V5.1.

Every match packet is formed only from information available before that match.
Same-day packets are frozen before any same-day result updates. Historical labels may
be replayed repeatedly for development, but no independent-confirmation claim is made.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from v510_historical_structure_features_r1 import (
    ResearchError, assign_fold, audit_data_identity, build_features,
    complete_seasons, select_core_features,
)
from v510_historical_structure_model_r1 import (
    align_probability, bootstrap, make_model, metric_components, metric_summary, select_C,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_prequential_algorithm_challenge_r3.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_prequential_algorithm_challenge_r3_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_prequential_algorithm_challenge_r3_stability.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def continuation_models(train: pd.DataFrame, features: list[str], C: float, config: dict[str, Any]) -> list[Any]:
    models = []
    for threshold in range(1, 8):
        eligible = train[train.total_class >= threshold - 1]
        target = (eligible.total_class >= threshold).astype(int)
        if target.nunique() != 2:
            raise ResearchError(f"continuation threshold {threshold} lacks both classes")
        model = make_model(C, config)
        model.fit(eligible[features], target)
        models.append(model)
    return models


def continuation_probability(models: list[Any], frame: pd.DataFrame, features: list[str], config: dict[str, Any]) -> np.ndarray:
    minimum = float(config["model_contract"]["minimum_probability"])
    q_values = []
    for threshold, model in enumerate(models, start=1):
        raw = model.predict_proba(frame[features])
        fitted = [int(value) for value in model.named_steps["model"].classes_]
        if 1 not in fitted:
            raise ResearchError(f"continuation threshold {threshold} missing positive class")
        q_values.append(np.clip(raw[:, fitted.index(1)], minimum, 1.0 - minimum))
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
        raise ResearchError(f"continuation probability sum residual {residual}")
    return output


def select_continuation_C(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[float, list[dict[str, Any]]]:
    receipts = []
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    for C in config["model_contract"]["regularization_C_grid"]:
        models = continuation_models(train, features, float(C), config)
        probability = continuation_probability(models, policy, features, config)
        components = metric_components(policy.total_class.to_numpy(int), probability, classes)
        receipts.append({
            "C": float(C),
            "policy_logloss": float(components.logloss.mean()),
            "policy_brier": float(components.brier.mean()),
            "policy_rps": float(components.rps.mean()),
            "probability_sum_max_residual": float(np.max(np.abs(probability.sum(axis=1) - 1.0))),
        })
    best = min(receipts, key=lambda row: (row["policy_logloss"], row["C"]))
    return float(best["C"]), receipts


def flat_probability(model: Any, frame: pd.DataFrame, features: list[str], config: dict[str, Any]) -> np.ndarray:
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    return align_probability(model, frame[features], classes)


def daily_mixture(
    frame: pd.DataFrame,
    expert_probabilities: list[np.ndarray],
    eta: float,
    initial_weights: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    if len(expert_probabilities) < 2:
        raise ResearchError("daily mixture requires at least two experts")
    classes = expert_probabilities[0].shape[1]
    for probability in expert_probabilities:
        if probability.shape != (len(frame), classes):
            raise ResearchError("expert probability shape mismatch")
    weights = (
        np.full(len(expert_probabilities), 1.0 / len(expert_probabilities), dtype=float)
        if initial_weights is None
        else np.asarray(initial_weights, dtype=float).copy()
    )
    weights /= weights.sum()
    output = np.zeros((len(frame), classes), dtype=float)
    receipts: list[dict[str, Any]] = []
    ordered = frame.reset_index().rename(columns={"index": "original_index"})
    minimum = 1e-15
    for date_key, day in ordered.groupby("date_key", sort=True):
        positions = day.index.to_numpy(int)
        start = weights.copy()
        output[positions] = sum(start[idx] * expert_probabilities[idx][positions] for idx in range(len(weights)))
        y = day.total_class.to_numpy(int)
        daily_loss = np.asarray([
            float(-np.log(np.clip(probability[positions, y], minimum, 1.0)).mean())
            for probability in expert_probabilities
        ])
        shifted = daily_loss - daily_loss.min()
        weights *= np.exp(-eta * shifted)
        weights = np.maximum(weights, 1e-15)
        weights /= weights.sum()
        receipts.append({
            "date_key": str(date_key),
            "rows": int(len(positions)),
            "start_weights": [float(value) for value in start],
            "expert_mean_logloss": [float(value) for value in daily_loss],
            "end_weights": [float(value) for value in weights],
        })
    return output, weights, receipts


def select_eta(
    policy: pd.DataFrame,
    expert_probabilities: list[np.ndarray],
    config: dict[str, Any],
) -> tuple[float, np.ndarray, list[dict[str, Any]]]:
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    receipts = []
    for eta in config["model_contract"]["daily_exponential_weight_eta_grid"]:
        probability, final_weights, _ = daily_mixture(policy, expert_probabilities, float(eta))
        components = metric_components(policy.total_class.to_numpy(int), probability, classes)
        receipts.append({
            "eta": float(eta),
            "policy_metrics": metric_summary(components),
            "final_weights": [float(value) for value in final_weights],
        })
    best = min(receipts, key=lambda row: (row["policy_metrics"]["logloss"], row["eta"]))
    return float(best["eta"]), np.asarray(best["final_weights"], dtype=float), receipts


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

    fold_receipts = []
    all_meta = []
    all_flat = []
    all_continuation = []
    all_mixture = []
    stability_rows = []

    for test_position in [int(value) for value in config["replay_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        train = fold[fold.split == "train"]
        policy = fold[fold.split == "policy"].copy()
        fit = fold[fold.split.isin(["train", "policy"])]
        test = fold[fold.split == "test"].copy()

        flat_C, flat_grid = select_C(train, policy, feature_names, "total_class", classes, config)
        continuation_C, continuation_grid = select_continuation_C(train, policy, feature_names, config)

        flat_train_model = make_model(flat_C, config)
        flat_train_model.fit(train[feature_names], train.total_class)
        policy_flat = flat_probability(flat_train_model, policy, feature_names, config)
        continuation_train_models = continuation_models(train, feature_names, continuation_C, config)
        policy_continuation = continuation_probability(continuation_train_models, policy, feature_names, config)
        eta, policy_final_weights, eta_grid = select_eta(policy, [policy_flat, policy_continuation], config)

        flat_fit_model = make_model(flat_C, config)
        flat_fit_model.fit(fit[feature_names], fit.total_class)
        test_flat_probability = flat_probability(flat_fit_model, test, feature_names, config)
        continuation_fit_models = continuation_models(fit, feature_names, continuation_C, config)
        test_continuation_probability = continuation_probability(
            continuation_fit_models, test, feature_names, config
        )
        test_mixture_probability, final_weights, daily_receipts = daily_mixture(
            test,
            [test_flat_probability, test_continuation_probability],
            eta,
            initial_weights=policy_final_weights,
        )

        y = test.total_class.to_numpy(int)
        flat_components = metric_components(y, test_flat_probability, classes)
        continuation_components = metric_components(y, test_continuation_probability, classes)
        mixture_components = metric_components(y, test_mixture_probability, classes)
        for components in (flat_components, continuation_components, mixture_components):
            components.index = test.index

        fold_receipts.append({
            "fold": test.fold.iloc[0],
            "rows": {"train": len(train), "policy": len(policy), "fit": len(fit), "test": len(test)},
            "flat": {
                "selected_C": flat_C,
                "policy_grid": flat_grid,
                "test_metrics": metric_summary(flat_components),
            },
            "continuation_ratio": {
                "selected_C": continuation_C,
                "policy_grid": continuation_grid,
                "test_metrics": metric_summary(continuation_components),
                "delta_minus_flat": {
                    metric: float(continuation_components[metric].mean() - flat_components[metric].mean())
                    for metric in flat_components.columns
                },
            },
            "daily_expert_mixture": {
                "selected_eta": eta,
                "policy_eta_grid": eta_grid,
                "policy_final_weights": [float(value) for value in policy_final_weights],
                "test_final_weights": [float(value) for value in final_weights],
                "test_metrics": metric_summary(mixture_components),
                "delta_minus_flat": {
                    metric: float(mixture_components[metric].mean() - flat_components[metric].mean())
                    for metric in flat_components.columns
                },
                "daily_updates": len(daily_receipts),
                "same_day_predictions_frozen": True,
            },
        })

        all_meta.append(test[["competition_id", "season", "fold"]])
        all_flat.append(flat_components)
        all_continuation.append(continuation_components)
        all_mixture.append(mixture_components)

        for competition, indexes in test.groupby("competition_id").groups.items():
            indexes = list(indexes)
            flat_summary = metric_summary(flat_components.loc[indexes])
            continuation_summary = metric_summary(continuation_components.loc[indexes])
            mixture_summary = metric_summary(mixture_components.loc[indexes])
            stability_rows.append({
                "competition_id": competition,
                "fold": test.fold.iloc[0],
                "rows": len(indexes),
                **{f"flat_{key}": value for key, value in flat_summary.items()},
                **{f"continuation_{key}": value for key, value in continuation_summary.items()},
                **{f"mixture_{key}": value for key, value in mixture_summary.items()},
                **{f"continuation_delta_{key}": continuation_summary[key] - flat_summary[key] for key in flat_summary},
                **{f"mixture_delta_{key}": mixture_summary[key] - flat_summary[key] for key in flat_summary},
            })

    meta = pd.concat(all_meta, ignore_index=True)
    flat = pd.concat(all_flat, ignore_index=True)
    continuation = pd.concat(all_continuation, ignore_index=True)
    mixture = pd.concat(all_mixture, ignore_index=True)
    continuation_delta = {metric: float(continuation[metric].mean() - flat[metric].mean()) for metric in flat.columns}
    mixture_delta = {metric: float(mixture[metric].mean() - flat[metric].mean()) for metric in flat.columns}
    continuation_bootstrap = bootstrap(meta, continuation, flat, ["competition_id", "fold"], config)
    mixture_bootstrap = bootstrap(meta, mixture, flat, ["competition_id", "fold"], config)
    continuation_win = all(continuation_delta[metric] < 0 for metric in ("logloss", "brier", "rps"))
    mixture_win = all(mixture_delta[metric] < 0 for metric in ("logloss", "brier", "rps"))

    result = {
        "schema_version": config["schema_version"],
        "status": (
            "PASS_VIEWED_HISTORICAL_ALGORITHM_DEVELOPMENT_SIGNAL"
            if continuation_win or mixture_win
            else "NO_MATERIAL_VIEWED_HISTORICAL_ALGORITHM_GAIN"
        ),
        "evidence_class": config["evidence_class"],
        "data_identity": identity,
        "split_contract": {
            "complete_seasons": seasons,
            "excluded_incomplete_latest_seasons": excluded,
            "rolling_windows": len(fold_receipts),
            "prediction_before_result": True,
            "same_day_freeze_before_update": True,
            "repeated_historical_replay_allowed": True,
            "independent_confirmation_claim": False,
        },
        "feature_contract": {
            "feature_count": len(feature_names),
            "features": feature_names,
            "current_match_result_used": False,
            "market_features_used": False,
            "web_context_features_used": False,
        },
        "algorithm_contract": {
            "flat": "multinomial logistic P(T=0..6,7+|X)",
            "continuation_ratio": "seven binary continuation hazards q_k=P(T>=k|T>=k-1,X) reconstructed into one coherent eight-bucket distribution",
            "daily_expert_mixture": "date-frozen exponential log-loss expert weighting; weights update only after all same-date outcomes are revealed",
        },
        "folds": fold_receipts,
        "pooled": {
            "test_rows": len(meta),
            "flat_metrics": metric_summary(flat),
            "continuation_metrics": metric_summary(continuation),
            "continuation_delta_minus_flat": continuation_delta,
            "continuation_bootstrap_competition_window_90": continuation_bootstrap,
            "continuation_all_proper_scores_improve": continuation_win,
            "mixture_metrics": metric_summary(mixture),
            "mixture_delta_minus_flat": mixture_delta,
            "mixture_bootstrap_competition_window_90": mixture_bootstrap,
            "mixture_all_proper_scores_improve": mixture_win,
        },
        "stability": {
            "competition_window_count": len(stability_rows),
            "continuation_logloss_wins": sum(row["continuation_delta_logloss"] < 0 for row in stability_rows),
            "mixture_logloss_wins": sum(row["mixture_delta_logloss"] < 0 for row in stability_rows),
        },
        "ruling": {
            "formal_weight": 0,
            "promotion": False,
            "unified_score_matrix_allowed": False,
            "current_match_probabilities_allowed": False,
            "exact_score_allowed": False,
            "EV_allowed": False,
            "next_algorithm_priority": [
                "bounded Beta-Binomial H|T,X across all totals",
                "normalization-aware multiclass calibration",
                "nonlinear continuation-ratio boosting",
                "larger online expert mixture",
            ],
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(stability_path, stability_rows)
    return result


def self_test() -> None:
    frame = pd.DataFrame({"date_key": ["2025-01-01", "2025-01-01", "2025-01-02"], "total_class": [0, 1, 1]})
    first = np.asarray([[0.8, 0.2], [0.4, 0.6], [0.5, 0.5]])
    second = np.asarray([[0.6, 0.4], [0.7, 0.3], [0.2, 0.8]])
    probability, weights, receipts = daily_mixture(frame, [first, second], 0.05)
    assert probability.shape == (3, 2)
    assert np.max(np.abs(probability.sum(axis=1) - 1.0)) < 1e-12
    assert len(receipts) == 2 and abs(weights.sum() - 1.0) < 1e-12


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
    print(json.dumps({"status": result["status"], "pooled": result["pooled"], "ruling": result["ruling"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
