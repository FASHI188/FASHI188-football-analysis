#!/usr/bin/env python3
"""R9 failure-domain and error-decomposition audit for the V5.1 R8 joint chain.

Viewed historical development only. Rebuild the frozen R8 architecture under the same
three rolling windows, then decompose paired candidate-minus-baseline errors by
competition, test season, realised total bucket, pre-match strength gap, venue-form
state, result type, draw type, and score shape. No tail exact allocation is performed.
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
    make_model,
    metric_components,
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
)
from evaluate_v510_historical_tail_mapping_r1 import attach_exact_labels
from evaluate_v510_full_range_score_allocation_r4 import (
    add_conditional_total_features,
    result_components,
    score_components,
)
from evaluate_v510_core_joint_chain_r8 import (
    build_joint,
    fit_beta_score_model,
    fit_current_score_models,
    flat_probability,
    joint_components,
    realised_conditional_probabilities,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_failure_domain_audit_r9.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_failure_domain_audit_r9_status.json"
DEFAULT_GROUPS = ROOT / "manifests" / "v510_failure_domain_audit_r9_groups.csv"
DEFAULT_WORST = ROOT / "manifests" / "v510_failure_domain_audit_r9_worst_domains.csv"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def finite_quantile_edges(series: pd.Series, quantiles: list[float]) -> list[float]:
    values = pd.to_numeric(series, errors="coerce").to_numpy(float)
    values = values[np.isfinite(values)]
    if len(values) < 20:
        raise ResearchError("not enough finite values for diagnostic quantile bins")
    raw = [float(np.quantile(values, q)) for q in quantiles]
    edges: list[float] = []
    for value in raw:
        if edges and value <= edges[-1]:
            value = float(np.nextafter(edges[-1], np.inf))
        edges.append(value)
    return edges


def assign_quantile_labels(
    series: pd.Series,
    edges: list[float],
    labels: list[str],
    missing_label: str = "MISSING",
) -> pd.Series:
    if len(labels) != len(edges) + 1:
        raise ResearchError("quantile label count mismatch")
    values = pd.to_numeric(series, errors="coerce").to_numpy(float)
    output = np.full(len(values), missing_label, dtype=object)
    finite = np.isfinite(values)
    output[finite] = np.asarray(labels, dtype=object)[np.searchsorted(edges, values[finite], side="right")]
    return pd.Series(output, index=series.index, dtype="object")


def draw_type(frame: pd.DataFrame) -> pd.Series:
    values = []
    for row in frame.itertuples():
        home = int(row.home_goals_exact)
        away = int(row.away_goals_exact)
        if home != away:
            values.append("NON_DRAW")
        elif home == 0:
            values.append("DRAW_0_0")
        elif home == 1:
            values.append("DRAW_1_1")
        elif home == 2:
            values.append("DRAW_2_2")
        else:
            values.append("DRAW_3PLUS")
    return pd.Series(values, index=frame.index, dtype="object")


def result_type(frame: pd.DataFrame) -> pd.Series:
    home = frame.home_goals_exact.to_numpy(int)
    away = frame.away_goals_exact.to_numpy(int)
    values = np.where(home > away, "HOME_WIN", np.where(home == away, "DRAW", "AWAY_WIN"))
    return pd.Series(values, index=frame.index, dtype="object")


def score_shape(frame: pd.DataFrame) -> pd.Series:
    values = []
    for row in frame.itertuples():
        home = int(row.home_goals_exact)
        away = int(row.away_goals_exact)
        if home == 0 and away == 0:
            value = "ZERO_ZERO"
        elif home == away:
            value = "SCORE_DRAW"
        elif away == 0:
            value = "HOME_CLEAN_SHEET_WIN"
        elif home == 0:
            value = "AWAY_CLEAN_SHEET_WIN"
        elif home > away:
            value = "BTTS_HOME_WIN"
        else:
            value = "BTTS_AWAY_WIN"
        values.append(value)
    return pd.Series(values, index=frame.index, dtype="object")


def total_bucket(frame: pd.DataFrame) -> pd.Series:
    total = frame.total_goals_exact.to_numpy(int)
    values = ["T7PLUS" if value >= 7 else f"T{value}" for value in total]
    return pd.Series(values, index=frame.index, dtype="object")


def attach_metric_columns(
    diagnostic: pd.DataFrame,
    prefix: str,
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
) -> None:
    for column in baseline.columns:
        diagnostic[f"{prefix}_baseline_{column}"] = baseline[column]
        diagnostic[f"{prefix}_candidate_{column}"] = candidate[column]
        diagnostic[f"{prefix}_delta_{column}"] = candidate[column] - baseline[column]


def clustered_bootstrap_delta(
    group: pd.DataFrame,
    delta_column: str,
    cluster_columns: list[str],
    samples: int,
    seed: int,
    interval: list[float],
) -> dict[str, float | int | None]:
    valid = group[np.isfinite(pd.to_numeric(group[delta_column], errors="coerce"))].copy()
    if valid.empty:
        return {"clusters": 0, "mean": None, "p05": None, "p95": None}
    keys = valid[cluster_columns].astype(str).agg("|".join, axis=1)
    unique = sorted(keys.unique())
    indexes = [np.flatnonzero(keys.to_numpy() == key) for key in unique]
    if len(indexes) < 2:
        mean = float(valid[delta_column].mean())
        return {"clusters": len(indexes), "mean": mean, "p05": None, "p95": None}
    sums = np.asarray([float(valid.iloc[index][delta_column].sum()) for index in indexes])
    counts = np.asarray([len(index) for index in indexes], dtype=float)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(indexes), size=(samples, len(indexes)))
    values = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    low, high = [float(value) for value in interval]
    return {
        "clusters": len(indexes),
        "mean": float(valid[delta_column].mean()),
        "p05": float(np.quantile(values, low)),
        "p95": float(np.quantile(values, high)),
    }


def group_cluster_columns(dimension: str) -> list[str]:
    if dimension == "competition_id":
        return ["fold"]
    if dimension in {"season", "fold"}:
        return ["competition_id"]
    return ["competition_id", "fold"]


def group_status(rows: int, minimum_rows: int, ll: dict[str, Any], brier: dict[str, Any]) -> str:
    if rows < minimum_rows:
        return "INSUFFICIENT_ROWS"
    ll_p05, ll_p95 = ll.get("p05"), ll.get("p95")
    br_p05, br_p95 = brier.get("p05"), brier.get("p95")
    if ll_p95 is not None and br_p95 is not None and ll_p95 < 0 and br_p95 < 0:
        return "ROBUST_GAIN"
    if (ll_p05 is not None and ll_p05 > 0) or (br_p05 is not None and br_p05 > 0):
        return "ROBUST_DEGRADATION"
    ll_mean = float(ll["mean"]) if ll.get("mean") is not None else np.nan
    br_mean = float(brier["mean"]) if brier.get("mean") is not None else np.nan
    if ll_mean < 0 and br_mean < 0:
        return "MEAN_GAIN_UNCERTAIN"
    if ll_mean > 0 or br_mean > 0:
        return "MIXED_OR_DEGRADATION"
    return "NEUTRAL"


def safe_mean(frame: pd.DataFrame, column: str) -> float | None:
    values = pd.to_numeric(frame[column], errors="coerce")
    values = values[np.isfinite(values)]
    return None if values.empty else float(values.mean())


def summarize_group(
    dimension: str,
    value: str,
    group: pd.DataFrame,
    config: dict[str, Any],
    ordinal: int,
) -> dict[str, Any]:
    rows = len(group)
    cluster_columns = group_cluster_columns(dimension)
    samples = int(config["diagnostic_bootstrap"]["samples"])
    seed = int(config["diagnostic_bootstrap"]["seed"]) + ordinal * 101
    interval = list(config["diagnostic_bootstrap"]["interval"])
    bootstrap_metrics = {}
    metric_columns = {
        "joint_logloss": "joint_delta_logloss",
        "joint_brier": "joint_delta_brier",
        "total_logloss": "total_delta_logloss",
        "total_brier": "total_delta_brier",
        "total_rps": "total_delta_rps",
        "score_logloss": "score_delta_logloss",
        "score_brier": "score_delta_brier",
        "score_rps": "score_delta_rps",
        "result_logloss": "result_delta_result_logloss",
        "result_brier": "result_delta_result_brier",
        "draw_binary_brier": "result_delta_draw_binary_brier",
    }
    for metric, column in metric_columns.items():
        if column in group.columns:
            bootstrap_metrics[metric] = clustered_bootstrap_delta(
                group, column, cluster_columns, samples, seed + len(bootstrap_metrics), interval
            )
    joint_ll = bootstrap_metrics["joint_logloss"]
    joint_brier = bootstrap_metrics["joint_brier"]
    minimum_rows = int(config["domain_contract"]["minimum_rows_for_status"])
    row = {
        "dimension": dimension,
        "value": str(value),
        "rows": rows,
        "clusters": joint_ll["clusters"],
        "tail_rows": int((group.total_goals_exact >= 7).sum()),
        "tail_rate": float((group.total_goals_exact >= 7).mean()),
        "draw_rows": int((group.result_type == "DRAW").sum()),
        "draw_rate": float((group.result_type == "DRAW").mean()),
        "zero_zero_rows": int((group.draw_type == "DRAW_0_0").sum()),
        "status": group_status(rows, minimum_rows, joint_ll, joint_brier),
    }
    for family, metrics in {
        "joint": ("logloss", "brier", "top1", "top3", "top5"),
        "total": ("logloss", "brier", "rps", "top1", "top2"),
        "score": ("logloss", "brier", "rps", "top1", "top2", "top3"),
        "result": ("result_logloss", "result_brier", "result_top1", "draw_binary_brier"),
    }.items():
        for metric in metrics:
            base_col = f"{family}_baseline_{metric}"
            cand_col = f"{family}_candidate_{metric}"
            delta_col = f"{family}_delta_{metric}"
            if base_col in group.columns:
                row[f"{family}_baseline_{metric}"] = safe_mean(group, base_col)
                row[f"{family}_candidate_{metric}"] = safe_mean(group, cand_col)
                row[f"{family}_delta_{metric}"] = safe_mean(group, delta_col)
    for metric, receipt in bootstrap_metrics.items():
        row[f"{metric}_clusters"] = receipt["clusters"]
        row[f"{metric}_p05"] = receipt["p05"]
        row[f"{metric}_p95"] = receipt["p95"]
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ResearchError(f"output rows empty for {path.name}")
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def reproduction_check(actual: dict[str, float], expected: dict[str, float], tolerance: float) -> dict[str, Any]:
    residuals = {key: float(actual[key] - float(value)) for key, value in expected.items()}
    maximum = max(abs(value) for value in residuals.values())
    return {"residuals": residuals, "max_abs_residual": maximum, "pass": maximum <= tolerance}


def run(
    config: dict[str, Any],
    out_path: Path,
    groups_path: Path,
    worst_path: Path,
) -> dict[str, Any]:
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    identity = audit_data_identity(raw, config)
    base = build_features(raw)
    features = add_conditional_total_features(attach_exact_labels(raw, base))
    core_features = select_core_features(features)
    seasons, excluded = complete_seasons(raw, config)
    classes = [int(value) for value in config["model_contract"]["direct_total_classes"]]
    nonlinear_candidate = dict(config["nonlinear_expert"])

    fold_receipts: list[dict[str, Any]] = []
    diagnostic_frames: list[pd.DataFrame] = []

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        train = fold[fold.split == "train"]
        policy = fold[fold.split == "policy"].copy()
        fit = fold[fold.split.isin(["train", "policy"])]
        test = fold[fold.split == "test"].copy()

        flat_C, _ = select_C(train, policy, core_features, "total_class", classes, config)
        linear_C, _ = select_continuation_C(train, policy, core_features, config)
        flat_train = make_model(flat_C, config)
        flat_train.fit(train[core_features], train.total_class)
        policy_flat = flat_probability(flat_train, policy, core_features, config)
        linear_train = continuation_models(train, core_features, linear_C, config)
        policy_linear = continuation_probability(linear_train, policy, core_features, config)
        nonlinear_train = boosting_continuation_models(train, core_features, nonlinear_candidate, config)
        policy_nonlinear = boosting_probability(nonlinear_train, policy, core_features, config)
        eta_two, policy_weights_two, _ = select_eta(policy, [policy_flat, policy_linear], config)
        eta_three, policy_weights_three, _ = select_eta(
            policy, [policy_flat, policy_linear, policy_nonlinear], config
        )

        flat_fit = make_model(flat_C, config)
        flat_fit.fit(fit[core_features], fit.total_class)
        test_flat = flat_probability(flat_fit, test, core_features, config)
        linear_fit = continuation_models(fit, core_features, linear_C, config)
        test_linear = continuation_probability(linear_fit, test, core_features, config)
        nonlinear_fit = boosting_continuation_models(fit, core_features, nonlinear_candidate, config)
        test_nonlinear = boosting_probability(nonlinear_fit, test, core_features, config)
        total_two, final_two, _ = daily_mixture(
            test, [test_flat, test_linear], eta_two, initial_weights=policy_weights_two
        )
        total_three, final_three, _ = daily_mixture(
            test, [test_flat, test_linear, test_nonlinear], eta_three,
            initial_weights=policy_weights_three,
        )

        current_score, current_receipt = fit_current_score_models(fold, core_features, test, config)
        beta_score, beta_receipt = fit_beta_score_model(fold, core_features, test, config)
        joint_baseline, audit_baseline = build_joint(total_two, current_score)
        joint_candidate, audit_candidate = build_joint(total_three, beta_score)

        y = test.total_class.to_numpy(int)
        total_base_components = metric_components(y, total_two, classes)
        total_candidate_components = metric_components(y, total_three, classes)
        joint_base_components = joint_components(test, joint_baseline)
        joint_candidate_components = joint_components(test, joint_candidate)
        for frame in (
            total_base_components, total_candidate_components,
            joint_base_components, joint_candidate_components,
        ):
            frame.index = test.index

        position = {int(index): pos for pos, index in enumerate(test.index)}
        core_test = test[test.total_goals_exact <= 6].copy()
        current_realised = realised_conditional_probabilities(core_test, current_score, position)
        beta_realised = realised_conditional_probabilities(core_test, beta_score, position)
        score_base = score_components(core_test, current_realised)
        score_candidate = score_components(core_test, beta_realised)
        result_base = result_components(core_test, current_realised)
        result_candidate = result_components(core_test, beta_realised)

        diagnostic = test[[
            "competition_id", "season", "fold", "date_key", "home_team", "away_team",
            "home_goals_exact", "away_goals_exact", "total_goals_exact",
            "pair_gd_diff", "pair_recent_points_diff",
        ]].copy()
        diagnostic["result_type"] = result_type(diagnostic)
        diagnostic["draw_type"] = draw_type(diagnostic)
        diagnostic["score_shape"] = score_shape(diagnostic)
        diagnostic["total_bucket"] = total_bucket(diagnostic)

        strength_edges = finite_quantile_edges(fit["pair_gd_diff"], [0.2, 0.4, 0.6, 0.8])
        form_edges = finite_quantile_edges(fit["pair_recent_points_diff"], [1.0 / 3.0, 2.0 / 3.0])
        diagnostic["strength_gap_bin"] = assign_quantile_labels(
            diagnostic["pair_gd_diff"], strength_edges,
            ["Q1_AWAY_STRONG", "Q2_AWAY_EDGE", "Q3_BALANCED", "Q4_HOME_EDGE", "Q5_HOME_STRONG"],
        )
        diagnostic["venue_form_bin"] = assign_quantile_labels(
            diagnostic["pair_recent_points_diff"], form_edges,
            ["T1_AWAY_FORM_EDGE", "T2_BALANCED_FORM", "T3_HOME_FORM_EDGE"],
        )

        attach_metric_columns(diagnostic, "joint", joint_base_components, joint_candidate_components)
        attach_metric_columns(diagnostic, "total", total_base_components, total_candidate_components)
        for family, baseline_frame, candidate_frame in (
            ("score", score_base, score_candidate),
            ("result", result_base, result_candidate),
        ):
            for column in baseline_frame.columns:
                diagnostic[f"{family}_baseline_{column}"] = np.nan
                diagnostic[f"{family}_candidate_{column}"] = np.nan
                diagnostic[f"{family}_delta_{column}"] = np.nan
                diagnostic.loc[core_test.index, f"{family}_baseline_{column}"] = baseline_frame[column]
                diagnostic.loc[core_test.index, f"{family}_candidate_{column}"] = candidate_frame[column]
                diagnostic.loc[core_test.index, f"{family}_delta_{column}"] = (
                    candidate_frame[column] - baseline_frame[column]
                )

        diagnostic_frames.append(diagnostic)
        fold_receipts.append({
            "fold": str(test.fold.iloc[0]),
            "rows": {"fit": len(fit), "test": len(test), "core": len(core_test)},
            "strength_gap_edges_from_fit_only": strength_edges,
            "venue_form_edges_from_fit_only": form_edges,
            "total_two_final_weights": [float(value) for value in final_two],
            "total_three_final_weights": [float(value) for value in final_three],
            "current_score_receipt_present": bool(current_receipt),
            "beta_score_legal_failures": int(beta_receipt["legal_mapping_failures"]),
            "baseline_joint_audit": audit_baseline,
            "candidate_joint_audit": audit_candidate,
        })

    diagnostic = pd.concat(diagnostic_frames).sort_values(
        ["competition_id", "date_key", "home_team", "away_team"]
    )
    dimensions = list(config["domain_contract"]["dimensions"])
    group_rows: list[dict[str, Any]] = []
    ordinal = 0
    for dimension in dimensions:
        for value, group in diagnostic.groupby(dimension, dropna=False, sort=True):
            group_rows.append(summarize_group(dimension, str(value), group, config, ordinal))
            ordinal += 1

    minimum_rows = int(config["domain_contract"]["minimum_rows_for_status"])
    eligible = [row for row in group_rows if int(row["rows"]) >= minimum_rows]
    worst = sorted(
        eligible,
        key=lambda row: (
            float(row.get("joint_delta_logloss") or 0.0),
            float(row.get("joint_delta_brier") or 0.0),
        ),
        reverse=True,
    )
    best = sorted(
        eligible,
        key=lambda row: (
            float(row.get("joint_delta_logloss") or 0.0),
            float(row.get("joint_delta_brier") or 0.0),
        ),
    )
    worst_rows = []
    for rank, row in enumerate(worst[: int(config["domain_contract"]["top_k_domains"])], start=1):
        worst_rows.append({"side": "WORST", "rank": rank, **row})
    for rank, row in enumerate(best[: int(config["domain_contract"]["top_k_domains"])], start=1):
        worst_rows.append({"side": "BEST", "rank": rank, **row})

    actual_reproduction = {
        "joint_baseline_logloss": float(diagnostic.joint_baseline_logloss.mean()),
        "joint_candidate_logloss": float(diagnostic.joint_candidate_logloss.mean()),
        "joint_delta_logloss": float(diagnostic.joint_delta_logloss.mean()),
        "joint_delta_brier": float(diagnostic.joint_delta_brier.mean()),
        "total_delta_logloss": float(diagnostic.total_delta_logloss.mean()),
        "core_score_delta_logloss": float(diagnostic.score_delta_logloss.mean(skipna=True)),
    }
    reproduction = reproduction_check(
        actual_reproduction,
        dict(config["frozen_r8_reproduction"]),
        float(config["domain_contract"]["reproduction_tolerance"]),
    )

    dimension_summary = {}
    for dimension in dimensions:
        rows = [row for row in group_rows if row["dimension"] == dimension]
        dimension_summary[dimension] = {
            "groups": len(rows),
            "eligible_groups": int(sum(int(row["rows"]) >= minimum_rows for row in rows)),
            "robust_gain_groups": int(sum(row["status"] == "ROBUST_GAIN" for row in rows)),
            "robust_degradation_groups": int(sum(row["status"] == "ROBUST_DEGRADATION" for row in rows)),
            "mixed_or_uncertain_groups": int(sum(
                row["status"] in {"MEAN_GAIN_UNCERTAIN", "MIXED_OR_DEGRADATION", "NEUTRAL"}
                for row in rows
            )),
        }

    robust_degradation = [row for row in eligible if row["status"] == "ROBUST_DEGRADATION"]
    if not reproduction["pass"]:
        status = "FAIL_R9_R8_REPRODUCTION_MISMATCH"
    elif robust_degradation:
        status = "COMPLETE_R9_FAILURE_DOMAINS_IDENTIFIED_R8_RETAINED_WITH_GATES"
    else:
        status = "COMPLETE_R9_NO_ROBUST_DEGRADATION_FOUND_R8_RETAINED"

    result = {
        "schema_version": config["schema_version"],
        "status": status,
        "evidence_class": config["evidence_class"],
        "data_identity": identity,
        "split_contract": {
            "complete_seasons": seasons,
            "excluded_incomplete_latest_seasons": excluded,
            "same_day_freeze_before_update": True,
            "test_outcomes_used_for_model_selection": False,
            "diagnostic_bins_fit_on_train_plus_policy_only": True,
            "repeated_historical_replay_allowed": True,
            "independent_confirmation_claim_allowed": False,
        },
        "diagnostic_contract": {
            "dimensions": dimensions,
            "strength_signal": "pre-match pair_gd_diff; fold-specific quintile edges fitted on train+policy only",
            "venue_form_signal": "pre-match pair_recent_points_diff; fold-specific tercile edges fitted on train+policy only",
            "minimum_rows_for_status": minimum_rows,
            "tail_exact_allocation_performed": False,
            "full_unified_matrix_claim": False,
        },
        "folds": fold_receipts,
        "reproduction": {
            "actual": actual_reproduction,
            "expected": config["frozen_r8_reproduction"],
            **reproduction,
        },
        "dimension_summary": dimension_summary,
        "robust_degradation_domains": robust_degradation,
        "worst_domains": worst[: int(config["domain_contract"]["top_k_domains"])],
        "best_domains": best[: int(config["domain_contract"]["top_k_domains"])],
        "ruling": {
            "r8_core_joint_component_retained": reproduction["pass"],
            "domain_gates_required": bool(robust_degradation),
            "tail_exact_distribution_confirmed": False,
            "full_unified_score_matrix_allowed": False,
            "formal_weight": 0,
            "promotion": False,
            "strict_PIT_market_context_rows": 0,
            "current_match_probabilities_generated": False,
            "exact_score_output_generated": False,
            "EV_generated": False,
            "fixed_outputs": ["总进球分布不可用。", "精确比分不可用。"],
        },
        "governance": config["governance"],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(groups_path, group_rows)
    write_csv(worst_path, worst_rows)
    return result


def self_test() -> None:
    frame = pd.DataFrame({
        "home_goals_exact": [0, 1, 2, 3, 2],
        "away_goals_exact": [0, 1, 1, 3, 0],
        "total_goals_exact": [0, 2, 3, 6, 2],
    })
    assert draw_type(frame).tolist() == [
        "DRAW_0_0", "DRAW_1_1", "NON_DRAW", "DRAW_3PLUS", "NON_DRAW"
    ]
    assert result_type(frame).tolist() == ["DRAW", "DRAW", "HOME_WIN", "DRAW", "HOME_WIN"]
    assert score_shape(frame).iloc[0] == "ZERO_ZERO"
    edges = finite_quantile_edges(pd.Series(np.arange(100)), [0.2, 0.4, 0.6, 0.8])
    labels = assign_quantile_labels(
        pd.Series([0, 25, 50, 75, 99]), edges,
        ["Q1", "Q2", "Q3", "Q4", "Q5"],
    )
    assert labels.tolist() == ["Q1", "Q2", "Q3", "Q4", "Q5"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--groups", type=Path, default=DEFAULT_GROUPS)
    parser.add_argument("--worst", type=Path, default=DEFAULT_WORST)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    result = run(load_json(args.config), args.out, args.groups, args.worst)
    print(json.dumps({
        "status": result["status"],
        "reproduction": result["reproduction"],
        "dimension_summary": result["dimension_summary"],
        "robust_degradation_domains": result["robust_degradation_domains"],
        "ruling": result["ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
