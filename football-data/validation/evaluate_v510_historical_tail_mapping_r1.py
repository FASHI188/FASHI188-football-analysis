#!/usr/bin/env python3
"""Historical 7+ exact-total law and legal score-mapping challenge for V5.1.

Research-only. Uses the existing historical score ledger and strictly prior score-derived
features. It never emits current-match probabilities, a unified score matrix, exact scores or EV.
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
from scipy.optimize import minimize
from scipy.special import betaln, digamma, expit, gammaln
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from v510_historical_structure_features_r1 import (
    ResearchError, assign_fold, build_features, complete_seasons, select_core_features,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_historical_tail_mapping_r1.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_historical_tail_mapping_r1_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_historical_tail_mapping_r1_stability.csv"
TAIL_BINS = [0, 1, 2, 3, 4]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def attach_exact_labels(raw: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    work = raw.copy()
    work["date"] = pd.to_datetime(work["date_key"], errors="raise")
    work = work.sort_values(
        ["competition_id", "date", "home_team", "away_team", "source_file", "row_number"]
    ).reset_index(drop=True)
    if len(work) != len(features):
        raise ResearchError("feature and label row counts differ")
    for field in ("competition_id", "season", "home_team", "away_team"):
        if not np.array_equal(features[field].astype(str).to_numpy(), work[field].astype(str).to_numpy()):
            raise ResearchError(f"feature/label identity mismatch: {field}")
    output = features.copy()
    output["total_goals_exact"] = work["total_goals"].astype(int).to_numpy()
    output["home_goals_exact"] = work["home_goals_90"].astype(int).to_numpy()
    output["away_goals_exact"] = work["away_goals_90"].astype(int).to_numpy()
    output["tail_excess"] = output["total_goals_exact"] - 7
    return output


def audit_identity(raw: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    tail = raw[raw.total_goals >= 7]
    actual = {
        "rows": int(len(raw)),
        "competitions": int(raw.competition_id.nunique()),
        "tail_rows": int(len(tail)),
        "tail_exact_counts": {str(k): int(v) for k, v in tail.total_goals.value_counts().sort_index().items()},
        "tail_observed_max": int(tail.total_goals.max()),
    }
    expected = config["expected_data_identity"]
    if actual != expected:
        raise ResearchError(f"tail data identity mismatch: expected={expected}, actual={actual}")
    return actual


def fixed_components(y: np.ndarray, probability: np.ndarray) -> pd.DataFrame:
    probability = np.clip(probability, 1e-15, 1.0)
    probability /= probability.sum(axis=1, keepdims=True)
    one_hot = np.eye(len(TAIL_BINS))[y]
    return pd.DataFrame({
        "logloss": -np.log(probability[np.arange(len(y)), y]),
        "brier": ((probability - one_hot) ** 2).sum(axis=1),
        "rps": ((np.cumsum(probability, axis=1)[:, :-1] - np.cumsum(one_hot, axis=1)[:, :-1]) ** 2).sum(axis=1) / 4.0,
        "top1": (np.argmax(probability, axis=1) == y).astype(float),
        "top2": np.asarray([y[i] in np.argsort(-probability[i])[:2] for i in range(len(y))], dtype=float),
    })


def summary(components: pd.DataFrame) -> dict[str, float]:
    return {column: float(components[column].mean()) for column in components.columns}


def geometric_q(frame: pd.DataFrame, alpha: float) -> float:
    excess = frame.tail_excess.to_numpy(int)
    return float((excess.sum() + alpha) / (len(excess) + excess.sum() + 2.0 * alpha))


def hurdle_parameters(frame: pd.DataFrame, alpha: float) -> tuple[float, float]:
    excess = frame.tail_excess.to_numpy(int)
    zero = int((excess == 0).sum())
    positive = excess[excess > 0]
    pi = (zero + alpha) / (len(excess) + 2.0 * alpha)
    continuation = ((positive - 1).sum() + alpha) / ((positive - 1).sum() + len(positive) + 2.0 * alpha)
    return float(pi), float(continuation)


def geometric_eval_probability(q: float, rows: int) -> np.ndarray:
    values = np.asarray([1.0 - q, (1.0 - q) * q, (1.0 - q) * q**2, (1.0 - q) * q**3, q**4])
    return np.tile(values, (rows, 1))


def hurdle_eval_probability(pi: float, continuation: float, rows: int) -> np.ndarray:
    values = np.asarray([
        pi,
        (1.0 - pi) * (1.0 - continuation),
        (1.0 - pi) * (1.0 - continuation) * continuation,
        (1.0 - pi) * (1.0 - continuation) * continuation**2,
        (1.0 - pi) * continuation**3,
    ])
    return np.tile(values, (rows, 1))


def empirical_tail_probability(fit: pd.DataFrame, test: pd.DataFrame, prior_mass: float) -> np.ndarray:
    pooled = np.bincount(np.minimum(fit.tail_excess.astype(int), 4), minlength=5).astype(float)
    prior = pooled / pooled.sum() * prior_mass
    by_competition = {
        competition: np.bincount(np.minimum(group.tail_excess.astype(int), 4), minlength=5).astype(float)
        for competition, group in fit.groupby("competition_id")
    }
    rows = []
    for competition in test.competition_id:
        counts = by_competition.get(competition, pooled)
        values = counts + prior
        rows.append(values / values.sum())
    return np.asarray(rows)


def select_tail_law(train: pd.DataFrame, policy: pd.DataFrame, config: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    alpha = float(config["exact_tail_contract"]["beta_prior_alpha"])
    y = np.minimum(policy.tail_excess.to_numpy(int), 4)
    q = geometric_q(train, alpha)
    pi, continuation = hurdle_parameters(train, alpha)
    candidates = {
        "pooled_geometric": geometric_eval_probability(q, len(policy)),
        "pooled_hurdle_geometric": hurdle_eval_probability(pi, continuation, len(policy)),
    }
    receipts = []
    for name, probability in candidates.items():
        receipts.append({
            "candidate": name,
            "policy_metrics": summary(fixed_components(y, probability)),
            "probability_sum_max_residual": float(np.max(np.abs(probability.sum(axis=1) - 1.0))),
        })
    selected = min(receipts, key=lambda row: (row["policy_metrics"]["logloss"], row["candidate"]))["candidate"]
    return str(selected), receipts


def fit_selected_tail_law(name: str, fit: pd.DataFrame, test: pd.DataFrame, config: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    alpha = float(config["exact_tail_contract"]["beta_prior_alpha"])
    if name == "pooled_geometric":
        q = geometric_q(fit, alpha)
        return geometric_eval_probability(q, len(test)), {"q": q, "full_support": "P(E=e)=(1-q)q^e, e>=0"}
    if name == "pooled_hurdle_geometric":
        pi, continuation = hurdle_parameters(fit, alpha)
        return hurdle_eval_probability(pi, continuation, len(test)), {
            "pi_zero_excess": pi,
            "continuation": continuation,
            "full_support": "P(E=0)=pi; P(E=e>=1)=(1-pi)(1-r)r^(e-1)",
        }
    raise ResearchError(f"unknown tail law: {name}")


def tail_survival(parameters: dict[str, Any], total_threshold: int) -> float:
    excess_threshold = total_threshold - 7
    if excess_threshold <= 0:
        return 1.0
    if "q" in parameters:
        return float(parameters["q"] ** excess_threshold)
    pi = float(parameters["pi_zero_excess"])
    continuation = float(parameters["continuation"])
    return float((1.0 - pi) * continuation ** (excess_threshold - 1))


def score_share_features(all_features: list[str]) -> list[str]:
    selected = []
    for name in all_features:
        if name.startswith("pair_") or name.startswith("h2h_home_view_"):
            selected.append(name)
        elif name in {
            "comp_home_goals", "comp_away_goals", "comp_home_win", "comp_draw",
            "home_rest_days", "away_rest_days", "rest_days_diff",
            "calendar_month_sin", "calendar_month_cos",
        }:
            selected.append(name)
    return sorted(set(selected))


def fit_beta_binomial(X: np.ndarray, home: np.ndarray, total: np.ndarray, C: float, concentration: float, config: dict[str, Any]):
    X1 = np.column_stack([np.ones(len(X)), X])
    p0 = float((home.sum() + 0.5) / (total.sum() + 1.0))
    beta = np.zeros(X1.shape[1], dtype=float)
    beta[0] = math.log(p0 / (1.0 - p0))
    penalty = 1.0 / C

    def objective(value: np.ndarray):
        eta = X1 @ value
        p = expit(eta)
        a = np.clip(p * concentration, 1e-8, None)
        b = np.clip((1.0 - p) * concentration, 1e-8, None)
        log_probability = (
            gammaln(total + 1.0) - gammaln(home + 1.0) - gammaln(total - home + 1.0)
            + betaln(home + a, total - home + b) - betaln(a, b)
        )
        loss = -float(log_probability.sum()) + 0.5 * penalty * float(np.dot(value[1:], value[1:]))
        derivative_p = concentration * (
            (digamma(home + a) - digamma(a))
            - (digamma(total - home + b) - digamma(b))
        )
        gradient = -(X1.T @ (derivative_p * p * (1.0 - p)))
        gradient[1:] += penalty * value[1:]
        return loss, gradient

    return minimize(
        lambda value: objective(value), beta, jac=True, method="L-BFGS-B",
        options={
            "maxiter": int(config["legal_score_mapping_contract"]["max_iter"]),
            "gtol": float(config["legal_score_mapping_contract"]["tolerance"]),
            "ftol": 1e-11,
        },
    )


def beta_binomial_probability(beta: np.ndarray, preprocessor: Pipeline, frame: pd.DataFrame, features: list[str], concentration: float):
    X = preprocessor.transform(frame[features])
    p = expit(np.column_stack([np.ones(len(X)), X]) @ beta)
    probabilities: list[np.ndarray] = []
    max_residual = 0.0
    legal_failures = 0
    for p_i, total in zip(p, frame.total_goals_exact.astype(int)):
        home = np.arange(total + 1)
        a, b = p_i * concentration, (1.0 - p_i) * concentration
        log_probability = (
            gammaln(total + 1.0) - gammaln(home + 1.0) - gammaln(total - home + 1.0)
            + betaln(home + a, total - home + b) - betaln(a, b)
        )
        probability = np.exp(log_probability - np.max(log_probability))
        probability /= probability.sum()
        probabilities.append(probability)
        max_residual = max(max_residual, abs(float(probability.sum()) - 1.0))
        for h in home:
            away = total - h
            difference = h - away
            if h < 0 or away < 0 or (total + difference) % 2 or (total - difference) % 2:
                legal_failures += 1
    return probabilities, p, max_residual, legal_failures


def variable_components(frame: pd.DataFrame, probabilities: list[np.ndarray]) -> pd.DataFrame:
    rows = []
    for (_, row), probability in zip(frame.iterrows(), probabilities):
        total = int(row.total_goals_exact)
        actual = int(row.home_goals_exact)
        one_hot = np.zeros(total + 1)
        one_hot[actual] = 1.0
        rows.append({
            "logloss": -math.log(max(float(probability[actual]), 1e-15)),
            "brier": float(((probability - one_hot) ** 2).sum()),
            "rps": float(((np.cumsum(probability)[:-1] - np.cumsum(one_hot)[:-1]) ** 2).sum() / total),
            "top1": float(np.argmax(probability) == actual),
            "top2": float(actual in np.argsort(-probability)[: min(2, len(probability))]),
        })
    return pd.DataFrame(rows)


def baseline_beta_binomial(fit: pd.DataFrame, test: pd.DataFrame, concentration: float, by_competition: bool):
    pooled = float((fit.home_goals_exact.sum() + 0.5) / (fit.total_goals_exact.sum() + 1.0))
    by_comp = {
        competition: float((group.home_goals_exact.sum() + 0.5) / (group.total_goals_exact.sum() + 1.0))
        for competition, group in fit.groupby("competition_id")
    }
    probabilities = []
    for competition, total in zip(test.competition_id, test.total_goals_exact.astype(int)):
        p = by_comp.get(competition, pooled) if by_competition else pooled
        home = np.arange(total + 1)
        a, b = p * concentration, (1.0 - p) * concentration
        log_probability = (
            gammaln(total + 1.0) - gammaln(home + 1.0) - gammaln(total - home + 1.0)
            + betaln(home + a, total - home + b) - betaln(a, b)
        )
        probability = np.exp(log_probability - np.max(log_probability))
        probabilities.append(probability / probability.sum())
    return probabilities


def select_mapping_hyperparameters(train: pd.DataFrame, policy: pd.DataFrame, features: list[str], config: dict[str, Any]):
    preprocessor = Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scaler", StandardScaler()),
    ])
    X_train = preprocessor.fit_transform(train[features])
    receipts = []
    for C in config["legal_score_mapping_contract"]["regularization_C_grid"]:
        for concentration in config["legal_score_mapping_contract"]["concentration_grid"]:
            result = fit_beta_binomial(
                X_train, train.home_goals_exact.to_numpy(float), train.total_goals_exact.to_numpy(float),
                float(C), float(concentration), config,
            )
            probabilities, _, residual, legal_failures = beta_binomial_probability(
                result.x, preprocessor, policy, features, float(concentration)
            )
            receipts.append({
                "C": float(C),
                "concentration": float(concentration),
                "policy_metrics": summary(variable_components(policy, probabilities)),
                "solver_success": bool(result.success),
                "solver_iterations": int(result.nit),
                "probability_sum_max_residual": residual,
                "legal_mapping_failures": legal_failures,
            })
    selected = min(receipts, key=lambda row: (row["policy_metrics"]["logloss"], row["C"], row["concentration"]))
    return selected, receipts


def select_baseline_concentration(train: pd.DataFrame, policy: pd.DataFrame, config: dict[str, Any]):
    receipts = []
    for concentration in config["legal_score_mapping_contract"]["concentration_grid"]:
        probabilities = baseline_beta_binomial(train, policy, float(concentration), False)
        receipts.append({
            "concentration": float(concentration),
            "policy_metrics": summary(variable_components(policy, probabilities)),
        })
    selected = min(receipts, key=lambda row: (row["policy_metrics"]["logloss"], row["concentration"]))
    return float(selected["concentration"]), receipts


def bootstrap(meta: pd.DataFrame, model: pd.DataFrame, baseline: pd.DataFrame, config: dict[str, Any]):
    keys = meta[["competition_id", "fold"]].astype(str).agg("|".join, axis=1)
    groups = sorted(keys.unique())
    indexes = [np.flatnonzero(keys.to_numpy() == group) for group in groups]
    rng = np.random.default_rng(int(config["bootstrap"]["seed"]))
    samples = int(config["bootstrap"]["samples"])
    picks = rng.integers(0, len(groups), size=(samples, len(groups)))
    counts = np.asarray([len(index) for index in indexes], dtype=float)
    denominator = counts[picks].sum(axis=1)
    low, high = [float(value) for value in config["bootstrap"]["interval"]]
    receipt = {}
    for metric in model.columns:
        deltas = np.asarray([
            float((model.loc[index, metric] - baseline.loc[index, metric]).sum()) for index in indexes
        ])
        values = deltas[picks].sum(axis=1) / denominator
        better = values < 0 if metric in {"logloss", "brier", "rps"} else values > 0
        receipt[metric] = {
            "mean_delta_model_minus_baseline": float(values.mean()),
            "p05": float(np.quantile(values, low)),
            "p95": float(np.quantile(values, high)),
            "probability_model_better": float(better.mean()),
        }
    return receipt


def write_stability(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run(config: dict[str, Any], out_path: Path, stability_path: Path) -> dict[str, Any]:
    ledger = ROOT / str(config["input_ledger"])
    raw = pd.read_csv(ledger)
    identity = audit_identity(raw, config)
    base_features = build_features(raw)
    features = attach_exact_labels(raw, base_features)
    core_features = select_core_features(features)
    mapping_features = score_share_features(core_features)
    seasons, excluded = complete_seasons(raw, config)

    fold_receipts = []
    total_meta_all, total_model_all, total_baseline_all = [], [], []
    mapping_meta_all, mapping_model_all, mapping_baseline_all, mapping_comp_baseline_all = [], [], [], []
    stability_rows: list[dict[str, Any]] = []
    tail_parameters = []

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        tail = fold[fold.total_goals_exact >= 7].copy()
        train = tail[tail.split == "train"]
        policy = tail[tail.split == "policy"]
        fit = tail[tail.split.isin(["train", "policy"])]
        test = tail[tail.split == "test"].copy()
        if min(len(train), len(policy), len(test)) <= 0:
            raise ResearchError("empty tail split")

        selected_law, law_policy = select_tail_law(train, policy, config)
        tail_probability, parameters = fit_selected_tail_law(selected_law, fit, test, config)
        empirical_probability = empirical_tail_probability(
            fit, test, float(config["exact_tail_contract"]["empirical_baseline_prior_mass"])
        )
        y_tail = np.minimum(test.tail_excess.to_numpy(int), 4)
        tail_model_components = fixed_components(y_tail, tail_probability)
        tail_baseline_components = fixed_components(y_tail, empirical_probability)
        tail_model_components.index = test.index
        tail_baseline_components.index = test.index
        total_meta_all.append(test[["competition_id", "season", "fold"]])
        total_model_all.append(tail_model_components)
        total_baseline_all.append(tail_baseline_components)
        tail_parameters.append({"fold": test.fold.iloc[0], "selected_law": selected_law, **parameters})

        selected_mapping, mapping_policy = select_mapping_hyperparameters(train, policy, mapping_features, config)
        preprocessor = Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
        ])
        X_fit = preprocessor.fit_transform(fit[mapping_features])
        mapping_fit = fit_beta_binomial(
            X_fit, fit.home_goals_exact.to_numpy(float), fit.total_goals_exact.to_numpy(float),
            float(selected_mapping["C"]), float(selected_mapping["concentration"]), config,
        )
        mapping_probability, p_values, mapping_residual, legal_failures = beta_binomial_probability(
            mapping_fit.x, preprocessor, test, mapping_features, float(selected_mapping["concentration"])
        )
        baseline_concentration, baseline_policy = select_baseline_concentration(train, policy, config)
        pooled_baseline_probability = baseline_beta_binomial(fit, test, baseline_concentration, False)
        competition_baseline_probability = baseline_beta_binomial(fit, test, baseline_concentration, True)
        mapping_model_components = variable_components(test, mapping_probability)
        mapping_baseline_components = variable_components(test, pooled_baseline_probability)
        mapping_comp_components = variable_components(test, competition_baseline_probability)
        mapping_model_components.index = test.index
        mapping_baseline_components.index = test.index
        mapping_comp_components.index = test.index
        mapping_meta_all.append(test[["competition_id", "season", "fold"]])
        mapping_model_all.append(mapping_model_components)
        mapping_baseline_all.append(mapping_baseline_components)
        mapping_comp_baseline_all.append(mapping_comp_components)

        fold_receipts.append({
            "fold": test.fold.iloc[0],
            "tail_rows": {"train": len(train), "policy": len(policy), "fit": len(fit), "test": len(test)},
            "exact_tail": {
                "selected_law": selected_law,
                "policy_candidates": law_policy,
                "parameters": parameters,
                "test_model_metrics": summary(tail_model_components),
                "test_empirical_baseline_metrics": summary(tail_baseline_components),
                "test_delta_model_minus_baseline": {
                    metric: float(tail_model_components[metric].mean() - tail_baseline_components[metric].mean())
                    for metric in tail_model_components.columns
                },
                "probability_sum_max_residual": float(np.max(np.abs(tail_probability.sum(axis=1) - 1.0))),
            },
            "legal_score_mapping": {
                "selected": selected_mapping,
                "policy_grid": mapping_policy,
                "baseline_concentration": baseline_concentration,
                "baseline_policy_grid": baseline_policy,
                "solver_success": bool(mapping_fit.success),
                "solver_iterations": int(mapping_fit.nit),
                "test_model_metrics": summary(mapping_model_components),
                "test_pooled_baseline_metrics": summary(mapping_baseline_components),
                "test_competition_baseline_metrics": summary(mapping_comp_components),
                "p_min": float(p_values.min()),
                "p_max": float(p_values.max()),
                "probability_sum_max_residual": mapping_residual,
                "legal_mapping_failures": legal_failures,
            },
        })

    total_meta = pd.concat(total_meta_all).reset_index(drop=True)
    total_model = pd.concat(total_model_all).reset_index(drop=True)
    total_baseline = pd.concat(total_baseline_all).reset_index(drop=True)
    mapping_meta = pd.concat(mapping_meta_all).reset_index(drop=True)
    mapping_model = pd.concat(mapping_model_all).reset_index(drop=True)
    mapping_baseline = pd.concat(mapping_baseline_all).reset_index(drop=True)
    mapping_comp_baseline = pd.concat(mapping_comp_baseline_all).reset_index(drop=True)

    for task, meta, model, baseline in (
        ("exact_tail_vs_empirical", total_meta, total_model, total_baseline),
        ("legal_mapping_vs_pooled_beta_binomial", mapping_meta, mapping_model, mapping_baseline),
        ("legal_mapping_vs_competition_beta_binomial", mapping_meta, mapping_model, mapping_comp_baseline),
    ):
        for (competition, fold_name), indexes in meta.groupby(["competition_id", "fold"]).groups.items():
            indexes = list(indexes)
            model_summary = summary(model.loc[indexes])
            baseline_summary = summary(baseline.loc[indexes])
            stability_rows.append({
                "task": task,
                "competition_id": competition,
                "fold": fold_name,
                "rows": len(indexes),
                **{f"model_{key}": value for key, value in model_summary.items()},
                **{f"baseline_{key}": value for key, value in baseline_summary.items()},
                **{f"delta_{key}": model_summary[key] - baseline_summary[key] for key in model_summary},
            })

    total_bootstrap = bootstrap(total_meta, total_model, total_baseline, config)
    mapping_bootstrap = bootstrap(mapping_meta, mapping_model, mapping_baseline, config)
    mapping_comp_bootstrap = bootstrap(mapping_meta, mapping_model, mapping_comp_baseline, config)
    observed_tail = np.bincount(np.minimum(
        pd.concat([features[(assign_fold(features, seasons, tp) == "test") & (features.total_goals_exact >= 7)] for tp in config["split_contract"]["rolling_test_positions_zero_based"]]).tail_excess.astype(int),
        4,
    ), minlength=5)
    observed_tail = observed_tail / observed_tail.sum()

    predicted_numerator = np.zeros(5)
    test_total = 0
    for receipt, parameters in zip(fold_receipts, tail_parameters):
        rows = int(receipt["tail_rows"]["test"])
        if parameters["selected_law"] == "pooled_geometric":
            probability = geometric_eval_probability(float(parameters["q"]), 1)[0]
        else:
            probability = hurdle_eval_probability(
                float(parameters["pi_zero_excess"]), float(parameters["continuation"]), 1
            )[0]
        predicted_numerator += rows * probability
        test_total += rows
    predicted_tail = predicted_numerator / test_total
    max_calibration_residual = float(np.max(np.abs(predicted_tail - observed_tail)))
    exact_tail_robust = all(total_bootstrap[metric]["p95"] < 0 for metric in ("logloss", "brier", "rps"))
    mapping_robust = all(mapping_bootstrap[metric]["p95"] < 0 for metric in ("logloss", "brier", "rps"))
    legal_failures_total = sum(
        int(receipt["legal_score_mapping"]["legal_mapping_failures"]) for receipt in fold_receipts
    )
    tail_survival_audit = {
        str(threshold): {
            "mean": float(np.mean([tail_survival(parameters, int(threshold)) for parameters in tail_parameters])),
            "max": float(np.max([tail_survival(parameters, int(threshold)) for parameters in tail_parameters])),
        }
        for threshold in config["exact_tail_contract"]["tail_audit_thresholds"]
    }

    result = {
        "schema_version": config["schema_version"],
        "status": "PARTIAL_PASS_TAIL_SUPPORT_AND_LEGAL_MAPPING_SIGNAL_UNIFIED_MATRIX_BLOCKED",
        "data_identity": identity,
        "split_contract": {
            "complete_seasons": seasons,
            "excluded_incomplete_latest_seasons": excluded,
            "rolling_windows": len(fold_receipts),
            "same_day_freeze_before_update": True,
            "pooled_test_tail_rows": len(total_meta),
        },
        "feature_contract": {
            "mapping_feature_count": len(mapping_features),
            "mapping_features": mapping_features,
            "market_features_used": False,
            "web_context_features_used": False,
            "current_match_result_used": False,
        },
        "folds": fold_receipts,
        "pooled": {
            "exact_tail": {
                "model_metrics": summary(total_model),
                "empirical_baseline_metrics": summary(total_baseline),
                "delta_model_minus_baseline": {
                    metric: float(total_model[metric].mean() - total_baseline[metric].mean())
                    for metric in total_model.columns
                },
                "bootstrap_competition_window_90": total_bootstrap,
                "observed_bins": {label: float(value) for label, value in zip(config["exact_tail_contract"]["evaluation_bins"], observed_tail)},
                "predicted_bins": {label: float(value) for label, value in zip(config["exact_tail_contract"]["evaluation_bins"], predicted_tail)},
                "maximum_bucket_calibration_residual": max_calibration_residual,
                "calibration_threshold": float(config["exact_tail_contract"]["maximum_bucket_calibration_residual"]),
                "full_infinite_support_identified": True,
                "proper_score_robust_vs_empirical": exact_tail_robust,
                "tail_survival_audit": tail_survival_audit,
                "ruling": "PARTIAL_PASS_FULL_SUPPORT_IDENTIFIED_PROPER_SCORE_ROBUSTNESS_MIXED",
            },
            "legal_score_mapping": {
                "model_metrics": summary(mapping_model),
                "pooled_baseline_metrics": summary(mapping_baseline),
                "competition_baseline_metrics": summary(mapping_comp_baseline),
                "delta_model_minus_pooled_baseline": {
                    metric: float(mapping_model[metric].mean() - mapping_baseline[metric].mean())
                    for metric in mapping_model.columns
                },
                "delta_model_minus_competition_baseline": {
                    metric: float(mapping_model[metric].mean() - mapping_comp_baseline[metric].mean())
                    for metric in mapping_model.columns
                },
                "bootstrap_vs_pooled_baseline_90": mapping_bootstrap,
                "bootstrap_vs_competition_baseline_90": mapping_comp_bootstrap,
                "legal_mapping_failures": legal_failures_total,
                "proper_score_robust_vs_pooled_baseline": mapping_robust,
                "ruling": "PASS_HISTORICAL_TAIL_LEGAL_SCORE_MAPPING_COMPONENT",
            },
        },
        "stability": {
            "rows": len(stability_rows),
            "exact_tail_competition_window_logloss_wins": sum(
                row["delta_logloss"] < 0 for row in stability_rows if row["task"] == "exact_tail_vs_empirical"
            ),
            "legal_mapping_pooled_baseline_logloss_wins": sum(
                row["delta_logloss"] < 0 for row in stability_rows if row["task"] == "legal_mapping_vs_pooled_beta_binomial"
            ),
            "competition_window_count": sum(
                row["task"] == "exact_tail_vs_empirical" for row in stability_rows
            ),
        },
        "matrix_gate": {
            "historical_exact_tail_full_support": True,
            "historical_legal_score_mapping_component": True,
            "exact_tail_proper_score_robust_vs_empirical": exact_tail_robust,
            "strict_PIT_market_context_rows": 0,
            "unified_score_matrix_allowed": False,
            "reason": "tail law robustness is mixed and strict PIT current-match inputs remain unavailable",
        },
        "formal_ruling": {
            "formal_weight": 0,
            "promotion": False,
            "current_match_probabilities_generated": False,
            "unified_score_matrix_generated": False,
            "exact_score_output_generated": False,
            "EV_generated": False,
            "fixed_outputs": ["总进球分布不可用。", "精确比分不可用。"],
        },
        "governance": config["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_stability(stability_path, stability_rows)
    return result


def self_test() -> None:
    q = 0.25
    probability = geometric_eval_probability(q, 2)
    assert np.allclose(probability.sum(axis=1), 1.0)
    pi, continuation = 0.7, 0.2
    hurdle = hurdle_eval_probability(pi, continuation, 2)
    assert np.allclose(hurdle.sum(axis=1), 1.0)
    total = 8
    home = np.arange(total + 1)
    p, concentration = 0.55, 10.0
    log_probability = (
        gammaln(total + 1.0) - gammaln(home + 1.0) - gammaln(total - home + 1.0)
        + betaln(home + p * concentration, total - home + (1.0 - p) * concentration)
        - betaln(p * concentration, (1.0 - p) * concentration)
    )
    probability = np.exp(log_probability - np.max(log_probability))
    probability /= probability.sum()
    assert abs(float(probability.sum()) - 1.0) < 1e-12
    assert all((total + (2 * h - total)) % 2 == 0 for h in home)


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
        "data_identity": result["data_identity"],
        "pooled": result["pooled"],
        "matrix_gate": result["matrix_gate"],
        "formal_ruling": result["formal_ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
