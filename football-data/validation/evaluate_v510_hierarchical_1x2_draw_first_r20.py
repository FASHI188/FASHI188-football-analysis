#!/usr/bin/env python3
"""R20 hierarchical draw-first 1X2 historical development replay.

Factorization:
  q = P(draw | X)
  r = P(home win | non-draw, X)
  P(away) = (1-q)(1-r), P(draw)=q, P(home)=(1-q)r

All model and weight choices are made on policy seasons. Test labels are untouched until
scoring. No manual probability offset or post-hoc draw multiplier is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import evaluate_v510_historical_1x2_gold_replay_r19 as r19
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_hierarchical_1x2_draw_first_r20.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_hierarchical_1x2_draw_first_r20_status.json"
DEFAULT_GOLD = ROOT / "manifests" / "v510_hierarchical_1x2_draw_first_r20_gold.csv"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_hierarchical_1x2_draw_first_r20_stability.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("R20 config root must be object")
    return value


def binary_catalog(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in config["hierarchical_model"]["binary_logistic_C_grid"]:
        rows.append({"name": f"logistic_C_{value}", "kind": "logistic", "C": float(value)})
    for index, value in enumerate(config["hierarchical_model"]["hist_gradient_boosting_candidates"], start=1):
        rows.append({"name": f"hist_gradient_boosting_{index}", "kind": "hist_gradient_boosting", **value})
    return rows


def draw_catalog(config: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for model in binary_catalog(config):
        for weight in config["hierarchical_model"]["draw_positive_weight_grid"]:
            output.append({**model, "positive_weight": float(weight), "name": f"{model['name']}_draw_weight_{weight}"})
    return output


def make_binary_model(spec: dict[str, Any]) -> Pipeline:
    if spec["kind"] == "logistic":
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(
                C=float(spec["C"]), solver="lbfgs", max_iter=1500,
                tol=1e-6, random_state=51020,
            )),
        ])
    if spec["kind"] == "hist_gradient_boosting":
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("model", HistGradientBoostingClassifier(
                learning_rate=float(spec["learning_rate"]),
                max_leaf_nodes=int(spec["max_leaf_nodes"]),
                max_iter=int(spec["max_iter"]),
                l2_regularization=float(spec["l2_regularization"]),
                random_state=51020,
            )),
        ])
    raise ResearchError(f"unknown R20 binary candidate: {spec['kind']}")


def fit_binary(model: Pipeline, frame: pd.DataFrame, y: np.ndarray, positive_weight: float = 1.0) -> Pipeline:
    weights = np.where(y == 1, float(positive_weight), 1.0)
    model.fit(frame, y, model__sample_weight=weights)
    return model


def predict_positive(model: Pipeline, frame: pd.DataFrame) -> np.ndarray:
    probability = np.asarray(model.predict_proba(frame), dtype=float)
    classes = [int(value) for value in model.classes_]
    if 1 not in classes:
        raise ResearchError("R20 binary model missing positive class")
    output = probability[:, classes.index(1)]
    return np.clip(output, 1e-12, 1.0 - 1e-12)


def combine_probability(q_draw: np.ndarray, r_home_non_draw: np.ndarray) -> np.ndarray:
    q = np.clip(np.asarray(q_draw, dtype=float), 1e-12, 1.0 - 1e-12)
    r = np.clip(np.asarray(r_home_non_draw, dtype=float), 1e-12, 1.0 - 1e-12)
    probability = np.column_stack([(1.0 - q) * (1.0 - r), q, (1.0 - q) * r])
    probability = np.clip(probability, 1e-12, 1.0)
    probability /= probability.sum(axis=1, keepdims=True)
    return probability


def components(y: np.ndarray, probability: np.ndarray) -> pd.DataFrame:
    frame = r19.row_components(y, probability)
    draw_actual = (y == 1).astype(float)
    frame["draw_binary_brier"] = (frame.p_draw.to_numpy(float) - draw_actual) ** 2
    return frame


def summary(frame: pd.DataFrame) -> dict[str, float]:
    base = r19.metric_summary(frame)
    draw_mask = frame.actual_class.to_numpy(int) == 1
    non_draw_mask = ~draw_mask
    base.update({
        "draw_binary_brier": float(frame.draw_binary_brier.mean()),
        "draw_logloss": float(frame.loc[draw_mask, "logloss"].mean()) if draw_mask.any() else 0.0,
        "non_draw_logloss": float(frame.loc[non_draw_mask, "logloss"].mean()) if non_draw_mask.any() else 0.0,
        "non_draw_brier": float(frame.loc[non_draw_mask, "brier"].mean()) if non_draw_mask.any() else 0.0,
    })
    return base


def domain_delta(candidate: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, float]:
    cand = summary(candidate)
    base = summary(baseline)
    keys = (
        "logloss", "brier", "rps", "top1_accuracy", "draw_binary_brier",
        "draw_logloss", "non_draw_logloss", "non_draw_brier", "draw_recall",
        "draw_precision", "draw_f1", "predicted_draw_rate",
    )
    return {key: float(cand[key] - base[key]) for key in keys}


def binary_score(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    probability = np.clip(p, 1e-12, 1.0 - 1e-12)
    logloss = float(np.mean(-(y * np.log(probability) + (1 - y) * np.log(1 - probability))))
    brier = float(np.mean((probability - y) ** 2))
    return logloss, brier


def select_side_model(
    train: pd.DataFrame, policy: pd.DataFrame, features: list[str], config: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    train_non_draw = train[train.goal_difference != 0]
    policy_non_draw = policy[policy.goal_difference != 0]
    y_train = (train_non_draw.goal_difference.to_numpy(float) > 0).astype(int)
    y_policy = (policy_non_draw.goal_difference.to_numpy(float) > 0).astype(int)
    receipts: list[dict[str, Any]] = []
    for spec in binary_catalog(config):
        model = fit_binary(make_binary_model(spec), train_non_draw[features], y_train)
        probability = predict_positive(model, policy_non_draw[features])
        logloss, brier = binary_score(y_policy, probability)
        receipts.append({"candidate": spec, "conditional_logloss": logloss, "conditional_brier": brier})
    receipts.sort(key=lambda row: (row["conditional_logloss"], row["conditional_brier"], row["candidate"]["name"]))
    return dict(receipts[0]["candidate"]), receipts


def select_draw_model(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    features: list[str],
    side_spec: dict[str, Any],
    baseline_probability: np.ndarray,
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    y_train_draw = (train.goal_difference.to_numpy(float) == 0).astype(int)
    y_policy = r19.result_label(policy.goal_difference)
    train_non_draw = train[train.goal_difference != 0]
    y_train_side = (train_non_draw.goal_difference.to_numpy(float) > 0).astype(int)
    side_model = fit_binary(make_binary_model(side_spec), train_non_draw[features], y_train_side)
    side_probability = predict_positive(side_model, policy[features])
    baseline_components = components(y_policy, baseline_probability)
    baseline_summary = summary(baseline_components)
    safety = config["policy_selection"]["draw_candidate_safety"]

    receipts: list[dict[str, Any]] = []
    for spec in draw_catalog(config):
        draw_model = fit_binary(
            make_binary_model(spec), train[features], y_train_draw,
            float(spec["positive_weight"]),
        )
        draw_probability = predict_positive(draw_model, policy[features])
        probability = combine_probability(draw_probability, side_probability)
        candidate_components = components(y_policy, probability)
        candidate_summary = summary(candidate_components)
        safe = (
            candidate_summary["logloss"] - baseline_summary["logloss"] <= float(safety["joint_logloss_margin_vs_R19"])
            and candidate_summary["brier"] - baseline_summary["brier"] <= float(safety["joint_brier_margin_vs_R19"])
            and candidate_summary["non_draw_logloss"] - baseline_summary["non_draw_logloss"] <= float(safety["non_draw_logloss_margin_vs_R19"])
            and candidate_summary["non_draw_brier"] - baseline_summary["non_draw_brier"] <= float(safety["non_draw_brier_margin_vs_R19"])
        )
        receipts.append({
            "candidate": spec,
            "safe": safe,
            "metrics": candidate_summary,
            "delta_vs_R19": domain_delta(candidate_components, baseline_components),
        })
    receipts.sort(key=lambda row: (
        not row["safe"],
        row["metrics"]["draw_binary_brier"],
        row["metrics"]["logloss"],
        row["metrics"]["brier"],
        row["metrics"]["rps"],
        row["candidate"]["name"],
    ))
    return dict(receipts[0]["candidate"]), receipts


def bootstrap(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    samples: int,
    seed: int,
    interval: list[float],
) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(seed)
    n = len(candidate)
    output: dict[str, dict[str, float]] = {}
    metric_columns = ("logloss", "brier", "rps", "top1", "draw_binary_brier")
    for metric in metric_columns:
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

    non_draw = candidate.actual_class.to_numpy(int) != 1
    for metric in ("logloss", "brier"):
        values = candidate.loc[non_draw, metric].to_numpy(float) - baseline.loc[non_draw, metric].to_numpy(float)
        draws = np.empty(samples, dtype=float)
        size = len(values)
        for index in range(samples):
            selection = rng.integers(0, size, size=size)
            draws[index] = float(values[selection].mean())
        output[f"non_draw_{metric}"] = {
            "point": float(values.mean()),
            "p05": float(np.quantile(draws, float(interval[0]))),
            "p95": float(np.quantile(draws, float(interval[1]))),
        }
    return output


def run(config: dict[str, Any], out: Path, gold_path: Path, stability_path: Path) -> dict[str, Any]:
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    identity = audit_data_identity(raw, config)
    features = build_features(raw)
    core_features = select_core_features(features)
    seasons, excluded = complete_seasons(raw, config)

    all_meta: list[pd.DataFrame] = []
    all_baseline: list[pd.DataFrame] = []
    all_candidate: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    stability: list[dict[str, Any]] = []

    for test_position in config["split_contract"]["rolling_test_positions_zero_based"]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, int(test_position))
        fold_name = f"window_{int(test_position)-1}_to_{int(test_position)}"
        fold["fold"] = fold_name
        train = fold[fold.split == "train"].copy()
        policy = fold[fold.split == "policy"].copy()
        fit = fold[fold.split.isin(["train", "policy"])].copy()
        test = fold[fold.split == "test"].copy()
        if min(len(train), len(policy), len(test)) <= 0:
            raise ResearchError(f"empty R20 split: {fold_name}")

        r19_spec, _ = r19.select_candidate(train, policy, core_features, config)
        r19_policy_model = r19.make_candidate(r19_spec, config)
        r19_policy_model.fit(train[core_features], r19.result_label(train.goal_difference))
        r19_policy_probability = r19.align_probability(r19_policy_model, policy[core_features])

        side_spec, side_grid = select_side_model(train, policy, core_features, config)
        draw_spec, draw_grid = select_draw_model(
            train, policy, core_features, side_spec, r19_policy_probability, config
        )

        r19_test_model = r19.make_candidate(r19_spec, config)
        r19_test_model.fit(fit[core_features], r19.result_label(fit.goal_difference))
        baseline_probability = r19.align_probability(r19_test_model, test[core_features])

        fit_non_draw = fit[fit.goal_difference != 0]
        side_y = (fit_non_draw.goal_difference.to_numpy(float) > 0).astype(int)
        side_model = fit_binary(make_binary_model(side_spec), fit_non_draw[core_features], side_y)
        side_probability = predict_positive(side_model, test[core_features])
        draw_y = (fit.goal_difference.to_numpy(float) == 0).astype(int)
        draw_model = fit_binary(
            make_binary_model(draw_spec), fit[core_features], draw_y,
            float(draw_spec["positive_weight"]),
        )
        draw_probability = predict_positive(draw_model, test[core_features])
        candidate_probability = combine_probability(draw_probability, side_probability)
        y_test = r19.result_label(test.goal_difference)
        baseline_components = components(y_test, baseline_probability)
        candidate_components = components(y_test, candidate_probability)
        baseline_components.index = test.index
        candidate_components.index = test.index

        meta = test[[
            "competition_id", "season", "date_key", "home_team", "away_team",
            "goal_difference", "fold"
        ]].copy()
        meta["actual_result"] = y_test
        all_meta.append(meta)
        all_baseline.append(baseline_components)
        all_candidate.append(candidate_components)

        folds.append({
            "fold": fold_name,
            "rows": {"train": len(train), "policy": len(policy), "fit": len(fit), "test": len(test)},
            "R19_baseline_candidate": r19_spec,
            "selected_side_candidate": side_spec,
            "selected_draw_candidate": draw_spec,
            "selected_draw_candidate_safe_on_policy": bool(draw_grid[0]["safe"]),
            "side_policy_grid": side_grid,
            "draw_policy_grid": draw_grid,
            "R19_baseline_metrics": summary(baseline_components),
            "R20_candidate_metrics": summary(candidate_components),
            "R20_delta_minus_R19": domain_delta(candidate_components, baseline_components),
            "same_day_predictions_frozen": True,
            "test_labels_used_for_selection": False,
        })

        for competition, indexes in test.groupby("competition_id").groups.items():
            base_summary = summary(baseline_components.loc[indexes])
            cand_summary = summary(candidate_components.loc[indexes])
            stability.append({
                "competition_id": competition,
                "fold": fold_name,
                "rows": len(indexes),
                "draw_candidate": draw_spec["name"],
                "side_candidate": side_spec["name"],
                "delta_logloss": cand_summary["logloss"] - base_summary["logloss"],
                "delta_brier": cand_summary["brier"] - base_summary["brier"],
                "delta_rps": cand_summary["rps"] - base_summary["rps"],
                "delta_top1": cand_summary["top1_accuracy"] - base_summary["top1_accuracy"],
                "delta_draw_binary_brier": cand_summary["draw_binary_brier"] - base_summary["draw_binary_brier"],
                "baseline_draw_recall": base_summary["draw_recall"],
                "candidate_draw_recall": cand_summary["draw_recall"],
                "baseline_predicted_draw_rate": base_summary["predicted_draw_rate"],
                "candidate_predicted_draw_rate": cand_summary["predicted_draw_rate"],
            })

    meta = pd.concat(all_meta).sort_index()
    baseline = pd.concat(all_baseline).sort_index()
    candidate = pd.concat(all_candidate).sort_index()
    if not (meta.index.equals(baseline.index) and meta.index.equals(candidate.index)):
        raise ResearchError("R20 pooled alignment failure")

    gold_index = r19.deterministic_gold(
        meta, int(config["gold_sample_contract"]["rows"]), int(config["gold_sample_contract"]["seed"])
    )
    gold_meta = meta.loc[gold_index]
    gold_baseline = baseline.loc[gold_index]
    gold_candidate = candidate.loc[gold_index]
    export = gold_meta.copy()
    export["identity"] = export.apply(r19.identity_text, axis=1)
    for prefix, frame in (("R19", gold_baseline), ("R20", gold_candidate)):
        export[f"{prefix}_p_away"] = frame.p_away
        export[f"{prefix}_p_draw"] = frame.p_draw
        export[f"{prefix}_p_home"] = frame.p_home
        export[f"{prefix}_prediction"] = frame.predicted_class
    export.sort_values("identity").to_csv(gold_path, index=False, encoding="utf-8")
    pd.DataFrame(stability).to_csv(stability_path, index=False, encoding="utf-8")

    interval = [float(value) for value in config["evaluation"]["interval"]]
    full_bootstrap = bootstrap(
        candidate, baseline, int(config["evaluation"]["full_rolling_bootstrap_samples"]),
        int(config["evaluation"]["bootstrap_seed"]), interval,
    )
    gold_bootstrap = bootstrap(
        gold_candidate, gold_baseline, int(config["evaluation"]["gold_bootstrap_samples"]),
        int(config["evaluation"]["bootstrap_seed"]) + 1, interval,
    )
    full_base = summary(baseline)
    full_candidate = summary(candidate)
    gold_base = summary(gold_baseline)
    gold_candidate_summary = summary(gold_candidate)

    overall_robust = all(full_bootstrap[key]["p95"] < 0 for key in ("logloss", "brier", "rps"))
    draw_robust = full_bootstrap["draw_binary_brier"]["p95"] < 0
    non_draw_safe = (
        full_bootstrap["non_draw_logloss"]["p95"] <= float(config["evaluation"]["non_draw_full_logloss_noninferiority_margin"])
        and full_bootstrap["non_draw_brier"]["p95"] <= float(config["evaluation"]["non_draw_full_brier_noninferiority_margin"])
    )
    draw_argmax_improved = full_candidate["draw_recall"] > full_base["draw_recall"]
    if overall_robust and draw_robust and non_draw_safe and draw_argmax_improved:
        status = "PASS_R20_HIERARCHICAL_DRAW_AND_OVERALL_ROBUST_SIGNAL"
    elif draw_robust and non_draw_safe and draw_argmax_improved:
        status = "PARTIAL_PASS_R20_DRAW_SIGNAL_NON_DRAW_SAFE_OVERALL_NOT_ROBUST"
    else:
        status = "FAIL_R20_HIERARCHICAL_DRAW_FIRST_NO_SAFE_RETAINABLE_SIGNAL"

    gold_identity_sha256 = hashlib.sha256(
        "\n".join(export.sort_values("identity").identity.tolist()).encode("utf-8")
    ).hexdigest()
    result = {
        "schema_version": "v510_hierarchical_1x2_draw_first_r20_status.1",
        "status": status,
        "classification": config["classification"],
        "data_identity": identity,
        "complete_seasons_excluded": excluded,
        "core_feature_count": len(core_features),
        "rolling_test_rows": int(len(meta)),
        "rolling_test_unique_identities": int(meta.apply(r19.identity_text, axis=1).nunique()),
        "gold_sample": {
            "rows": int(len(export)),
            "selection_uses_labels": False,
            "identity_sha256": gold_identity_sha256,
            "R19_baseline_metrics": gold_base,
            "R20_candidate_metrics": gold_candidate_summary,
            "R20_delta_minus_R19": domain_delta(gold_candidate, gold_baseline),
            "paired_bootstrap": gold_bootstrap,
        },
        "full_rolling": {
            "R19_baseline_metrics": full_base,
            "R20_candidate_metrics": full_candidate,
            "R20_delta_minus_R19": domain_delta(candidate, baseline),
            "paired_bootstrap": full_bootstrap,
            "overall_proper_score_robust_gate": overall_robust,
            "draw_binary_brier_robust_gate": draw_robust,
            "non_draw_noninferiority_gate": non_draw_safe,
            "draw_argmax_improved": draw_argmax_improved,
        },
        "folds": folds,
        "ruling": {
            "historical_training_and_repeated_replay_allowed": True,
            "hierarchical_probability_mapping_audited": True,
            "manual_draw_probability_offset_used": False,
            "test_labels_used_for_selection": False,
            "forward_matches_are_final_confirmation_not_training_prerequisite": True,
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
