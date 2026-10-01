#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from v510_historical_structure_features_r1 import ResearchError, TOTAL_CLASSES

def make_model(C: float, config: dict[str, Any]) -> Pipeline:
    contract = config["model_contract"]
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scaler", StandardScaler()),
        ("model", LogisticRegression(
            C=C,
            solver=str(contract["solver"]),
            max_iter=int(contract["max_iter"]),
            tol=float(contract["tolerance"]),
        )),
    ])


def align_probability(model: Pipeline, X: pd.DataFrame, classes: list[int]) -> np.ndarray:
    raw = model.predict_proba(X)
    fitted = [int(x) for x in model.named_steps["model"].classes_]
    positions = {value: idx for idx, value in enumerate(classes)}
    output = np.zeros((len(X), len(classes)), dtype=float)
    for idx, value in enumerate(fitted):
        if value in positions:
            output[:, positions[value]] = raw[:, idx]
    row_sums = output.sum(axis=1, keepdims=True)
    if np.any(row_sums <= 0):
        raise ResearchError("probability row with non-positive mass")
    output /= row_sums
    return output


def metric_components(y: np.ndarray, probabilities: np.ndarray, classes: list[int]) -> pd.DataFrame:
    probabilities = np.clip(probabilities, 1e-15, 1.0)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    positions = {value: idx for idx, value in enumerate(classes)}
    y_index = np.asarray([positions[int(value)] for value in y], dtype=int)
    one_hot = np.zeros_like(probabilities)
    one_hot[np.arange(len(y)), y_index] = 1.0
    logloss = -np.log(probabilities[np.arange(len(y)), y_index])
    brier = ((probabilities - one_hot) ** 2).sum(axis=1)
    if len(classes) > 1:
        rps = (
            (np.cumsum(probabilities, axis=1)[:, :-1] - np.cumsum(one_hot, axis=1)[:, :-1]) ** 2
        ).sum(axis=1) / (len(classes) - 1)
    else:
        rps = np.zeros(len(y))
    order = np.argsort(-probabilities, axis=1)
    top1 = (order[:, 0] == y_index).astype(float)
    top2 = np.asarray([y_index[i] in order[i, : min(2, len(classes))] for i in range(len(y))], dtype=float)
    return pd.DataFrame({"logloss": logloss, "brier": brier, "rps": rps, "top1": top1, "top2": top2})


def metric_summary(components: pd.DataFrame) -> dict[str, float]:
    return {name: float(components[name].mean()) for name in components.columns}


def empirical_probability(
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: str,
    classes: list[int],
    alpha: float,
) -> np.ndarray:
    pooled = Counter(int(value) for value in train[target])
    competition_counts = {
        competition: Counter(int(value) for value in matches[target])
        for competition, matches in train.groupby("competition_id")
    }
    output = np.zeros((len(test), len(classes)), dtype=float)
    for idx, competition in enumerate(test["competition_id"]):
        counts = competition_counts.get(competition, pooled)
        values = np.asarray([counts[value] + alpha for value in classes], dtype=float)
        output[idx] = values / values.sum()
    return output


def calibration(y: np.ndarray, probabilities: np.ndarray, classes: list[int]) -> dict[str, Any]:
    observed = np.asarray([(y == value).mean() for value in classes])
    predicted = probabilities.mean(axis=0)
    residual = predicted - observed
    return {
        "observed": {str(value): float(observed[idx]) for idx, value in enumerate(classes)},
        "predicted": {str(value): float(predicted[idx]) for idx, value in enumerate(classes)},
        "mean_absolute_bucket_residual": float(np.mean(np.abs(residual))),
        "max_absolute_bucket_residual": float(np.max(np.abs(residual))),
    }


def select_C(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    features: list[str],
    target: str,
    classes: list[int],
    config: dict[str, Any],
) -> tuple[float, list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    for C in config["model_contract"]["regularization_C_grid"]:
        model = make_model(float(C), config)
        model.fit(train[features], train[target])
        probabilities = align_probability(model, policy[features], classes)
        receipts.append({
            "C": float(C),
            "policy_logloss": float(metric_components(policy[target].to_numpy(int), probabilities, classes)["logloss"].mean()),
            "max_solver_iterations": int(np.max(model.named_steps["model"].n_iter_)),
            "probability_sum_max_residual": float(np.max(np.abs(probabilities.sum(axis=1) - 1.0))),
        })
    best = min(receipts, key=lambda row: (row["policy_logloss"], row["C"]))
    return float(best["C"]), receipts


def fit_direct_total(fold: pd.DataFrame, features: list[str], config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    train = fold[fold.split == "train"]
    policy = fold[fold.split == "policy"]
    fit = fold[fold.split.isin(["train", "policy"])]
    test = fold[fold.split == "test"].copy()
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    selected_C, policy_receipts = select_C(train, policy, features, "total_class", classes, config)
    model = make_model(selected_C, config)
    model.fit(fit[features], fit.total_class)
    model_probability = align_probability(model, test[features], classes)
    baseline_probability = empirical_probability(
        fit, test, "total_class", classes, float(config["model_contract"]["competition_empirical_alpha"])
    )
    y = test.total_class.to_numpy(int)
    model_components = metric_components(y, model_probability, classes)
    baseline_components = metric_components(y, baseline_probability, classes)
    model_components.index = test.index
    baseline_components.index = test.index
    tail_position = classes.index(7)
    tail_truth = (y == 7).astype(float)
    receipt = {
        "selected_C": selected_C,
        "policy_grid": policy_receipts,
        "test_rows": len(test),
        "model_metrics": metric_summary(model_components),
        "baseline_metrics": metric_summary(baseline_components),
        "delta_model_minus_baseline": {
            name: float(model_components[name].mean() - baseline_components[name].mean())
            for name in model_components.columns
        },
        "model_calibration": calibration(y, model_probability, classes),
        "baseline_calibration": calibration(y, baseline_probability, classes),
        "probability_sum_max_residual": float(np.max(np.abs(model_probability.sum(axis=1) - 1.0))),
        "tail7": {
            "test_rows": int(tail_truth.sum()),
            "observed_rate": float(tail_truth.mean()),
            "model_mean_probability": float(model_probability[:, tail_position].mean()),
            "baseline_mean_probability": float(baseline_probability[:, tail_position].mean()),
            "model_binary_brier": float(np.mean((model_probability[:, tail_position] - tail_truth) ** 2)),
            "baseline_binary_brier": float(np.mean((baseline_probability[:, tail_position] - tail_truth) ** 2)),
            "model_top1_recall": (
                float(np.mean(np.argmax(model_probability[y == 7], axis=1) == tail_position))
                if np.any(y == 7) else None
            ),
        },
    }
    return test, model_components, baseline_components, receipt


def fit_conditional_D(fold: pd.DataFrame, features: list[str], config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    all_model: list[pd.DataFrame] = []
    all_baseline: list[pd.DataFrame] = []
    all_meta: list[pd.DataFrame] = []
    per_total: dict[str, Any] = {}
    contract = config["model_contract"]

    for total in TOTAL_CLASSES:
        train = fold[(fold.split == "train") & (fold.total_class == total)]
        policy = fold[(fold.split == "policy") & (fold.total_class == total)]
        fit = fold[(fold.split.isin(["train", "policy"])) & (fold.total_class == total)]
        test = fold[(fold.split == "test") & (fold.total_class == total)].copy()
        classes = (
            list(range(-total, total + 1, 2))
            if total < 7
            else list(range(int(contract["conditional_tail_support_min"]), int(contract["conditional_tail_support_max"]) + 1))
        )
        unseen = sorted(set(int(value) for value in test.goal_difference.unique()) - set(classes))
        if unseen:
            raise ResearchError(f"conditional support misses test D for T={total}: {unseen}")
        y = test.goal_difference.to_numpy(int)
        policy_receipts: list[dict[str, Any]] = []

        if len(classes) == 1:
            status = "DETERMINISTIC"
            selected_C = None
            model_probability = np.ones((len(test), 1), dtype=float)
            baseline_probability = np.ones((len(test), 1), dtype=float)
        elif total == 7:
            status = "EMPIRICAL_TAIL_FIXED_SUPPORT_NO_EXACT_TOTAL"
            selected_C = None
            baseline_probability = empirical_probability(
                fit, test, "goal_difference", classes, float(contract["tail_empirical_alpha"])
            )
            model_probability = baseline_probability.copy()
        else:
            status = "LOGISTIC_CHALLENGER"
            selected_C, policy_receipts = select_C(
                train, policy, features, "goal_difference", classes, config
            )
            model = make_model(selected_C, config)
            model.fit(fit[features], fit.goal_difference)
            model_probability = align_probability(model, test[features], classes)
            baseline_probability = empirical_probability(
                fit, test, "goal_difference", classes, float(contract["competition_empirical_alpha"])
            )

        model_components = metric_components(y, model_probability, classes)
        baseline_components = metric_components(y, baseline_probability, classes)
        model_components.index = test.index
        baseline_components.index = test.index
        all_model.append(model_components)
        all_baseline.append(baseline_components)
        all_meta.append(test[["competition_id", "season", "fold"]])
        per_total[str(total)] = {
            "status": status,
            "support": classes,
            "train_rows": len(train),
            "policy_rows": len(policy),
            "test_rows": len(test),
            "selected_C": selected_C,
            "policy_grid": policy_receipts,
            "model_metrics": metric_summary(model_components),
            "baseline_metrics": metric_summary(baseline_components),
            "delta_model_minus_baseline": {
                name: float(model_components[name].mean() - baseline_components[name].mean())
                for name in model_components.columns
            },
            "probability_sum_max_residual": float(np.max(np.abs(model_probability.sum(axis=1) - 1.0))) if len(test) else 0.0,
        }

    model_components = pd.concat(all_model).sort_index()
    baseline_components = pd.concat(all_baseline).sort_index()
    meta = pd.concat(all_meta).sort_index()
    receipt = {
        "test_rows": len(meta),
        "model_metrics": metric_summary(model_components),
        "baseline_metrics": metric_summary(baseline_components),
        "delta_model_minus_baseline": {
            name: float(model_components[name].mean() - baseline_components[name].mean())
            for name in model_components.columns
        },
        "per_total": per_total,
        "tail7_mapping_status": "P(D|T=7+) EMPIRICAL ONLY; EXACT TOTAL AND SCORE MAPPING UNAVAILABLE",
    }
    return meta, model_components, baseline_components, receipt


def bootstrap(
    meta: pd.DataFrame,
    model_components: pd.DataFrame,
    baseline_components: pd.DataFrame,
    cluster_columns: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    boot = config["bootstrap"]
    rng = np.random.default_rng(int(boot["seed"]))
    keys = meta[cluster_columns].astype(str).agg("|".join, axis=1)
    groups = sorted(keys.unique())
    counts: list[int] = []
    model_sums = {metric: [] for metric in model_components.columns}
    baseline_sums = {metric: [] for metric in model_components.columns}
    for group in groups:
        mask = keys.values == group
        counts.append(int(mask.sum()))
        for metric in model_components.columns:
            model_sums[metric].append(float(model_components.loc[mask, metric].sum()))
            baseline_sums[metric].append(float(baseline_components.loc[mask, metric].sum()))
    count_array = np.asarray(counts, dtype=float)
    sample_count = int(boot["samples"])
    picks = rng.integers(0, len(groups), size=(sample_count, len(groups)))
    denominator = count_array[picks].sum(axis=1)
    q_low, q_high = [float(value) for value in boot["interval"]]
    result: dict[str, Any] = {}
    for metric in model_components.columns:
        model_array = np.asarray(model_sums[metric])
        baseline_array = np.asarray(baseline_sums[metric])
        delta = (model_array[picks].sum(axis=1) - baseline_array[picks].sum(axis=1)) / denominator
        better = delta < 0 if metric in {"logloss", "brier", "rps"} else delta > 0
        result[metric] = {
            "mean_delta_model_minus_baseline": float(delta.mean()),
            "p05": float(np.quantile(delta, q_low)),
            "p95": float(np.quantile(delta, q_high)),
            "probability_model_better": float(better.mean()),
        }
    return result
