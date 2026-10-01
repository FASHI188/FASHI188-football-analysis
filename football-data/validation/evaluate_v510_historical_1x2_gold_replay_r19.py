#!/usr/bin/env python3
"""R19 direct 1X2 historical-development replay.

Uses the existing 26,873-row score/identity ledger. Every feature for a match is frozen
before that match and before any same-day update. Candidate choice is made on the policy
season only. A deterministic label-blind 1,000-match sample is used for fast development;
the full 15,639 rolling-test pool remains the stage-acceptance audit.

This is VIEWED_HISTORICAL_DEVELOPMENT. It is not independent confirmation and cannot
produce current-match probabilities, a unified score matrix, exact scores or EV.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_historical_1x2_gold_replay_r19.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_historical_1x2_gold_replay_r19_status.json"
DEFAULT_GOLD = ROOT / "manifests" / "v510_historical_1x2_gold_replay_r19_gold.csv"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_historical_1x2_gold_replay_r19_stability.csv"
CLASSES = [0, 1, 2]  # away, draw, home


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("R19 config root must be object")
    return value


def result_label(goal_difference: pd.Series) -> np.ndarray:
    values = goal_difference.to_numpy(float)
    return np.where(values > 0, 2, np.where(values == 0, 1, 0)).astype(int)


def align_probability(model: Any, frame: pd.DataFrame) -> np.ndarray:
    raw = np.asarray(model.predict_proba(frame), dtype=float)
    output = np.zeros((len(frame), len(CLASSES)), dtype=float)
    for source, label in enumerate(model.classes_):
        output[:, CLASSES.index(int(label))] = raw[:, source]
    output = np.clip(output, 1e-15, 1.0)
    output /= output.sum(axis=1, keepdims=True)
    return output


def baseline_probability(frame: pd.DataFrame, train_y: np.ndarray) -> np.ndarray:
    prior = np.bincount(train_y, minlength=3).astype(float) + 1.0
    prior /= prior.sum()
    home = pd.to_numeric(frame["comp_home_win"], errors="coerce").to_numpy(float)
    draw = pd.to_numeric(frame["comp_draw"], errors="coerce").to_numpy(float)
    away = 1.0 - home - draw
    probability = np.column_stack([away, draw, home])
    bad = ~np.isfinite(probability).all(axis=1) | (probability < 0).any(axis=1)
    probability[bad] = prior
    probability = np.clip(probability, 1e-12, None)
    probability /= probability.sum(axis=1, keepdims=True)
    return probability


def make_candidate(spec: dict[str, Any], config: dict[str, Any]) -> Pipeline:
    kind = str(spec["kind"])
    if kind == "logistic":
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(
                C=float(spec["C"]),
                solver="lbfgs",
                max_iter=1500,
                tol=1e-6,
                multi_class="auto",
                random_state=51019,
            )),
        ])
    if kind == "hist_gradient_boosting":
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("model", HistGradientBoostingClassifier(
                learning_rate=float(spec["learning_rate"]),
                max_leaf_nodes=int(spec["max_leaf_nodes"]),
                max_iter=int(spec["max_iter"]),
                l2_regularization=float(spec["l2_regularization"]),
                random_state=51019,
            )),
        ])
    raise ResearchError(f"unknown R19 candidate kind: {kind}")


def candidate_catalog(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in config["model_contract"]["logistic_C_grid"]:
        rows.append({"name": f"logistic_C_{value}", "kind": "logistic", "C": float(value)})
    for index, value in enumerate(config["model_contract"]["hist_gradient_boosting_candidates"], start=1):
        rows.append({"name": f"hist_gradient_boosting_{index}", "kind": "hist_gradient_boosting", **value})
    return rows


def row_components(y: np.ndarray, probability: np.ndarray) -> pd.DataFrame:
    p = np.clip(np.asarray(probability, dtype=float), 1e-15, 1.0)
    p /= p.sum(axis=1, keepdims=True)
    one_hot = np.eye(3, dtype=float)[y]
    cumulative_p = np.cumsum(p, axis=1)[:, :-1]
    cumulative_y = np.cumsum(one_hot, axis=1)[:, :-1]
    prediction = np.argmax(p, axis=1)
    return pd.DataFrame({
        "logloss": -np.log(p[np.arange(len(y)), y]),
        "brier": np.sum((p - one_hot) ** 2, axis=1),
        "rps": np.mean((cumulative_p - cumulative_y) ** 2, axis=1),
        "top1": (prediction == y).astype(float),
        "predicted_class": prediction,
        "actual_class": y,
        "p_away": p[:, 0],
        "p_draw": p[:, 1],
        "p_home": p[:, 2],
    })


def metric_summary(components: pd.DataFrame) -> dict[str, float]:
    actual = components.actual_class.to_numpy(int)
    predicted = components.predicted_class.to_numpy(int)
    draw_actual = actual == 1
    draw_predicted = predicted == 1
    true_draw = int(np.sum(draw_actual & draw_predicted))
    predicted_draws = int(draw_predicted.sum())
    actual_draws = int(draw_actual.sum())
    recall = true_draw / actual_draws if actual_draws else 0.0
    precision = true_draw / predicted_draws if predicted_draws else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "rows": int(len(components)),
        "logloss": float(components.logloss.mean()),
        "brier": float(components.brier.mean()),
        "rps": float(components.rps.mean()),
        "top1_accuracy": float(components.top1.mean()),
        "draw_recall": float(recall),
        "draw_precision": float(precision),
        "draw_f1": float(f1),
        "predicted_draw_rate": float(draw_predicted.mean()),
        "actual_draw_rate": float(draw_actual.mean()),
        "mean_p_away": float(components.p_away.mean()),
        "mean_p_draw": float(components.p_draw.mean()),
        "mean_p_home": float(components.p_home.mean()),
    }


def delta(candidate: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, float]:
    output = {}
    for column in ("logloss", "brier", "rps", "top1"):
        output[column] = float(candidate[column].mean() - baseline[column].mean())
    return output


def select_candidate(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    y_train = result_label(train.goal_difference)
    y_policy = result_label(policy.goal_difference)
    receipts: list[dict[str, Any]] = []
    for spec in candidate_catalog(config):
        model = make_candidate(spec, config)
        model.fit(train[features], y_train)
        probability = align_probability(model, policy[features])
        components = row_components(y_policy, probability)
        summary = metric_summary(components)
        receipts.append({"candidate": spec, "policy_metrics": summary})
    receipts.sort(key=lambda row: (
        row["policy_metrics"]["logloss"],
        row["policy_metrics"]["brier"],
        row["policy_metrics"]["rps"],
        row["candidate"]["name"],
    ))
    return dict(receipts[0]["candidate"]), receipts


def identity_text(row: pd.Series) -> str:
    return "|".join(str(row[field]) for field in (
        "competition_id", "season", "date_key", "home_team", "away_team", "fold"
    ))


def deterministic_gold(meta: pd.DataFrame, rows: int, seed: int) -> pd.Index:
    work = meta.copy()
    work["identity"] = work.apply(identity_text, axis=1)
    if work.identity.duplicated().any():
        duplicates = work.loc[work.identity.duplicated(), "identity"].head().tolist()
        raise ResearchError(f"duplicate R19 test identities: {duplicates}")
    work["rank"] = work.identity.map(
        lambda value: hashlib.sha256(f"{seed}|{value}".encode("utf-8")).hexdigest()
    )
    return work.sort_values(["rank", "identity"]).head(rows).index


def paired_bootstrap(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    samples: int,
    seed: int,
    interval: list[float],
) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(seed)
    n = len(candidate)
    output: dict[str, dict[str, float]] = {}
    for metric in ("logloss", "brier", "rps", "top1"):
        values = candidate[metric].to_numpy(float) - baseline[metric].to_numpy(float)
        draws = np.empty(samples, dtype=float)
        for index in range(samples):
            selection = rng.integers(0, n, size=n)
            draws[index] = float(values[selection].mean())
        output[metric] = {
            "point": float(values.mean()),
            "p05": float(np.quantile(draws, float(interval[0]))),
            "p95": float(np.quantile(draws, float(interval[1]))),
        }
    return output


def write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8")


def run(config: dict[str, Any], out: Path, gold_path: Path, stability_path: Path) -> dict[str, Any]:
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    identity = audit_data_identity(raw, config)
    features = build_features(raw)
    core_features = select_core_features(features)
    seasons, excluded = complete_seasons(raw, config)

    all_meta: list[pd.DataFrame] = []
    all_baseline: list[pd.DataFrame] = []
    all_candidate: list[pd.DataFrame] = []
    fold_receipts: list[dict[str, Any]] = []
    stability_rows: list[dict[str, Any]] = []

    positions = [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]
    for test_position in positions:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold_name = f"window_{test_position - 1}_to_{test_position}"
        fold["fold"] = fold_name
        train = fold[fold.split == "train"].copy()
        policy = fold[fold.split == "policy"].copy()
        fit = fold[fold.split.isin(["train", "policy"])].copy()
        test = fold[fold.split == "test"].copy()
        if min(len(train), len(policy), len(test)) <= 0:
            raise ResearchError(f"empty R19 split at {fold_name}")

        selected, policy_grid = select_candidate(train, policy, core_features, config)
        model = make_candidate(selected, config)
        y_fit = result_label(fit.goal_difference)
        y_test = result_label(test.goal_difference)
        model.fit(fit[core_features], y_fit)
        candidate_probability = align_probability(model, test[core_features])
        baseline = baseline_probability(test, result_label(train.goal_difference))
        candidate_components = row_components(y_test, candidate_probability)
        baseline_components = row_components(y_test, baseline)
        candidate_components.index = test.index
        baseline_components.index = test.index

        meta = test[[
            "competition_id", "season", "date_key", "home_team", "away_team",
            "goal_difference", "fold"
        ]].copy()
        meta["actual_result"] = y_test
        all_meta.append(meta)
        all_candidate.append(candidate_components)
        all_baseline.append(baseline_components)

        fold_receipts.append({
            "fold": fold_name,
            "rows": {"train": len(train), "policy": len(policy), "fit": len(fit), "test": len(test)},
            "selected_candidate": selected,
            "policy_candidates": policy_grid,
            "baseline_metrics": metric_summary(baseline_components),
            "candidate_metrics": metric_summary(candidate_components),
            "candidate_delta_minus_baseline": delta(candidate_components, baseline_components),
            "same_day_predictions_frozen": True,
            "test_labels_used_for_selection": False,
        })

        for competition, indexes in test.groupby("competition_id").groups.items():
            base_summary = metric_summary(baseline_components.loc[indexes])
            cand_summary = metric_summary(candidate_components.loc[indexes])
            stability_rows.append({
                "competition_id": competition,
                "fold": fold_name,
                "rows": len(indexes),
                "selected_candidate": selected["name"],
                "baseline_logloss": base_summary["logloss"],
                "candidate_logloss": cand_summary["logloss"],
                "delta_logloss": cand_summary["logloss"] - base_summary["logloss"],
                "baseline_brier": base_summary["brier"],
                "candidate_brier": cand_summary["brier"],
                "delta_brier": cand_summary["brier"] - base_summary["brier"],
                "baseline_rps": base_summary["rps"],
                "candidate_rps": cand_summary["rps"],
                "delta_rps": cand_summary["rps"] - base_summary["rps"],
                "baseline_top1": base_summary["top1_accuracy"],
                "candidate_top1": cand_summary["top1_accuracy"],
                "delta_top1": cand_summary["top1_accuracy"] - base_summary["top1_accuracy"],
                "candidate_draw_recall": cand_summary["draw_recall"],
                "candidate_predicted_draw_rate": cand_summary["predicted_draw_rate"],
            })

    meta = pd.concat(all_meta).sort_index()
    baseline = pd.concat(all_baseline).sort_index()
    candidate = pd.concat(all_candidate).sort_index()
    if not (meta.index.equals(baseline.index) and meta.index.equals(candidate.index)):
        raise ResearchError("R19 pooled index alignment failure")

    sample_rows = int(config["gold_sample_contract"]["rows"])
    gold_index = deterministic_gold(meta, sample_rows, int(config["gold_sample_contract"]["seed"]))
    gold_meta = meta.loc[gold_index].copy()
    gold_baseline = baseline.loc[gold_index].copy()
    gold_candidate = candidate.loc[gold_index].copy()
    gold_export = gold_meta.copy()
    gold_export["identity"] = gold_export.apply(identity_text, axis=1)
    gold_export["baseline_p_away"] = gold_baseline.p_away
    gold_export["baseline_p_draw"] = gold_baseline.p_draw
    gold_export["baseline_p_home"] = gold_baseline.p_home
    gold_export["candidate_p_away"] = gold_candidate.p_away
    gold_export["candidate_p_draw"] = gold_candidate.p_draw
    gold_export["candidate_p_home"] = gold_candidate.p_home
    gold_export["baseline_prediction"] = gold_baseline.predicted_class
    gold_export["candidate_prediction"] = gold_candidate.predicted_class
    gold_export = gold_export.sort_values("identity")
    write_csv(gold_path, gold_export.reset_index(drop=True))
    write_csv(stability_path, pd.DataFrame(stability_rows))

    interval = [float(value) for value in config["evaluation"]["interval"]]
    full_boot = paired_bootstrap(
        candidate, baseline,
        int(config["evaluation"]["full_rolling_bootstrap_samples"]),
        int(config["evaluation"]["bootstrap_seed"]), interval,
    )
    gold_boot = paired_bootstrap(
        gold_candidate, gold_baseline,
        int(config["evaluation"]["gold_bootstrap_samples"]),
        int(config["evaluation"]["bootstrap_seed"]) + 1, interval,
    )
    full_summary_baseline = metric_summary(baseline)
    full_summary_candidate = metric_summary(candidate)
    gold_summary_baseline = metric_summary(gold_baseline)
    gold_summary_candidate = metric_summary(gold_candidate)

    full_primary_point = all(
        full_summary_candidate[key] < full_summary_baseline[key]
        for key in ("logloss", "brier", "rps")
    )
    full_primary_robust = all(full_boot[key]["p95"] < 0 for key in ("logloss", "brier", "rps"))
    gold_primary_point = all(
        gold_summary_candidate[key] < gold_summary_baseline[key]
        for key in ("logloss", "brier", "rps")
    )
    if full_primary_robust:
        status = "PASS_R19_HISTORICAL_1X2_FULL_ROLLING_PROPER_SCORE_SIGNAL"
    elif full_primary_point and gold_primary_point:
        status = "PARTIAL_PASS_R19_HISTORICAL_1X2_POINT_SIGNAL_NOT_ROBUST"
    else:
        status = "FAIL_R19_HISTORICAL_1X2_NO_RETAINABLE_SIGNAL"

    identity_list = gold_export.identity.tolist()
    gold_identity_sha256 = hashlib.sha256("\n".join(identity_list).encode("utf-8")).hexdigest()
    result = {
        "schema_version": "v510_historical_1x2_gold_replay_r19_status.1",
        "status": status,
        "classification": config["classification"],
        "data_identity": identity,
        "complete_seasons_excluded": excluded,
        "core_feature_count": len(core_features),
        "rolling_test_rows": int(len(meta)),
        "rolling_test_unique_identities": int(meta.apply(identity_text, axis=1).nunique()),
        "gold_sample": {
            "rows": int(len(gold_export)),
            "selection_uses_labels": False,
            "identity_sha256": gold_identity_sha256,
            "actual_result_counts": {
                "away_win": int((gold_meta.actual_result == 0).sum()),
                "draw": int((gold_meta.actual_result == 1).sum()),
                "home_win": int((gold_meta.actual_result == 2).sum()),
            },
            "baseline_metrics": gold_summary_baseline,
            "candidate_metrics": gold_summary_candidate,
            "candidate_delta_minus_baseline": delta(gold_candidate, gold_baseline),
            "paired_bootstrap": gold_boot,
        },
        "full_rolling": {
            "baseline_metrics": full_summary_baseline,
            "candidate_metrics": full_summary_candidate,
            "candidate_delta_minus_baseline": delta(candidate, baseline),
            "paired_bootstrap": full_boot,
            "proper_score_point_gate": full_primary_point,
            "proper_score_robust_gate": full_primary_robust,
        },
        "folds": fold_receipts,
        "ruling": {
            "historical_training_and_repeated_replay_allowed": True,
            "gold_sample_is_fast_development_screen": True,
            "full_rolling_test_is_stage_acceptance": True,
            "forward_matches_are_final_confirmation_not_training_prerequisite": True,
            "independent_confirmation_claim": False,
            "formal_weight": 0,
            "current_match_probability_allowed": False,
            "unified_matrix_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
            "fixed_outputs": ["总进球分布不可用。", "精确比分不可用。"],
        },
        "hard_limits": config["hard_limits"],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--stability", type=Path, default=DEFAULT_STABILITY)
    args = parser.parse_args()
    result = run(load_json(args.config), args.out, args.gold, args.stability)
    print(json.dumps({
        "status": result["status"],
        "rolling_test_rows": result["rolling_test_rows"],
        "gold_sample": result["gold_sample"],
        "full_rolling": result["full_rolling"],
        "ruling": result["ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
