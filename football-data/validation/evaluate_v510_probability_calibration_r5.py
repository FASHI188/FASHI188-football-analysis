#!/usr/bin/env python3
"""R5 probability calibration challenge for the direct total-goals distribution.

Viewed historical development only. The base direct-total model is trained without the
policy season. The policy season is split by complete date packets into an early
calibration-fit segment and a later calibration-selection segment. No test outcome is
used to fit or select a calibrator. The selected calibrator is then refit on the full
policy packet and applied to the next chronological test season.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import softmax

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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_probability_calibration_r5.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_probability_calibration_r5_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_probability_calibration_r5_stability.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def model_config(config: dict[str, Any]) -> dict[str, Any]:
    output = dict(config)
    output["model_contract"] = dict(config["base_model"])
    return output


def normalize(probability: np.ndarray, minimum: float) -> np.ndarray:
    output = np.asarray(probability, dtype=float)
    if output.ndim != 2:
        raise ResearchError("probability array must be two-dimensional")
    output = np.clip(output, minimum, 1.0)
    output /= output.sum(axis=1, keepdims=True)
    return output


def split_policy_packets(policy: pd.DataFrame, fraction: float) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    dates = sorted(str(value) for value in policy.date_key.unique())
    if len(dates) < 4:
        raise ResearchError("policy season has fewer than four complete date packets")
    cut = int(math.floor(len(dates) * fraction))
    cut = min(max(cut, 2), len(dates) - 2)
    fit_dates = set(dates[:cut])
    calibration_fit = policy[policy.date_key.astype(str).isin(fit_dates)].copy()
    calibration_selection = policy[~policy.date_key.astype(str).isin(fit_dates)].copy()
    if min(len(calibration_fit), len(calibration_selection)) <= 0:
        raise ResearchError("empty calibration policy split")
    return calibration_fit, calibration_selection, {
        "date_packets": len(dates),
        "fit_date_packets": cut,
        "selection_date_packets": len(dates) - cut,
        "fit_rows": len(calibration_fit),
        "selection_rows": len(calibration_selection),
        "last_fit_date": dates[cut - 1],
        "first_selection_date": dates[cut],
    }


def nll(y: np.ndarray, probability: np.ndarray) -> float:
    return float(-np.log(np.clip(probability[np.arange(len(y)), y], 1e-15, 1.0)).mean())


def brier(y: np.ndarray, probability: np.ndarray) -> float:
    one_hot = np.zeros_like(probability)
    one_hot[np.arange(len(y)), y] = 1.0
    return float(((probability - one_hot) ** 2).sum(axis=1).mean())


def temperature_probability(probability: np.ndarray, temperature: float, minimum: float) -> np.ndarray:
    if temperature <= 0:
        raise ResearchError("temperature must be positive")
    logits = np.log(np.clip(probability, minimum, 1.0)) / temperature
    return softmax(logits, axis=1)


def prior_blend_probability(
    probability: np.ndarray,
    alpha: float,
    prior: np.ndarray,
    minimum: float,
) -> np.ndarray:
    if not 0.0 <= alpha <= 1.0:
        raise ResearchError("prior blend alpha must be in [0,1]")
    output = (1.0 - alpha) * probability + alpha * prior.reshape(1, -1)
    return normalize(output, minimum)


def empirical_prior(y: np.ndarray, classes: int, alpha: float = 1.0) -> np.ndarray:
    counts = np.bincount(y, minlength=classes).astype(float) + alpha
    return counts / counts.sum()


def vector_probability(
    probability: np.ndarray,
    parameters: np.ndarray,
    minimum: float,
) -> np.ndarray:
    classes = probability.shape[1]
    parameters = np.asarray(parameters, dtype=float)
    if len(parameters) != classes + classes - 1:
        raise ResearchError("vector scaling parameter length mismatch")
    log_scale = parameters[:classes]
    bias = np.concatenate([parameters[classes:], np.asarray([0.0])])
    logits = np.log(np.clip(probability, minimum, 1.0)) * np.exp(log_scale) + bias
    return softmax(logits, axis=1)


def fit_vector_scaling(
    probability: np.ndarray,
    y: np.ndarray,
    l2: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    classes = probability.shape[1]
    contract = config["calibration_candidates"]["vector_logit_scaling"]
    initial = np.zeros(classes + classes - 1, dtype=float)

    def objective(parameters: np.ndarray) -> float:
        calibrated = vector_probability(
            probability,
            parameters,
            float(config["selection"]["minimum_probability"]),
        )
        penalty = float(l2) * float(np.dot(parameters, parameters)) / max(len(y), 1)
        return nll(y, calibrated) + penalty

    bounds = [(-2.0, 2.0)] * classes + [(-3.0, 3.0)] * (classes - 1)
    fitted = minimize(
        objective,
        initial,
        method="L-BFGS-B",
        bounds=bounds,
        options={
            "maxiter": int(contract["max_iter"]),
            "ftol": float(contract["tolerance"]),
        },
    )
    if not np.isfinite(fitted.fun):
        raise ResearchError("vector scaling objective is non-finite")
    return {
        "parameters": np.asarray(fitted.x, dtype=float),
        "success": bool(fitted.success),
        "status": int(fitted.status),
        "message": str(fitted.message),
        "iterations": int(fitted.nit),
        "objective": float(fitted.fun),
        "l2": float(l2),
    }


def candidate_catalog(
    fit_probability: np.ndarray,
    fit_y: np.ndarray,
    selection_probability: np.ndarray,
    selection_y: np.ndarray,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    minimum = float(config["selection"]["minimum_probability"])
    candidates: list[dict[str, Any]] = []

    def add(name: str, hyper: dict[str, Any], probability: np.ndarray, complexity: int, fit_receipt: dict[str, Any] | None = None) -> None:
        candidates.append({
            "name": name,
            "hyperparameters": hyper,
            "selection_logloss": nll(selection_y, probability),
            "selection_brier": brier(selection_y, probability),
            "complexity_rank": complexity,
            "fit_receipt": fit_receipt,
            "probability_sum_max_residual": float(np.max(np.abs(probability.sum(axis=1) - 1.0))),
        })

    add("identity", {}, selection_probability.copy(), 0)

    for temperature in config["calibration_candidates"]["temperature_scaling"]["temperature_grid"]:
        calibrated = temperature_probability(selection_probability, float(temperature), minimum)
        add("temperature_scaling", {"temperature": float(temperature)}, calibrated, 1)

    fit_prior = empirical_prior(fit_y, fit_probability.shape[1])
    for alpha in config["calibration_candidates"]["prior_blend"]["alpha_grid"]:
        calibrated = prior_blend_probability(selection_probability, float(alpha), fit_prior, minimum)
        add("prior_blend", {"alpha": float(alpha)}, calibrated, 1)

    for l2 in config["calibration_candidates"]["vector_logit_scaling"]["l2_grid"]:
        fitted = fit_vector_scaling(fit_probability, fit_y, float(l2), config)
        calibrated = vector_probability(selection_probability, fitted["parameters"], minimum)
        add(
            "vector_logit_scaling",
            {"l2": float(l2)},
            calibrated,
            2,
            {key: value for key, value in fitted.items() if key != "parameters"},
        )

    return candidates


def choose_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return min(
        candidates,
        key=lambda row: (
            row["selection_logloss"],
            row["selection_brier"],
            row["complexity_rank"],
            row["name"],
            json.dumps(row["hyperparameters"], sort_keys=True),
        ),
    )


def refit_and_apply(
    selected: dict[str, Any],
    policy_probability: np.ndarray,
    policy_y: np.ndarray,
    test_probability: np.ndarray,
    config: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    minimum = float(config["selection"]["minimum_probability"])
    name = str(selected["name"])
    hyper = dict(selected["hyperparameters"])
    if name == "identity":
        return test_probability.copy(), {"name": name, "hyperparameters": hyper}
    if name == "temperature_scaling":
        return temperature_probability(test_probability, float(hyper["temperature"]), minimum), {
            "name": name,
            "hyperparameters": hyper,
        }
    if name == "prior_blend":
        prior = empirical_prior(policy_y, policy_probability.shape[1])
        calibrated = prior_blend_probability(test_probability, float(hyper["alpha"]), prior, minimum)
        return calibrated, {
            "name": name,
            "hyperparameters": hyper,
            "policy_prior": [float(value) for value in prior],
        }
    if name == "vector_logit_scaling":
        fitted = fit_vector_scaling(policy_probability, policy_y, float(hyper["l2"]), config)
        calibrated = vector_probability(test_probability, fitted["parameters"], minimum)
        return calibrated, {
            "name": name,
            "hyperparameters": hyper,
            "fit_receipt": {key: value for key, value in fitted.items() if key != "parameters"},
            "log_scales": [float(value) for value in fitted["parameters"][: probability_classes(test_probability)]],
            "biases_except_reference": [float(value) for value in fitted["parameters"][probability_classes(test_probability):]],
        }
    raise ResearchError(f"unknown selected calibrator {name}")


def probability_classes(probability: np.ndarray) -> int:
    return int(probability.shape[1])


def calibration_diagnostics(
    y: np.ndarray,
    probability: np.ndarray,
    bins: int,
) -> dict[str, Any]:
    classes = probability.shape[1]
    edges = np.linspace(0.0, 1.0, bins + 1)
    class_ece: dict[str, float] = {}
    class_max_residual: dict[str, float] = {}
    all_residuals: list[float] = []
    for class_index in range(classes):
        p = probability[:, class_index]
        truth = (y == class_index).astype(float)
        ece = 0.0
        residuals: list[float] = []
        for bin_index in range(bins):
            lower, upper = edges[bin_index], edges[bin_index + 1]
            mask = (p >= lower) & ((p < upper) if bin_index < bins - 1 else (p <= upper))
            if not np.any(mask):
                continue
            residual = abs(float(p[mask].mean() - truth[mask].mean()))
            residuals.append(residual)
            all_residuals.append(residual)
            ece += float(mask.mean()) * residual
        class_ece[str(class_index)] = float(ece)
        class_max_residual[str(class_index)] = float(max(residuals) if residuals else 0.0)
    observed = np.bincount(y, minlength=classes).astype(float) / len(y)
    predicted = probability.mean(axis=0)
    return {
        "macro_class_ece": float(np.mean(list(class_ece.values()))),
        "max_bin_residual": float(max(all_residuals) if all_residuals else 0.0),
        "class_ece": class_ece,
        "class_max_bin_residual": class_max_residual,
        "observed_class_rate": {str(index): float(value) for index, value in enumerate(observed)},
        "predicted_class_rate": {str(index): float(value) for index, value in enumerate(predicted)},
        "mean_absolute_class_margin_residual": float(np.mean(np.abs(predicted - observed))),
        "max_absolute_class_margin_residual": float(np.max(np.abs(predicted - observed))),
    }


def cutoff_diagnostics(y: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for cutoff in range(probability.shape[1] - 1):
        forecast = probability[:, : cutoff + 1].sum(axis=1)
        truth = (y <= cutoff).astype(float)
        rows[str(cutoff)] = {
            "binary_brier": float(np.mean((forecast - truth) ** 2)),
            "forecast_mean": float(forecast.mean()),
            "observed_rate": float(truth.mean()),
            "absolute_margin_residual": abs(float(forecast.mean() - truth.mean())),
        }
    return rows


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
    local_config = model_config(config)
    classes = [int(value) for value in config["base_model"]["direct_total_classes"]]
    minimum = float(config["selection"]["minimum_probability"])

    fold_receipts: list[dict[str, Any]] = []
    all_meta: list[pd.DataFrame] = []
    all_raw: list[pd.DataFrame] = []
    all_calibrated: list[pd.DataFrame] = []
    stability_rows: list[dict[str, Any]] = []
    selected_counter: Counter[str] = Counter()
    selected_hyper_counter: Counter[str] = Counter()

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        train = fold[fold.split == "train"]
        policy = fold[fold.split == "policy"].copy()
        fit = fold[fold.split.isin(["train", "policy"])]
        test = fold[fold.split == "test"].copy()
        calibration_fit, calibration_selection, policy_split = split_policy_packets(
            policy,
            float(config["split_contract"]["policy_calibration_fit_fraction"]),
        )

        selected_C, C_grid = select_C(
            train,
            calibration_fit,
            feature_names,
            "total_class",
            classes,
            local_config,
        )
        policy_base_model = make_model(selected_C, local_config)
        policy_base_model.fit(train[feature_names], train.total_class)
        policy_probability = align_probability(policy_base_model, policy[feature_names], classes)
        fit_positions = policy.index.get_indexer(calibration_fit.index)
        selection_positions = policy.index.get_indexer(calibration_selection.index)
        if np.any(fit_positions < 0) or np.any(selection_positions < 0):
            raise ResearchError("policy calibration indexes are not aligned")
        fit_probability = policy_probability[fit_positions]
        selection_probability = policy_probability[selection_positions]
        fit_y = calibration_fit.total_class.to_numpy(int)
        selection_y = calibration_selection.total_class.to_numpy(int)

        candidates = candidate_catalog(
            fit_probability,
            fit_y,
            selection_probability,
            selection_y,
            config,
        )
        selected = choose_candidate(candidates)
        selected_counter[str(selected["name"])] += 1
        selected_hyper_counter[f"{selected['name']}:{json.dumps(selected['hyperparameters'], sort_keys=True)}"] += 1

        test_base_model = make_model(selected_C, local_config)
        test_base_model.fit(fit[feature_names], fit.total_class)
        raw_test_probability = align_probability(test_base_model, test[feature_names], classes)
        calibrated_test_probability, final_receipt = refit_and_apply(
            selected,
            policy_probability,
            policy.total_class.to_numpy(int),
            raw_test_probability,
            config,
        )
        calibrated_test_probability = normalize(calibrated_test_probability, minimum)
        residual = float(np.max(np.abs(calibrated_test_probability.sum(axis=1) - 1.0)))
        if residual > float(config["selection"]["probability_sum_tolerance"]):
            raise ResearchError(f"calibrated probability sum residual {residual}")

        y = test.total_class.to_numpy(int)
        raw_components = metric_components(y, raw_test_probability, classes)
        calibrated_components = metric_components(y, calibrated_test_probability, classes)
        raw_components.index = test.index
        calibrated_components.index = test.index

        fold_receipts.append({
            "fold": str(test.fold.iloc[0]),
            "rows": {
                "train": len(train),
                "policy": len(policy),
                "fit_train_plus_policy": len(fit),
                "test": len(test),
            },
            "policy_calibration_split": policy_split,
            "base_model": {
                "selected_C": selected_C,
                "C_grid": C_grid,
            },
            "candidate_catalog": candidates,
            "selected_calibrator": selected,
            "final_refit": final_receipt,
            "test": {
                "raw_metrics": metric_summary(raw_components),
                "calibrated_metrics": metric_summary(calibrated_components),
                "delta_calibrated_minus_raw": {
                    metric: float(calibrated_components[metric].mean() - raw_components[metric].mean())
                    for metric in raw_components.columns
                },
                "raw_calibration": calibration_diagnostics(y, raw_test_probability, int(config["selection"]["calibration_bins"])),
                "calibrated_calibration": calibration_diagnostics(y, calibrated_test_probability, int(config["selection"]["calibration_bins"])),
                "raw_cutoffs": cutoff_diagnostics(y, raw_test_probability),
                "calibrated_cutoffs": cutoff_diagnostics(y, calibrated_test_probability),
                "probability_sum_max_residual": residual,
            },
        })

        all_meta.append(test[["competition_id", "season", "fold"]])
        all_raw.append(raw_components)
        all_calibrated.append(calibrated_components)
        for competition, indexes in test.groupby("competition_id").groups.items():
            indexes = list(indexes)
            raw_summary = metric_summary(raw_components.loc[indexes])
            calibrated_summary = metric_summary(calibrated_components.loc[indexes])
            stability_rows.append({
                "competition_id": competition,
                "fold": str(test.fold.iloc[0]),
                "rows": len(indexes),
                "selected_calibrator": str(selected["name"]),
                **{f"raw_{key}": value for key, value in raw_summary.items()},
                **{f"calibrated_{key}": value for key, value in calibrated_summary.items()},
                **{f"delta_{key}": calibrated_summary[key] - raw_summary[key] for key in raw_summary},
            })

    meta = pd.concat(all_meta, ignore_index=True)
    raw_components = pd.concat(all_raw, ignore_index=True)
    calibrated_components = pd.concat(all_calibrated, ignore_index=True)
    delta = {
        metric: float(calibrated_components[metric].mean() - raw_components[metric].mean())
        for metric in raw_components.columns
    }
    boot = bootstrap(meta, calibrated_components, raw_components, ["competition_id", "fold"], config)
    logloss_robust = boot["logloss"]["p95"] < 0
    brier_robust = boot["brier"]["p95"] < 0
    rps_not_worse = boot["rps"]["p95"] <= 0
    if logloss_robust and brier_robust and rps_not_worse:
        status = "PASS_R5_CALIBRATION_PROPER_SCORE_ROBUST"
    elif logloss_robust and delta["brier"] < 0:
        status = "PARTIAL_PASS_R5_CALIBRATION_LOGLOSS_ROBUST_BRIER_MEAN_ONLY"
    else:
        status = "FAIL_R5_CALIBRATION_NO_ROBUST_GAIN"

    result = {
        "schema_version": config["schema_version"],
        "status": status,
        "evidence_class": config["evidence_class"],
        "data_identity": identity,
        "split_contract": {
            "complete_seasons": seasons,
            "excluded_incomplete_latest_seasons": excluded,
            "rolling_windows": len(fold_receipts),
            "policy_split_by_complete_date_packets": True,
            "same_day_freeze_before_update": True,
            "test_outcomes_used_for_calibration_fit_or_selection": False,
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
            "base": "multinomial logistic P(T=0..6,7+|X)",
            "calibration_candidates": list(config["calibration_candidates"].keys()),
            "selection": "early policy date packets fit calibrators; later policy date packets select candidate; selected candidate refit on full policy before next-season test",
            "normalization_aware": True,
            "probability_simplex_preserved": True,
        },
        "folds": fold_receipts,
        "pooled": {
            "test_rows": len(meta),
            "raw_metrics": metric_summary(raw_components),
            "calibrated_metrics": metric_summary(calibrated_components),
            "delta_calibrated_minus_raw": delta,
            "bootstrap_competition_window_90": boot,
            "selected_calibrator_counts": dict(selected_counter),
            "selected_hyperparameter_counts": dict(selected_hyper_counter),
            "logloss_robust": logloss_robust,
            "brier_robust": brier_robust,
            "rps_not_worse_at_90_percent": rps_not_worse,
        },
        "stability": {
            "competition_window_count": len(stability_rows),
            "logloss_wins": int(sum(row["delta_logloss"] < 0 for row in stability_rows)),
            "brier_wins": int(sum(row["delta_brier"] < 0 for row in stability_rows)),
            "rps_wins": int(sum(row["delta_rps"] < 0 for row in stability_rows)),
            "top1_wins": int(sum(row["delta_top1"] > 0 for row in stability_rows)),
        },
        "ruling": {
            "historical_calibration_component_retained": status.startswith("PASS_") or status.startswith("PARTIAL_PASS_"),
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
    probability = np.asarray([[0.2, 0.3, 0.5], [0.7, 0.2, 0.1]], dtype=float)
    y = np.asarray([2, 0], dtype=int)
    for temperature in (0.8, 1.2):
        transformed = temperature_probability(probability, temperature, 1e-12)
        assert np.max(np.abs(transformed.sum(axis=1) - 1.0)) < 1e-12
    prior = empirical_prior(y, 3)
    blended = prior_blend_probability(probability, 0.1, prior, 1e-12)
    assert np.max(np.abs(blended.sum(axis=1) - 1.0)) < 1e-12
    config = {
        "calibration_candidates": {"vector_logit_scaling": {"max_iter": 50, "tolerance": 1e-8}},
        "selection": {"minimum_probability": 1e-12},
    }
    fitted = fit_vector_scaling(probability, y, 1.0, config)
    vector = vector_probability(probability, fitted["parameters"], 1e-12)
    assert np.isfinite(vector).all()
    assert np.max(np.abs(vector.sum(axis=1) - 1.0)) < 1e-12
    diagnostics = calibration_diagnostics(y, probability, 5)
    assert diagnostics["macro_class_ece"] >= 0


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
