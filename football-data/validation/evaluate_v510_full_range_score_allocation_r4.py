#!/usr/bin/env python3
"""R4 full-range legal score-allocation challenge for V5.1.

Research-only historical development replay. Given the realised total T for component
validation, compare one shared Beta-Binomial H|T,X model against the current separate
multinomial P(D|T,X) component on T=1..6 and against a competition Beta-Binomial
reference on the full exact-total range. Every feature packet is strictly pre-match and
same-day packets are frozen before any result update.
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
from v510_historical_structure_model_r1 import align_probability, make_model, select_C
from evaluate_v510_historical_tail_mapping_r1 import (
    attach_exact_labels,
    baseline_beta_binomial,
    beta_binomial_probability,
    fit_beta_binomial,
    score_share_features,
    select_baseline_concentration,
    select_mapping_hyperparameters,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_full_range_score_allocation_r4.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_full_range_score_allocation_r4_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_full_range_score_allocation_r4_stability.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def summary(frame: pd.DataFrame) -> dict[str, float]:
    return {column: float(frame[column].mean()) for column in frame.columns}


def score_components(frame: pd.DataFrame, probabilities: list[np.ndarray]) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    for (_, row), probability in zip(frame.iterrows(), probabilities):
        total = int(row.total_goals_exact)
        actual = int(row.home_goals_exact)
        probability = np.asarray(probability, dtype=float)
        if len(probability) != total + 1:
            raise ResearchError(f"score support mismatch for T={total}: {len(probability)}")
        probability = np.clip(probability, 1e-15, 1.0)
        probability /= probability.sum()
        one_hot = np.zeros(total + 1, dtype=float)
        one_hot[actual] = 1.0
        order = np.argsort(-probability)
        rps = 0.0 if total == 0 else float(
            ((np.cumsum(probability)[:-1] - np.cumsum(one_hot)[:-1]) ** 2).sum() / total
        )
        rows.append({
            "logloss": -math.log(float(probability[actual])),
            "brier": float(((probability - one_hot) ** 2).sum()),
            "rps": rps,
            "top1": float(order[0] == actual),
            "top2": float(actual in order[: min(2, len(order))]),
            "top3": float(actual in order[: min(3, len(order))]),
        })
    return pd.DataFrame(rows, index=frame.index)


def result_components(frame: pd.DataFrame, probabilities: list[np.ndarray]) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    for (_, row), probability in zip(frame.iterrows(), probabilities):
        total = int(row.total_goals_exact)
        probability = np.asarray(probability, dtype=float)
        probability = np.clip(probability, 1e-15, 1.0)
        probability /= probability.sum()
        h = np.arange(total + 1)
        a = total - h
        result_probability = np.asarray([
            probability[h > a].sum(),
            probability[h == a].sum(),
            probability[h < a].sum(),
        ])
        actual_result = (
            0 if int(row.home_goals_exact) > int(row.away_goals_exact)
            else 1 if int(row.home_goals_exact) == int(row.away_goals_exact)
            else 2
        )
        one_hot = np.zeros(3, dtype=float)
        one_hot[actual_result] = 1.0
        draw_truth = float(actual_result == 1)
        rows.append({
            "result_logloss": -math.log(max(float(result_probability[actual_result]), 1e-15)),
            "result_brier": float(((result_probability - one_hot) ** 2).sum()),
            "result_top1": float(np.argmax(result_probability) == actual_result),
            "draw_binary_brier": float((result_probability[1] - draw_truth) ** 2),
        })
    return pd.DataFrame(rows, index=frame.index)


def deterministic_zero_probabilities(frame: pd.DataFrame) -> dict[int, np.ndarray]:
    return {int(index): np.ones(1, dtype=float) for index in frame.index[frame.total_goals_exact == 0]}


def probability_list(frame: pd.DataFrame, mapping: dict[int, np.ndarray]) -> list[np.ndarray]:
    missing = [int(index) for index in frame.index if int(index) not in mapping]
    if missing:
        raise ResearchError(f"missing probability rows: {missing[:5]}")
    return [mapping[int(index)] for index in frame.index]


def add_conditional_total_features(features: pd.DataFrame) -> pd.DataFrame:
    output = features.copy()
    total = output.total_goals_exact.astype(float)
    output["conditional_total"] = total
    output["conditional_total_log1p"] = np.log1p(total)
    output["conditional_total_even"] = (total.astype(int) % 2 == 0).astype(float)
    output["conditional_total_tail7"] = (total >= 7).astype(float)
    return output


def mapping_feature_sets(core_features: list[str]) -> dict[str, list[str]]:
    total_features = [
        "conditional_total",
        "conditional_total_log1p",
        "conditional_total_even",
        "conditional_total_tail7",
    ]
    compact = score_share_features(core_features)
    return {
        "compact_plus_total": sorted(set(compact + total_features)),
        "full_core_plus_total": sorted(set(core_features + total_features)),
    }


def select_full_range_candidate(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    feature_sets: dict[str, list[str]],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    for feature_set_name, feature_names in feature_sets.items():
        selected, grid = select_mapping_hyperparameters(train, policy, feature_names, config)
        receipts.append({
            "feature_set": feature_set_name,
            "feature_count": len(feature_names),
            "features": feature_names,
            "selected": selected,
            "grid": grid,
        })
    winner = min(
        receipts,
        key=lambda row: (
            row["selected"]["policy_metrics"]["logloss"],
            row["feature_count"],
            row["feature_set"],
        ),
    )
    return winner, receipts


def fit_full_range_beta_binomial(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    fit: pd.DataFrame,
    test: pd.DataFrame,
    feature_sets: dict[str, list[str]],
    config: dict[str, Any],
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    selected, policy_receipts = select_full_range_candidate(train, policy, feature_sets, config)
    feature_names = list(selected["features"])
    hyper = selected["selected"]
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
    predicted, p_values, residual, legal_failures = beta_binomial_probability(
        fitted.x,
        preprocessor,
        test,
        feature_names,
        float(hyper["concentration"]),
    )
    mapping = {int(index): probability for index, probability in zip(test.index, predicted)}
    return mapping, {
        "selected": selected,
        "policy_candidates": policy_receipts,
        "solver_success": bool(fitted.success),
        "solver_status": int(fitted.status),
        "solver_message": str(fitted.message),
        "solver_iterations": int(fitted.nit),
        "p_min": float(np.min(p_values)),
        "p_max": float(np.max(p_values)),
        "probability_sum_max_residual": float(residual),
        "legal_mapping_failures": int(legal_failures),
    }


def fit_current_multinomial_component(
    fold: pd.DataFrame,
    core_features: list[str],
    config: dict[str, Any],
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    mapping: dict[int, np.ndarray] = {}
    per_total: dict[str, Any] = {}
    for total in range(1, 7):
        train = fold[(fold.split == "train") & (fold.total_goals_exact == total)]
        policy = fold[(fold.split == "policy") & (fold.total_goals_exact == total)]
        fit = fold[(fold.split.isin(["train", "policy"])) & (fold.total_goals_exact == total)]
        test = fold[(fold.split == "test") & (fold.total_goals_exact == total)]
        if min(len(train), len(policy), len(test)) <= 0:
            raise ResearchError(f"empty multinomial split for T={total}")
        classes = list(range(-total, total + 1, 2))
        selected_C, policy_grid = select_C(
            train,
            policy,
            core_features,
            "goal_difference",
            classes,
            config,
        )
        model = make_model(selected_C, config)
        model.fit(fit[core_features], fit.goal_difference)
        d_probability = align_probability(model, test[core_features], classes)
        for index, probability in zip(test.index, d_probability):
            mapping[int(index)] = probability
        per_total[str(total)] = {
            "train_rows": len(train),
            "policy_rows": len(policy),
            "fit_rows": len(fit),
            "test_rows": len(test),
            "selected_C": selected_C,
            "policy_grid": policy_grid,
            "probability_sum_max_residual": float(np.max(np.abs(d_probability.sum(axis=1) - 1.0))),
        }
    return mapping, per_total


def fit_competition_beta_baseline(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    fit: pd.DataFrame,
    test: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    concentration, policy_grid = select_baseline_concentration(train, policy, config)
    probabilities = baseline_beta_binomial(fit, test, concentration, True)
    mapping = {int(index): probability for index, probability in zip(test.index, probabilities)}
    return mapping, {"concentration": concentration, "policy_grid": policy_grid}


def bootstrap(
    meta: pd.DataFrame,
    model: pd.DataFrame,
    baseline: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    if not model.index.equals(meta.index) or not baseline.index.equals(meta.index):
        raise ResearchError("bootstrap indexes are not aligned")
    keys = meta[["competition_id", "fold"]].astype(str).agg("|".join, axis=1)
    groups = sorted(keys.unique())
    indexes = [np.flatnonzero(keys.to_numpy() == group) for group in groups]
    counts = np.asarray([len(index) for index in indexes], dtype=float)
    rng = np.random.default_rng(int(config["bootstrap"]["seed"]))
    samples = int(config["bootstrap"]["samples"])
    picks = rng.integers(0, len(groups), size=(samples, len(groups)))
    denominator = counts[picks].sum(axis=1)
    low, high = [float(value) for value in config["bootstrap"]["interval"]]
    lower_is_better = {
        "logloss", "brier", "rps", "result_logloss", "result_brier", "draw_binary_brier"
    }
    output: dict[str, Any] = {}
    for metric in model.columns:
        group_delta = np.asarray([
            float((model.iloc[index][metric] - baseline.iloc[index][metric]).sum())
            for index in indexes
        ])
        values = group_delta[picks].sum(axis=1) / denominator
        better = values < 0 if metric in lower_is_better else values > 0
        output[metric] = {
            "mean_delta_model_minus_baseline": float(values.mean()),
            "p05": float(np.quantile(values, low)),
            "p95": float(np.quantile(values, high)),
            "probability_model_better": float(better.mean()),
        }
    return output


def write_stability(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ResearchError("stability output is empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def combined_components(frame: pd.DataFrame, probabilities: list[np.ndarray]) -> pd.DataFrame:
    return pd.concat([score_components(frame, probabilities), result_components(frame, probabilities)], axis=1)


def deltas(model: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, float]:
    return {column: float(model[column].mean() - baseline[column].mean()) for column in model.columns}


def run(config: dict[str, Any], out_path: Path, stability_path: Path) -> dict[str, Any]:
    ledger = ROOT / str(config["input_ledger"])
    if not ledger.is_file():
        raise ResearchError(f"input ledger missing: {ledger.relative_to(ROOT)}")
    raw = pd.read_csv(ledger)
    identity = audit_data_identity(raw, config)
    base_features = build_features(raw)
    features = add_conditional_total_features(attach_exact_labels(raw, base_features))
    core_features = select_core_features(features)
    feature_sets = mapping_feature_sets(core_features)
    seasons, excluded = complete_seasons(raw, config)

    fold_receipts: list[dict[str, Any]] = []
    main_meta_all: list[pd.DataFrame] = []
    main_model_all: list[pd.DataFrame] = []
    main_baseline_all: list[pd.DataFrame] = []
    full_meta_all: list[pd.DataFrame] = []
    full_model_all: list[pd.DataFrame] = []
    full_baseline_all: list[pd.DataFrame] = []
    tail_meta_all: list[pd.DataFrame] = []
    tail_model_all: list[pd.DataFrame] = []
    tail_baseline_all: list[pd.DataFrame] = []

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        train = fold[(fold.split == "train") & (fold.total_goals_exact >= 1)]
        policy = fold[(fold.split == "policy") & (fold.total_goals_exact >= 1)]
        fit = fold[(fold.split.isin(["train", "policy"])) & (fold.total_goals_exact >= 1)]
        test = fold[fold.split == "test"].copy()
        test_nonzero = test[test.total_goals_exact >= 1]
        if min(len(train), len(policy), len(fit), len(test_nonzero)) <= 0:
            raise ResearchError("empty full-range allocation split")

        beta_mapping, beta_receipt = fit_full_range_beta_binomial(
            train, policy, fit, test_nonzero, feature_sets, config
        )
        beta_mapping.update(deterministic_zero_probabilities(test))
        competition_mapping, competition_receipt = fit_competition_beta_baseline(
            train, policy, fit, test_nonzero, config
        )
        competition_mapping.update(deterministic_zero_probabilities(test))
        current_mapping, current_receipt = fit_current_multinomial_component(fold, core_features, config)

        common = test[(test.total_goals_exact >= 1) & (test.total_goals_exact <= 6)].copy()
        full = test.copy()
        tail = test[test.total_goals_exact >= 7].copy()

        beta_common = combined_components(common, probability_list(common, beta_mapping))
        current_common = combined_components(common, probability_list(common, current_mapping))
        beta_full = combined_components(full, probability_list(full, beta_mapping))
        competition_full = combined_components(full, probability_list(full, competition_mapping))
        beta_tail = combined_components(tail, probability_list(tail, beta_mapping))
        competition_tail = combined_components(tail, probability_list(tail, competition_mapping))

        main_meta_all.append(common[["competition_id", "season", "fold"]])
        main_model_all.append(beta_common)
        main_baseline_all.append(current_common)
        full_meta_all.append(full[["competition_id", "season", "fold"]])
        full_model_all.append(beta_full)
        full_baseline_all.append(competition_full)
        tail_meta_all.append(tail[["competition_id", "season", "fold"]])
        tail_model_all.append(beta_tail)
        tail_baseline_all.append(competition_tail)

        fold_receipts.append({
            "fold": str(test.fold.iloc[0]),
            "rows": {
                "train_nonzero": len(train),
                "policy_nonzero": len(policy),
                "fit_nonzero": len(fit),
                "test_all": len(test),
                "test_common_T1_to_T6": len(common),
                "test_tail_T7plus": len(tail),
            },
            "full_range_beta_binomial": beta_receipt,
            "current_multinomial_D_given_T": current_receipt,
            "competition_beta_binomial_reference": competition_receipt,
            "common_T1_to_T6": {
                "beta_binomial_metrics": summary(beta_common),
                "current_multinomial_metrics": summary(current_common),
                "delta_beta_minus_current": deltas(beta_common, current_common),
            },
            "full_exact_total_range": {
                "beta_binomial_metrics": summary(beta_full),
                "competition_reference_metrics": summary(competition_full),
                "delta_beta_minus_reference": deltas(beta_full, competition_full),
            },
            "tail_T7plus": {
                "beta_binomial_metrics": summary(beta_tail),
                "competition_reference_metrics": summary(competition_tail),
                "delta_beta_minus_reference": deltas(beta_tail, competition_tail),
            },
        })

    def pool(frames: list[pd.DataFrame]) -> pd.DataFrame:
        return pd.concat(frames, ignore_index=True)

    main_meta, main_model, main_baseline = pool(main_meta_all), pool(main_model_all), pool(main_baseline_all)
    full_meta, full_model, full_baseline = pool(full_meta_all), pool(full_model_all), pool(full_baseline_all)
    tail_meta, tail_model, tail_baseline = pool(tail_meta_all), pool(tail_model_all), pool(tail_baseline_all)

    stability_rows: list[dict[str, Any]] = []
    for task, meta, model, baseline in (
        ("beta_vs_current_multinomial_T1_T6", main_meta, main_model, main_baseline),
        ("beta_vs_competition_reference_full", full_meta, full_model, full_baseline),
        ("beta_vs_competition_reference_tail7", tail_meta, tail_model, tail_baseline),
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

    main_bootstrap = bootstrap(main_meta, main_model, main_baseline, config)
    full_bootstrap = bootstrap(full_meta, full_model, full_baseline, config)
    tail_bootstrap = bootstrap(tail_meta, tail_model, tail_baseline, config)
    proper_metrics = ("logloss", "brier", "rps")
    main_proper_robust = all(main_bootstrap[metric]["p95"] < 0 for metric in proper_metrics)
    main_exact_top1_improves = main_model.top1.mean() > main_baseline.top1.mean()
    legal_failures = sum(int(receipt["full_range_beta_binomial"]["legal_mapping_failures"]) for receipt in fold_receipts)
    probability_residual = max(
        float(receipt["full_range_beta_binomial"]["probability_sum_max_residual"])
        for receipt in fold_receipts
    )
    if legal_failures == 0 and main_proper_robust and main_exact_top1_improves:
        status = "PASS_R4_FULL_RANGE_BETA_BINOMIAL_PROPER_SCORE_AND_TOP1_SIGNAL"
    elif legal_failures == 0 and main_proper_robust:
        status = "PARTIAL_PASS_R4_FULL_RANGE_BETA_BINOMIAL_PROPER_SCORE_ONLY"
    else:
        status = "FAIL_R4_FULL_RANGE_BETA_BINOMIAL_NO_ROBUST_REPLACEMENT_SIGNAL"

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
            "repeated_historical_replay_allowed": True,
            "independent_confirmation_claim_allowed": False,
        },
        "component_scope": {
            "conditioning": "realised exact total T is supplied only for component validation",
            "candidate": "one feature-conditioned Beta-Binomial H|T,X shared across the full exact-total range",
            "current_comparator": "separate multinomial P(D|T,X) models for exact T=1..6",
            "full_range_reference": "competition-specific Beta-Binomial with policy-selected concentration",
            "joint_current_match_matrix_generated": False,
        },
        "feature_contract": {
            "core_feature_count": len(core_features),
            "candidate_feature_sets": feature_sets,
            "market_features_used": False,
            "web_context_features_used": False,
            "current_match_result_used": False,
            "conditional_total_is_known_only_inside_component_evaluation": True,
        },
        "folds": fold_receipts,
        "pooled": {
            "common_T1_to_T6_beta_vs_current_multinomial": {
                "rows": len(main_meta),
                "beta_binomial_metrics": summary(main_model),
                "current_multinomial_metrics": summary(main_baseline),
                "delta_beta_minus_current": deltas(main_model, main_baseline),
                "bootstrap_competition_window_90": main_bootstrap,
                "proper_score_robust": main_proper_robust,
                "exact_score_top1_improves": main_exact_top1_improves,
            },
            "full_exact_total_range_beta_vs_competition_reference": {
                "rows": len(full_meta),
                "beta_binomial_metrics": summary(full_model),
                "competition_reference_metrics": summary(full_baseline),
                "delta_beta_minus_reference": deltas(full_model, full_baseline),
                "bootstrap_competition_window_90": full_bootstrap,
            },
            "tail_T7plus_beta_vs_competition_reference": {
                "rows": len(tail_meta),
                "beta_binomial_metrics": summary(tail_model),
                "competition_reference_metrics": summary(tail_baseline),
                "delta_beta_minus_reference": deltas(tail_model, tail_baseline),
                "bootstrap_competition_window_90": tail_bootstrap,
            },
        },
        "audits": {
            "legal_mapping_failures": legal_failures,
            "probability_sum_max_residual": probability_residual,
            "T_D_parity_failures": 0 if legal_failures == 0 else None,
            "nonnegative_integer_score_support": legal_failures == 0,
        },
        "stability": {
            "rows": len(stability_rows),
            "common_competition_window_count": int(sum(row["task"] == "beta_vs_current_multinomial_T1_T6" for row in stability_rows)),
            "common_logloss_wins": int(sum(row["task"] == "beta_vs_current_multinomial_T1_T6" and row["delta_logloss"] < 0 for row in stability_rows)),
            "common_exact_top1_wins": int(sum(row["task"] == "beta_vs_current_multinomial_T1_T6" and row["delta_top1"] > 0 for row in stability_rows)),
        },
        "ruling": {
            "historical_component_replacement_allowed": status.startswith("PASS_") or status.startswith("PARTIAL_PASS_"),
            "formal_weight": 0,
            "promotion": False,
            "strict_PIT_market_context_rows": 0,
            "unified_score_matrix_allowed": False,
            "reason": "R4 validates only H|T,X allocation under realised T; direct total and strict PIT current-match inputs remain blocked",
            "current_match_probabilities_generated": False,
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
    frame = pd.DataFrame([
        {"total_goals_exact": 0, "home_goals_exact": 0, "away_goals_exact": 0},
        {"total_goals_exact": 2, "home_goals_exact": 1, "away_goals_exact": 1},
        {"total_goals_exact": 3, "home_goals_exact": 2, "away_goals_exact": 1},
    ])
    probabilities = [
        np.asarray([1.0]),
        np.asarray([0.2, 0.6, 0.2]),
        np.asarray([0.1, 0.2, 0.6, 0.1]),
    ]
    score = score_components(frame, probabilities)
    result = result_components(frame, probabilities)
    assert len(score) == len(result) == 3
    assert np.isfinite(score.to_numpy()).all()
    assert np.isfinite(result.to_numpy()).all()
    assert score.loc[0, "rps"] == 0.0
    assert abs(float(result.loc[1, "draw_binary_brier"]) - 0.16) < 1e-12
    assert float(result.loc[2, "draw_binary_brier"]) == 0.0


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
        "data_identity": result["data_identity"],
        "pooled": result["pooled"],
        "audits": result["audits"],
        "stability": result["stability"],
        "ruling": result["ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
