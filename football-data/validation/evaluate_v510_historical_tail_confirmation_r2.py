#!/usr/bin/env python3
"""Frozen R2 confirmation for the historical 7+ exact-total law.

Four infinite-support candidate families are selected only on the policy season.
Test labels are used once for reporting and never for candidate selection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from v510_historical_structure_features_r1 import (
    ResearchError, assign_fold, build_features, complete_seasons, select_core_features,
)
from evaluate_v510_historical_tail_mapping_r1 import (
    attach_exact_labels, bootstrap, empirical_tail_probability, fixed_components,
    hurdle_eval_probability, hurdle_parameters, score_share_features, summary,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_historical_tail_confirmation_r2.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_historical_tail_confirmation_r2_status.json"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def audit_identity(raw: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    tail = raw[raw.total_goals >= 7]
    actual = {
        "rows": int(len(raw)),
        "competitions": int(raw.competition_id.nunique()),
        "tail_rows": int(len(tail)),
        "tail_exact_counts": {str(k): int(v) for k, v in tail.total_goals.value_counts().sort_index().items()},
    }
    if actual != config["expected_data_identity"]:
        raise ResearchError(f"data identity mismatch: expected={config['expected_data_identity']}, actual={actual}")
    return actual


def pooled_probability(train: pd.DataFrame, pred: pd.DataFrame) -> np.ndarray:
    pi, continuation = hurdle_parameters(train, 0.5)
    return hurdle_eval_probability(pi, continuation, len(pred))


def hierarchical_probability(train: pd.DataFrame, pred: pd.DataFrame, prior_mass: float) -> np.ndarray:
    global_pi, global_continuation = hurdle_parameters(train, 0.5)
    output = []
    for competition in pred.competition_id:
        group = train[train.competition_id == competition]
        excess = group.tail_excess.to_numpy(int)
        zeros = int((excess == 0).sum())
        pi = (zeros + global_pi * prior_mass) / (len(excess) + prior_mass)
        positive = excess[excess > 0]
        continuations = int((positive - 1).sum())
        stops = len(positive)
        continuation = (
            continuations + global_continuation * prior_mass
        ) / (continuations + stops + prior_mass)
        output.append(hurdle_eval_probability(float(pi), float(continuation), 1)[0])
    return np.asarray(output)


def make_logit(C: float) -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scaler", StandardScaler()),
        ("model", LogisticRegression(C=C, solver="lbfgs", max_iter=1000, tol=1e-6)),
    ])


def positive_class_probability(model: Pipeline, frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    classes = [int(value) for value in model.named_steps["model"].classes_]
    if 1 not in classes:
        raise ResearchError("binary model has no positive class")
    return model.predict_proba(frame[features])[:, classes.index(1)]


def feature_zero_probability(train: pd.DataFrame, pred: pd.DataFrame, features: list[str], C: float) -> np.ndarray:
    model = make_logit(C)
    model.fit(train[features], (train.tail_excess.to_numpy(int) == 0).astype(int))
    pi = positive_class_probability(model, pred, features)
    _, continuation = hurdle_parameters(train, 0.5)
    r = np.full(len(pred), continuation, dtype=float)
    return np.column_stack([
        pi,
        (1.0 - pi) * (1.0 - r),
        (1.0 - pi) * (1.0 - r) * r,
        (1.0 - pi) * (1.0 - r) * r**2,
        (1.0 - pi) * r**3,
    ])


def continuation_training_rows(train: pd.DataFrame) -> tuple[list[int], np.ndarray]:
    indexes: list[int] = []
    labels: list[int] = []
    for idx, row in train[train.tail_excess > 0].iterrows():
        excess = int(row.tail_excess)
        for stage in range(1, excess + 1):
            indexes.append(idx)
            labels.append(int(excess > stage))
    return indexes, np.asarray(labels, dtype=int)


def feature_both_probability(
    train: pd.DataFrame,
    pred: pd.DataFrame,
    features: list[str],
    zero_C: float,
    continuation_C: float,
) -> np.ndarray:
    zero_model = make_logit(zero_C)
    zero_model.fit(train[features], (train.tail_excess.to_numpy(int) == 0).astype(int))
    pi = positive_class_probability(zero_model, pred, features)
    indexes, labels = continuation_training_rows(train)
    if len(np.unique(labels)) < 2:
        _, pooled_continuation = hurdle_parameters(train, 0.5)
        r = np.full(len(pred), pooled_continuation, dtype=float)
    else:
        continuation_model = make_logit(continuation_C)
        continuation_model.fit(train.loc[indexes, features], labels)
        r = positive_class_probability(continuation_model, pred, features)
    r = np.clip(r, 1e-6, 1.0 - 1e-6)
    return np.column_stack([
        pi,
        (1.0 - pi) * (1.0 - r),
        (1.0 - pi) * (1.0 - r) * r,
        (1.0 - pi) * (1.0 - r) * r**2,
        (1.0 - pi) * r**3,
    ])


def candidate_probability(
    name: str,
    train: pd.DataFrame,
    pred: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> np.ndarray:
    catalog = config["candidate_catalog"]
    if name == "pooled_hurdle_geometric":
        return pooled_probability(train, pred)
    if name == "hierarchical_hurdle_geometric":
        return hierarchical_probability(train, pred, float(catalog[name]["competition_prior_mass"]))
    if name == "feature_zero_hurdle_geometric":
        return feature_zero_probability(train, pred, features, float(catalog[name]["C"]))
    if name == "feature_both_hurdle_geometric":
        return feature_both_probability(
            train,
            pred,
            features,
            float(catalog[name]["zero_C"]),
            float(catalog[name]["continuation_C"]),
        )
    raise ResearchError(f"unknown candidate: {name}")


def logloss(frame: pd.DataFrame, probability: np.ndarray) -> float:
    target = np.minimum(frame.tail_excess.to_numpy(int), 4)
    return float(-np.log(np.clip(probability[np.arange(len(target)), target], 1e-15, 1.0)).mean())


def run(config: dict[str, Any], out_path: Path) -> dict[str, Any]:
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    identity = audit_identity(raw, config)
    features = attach_exact_labels(raw, build_features(raw))
    score_features = score_share_features(select_core_features(features))
    seasons, excluded = complete_seasons(raw, config)

    fold_receipts = []
    model_components_all = []
    empirical_components_all = []
    pooled_components_all = []
    meta_all = []

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        tail = fold[fold.total_goals_exact >= 7].copy()
        train = tail[tail.split == "train"]
        policy = tail[tail.split == "policy"]
        fit = tail[tail.split.isin(["train", "policy"])]
        test = tail[tail.split == "test"].copy()

        policy_receipts = []
        for name in config["candidate_catalog"]:
            probability = candidate_probability(name, train, policy, score_features, config)
            policy_receipts.append({
                "candidate": name,
                "policy_logloss": logloss(policy, probability),
                "probability_sum_max_residual": float(np.max(np.abs(probability.sum(axis=1) - 1.0))),
            })
        selected = min(policy_receipts, key=lambda row: (row["policy_logloss"], row["candidate"]))["candidate"]
        model_probability = candidate_probability(selected, fit, test, score_features, config)
        pooled_probability_test = pooled_probability(fit, test)
        empirical_probability_test = empirical_tail_probability(
            fit, test, float(config["empirical_baseline_prior_mass"])
        )
        target = np.minimum(test.tail_excess.to_numpy(int), 4)
        model_components = fixed_components(target, model_probability)
        pooled_components = fixed_components(target, pooled_probability_test)
        empirical_components = fixed_components(target, empirical_probability_test)
        model_components.index = test.index
        pooled_components.index = test.index
        empirical_components.index = test.index
        model_components_all.append(model_components)
        pooled_components_all.append(pooled_components)
        empirical_components_all.append(empirical_components)
        meta_all.append(test[["competition_id", "season", "fold"]])

        fold_receipts.append({
            "fold": test.fold.iloc[0],
            "rows": {"train": len(train), "policy": len(policy), "fit": len(fit), "test": len(test)},
            "selected_candidate": selected,
            "policy_candidates": policy_receipts,
            "test_model_metrics": summary(model_components),
            "test_empirical_baseline_metrics": summary(empirical_components),
            "test_pooled_hurdle_metrics": summary(pooled_components),
            "delta_model_minus_empirical": {
                metric: float(model_components[metric].mean() - empirical_components[metric].mean())
                for metric in model_components.columns
            },
            "delta_model_minus_pooled_hurdle": {
                metric: float(model_components[metric].mean() - pooled_components[metric].mean())
                for metric in model_components.columns
            },
            "probability_sum_max_residual": float(np.max(np.abs(model_probability.sum(axis=1) - 1.0))),
        })

    model_components = pd.concat(model_components_all).reset_index(drop=True)
    empirical_components = pd.concat(empirical_components_all).reset_index(drop=True)
    pooled_components = pd.concat(pooled_components_all).reset_index(drop=True)
    meta = pd.concat(meta_all).reset_index(drop=True)
    empirical_bootstrap = bootstrap(meta, model_components, empirical_components, config)
    pooled_bootstrap = bootstrap(meta, model_components, pooled_components, config)
    required = [str(value) for value in config["confirmation_gate"]["required_metrics"]]
    confirmed = all(empirical_bootstrap[metric]["p95"] < 0 for metric in required)

    result = {
        "schema_version": config["schema_version"],
        "status": (
            "PASS_CONFIRMATION_HISTORICAL_EXACT_TAIL_ROBUST"
            if confirmed
            else "FAIL_CONFIRMATION_HISTORICAL_EXACT_TAIL_NOT_ROBUST_MATRIX_BLOCKED"
        ),
        "data_identity": identity,
        "split_contract": {
            "complete_seasons": seasons,
            "excluded_incomplete_latest_seasons": excluded,
            "rolling_windows": len(fold_receipts),
            "same_day_freeze_before_update": True,
            "pooled_test_tail_rows": len(meta),
        },
        "candidate_contract": {
            "catalog": config["candidate_catalog"],
            "policy_only_selection": True,
            "selection_metric": config["selection_metric"],
            "test_labels_used_for_selection": False,
            "all_candidates_have_infinite_discrete_support": True,
            "feature_count": len(score_features),
            "features": score_features,
        },
        "folds": fold_receipts,
        "pooled": {
            "model_metrics": summary(model_components),
            "empirical_baseline_metrics": summary(empirical_components),
            "pooled_hurdle_metrics": summary(pooled_components),
            "delta_model_minus_empirical": {
                metric: float(model_components[metric].mean() - empirical_components[metric].mean())
                for metric in model_components.columns
            },
            "delta_model_minus_pooled_hurdle": {
                metric: float(model_components[metric].mean() - pooled_components[metric].mean())
                for metric in model_components.columns
            },
            "bootstrap_vs_empirical_90": empirical_bootstrap,
            "bootstrap_vs_pooled_hurdle_90": pooled_bootstrap,
            "confirmation_pass": confirmed,
        },
        "diagnostic": {
            "selected_candidate_counts": {
                name: sum(row["selected_candidate"] == name for row in fold_receipts)
                for name in config["candidate_catalog"]
            },
            "feature_candidates_selected": sum(
                row["selected_candidate"].startswith("feature_") for row in fold_receipts
            ),
            "interpretation": (
                "competition shrinkage or feature conditioning did not make the exact-tail law robust against the empirical baseline"
                if not confirmed else
                "the frozen candidate catalog cleared the historical exact-tail confirmation gate"
            ),
        },
        "matrix_gate": {
            "r1_infinite_support_retained": True,
            "r1_legal_tail_score_mapping_retained_as_historical_component": True,
            "r2_exact_tail_confirmation_pass": confirmed,
            "strict_PIT_market_context_rows": 0,
            "unified_score_matrix_allowed": False,
            "reason": (
                "strict PIT current-match inputs remain unavailable"
                if confirmed else
                "exact-tail confirmation failed and strict PIT current-match inputs remain unavailable"
            ),
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
    return result


def self_test() -> None:
    frame = pd.DataFrame({
        "competition_id": ["A", "A", "B"],
        "tail_excess": [0, 1, 3],
    })
    probability = hierarchical_probability(frame, frame, 20.0)
    assert probability.shape == (3, 5)
    assert np.allclose(probability.sum(axis=1), 1.0)
    assert np.all(probability >= 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    result = run(load_json(args.config), args.out)
    print(json.dumps({
        "status": result["status"],
        "pooled": result["pooled"],
        "diagnostic": result["diagnostic"],
        "matrix_gate": result["matrix_gate"],
        "formal_ruling": result["formal_ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
