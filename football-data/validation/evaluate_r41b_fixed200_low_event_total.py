#!/usr/bin/env python3
"""R41B: disjoint fixed-200 low-event hierarchical direct-total challenger.

The fixed200 is retrospective/viewed evidence. It is identity-selected without labels,
disjoint from R41A, and never used for regularization or threshold selection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import (
    IDENTITY_FIELDS,
    add_identity_key,
    bootstrap_difference,
    load_json,
    select_fixed_identities,
    split_for_latest_complete,
)
from v510_historical_structure_features_r1 import (
    ResearchError,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import (
    align_probability,
    empirical_probability,
    make_model,
    metric_components,
    metric_summary,
    select_C,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r41b_fixed200_low_event_total.json"
DEFAULT_OUT = ROOT / "manifests" / "r41b_fixed200_low_event_total_status.json"
TOTAL_CLASSES = list(range(8))


def fit_probability(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    fit: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    target: str,
    classes: list[int],
    base_cfg: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    selected_C, grid = select_C(train, policy, features, target, classes, base_cfg)
    model = make_model(selected_C, base_cfg)
    model.fit(fit[features], fit[target])
    probabilities = align_probability(model, test[features], classes)
    return probabilities, {
        "selected_C": float(selected_C),
        "policy_grid": grid,
        "probability_sum_max_residual": float(np.max(np.abs(probabilities.sum(axis=1) - 1.0))),
    }


def per_row_logloss(y: np.ndarray, p: np.ndarray, classes: list[int]) -> np.ndarray:
    positions = {int(value): idx for idx, value in enumerate(classes)}
    idx = np.asarray([positions[int(value)] for value in y], dtype=int)
    return -np.log(np.clip(p[np.arange(len(y)), idx], 1e-15, 1.0))


def score_breakdown(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for score, part in frame.groupby("score_label", sort=True):
        rows.append({
            "score": str(score),
            "rows": int(len(part)),
            "direct_multinomial_true_total_nll": float(part.baseline_nll.mean()),
            "low_event_hierarchy_true_total_nll": float(part.challenger_nll.mean()),
            "delta_challenger_minus_direct": float((part.challenger_nll - part.baseline_nll).mean()),
        })
    return rows


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    data_identity = audit_data_identity(raw, base_cfg)
    seasons, excluded = complete_seasons(raw, base_cfg)
    raw_keyed = add_identity_key(raw)

    target_parts = []
    target_pos = int(cfg["fit_contract"]["test_season_position_zero_based"])
    for competition, sequence in seasons.items():
        target_season = sequence[target_pos]
        target_parts.append(
            raw_keyed[(raw_keyed.competition_id.astype(str) == str(competition)) & (raw_keyed.season.astype(str) == str(target_season))]
        )
    target_pool = pd.concat(target_parts, ignore_index=True)

    prior_ids, prior_sha = select_fixed_identities(
        target_pool,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["excluded_prior_sample_seed"]),
    )
    remaining = target_pool[~target_pool.identity_key.isin(set(prior_ids))].copy()
    selected_ids, sample_sha = select_fixed_identities(
        remaining,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["seed"]),
    )
    overlap = len(set(prior_ids) & set(selected_ids))
    if overlap:
        raise ResearchError(f"R41A/R41B sample overlap: {overlap}")

    features = add_identity_key(build_features(raw))
    feature_names = select_core_features(features)
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    features["low_event"] = (features.total_class.astype(int) <= 2).astype(int)
    test = features[features.identity_key.isin(set(selected_ids))].copy().sort_values("identity_key")
    if len(test) != int(cfg["sample_contract"]["sample_size"]):
        raise ResearchError(f"R41B fixed200 reproduction failed: {len(test)}")
    if not (test.split == "target_pool").all():
        raise ResearchError("R41B fixed200 contains non-target-season row")

    train = features[features.split == "train"]
    policy = features[features.split == "policy"]
    fit = features[features.split.isin(["train", "policy"])]

    direct_p, direct_receipt = fit_probability(
        train, policy, fit, test, feature_names, "total_class", TOTAL_CLASSES, base_cfg
    )

    low_binary_p, low_gate_receipt = fit_probability(
        train, policy, fit, test, feature_names, "low_event", [0, 1], base_cfg
    )
    low_train, low_policy, low_fit = train[train.low_event == 1], policy[policy.low_event == 1], fit[fit.low_event == 1]
    high_train, high_policy, high_fit = train[train.low_event == 0], policy[policy.low_event == 0], fit[fit.low_event == 0]
    low_cond_p, low_cond_receipt = fit_probability(
        low_train, low_policy, low_fit, test, feature_names, "total_class", [0, 1, 2], base_cfg
    )
    high_cond_p, high_cond_receipt = fit_probability(
        high_train, high_policy, high_fit, test, feature_names, "total_class", [3, 4, 5, 6, 7], base_cfg
    )

    challenger_p = np.zeros((len(test), 8), dtype=float)
    p_low = low_binary_p[:, 1]
    p_high = low_binary_p[:, 0]
    challenger_p[:, 0:3] = p_low[:, None] * low_cond_p
    challenger_p[:, 3:8] = p_high[:, None] * high_cond_p
    residual = float(np.max(np.abs(challenger_p.sum(axis=1) - 1.0)))
    if residual > 1e-10:
        raise ResearchError(f"challenger probability conservation failure: {residual}")

    y = test.total_class.to_numpy(int)
    direct_components = metric_components(y, direct_p, TOTAL_CLASSES)
    challenger_components = metric_components(y, challenger_p, TOTAL_CLASSES)
    delta = {name: float(challenger_components[name].mean() - direct_components[name].mean()) for name in direct_components.columns}
    row_log_delta = challenger_components.logloss.to_numpy(float) - direct_components.logloss.to_numpy(float)
    boot = bootstrap_difference(row_log_delta, cfg)

    y_low = test.low_event.to_numpy(int)
    direct_binary = np.column_stack([direct_p[:, 3:8].sum(axis=1), direct_p[:, 0:3].sum(axis=1)])
    low_gate_components = metric_components(y_low, low_binary_p, [0, 1])
    direct_low_components = metric_components(y_low, direct_binary, [0, 1])
    low_gate_delta = {
        name: float(low_gate_components[name].mean() - direct_low_components[name].mean())
        for name in low_gate_components.columns
    }

    truth_cols = IDENTITY_FIELDS + ["home_goals_90", "away_goals_90", "total_goals"]
    truth = raw_keyed[truth_cols + ["identity_key"]].copy()
    if truth.identity_key.duplicated().any():
        raise ResearchError("non-unique truth identity")
    rows = test.merge(truth, on=IDENTITY_FIELDS + ["identity_key"], how="left", validate="one_to_one")
    rows["baseline_nll"] = per_row_logloss(y, direct_p, TOTAL_CLASSES)
    rows["challenger_nll"] = per_row_logloss(y, challenger_p, TOTAL_CLASSES)
    rows["is_draw"] = rows.home_goals_90.astype(int) == rows.away_goals_90.astype(int)
    rows["score_label"] = rows.home_goals_90.astype(int).astype(str) + "-" + rows.away_goals_90.astype(int).astype(str)
    draw_rows = rows[rows.is_draw].copy()
    draw_boot = bootstrap_difference((draw_rows.challenger_nll - draw_rows.baseline_nll).to_numpy(float), cfg, 1)

    pass_gate = (
        delta["logloss"] < 0
        and boot["p95"] < 0
        and delta["brier"] <= 0
        and delta["rps"] <= 0
    )
    scientific = (
        "PASS_R41B_LOW_EVENT_HIERARCHY_INCREMENT_FIXED200"
        if pass_gate
        else "FAIL_R41B_LOW_EVENT_HIERARCHY_NO_INCREMENT_FIXED200"
    )

    competition_counts = test.groupby("competition_id").size().sort_index()
    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R41B_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": scientific,
        "data_identity": data_identity,
        "sample": {
            "rows": int(len(test)),
            "target_pool_rows": int(len(target_pool)),
            "remaining_after_R41A_exclusion": int(len(remaining)),
            "identity_sha256": sample_sha,
            "seed": int(cfg["sample_contract"]["seed"]),
            "R41A_reproduced_identity_sha256": prior_sha,
            "R41A_overlap": int(overlap),
            "competitions_represented": int(test.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in competition_counts.items()},
            "date_min": str(test.date_key.min()),
            "date_max": str(test.date_key.max()),
            "labels_used_for_identity_selection": False,
            "blind_claim": False,
        },
        "model_contract": {
            "feature_count": int(len(feature_names)),
            "direct_multinomial": direct_receipt,
            "low_event_gate": low_gate_receipt,
            "low_conditional_0_1_2": low_cond_receipt,
            "high_conditional_3_to_7plus": high_cond_receipt,
            "challenger_probability_sum_max_residual": residual,
            "fixed200_used_for_regularization_selection": False,
        },
        "metrics": {
            "direct_multinomial": metric_summary(direct_components),
            "low_event_hierarchy": metric_summary(challenger_components),
            "delta_challenger_minus_direct": delta,
            "paired_bootstrap_logloss_delta_90": boot,
            "low_event_binary_direct_implied": metric_summary(direct_low_components),
            "low_event_binary_explicit_gate": metric_summary(low_gate_components),
            "low_event_binary_delta_explicit_minus_direct": low_gate_delta,
        },
        "draw_diagnostics": {
            "actual_draws": int(len(draw_rows)),
            "mean_true_total_nll_direct": float(draw_rows.baseline_nll.mean()) if len(draw_rows) else None,
            "mean_true_total_nll_challenger": float(draw_rows.challenger_nll.mean()) if len(draw_rows) else None,
            "mean_delta_challenger_minus_direct": float((draw_rows.challenger_nll - draw_rows.baseline_nll).mean()) if len(draw_rows) else None,
            "paired_bootstrap_logloss_delta_90": draw_boot,
            "score_breakdown": score_breakdown(draw_rows),
        },
        "gate": {
            "logloss_mean_better": bool(delta["logloss"] < 0),
            "logloss_bootstrap_p95_below_zero": bool(boot["p95"] < 0),
            "brier_nonworse": bool(delta["brier"] <= 0),
            "rps_nonworse": bool(delta["rps"] <= 0),
            "all_required": bool(pass_gate),
        },
        "interpretation": (
            "A structural low-event hierarchy adds reproducible direct-total information on this disjoint fixed200; replicate before any broader conclusion."
            if pass_gate
            else "Reparameterizing the same historical information into an explicit low-event hierarchy did not robustly improve direct total goals; the R41A bottleneck therefore points more toward missing/new PIT opportunity-state information than model geometry alone."
        ),
        "governance": cfg["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    p_low = np.asarray([0.7, 0.2])
    low = np.asarray([[0.2, 0.3, 0.5], [0.4, 0.3, 0.3]])
    high = np.asarray([[0.1, 0.2, 0.3, 0.2, 0.2], [0.2, 0.2, 0.2, 0.2, 0.2]])
    p = np.zeros((2, 8))
    p[:, :3] = p_low[:, None] * low
    p[:, 3:] = (1 - p_low)[:, None] * high
    assert np.max(np.abs(p.sum(axis=1) - 1.0)) < 1e-12
    assert np.all(p >= 0)


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
        "scientific_verdict": result["scientific_verdict"],
        "sample": result["sample"],
        "metrics": result["metrics"],
        "draw_diagnostics": result["draw_diagnostics"],
        "gate": result["gate"],
        "interpretation": result["interpretation"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
