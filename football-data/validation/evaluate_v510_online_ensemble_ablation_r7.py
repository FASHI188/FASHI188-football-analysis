#!/usr/bin/env python3
"""R7 ablation: does the nonlinear expert improve the existing online mixture?

Viewed historical development only. Compare the original two-expert date-frozen mixture
(flat multinomial + linear continuation) against a three-expert mixture that adds the
R6 shallow nonlinear continuation expert. Eta is selected independently on each policy
season; test outcomes do not select models or weights.
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
from evaluate_v510_nonlinear_continuation_boosting_r6 import (
    boosting_continuation_models,
    boosting_probability,
    distribution_difference,
    loss_correlation,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_online_ensemble_ablation_r7.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_online_ensemble_ablation_r7_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_online_ensemble_ablation_r7_stability.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def flat_probability(model: Any, frame: pd.DataFrame, features: list[str], config: dict[str, Any]) -> np.ndarray:
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    return align_probability(model, frame[features], classes)


def tail_diagnostics(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    truth = (y == 7).astype(float)
    forecast = probability[:, 7]
    return {
        "observed_rate": float(truth.mean()),
        "mean_probability": float(forecast.mean()),
        "binary_brier": float(np.mean((forecast - truth) ** 2)),
        "margin_residual": float(forecast.mean() - truth.mean()),
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
    nonlinear_candidate = dict(config["nonlinear_expert"])

    fold_receipts: list[dict[str, Any]] = []
    all_meta: list[pd.DataFrame] = []
    all_flat: list[pd.DataFrame] = []
    all_two: list[pd.DataFrame] = []
    all_three: list[pd.DataFrame] = []
    stability_rows: list[dict[str, Any]] = []

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

        flat_train_model = make_model(flat_C, config)
        flat_train_model.fit(train[feature_names], train.total_class)
        policy_flat = flat_probability(flat_train_model, policy, feature_names, config)
        linear_train_models = continuation_models(train, feature_names, linear_C, config)
        policy_linear = continuation_probability(linear_train_models, policy, feature_names, config)
        nonlinear_train_models = boosting_continuation_models(
            train, feature_names, nonlinear_candidate, config
        )
        policy_nonlinear = boosting_probability(
            nonlinear_train_models, policy, feature_names, config
        )
        eta_two, policy_weights_two, eta_grid_two = select_eta(
            policy, [policy_flat, policy_linear], config
        )
        eta_three, policy_weights_three, eta_grid_three = select_eta(
            policy, [policy_flat, policy_linear, policy_nonlinear], config
        )

        flat_fit_model = make_model(flat_C, config)
        flat_fit_model.fit(fit[feature_names], fit.total_class)
        test_flat_probability = flat_probability(flat_fit_model, test, feature_names, config)
        linear_fit_models = continuation_models(fit, feature_names, linear_C, config)
        test_linear_probability = continuation_probability(
            linear_fit_models, test, feature_names, config
        )
        nonlinear_fit_models = boosting_continuation_models(
            fit, feature_names, nonlinear_candidate, config
        )
        test_nonlinear_probability = boosting_probability(
            nonlinear_fit_models, test, feature_names, config
        )
        test_two_probability, final_weights_two, updates_two = daily_mixture(
            test,
            [test_flat_probability, test_linear_probability],
            eta_two,
            initial_weights=policy_weights_two,
        )
        test_three_probability, final_weights_three, updates_three = daily_mixture(
            test,
            [test_flat_probability, test_linear_probability, test_nonlinear_probability],
            eta_three,
            initial_weights=policy_weights_three,
        )

        y = test.total_class.to_numpy(int)
        flat_components = metric_components(y, test_flat_probability, classes)
        two_components = metric_components(y, test_two_probability, classes)
        three_components = metric_components(y, test_three_probability, classes)
        for components in (flat_components, two_components, three_components):
            components.index = test.index

        fold_receipts.append({
            "fold": str(test.fold.iloc[0]),
            "rows": {"train": len(train), "policy": len(policy), "fit": len(fit), "test": len(test)},
            "base_models": {
                "flat_selected_C": flat_C,
                "flat_policy_grid": flat_grid,
                "linear_selected_C": linear_C,
                "linear_policy_grid": linear_grid,
                "nonlinear_candidate": nonlinear_candidate,
            },
            "two_expert": {
                "selected_eta": eta_two,
                "policy_eta_grid": eta_grid_two,
                "policy_final_weights": [float(value) for value in policy_weights_two],
                "test_final_weights": [float(value) for value in final_weights_two],
                "daily_updates": len(updates_two),
                "metrics": metric_summary(two_components),
                "tail": tail_diagnostics(y, test_two_probability),
            },
            "three_expert": {
                "selected_eta": eta_three,
                "policy_eta_grid": eta_grid_three,
                "policy_final_weights": [float(value) for value in policy_weights_three],
                "test_final_weights": [float(value) for value in final_weights_three],
                "daily_updates": len(updates_three),
                "metrics": metric_summary(three_components),
                "tail": tail_diagnostics(y, test_three_probability),
                "delta_minus_two": {
                    metric: float(three_components[metric].mean() - two_components[metric].mean())
                    for metric in two_components.columns
                },
                "delta_minus_flat": {
                    metric: float(three_components[metric].mean() - flat_components[metric].mean())
                    for metric in flat_components.columns
                },
                "difference_from_two": distribution_difference(
                    test_three_probability, test_two_probability
                ),
                "row_logloss_correlation_with_two": loss_correlation(
                    y, test_three_probability, test_two_probability
                ),
            },
            "same_day_predictions_frozen": True,
        })

        all_meta.append(test[["competition_id", "season", "fold"]])
        all_flat.append(flat_components)
        all_two.append(two_components)
        all_three.append(three_components)
        for competition, indexes in test.groupby("competition_id").groups.items():
            indexes = list(indexes)
            flat_summary = metric_summary(flat_components.loc[indexes])
            two_summary = metric_summary(two_components.loc[indexes])
            three_summary = metric_summary(three_components.loc[indexes])
            stability_rows.append({
                "competition_id": competition,
                "fold": str(test.fold.iloc[0]),
                "rows": len(indexes),
                **{f"flat_{key}": value for key, value in flat_summary.items()},
                **{f"two_{key}": value for key, value in two_summary.items()},
                **{f"three_{key}": value for key, value in three_summary.items()},
                **{f"three_delta_two_{key}": three_summary[key] - two_summary[key] for key in two_summary},
                **{f"three_delta_flat_{key}": three_summary[key] - flat_summary[key] for key in flat_summary},
            })

    meta = pd.concat(all_meta, ignore_index=True)
    flat = pd.concat(all_flat, ignore_index=True)
    two = pd.concat(all_two, ignore_index=True)
    three = pd.concat(all_three, ignore_index=True)

    def delta(model: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, float]:
        return {metric: float(model[metric].mean() - baseline[metric].mean()) for metric in baseline.columns}

    three_delta_two = delta(three, two)
    three_delta_flat = delta(three, flat)
    boot_two = bootstrap(meta, three, two, ["competition_id", "fold"], config)
    boot_flat = bootstrap(meta, three, flat, ["competition_id", "fold"], config)
    proper = ("logloss", "brier", "rps")
    robust_over_two = all(boot_two[metric]["p95"] < 0 for metric in proper)
    robust_over_flat = all(boot_flat[metric]["p95"] < 0 for metric in proper)
    if robust_over_two and robust_over_flat:
        status = "PASS_R7_THREE_EXPERT_INCREMENTAL_PROPER_SCORE_GAIN"
    elif robust_over_flat:
        status = "PARTIAL_PASS_R7_THREE_EXPERT_ONLY_ROBUST_OVER_FLAT"
    else:
        status = "FAIL_R7_NONLINEAR_EXPERT_NO_INCREMENTAL_ENSEMBLE_GAIN"

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
            "test_outcomes_used_for_eta_or_model_selection": False,
            "repeated_historical_replay_allowed": True,
            "independent_confirmation_claim_allowed": False,
        },
        "algorithm_contract": {
            "two_expert": "flat multinomial plus linear continuation, date-frozen exponential Log Loss weights",
            "three_expert": "two-expert set plus the shallow nonlinear continuation expert selected in all three R6 policy folds",
            "eta_selected_separately_on_policy": True,
            "probability_simplex_preserved": True,
        },
        "folds": fold_receipts,
        "pooled": {
            "test_rows": len(meta),
            "flat_metrics": metric_summary(flat),
            "two_expert_metrics": metric_summary(two),
            "three_expert_metrics": metric_summary(three),
            "three_delta_minus_two": three_delta_two,
            "three_delta_minus_flat": three_delta_flat,
            "three_bootstrap_vs_two_90": boot_two,
            "three_bootstrap_vs_flat_90": boot_flat,
            "three_robust_over_two": robust_over_two,
            "three_robust_over_flat": robust_over_flat,
        },
        "stability": {
            "competition_window_count": len(stability_rows),
            "three_logloss_wins_vs_two": int(sum(row["three_delta_two_logloss"] < 0 for row in stability_rows)),
            "three_brier_wins_vs_two": int(sum(row["three_delta_two_brier"] < 0 for row in stability_rows)),
            "three_rps_wins_vs_two": int(sum(row["three_delta_two_rps"] < 0 for row in stability_rows)),
            "three_top1_wins_vs_two": int(sum(row["three_delta_two_top1"] > 0 for row in stability_rows)),
        },
        "ruling": {
            "nonlinear_expert_incrementally_retained": status.startswith("PASS_"),
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
    y = np.asarray([0, 7, 2], dtype=int)
    probability = np.asarray([
        [0.4, 0.2, 0.1, 0.1, 0.05, 0.05, 0.05, 0.05],
        [0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.1, 0.6],
        [0.1, 0.1, 0.4, 0.1, 0.1, 0.05, 0.05, 0.1],
    ])
    diagnostic = tail_diagnostics(y, probability)
    assert diagnostic["observed_rate"] == 1.0 / 3.0
    assert diagnostic["binary_brier"] >= 0


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
