#!/usr/bin/env python3
"""R21 draw-vs-close-win information-bound audit.

The target is deliberately narrower than 1X2: distinguish true draws (goal difference 0)
from otherwise similar one-goal results (goal difference -1 or +1). Candidate selection
uses only the policy season. Test outcomes are used only after probabilities are frozen,
including the post-hoc matched-pair diagnostic.

This is VIEWED_HISTORICAL_INFORMATION_BOUND_RESEARCH. It cannot promote a model, create
current-match probabilities, unlock the unified score matrix, produce exact scores or EV.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
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
DEFAULT_CONFIG = ROOT / "config" / "v510_draw_close_win_information_bound_r21.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_draw_close_win_information_bound_r21_status.json"
DEFAULT_GOLD = ROOT / "manifests" / "v510_draw_close_win_information_bound_r21_gold.csv"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_draw_close_win_information_bound_r21_stability.csv"
DEFAULT_PAIRS = ROOT / "manifests" / "v510_draw_close_win_information_bound_r21_pairs.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("R21 config root must be object")
    return value


def close_subset(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame[frame.goal_difference.isin([-1, 0, 1])].copy()
    out["is_draw"] = (out.goal_difference == 0).astype(int)
    return out


def make_candidate(spec: dict[str, Any]) -> Pipeline:
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
                random_state=51021,
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
                random_state=51021,
            )),
        ])
    raise ResearchError(f"unknown R21 candidate kind: {kind}")


def candidate_catalog(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in config["model_contract"]["logistic_C_grid"]:
        rows.append({"name": f"logistic_C_{value}", "kind": "logistic", "C": float(value)})
    for index, value in enumerate(config["model_contract"]["hist_gradient_boosting_candidates"], start=1):
        rows.append({"name": f"hist_gradient_boosting_{index}", "kind": "hist_gradient_boosting", **value})
    return rows


def draw_probability(model: Any, frame: pd.DataFrame) -> np.ndarray:
    raw = np.asarray(model.predict_proba(frame), dtype=float)
    classes = [int(value) for value in model.classes_]
    if 1 not in classes:
        raise ResearchError(f"draw class absent from fitted R21 model: classes={classes}")
    p = raw[:, classes.index(1)]
    return np.clip(p, 1e-12, 1.0 - 1e-12)


def competition_prior(fit: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    global_draws = float(fit.is_draw.sum())
    global_rows = float(len(fit))
    global_p = (global_draws + 1.0) / (global_rows + 2.0)
    stats = fit.groupby("competition_id").is_draw.agg(["sum", "count"])
    mapping = {
        str(index): (float(row["sum"]) + 1.0) / (float(row["count"]) + 2.0)
        for index, row in stats.iterrows()
    }
    return np.asarray([mapping.get(str(value), global_p) for value in test.competition_id], dtype=float)


def row_components(y: np.ndarray, p: np.ndarray) -> pd.DataFrame:
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1.0 - 1e-12)
    y = np.asarray(y, dtype=int)
    return pd.DataFrame({
        "logloss": -(y * np.log(p) + (1 - y) * np.log(1 - p)),
        "brier": (p - y) ** 2,
        "probability": p,
        "actual": y,
    })


def metric_summary(components: pd.DataFrame) -> dict[str, float]:
    y = components.actual.to_numpy(int)
    p = components.probability.to_numpy(float)
    auc = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else float("nan")
    ap = float(average_precision_score(y, p)) if int(y.sum()) else float("nan")
    return {
        "rows": int(len(components)),
        "draws": int(y.sum()),
        "draw_rate": float(y.mean()),
        "logloss": float(components.logloss.mean()),
        "brier": float(components.brier.mean()),
        "auc": auc,
        "average_precision": ap,
        "mean_probability": float(p.mean()),
        "probability_std": float(p.std(ddof=0)),
    }


def select_candidate(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    y_train = train.is_draw.to_numpy(int)
    y_policy = policy.is_draw.to_numpy(int)
    if len(np.unique(y_train)) != 2 or len(np.unique(y_policy)) != 2:
        raise ResearchError("R21 train/policy must each contain draw and close-win outcomes")
    for spec in candidate_catalog(config):
        model = make_candidate(spec)
        model.fit(train[features], y_train)
        p = draw_probability(model, policy[features])
        summary = metric_summary(row_components(y_policy, p))
        receipts.append({"candidate": spec, "policy_metrics": summary})
    receipts.sort(key=lambda row: (
        row["policy_metrics"]["logloss"],
        row["policy_metrics"]["brier"],
        -row["policy_metrics"]["auc"],
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
        raise ResearchError("duplicate R21 test identity")
    work["rank"] = work.identity.map(
        lambda value: hashlib.sha256(f"{seed}|{value}".encode("utf-8")).hexdigest()
    )
    return work.sort_values(["rank", "identity"]).head(rows).index


def bootstrap(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    pairs: pd.DataFrame,
    samples: int,
    seed: int,
    interval: list[float],
) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(seed)
    n = len(candidate)
    y = candidate.actual.to_numpy(int)
    p = candidate.probability.to_numpy(float)
    delta_logloss = candidate.logloss.to_numpy(float) - baseline.logloss.to_numpy(float)
    delta_brier = candidate.brier.to_numpy(float) - baseline.brier.to_numpy(float)
    auc_draws: list[float] = []
    ap_draws: list[float] = []
    ll_draws = np.empty(samples, dtype=float)
    br_draws = np.empty(samples, dtype=float)
    for index in range(samples):
        selection = rng.integers(0, n, size=n)
        ys, ps = y[selection], p[selection]
        ll_draws[index] = float(delta_logloss[selection].mean())
        br_draws[index] = float(delta_brier[selection].mean())
        if len(np.unique(ys)) == 2:
            auc_draws.append(float(roc_auc_score(ys, ps)))
            ap_draws.append(float(average_precision_score(ys, ps)))

    def receipt(point: float, values: np.ndarray | list[float]) -> dict[str, float]:
        values = np.asarray(values, dtype=float)
        return {
            "point": float(point),
            "p05": float(np.quantile(values, float(interval[0]))),
            "p95": float(np.quantile(values, float(interval[1]))),
        }

    output = {
        "delta_logloss": receipt(float(delta_logloss.mean()), ll_draws),
        "delta_brier": receipt(float(delta_brier.mean()), br_draws),
        "auc": receipt(float(roc_auc_score(y, p)), auc_draws),
        "average_precision": receipt(float(average_precision_score(y, p)), ap_draws),
    }
    if len(pairs):
        pair_values = pairs.p_draw_draw.to_numpy(float) - pairs.p_draw_close_win.to_numpy(float)
        pair_scores = (pair_values > 0).astype(float) + 0.5 * (pair_values == 0).astype(float)
        pair_boot = np.empty(samples, dtype=float)
        pair_diff_boot = np.empty(samples, dtype=float)
        for index in range(samples):
            selection = rng.integers(0, len(pairs), size=len(pairs))
            pair_boot[index] = float(pair_scores[selection].mean())
            pair_diff_boot[index] = float(pair_values[selection].mean())
        output["pairwise_accuracy"] = receipt(float(pair_scores.mean()), pair_boot)
        output["pairwise_probability_difference"] = receipt(float(pair_values.mean()), pair_diff_boot)
    return output


def matching_columns(core_features: list[str], config: dict[str, Any]) -> list[str]:
    requested = [str(value) for value in config["matching_contract"]["feature_names"]]
    selected = [value for value in requested if value in core_features]
    minimum = int(config["matching_contract"]["minimum_features"])
    if len(selected) < minimum:
        raise ResearchError(f"R21 matching feature count {len(selected)} below minimum {minimum}")
    return selected


def make_pairs(
    fit: pd.DataFrame,
    test: pd.DataFrame,
    p: pd.Series,
    features: list[str],
    fold_name: str,
) -> pd.DataFrame:
    transformer = Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scaler", StandardScaler()),
    ])
    transformer.fit(fit[features])
    matrix = np.asarray(transformer.transform(test[features]), dtype=float)
    position = {index: offset for offset, index in enumerate(test.index)}
    rows: list[dict[str, Any]] = []
    for competition, group in test.groupby("competition_id", sort=True):
        draw_index = group.index[group.is_draw == 1].tolist()
        win_index = group.index[group.is_draw == 0].tolist()
        if not draw_index or not win_index:
            continue
        draw_matrix = matrix[[position[index] for index in draw_index]]
        win_matrix = matrix[[position[index] for index in win_index]]
        cost = np.sum((draw_matrix[:, None, :] - win_matrix[None, :, :]) ** 2, axis=2)
        draw_assignment, win_assignment = linear_sum_assignment(cost)
        for left, right in zip(draw_assignment, win_assignment):
            draw_id = draw_index[int(left)]
            win_id = win_index[int(right)]
            draw_row, win_row = test.loc[draw_id], test.loc[win_id]
            rows.append({
                "competition_id": competition,
                "fold": fold_name,
                "draw_identity": identity_text(draw_row),
                "close_win_identity": identity_text(win_row),
                "close_win_goal_difference": int(win_row.goal_difference),
                "distance_squared": float(cost[int(left), int(right)]),
                "p_draw_draw": float(p.loc[draw_id]),
                "p_draw_close_win": float(p.loc[win_id]),
            })
    return pd.DataFrame(rows)


def run(config: dict[str, Any], out: Path, gold_path: Path, stability_path: Path, pairs_path: Path) -> dict[str, Any]:
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    identity = audit_data_identity(raw, config)
    features = build_features(raw)
    core_features = select_core_features(features)
    match_features = matching_columns(core_features, config)
    seasons, excluded = complete_seasons(raw, config)

    all_meta: list[pd.DataFrame] = []
    all_candidate: list[pd.DataFrame] = []
    all_baseline: list[pd.DataFrame] = []
    all_pairs: list[pd.DataFrame] = []
    fold_receipts: list[dict[str, Any]] = []
    stability_rows: list[dict[str, Any]] = []

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold_name = f"window_{test_position - 1}_to_{test_position}"
        fold["fold"] = fold_name
        train = close_subset(fold[fold.split == "train"])
        policy = close_subset(fold[fold.split == "policy"])
        fit = close_subset(fold[fold.split.isin(["train", "policy"])])
        test = close_subset(fold[fold.split == "test"])
        if min(len(train), len(policy), len(fit), len(test)) <= 0:
            raise ResearchError(f"empty R21 split at {fold_name}")

        selected, policy_grid = select_candidate(train, policy, core_features, config)
        model = make_candidate(selected)
        model.fit(fit[core_features], fit.is_draw.to_numpy(int))
        p_candidate = pd.Series(draw_probability(model, test[core_features]), index=test.index)
        p_baseline = pd.Series(competition_prior(fit, test), index=test.index)
        candidate = row_components(test.is_draw.to_numpy(int), p_candidate.to_numpy(float))
        baseline = row_components(test.is_draw.to_numpy(int), p_baseline.to_numpy(float))
        candidate.index = test.index
        baseline.index = test.index
        pairs = make_pairs(fit, test, p_candidate, match_features, fold_name)

        meta = test[[
            "competition_id", "season", "date_key", "home_team", "away_team",
            "goal_difference", "fold", "is_draw"
        ]].copy()
        all_meta.append(meta)
        all_candidate.append(candidate)
        all_baseline.append(baseline)
        all_pairs.append(pairs)

        fold_receipts.append({
            "fold": fold_name,
            "rows": {"train": len(train), "policy": len(policy), "fit": len(fit), "test": len(test)},
            "selected_candidate": selected,
            "policy_candidates": policy_grid,
            "baseline_metrics": metric_summary(baseline),
            "candidate_metrics": metric_summary(candidate),
            "matched_pairs": int(len(pairs)),
            "test_labels_used_for_candidate_selection": False,
            "test_labels_used_only_for_post_prediction_pair_diagnostic": True,
        })

        for competition, indexes in test.groupby("competition_id").groups.items():
            cand = candidate.loc[indexes]
            base = baseline.loc[indexes]
            cand_summary = metric_summary(cand)
            base_summary = metric_summary(base)
            comp_pairs = pairs[pairs.competition_id == competition]
            pair_accuracy = float("nan")
            if len(comp_pairs):
                diff = comp_pairs.p_draw_draw.to_numpy(float) - comp_pairs.p_draw_close_win.to_numpy(float)
                pair_accuracy = float(((diff > 0).astype(float) + 0.5 * (diff == 0)).mean())
            stability_rows.append({
                "competition_id": competition,
                "fold": fold_name,
                "rows": int(len(indexes)),
                "draws": int(cand_summary["draws"]),
                "selected_candidate": selected["name"],
                "candidate_auc": cand_summary["auc"],
                "candidate_average_precision": cand_summary["average_precision"],
                "delta_logloss": cand_summary["logloss"] - base_summary["logloss"],
                "delta_brier": cand_summary["brier"] - base_summary["brier"],
                "matched_pairs": int(len(comp_pairs)),
                "pairwise_accuracy": pair_accuracy,
            })

    meta = pd.concat(all_meta).sort_index()
    candidate = pd.concat(all_candidate).sort_index()
    baseline = pd.concat(all_baseline).sort_index()
    pairs = pd.concat(all_pairs, ignore_index=True) if all_pairs else pd.DataFrame()
    if not (meta.index.equals(candidate.index) and meta.index.equals(baseline.index)):
        raise ResearchError("R21 pooled index alignment failure")

    samples = int(config["evaluation"]["bootstrap_samples"])
    interval = [float(value) for value in config["evaluation"]["interval"]]
    boot = bootstrap(candidate, baseline, pairs, samples, int(config["evaluation"]["bootstrap_seed"]), interval)
    cand_summary = metric_summary(candidate)
    base_summary = metric_summary(baseline)
    stability = pd.DataFrame(stability_rows)

    fold_auc_wins = sum(float(row["candidate_metrics"]["auc"]) > 0.5 for row in fold_receipts)
    eligible_stability = stability[np.isfinite(stability.candidate_auc)]
    stability_auc_win_rate = float((eligible_stability.candidate_auc > 0.5).mean()) if len(eligible_stability) else 0.0
    proper_robust = bool(boot["delta_logloss"]["p95"] < 0 and boot["delta_brier"]["p95"] < 0)
    discrimination_robust = bool(
        boot["auc"]["p05"] > 0.5
        and boot.get("pairwise_accuracy", {}).get("p05", 0.0) > 0.5
    )
    stable = bool(
        fold_auc_wins >= int(config["gates"]["minimum_fold_auc_wins"])
        and stability_auc_win_rate >= float(config["gates"]["minimum_competition_window_auc_win_rate"])
    )
    passed = proper_robust and discrimination_robust and stable
    status = (
        "PASS_R21_EXISTING_FEATURES_CONTAIN_ROBUST_DRAW_CLOSE_WIN_SIGNAL_RESEARCH_ONLY"
        if passed
        else "FAIL_R21_EXISTING_FEATURES_INFORMATION_BOUND_NO_ROBUST_DRAW_CLOSE_WIN_SIGNAL"
    )

    gold_rows = min(int(config["gold_sample_contract"]["rows"]), len(meta))
    gold_index = deterministic_gold(meta, gold_rows, int(config["gold_sample_contract"]["seed"]))
    gold = meta.loc[gold_index].copy()
    gold["identity"] = gold.apply(identity_text, axis=1)
    gold["baseline_p_draw"] = baseline.loc[gold_index, "probability"]
    gold["candidate_p_draw"] = candidate.loc[gold_index, "probability"]
    gold.to_csv(gold_path, index=False, encoding="utf-8")
    stability.to_csv(stability_path, index=False, encoding="utf-8")
    pairs.to_csv(pairs_path, index=False, encoding="utf-8")

    receipt = {
        "schema_version": "v510_draw_close_win_information_bound_r21_status.1",
        "status": status,
        "classification": "VIEWED_HISTORICAL_INFORMATION_BOUND_RESEARCH",
        "data_identity": identity,
        "complete_seasons_excluded": excluded,
        "core_feature_count": int(len(core_features)),
        "matching_feature_count": int(len(match_features)),
        "matching_features": match_features,
        "rolling_close_result_rows": int(len(meta)),
        "rolling_draw_rows": int(meta.is_draw.sum()),
        "rolling_one_goal_win_rows": int((meta.is_draw == 0).sum()),
        "rolling_unique_identities": int(meta.apply(identity_text, axis=1).nunique()),
        "baseline_metrics": base_summary,
        "candidate_metrics": cand_summary,
        "candidate_delta_minus_baseline": {
            "logloss": cand_summary["logloss"] - base_summary["logloss"],
            "brier": cand_summary["brier"] - base_summary["brier"],
            "auc_minus_random": cand_summary["auc"] - 0.5,
            "average_precision_minus_prevalence": cand_summary["average_precision"] - cand_summary["draw_rate"],
        },
        "paired_bootstrap": boot,
        "matched_pairs": int(len(pairs)),
        "stability": {
            "fold_auc_wins_over_random": int(fold_auc_wins),
            "folds": int(len(fold_receipts)),
            "competition_window_auc_win_rate": stability_auc_win_rate,
            "eligible_competition_windows": int(len(eligible_stability)),
        },
        "gates": {
            "proper_score_robust_improvement": proper_robust,
            "draw_close_win_discrimination_robust": discrimination_robust,
            "cross_window_stability": stable,
            "overall_pass": passed,
        },
        "folds": fold_receipts,
        "interpretation": (
            "Existing strictly pre-match historical score-identity features contain a reproducible draw-vs-one-goal-win signal. This remains research-only and requires new strict-PIT information before any formal use."
            if passed else
            "Within the existing strictly pre-match historical score-identity feature set, draws cannot be robustly separated from one-goal wins. Further classifier, class-weight, calibration or argmax work on the same information set is not an evidence-based solution; new strict-PIT market/context information is required."
        ),
        "ruling": {
            "formal_weight": 0,
            "historical_labels_viewed": True,
            "candidate_selected_on_policy_only": True,
            "test_labels_used_for_selection": False,
            "post_prediction_matching_uses_test_outcomes_for_diagnostic_only": True,
            "manual_draw_offset_used": False,
            "class_weight_used": False,
            "threshold_tuning_used": False,
            "current_match_probability_allowed": False,
            "unified_matrix_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False
        },
        "hard_limits": config["hard_limits"]
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return receipt


def self_test() -> None:
    y = np.asarray([1, 0, 1, 0, 1, 0], dtype=int)
    p = np.asarray([0.8, 0.2, 0.7, 0.3, 0.6, 0.4], dtype=float)
    components = row_components(y, p)
    summary = metric_summary(components)
    if not (summary["auc"] > 0.9 and summary["brier"] < 0.2):
        raise ResearchError("R21 self-test metric failure")
    print(json.dumps({"self_test": "PASS", "metrics": summary}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--stability", type=Path, default=DEFAULT_STABILITY)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    receipt = run(load_json(args.config), args.out, args.gold, args.stability, args.pairs)
    print(json.dumps({
        "status": receipt["status"],
        "rolling_close_result_rows": receipt["rolling_close_result_rows"],
        "candidate_metrics": receipt["candidate_metrics"],
        "paired_bootstrap": receipt["paired_bootstrap"],
        "stability": receipt["stability"],
        "gates": receipt["gates"],
        "ruling": receipt["ruling"]
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
