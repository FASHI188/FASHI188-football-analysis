#!/usr/bin/env python3
"""Three-window historical score-label structure challenger for V5.1.

Research-only: historical identities and strictly prior score labels only.
No market/context coefficient, current-match probability, score matrix, exact score or EV.
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

from v510_historical_structure_features_r1 import (
    ResearchError, assign_fold, audit_data_identity, build_features,
    complete_seasons, select_core_features,
)
from v510_historical_structure_model_r1 import (
    bootstrap, fit_conditional_D, fit_direct_total, metric_components, metric_summary,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_historical_label_structure_rolling_r1.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_historical_label_structure_rolling_r1_status.json"
DEFAULT_STABILITY = ROOT / "manifests" / "v510_historical_label_structure_rolling_r1_stability.csv"

def load_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("config root must be an object")
    return value


def write_stability(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ResearchError("stability output is empty")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run(config: dict[str, Any], out_path: Path, stability_path: Path) -> dict[str, Any]:
    ledger = ROOT / str(config["input_ledger"])
    if not ledger.is_file():
        raise ResearchError(f"ledger missing: {ledger.relative_to(ROOT)}")
    raw = pd.read_csv(ledger)
    data_identity = audit_data_identity(raw, config)
    features = build_features(raw)
    feature_names = select_core_features(features)
    seasons, excluded = complete_seasons(raw, config)

    fold_receipts: list[dict[str, Any]] = []
    direct_model_all: list[pd.DataFrame] = []
    direct_baseline_all: list[pd.DataFrame] = []
    direct_meta_all: list[pd.DataFrame] = []
    conditional_model_all: list[pd.DataFrame] = []
    conditional_baseline_all: list[pd.DataFrame] = []
    conditional_meta_all: list[pd.DataFrame] = []

    for test_position in [int(value) for value in config["split_contract"]["rolling_test_positions_zero_based"]]:
        fold = features.copy()
        fold["split"] = assign_fold(fold, seasons, test_position)
        fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
        direct_meta, direct_model, direct_baseline, direct_receipt = fit_direct_total(fold, feature_names, config)
        cond_meta, cond_model, cond_baseline, cond_receipt = fit_conditional_D(fold, feature_names, config)
        fold_receipts.append({
            "fold": f"window_{test_position - 1}_to_{test_position}",
            "test_season_position_one_based": test_position + 1,
            "split_rows": {key: int(value) for key, value in fold.split.value_counts().items()},
            "direct_total": direct_receipt,
            "conditional_D_given_T": cond_receipt,
        })
        direct_model_all.append(direct_model)
        direct_baseline_all.append(direct_baseline)
        direct_meta_all.append(direct_meta[["competition_id", "season", "fold"]])
        conditional_model_all.append(cond_model)
        conditional_baseline_all.append(cond_baseline)
        conditional_meta_all.append(cond_meta)

    direct_model = pd.concat(direct_model_all, ignore_index=True)
    direct_baseline = pd.concat(direct_baseline_all, ignore_index=True)
    direct_meta = pd.concat(direct_meta_all, ignore_index=True)
    conditional_model = pd.concat(conditional_model_all, ignore_index=True)
    conditional_baseline = pd.concat(conditional_baseline_all, ignore_index=True)
    conditional_meta = pd.concat(conditional_meta_all, ignore_index=True)

    stability_rows: list[dict[str, Any]] = []
    for task, meta, model_components, baseline_components in (
        ("direct_total", direct_meta, direct_model, direct_baseline),
        ("conditional_D_given_T", conditional_meta, conditional_model, conditional_baseline),
    ):
        for (competition, fold_name), indexes in meta.groupby(["competition_id", "fold"]).groups.items():
            indexes = list(indexes)
            model_summary = metric_summary(model_components.loc[indexes])
            baseline_summary = metric_summary(baseline_components.loc[indexes])
            stability_rows.append({
                "task": task,
                "competition_id": competition,
                "fold": fold_name,
                "rows": len(indexes),
                **{f"model_{key}": value for key, value in model_summary.items()},
                **{f"baseline_{key}": value for key, value in baseline_summary.items()},
                **{f"delta_{key}": model_summary[key] - baseline_summary[key] for key in model_summary},
            })

    direct_delta = {
        metric: float(direct_model[metric].mean() - direct_baseline[metric].mean())
        for metric in direct_model.columns
    }
    conditional_delta = {
        metric: float(conditional_model[metric].mean() - conditional_baseline[metric].mean())
        for metric in conditional_model.columns
    }
    direct_boot_window = bootstrap(direct_meta, direct_model, direct_baseline, ["competition_id", "fold"], config)
    direct_boot_comp = bootstrap(direct_meta, direct_model, direct_baseline, ["competition_id"], config)
    conditional_boot_window = bootstrap(
        conditional_meta, conditional_model, conditional_baseline, ["competition_id", "fold"], config
    )
    conditional_boot_comp = bootstrap(
        conditional_meta, conditional_model, conditional_baseline, ["competition_id"], config
    )

    direct_signal = (
        direct_delta["logloss"] < 0
        and direct_delta["rps"] < 0
        and direct_boot_window["logloss"]["p95"] < 0
        and direct_boot_window["rps"]["p95"] < 0
    )
    conditional_signal = all(
        conditional_boot_window[metric]["p95"] < 0 for metric in ("logloss", "brier", "rps")
    )
    result = {
        "schema_version": config["schema_version"],
        "status": (
            "PASS_HISTORICAL_STRUCTURE_SIGNAL_TAIL_AND_FORMAL_PIT_BLOCKED"
            if direct_signal and conditional_signal
            else "FAIL_HISTORICAL_STRUCTURE_SIGNAL"
        ),
        "data_identity": data_identity,
        "split_contract": {
            "complete_seasons": seasons,
            "excluded_incomplete_latest_seasons": excluded,
            "rolling_windows": len(fold_receipts),
            "same_day_freeze_before_update": True,
        },
        "feature_contract": {
            "feature_count": len(feature_names),
            "features": feature_names,
            "inputs": [
                "competition identity", "season identity", "match date", "home/away team identity",
                "strictly prior completed 90-minute score labels",
            ],
            "market_features_used": False,
            "web_context_features_used": False,
            "current_match_result_used": False,
        },
        "algorithm_contract": {
            "direct_total": "multinomial logistic regression directly predicts T=0,1,2,3,4,5,6,7+",
            "conditional_D_given_T": "separate multinomial logistic P(D|T,X) for T=1..6; T=0 deterministic; T=7+ empirical fixed support only",
            "baseline": "competition-specific historical empirical distribution with symmetric Dirichlet smoothing",
            "objective": "policy-season Log Score selects C; test reports Log Score, multiclass Brier, RPS and Top-k",
            "probability_conservation_required": True,
        },
        "folds": fold_receipts,
        "pooled": {
            "direct_total": {
                "test_rows": len(direct_meta),
                "model_metrics": metric_summary(direct_model),
                "baseline_metrics": metric_summary(direct_baseline),
                "delta_model_minus_baseline": direct_delta,
                "bootstrap_competition_window_90": direct_boot_window,
                "bootstrap_competition_90": direct_boot_comp,
                "signal_detected": direct_signal,
                "brier_robust_at_90_percent": direct_boot_window["brier"]["p95"] < 0,
            },
            "conditional_D_given_T": {
                "test_rows": len(conditional_meta),
                "model_metrics": metric_summary(conditional_model),
                "baseline_metrics": metric_summary(conditional_baseline),
                "delta_model_minus_baseline": conditional_delta,
                "bootstrap_competition_window_90": conditional_boot_window,
                "bootstrap_competition_90": conditional_boot_comp,
                "signal_detected": conditional_signal,
            },
        },
        "stability": {
            "direct_total_competition_window_logloss_wins": sum(
                row["delta_logloss"] < 0 for row in stability_rows if row["task"] == "direct_total"
            ),
            "direct_total_competition_window_count": sum(
                row["task"] == "direct_total" for row in stability_rows
            ),
            "conditional_competition_window_logloss_wins": sum(
                row["delta_logloss"] < 0 for row in stability_rows if row["task"] == "conditional_D_given_T"
            ),
            "conditional_competition_window_count": sum(
                row["task"] == "conditional_D_given_T" for row in stability_rows
            ),
        },
        "tail_and_matrix_ruling": {
            "direct_7plus_bucket_evaluated": True,
            "conditional_7plus_exact_total_decomposed": False,
            "conditional_7plus_model": "empirical fixed-support reference only",
            "unified_score_matrix_allowed": False,
            "reason": "T=7+ exact-total decomposition and legal score mapping remain unresolved",
        },
        "formal_ruling": {
            "formal_weight": 0,
            "promotion": False,
            "strict_PIT_market_context_rows": 0,
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
    synthetic = pd.DataFrame([
        {"competition_id": "X", "season": "1", "date_key": "2021-01-01", "home_team": "A", "away_team": "B", "home_goals_90": 1, "away_goals_90": 0, "result_consistent": True, "total_goals": 1, "goal_difference": 1, "source_file": "x", "row_number": 2},
        {"competition_id": "X", "season": "1", "date_key": "2021-01-01", "home_team": "C", "away_team": "D", "home_goals_90": 0, "away_goals_90": 0, "result_consistent": True, "total_goals": 0, "goal_difference": 0, "source_file": "x", "row_number": 3},
        {"competition_id": "X", "season": "1", "date_key": "2021-01-02", "home_team": "A", "away_team": "C", "home_goals_90": 2, "away_goals_90": 1, "result_consistent": True, "total_goals": 3, "goal_difference": 1, "source_file": "x", "row_number": 4},
    ])
    features = build_features(synthetic)
    assert features.loc[0, "comp_n_log"] == features.loc[1, "comp_n_log"] == 0.0
    assert features.loc[2, "comp_n_log"] == math.log1p(2)
    probabilities = np.asarray([[0.2, 0.8], [0.7, 0.3]])
    components = metric_components(np.asarray([1, 0]), probabilities, [0, 1])
    assert len(components) == 2 and np.isfinite(components.to_numpy()).all()
    assert abs(float(probabilities.sum(axis=1).max()) - 1.0) < 1e-12


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
    result = run(load_config(args.config), args.out, args.stability)
    compact = {
        "status": result["status"],
        "data_identity": result["data_identity"],
        "pooled": result["pooled"],
        "stability": result["stability"],
        "tail_and_matrix_ruling": result["tail_and_matrix_ruling"],
        "formal_ruling": result["formal_ruling"],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
