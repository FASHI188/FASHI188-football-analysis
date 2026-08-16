#!/usr/bin/env python3
"""Post-result same-fixed500 oracle diagnostics.

No new sample is consumed. Two counterfactuals are measured on the exact frozen 500:
1) Oracle parity: replace learned P(parity|X) with the realized parity, but keep the
   frozen parity-branch T models and existing conditional-GD model.
2) Oracle T: replace Direct-T with the realized total class, keeping only the existing
   conditional-GD model.

These are diagnosis-only and cannot change the frozen fixed500 verdict or authorize
promotion. formal_weight=0.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_direct_t_gd_joint_fixed200_r1 import (
    KEYS,
    LABELS,
    assemble_joint,
    conditional_probabilities,
    direct_total_probabilities,
    load_config,
)
from evaluate_direct_t_parity_gd_fixed500_r1 import (
    attach_exact_total,
    hda_metrics,
    load_experiment,
    sample_fixed_n,
    score_metrics,
)
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model, select_C

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "direct_t_parity_gd_fixed500_oracles_r1.json"
ROWS_OUT = ROOT / "manifests" / "direct_t_parity_gd_fixed500_oracles_r1_rows.csv"


def fit_branch_probabilities(
    fold: pd.DataFrame,
    sample: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[dict[int, tuple[list[int], np.ndarray]], dict[str, Any]]:
    train = fold[fold.split == "train"]
    policy = fold[fold.split == "policy"]
    fit = fold[fold.split.isin(["train", "policy"])]
    branch_classes = {0: [0, 2, 4, 6, 7], 1: [1, 3, 5, 7]}
    output: dict[int, tuple[list[int], np.ndarray]] = {}
    receipt: dict[str, Any] = {}
    for parity, classes in branch_classes.items():
        train_b = train[train.exact_parity == parity]
        policy_b = policy[policy.exact_parity == parity]
        fit_b = fit[fit.exact_parity == parity]
        selected_C, policy_grid = select_C(train_b, policy_b, features, "total_class", classes, config)
        model = make_model(selected_C, config)
        model.fit(fit_b[features], fit_b.total_class)
        probs = align_probability(model, sample[features], classes)
        output[parity] = (classes, probs)
        receipt[str(parity)] = {
            "support": classes,
            "selected_C": selected_C,
            "policy_grid": policy_grid,
            "fit_rows": int(len(fit_b)),
        }
    return output, receipt


def oracle_parity_total(
    actual_parity: np.ndarray,
    branches: dict[int, tuple[list[int], np.ndarray]],
) -> np.ndarray:
    n = len(actual_parity)
    out = np.zeros((n, 8), dtype=float)
    for parity in (0, 1):
        classes, probs = branches[parity]
        idx = np.flatnonzero(actual_parity == parity)
        for j, total_class in enumerate(classes):
            out[idx, total_class] += probs[idx, j]
    residual = float(np.max(np.abs(out.sum(axis=1) - 1.0)))
    if residual > 1e-10:
        raise ResearchError(f"oracle parity total mass failure: {residual}")
    return out


def oracle_t_frame(
    rows: pd.DataFrame,
    cond: dict[int, tuple[list[int], np.ndarray]],
    prefix: str,
) -> None:
    ph: list[float] = []
    pd_: list[float] = []
    pa: list[float] = []
    pred_result: list[str] = []
    pred_score: list[str] = []
    pred_score_top3: list[str] = []
    for pos, (_, row) in enumerate(rows.iterrows()):
        actual_total = int(row.actual_total)
        total_class = min(actual_total, 7)
        classes, probs_all = cond[total_class]
        probs = probs_all[pos]
        h = float(sum(p for g, p in zip(classes, probs) if g > 0))
        d = float(sum(p for g, p in zip(classes, probs) if g == 0))
        a = float(sum(p for g, p in zip(classes, probs) if g < 0))
        mass = h + d + a
        h, d, a = h / mass, d / mass, a / mass
        ph.append(h); pd_.append(d); pa.append(a)
        pred_result.append(LABELS[int(np.argmax([h, d, a]))])
        if actual_total <= 6:
            ranked = sorted(zip(classes, probs), key=lambda kv: (-float(kv[1]), int(kv[0])))
            scores: list[str] = []
            for gd, _ in ranked[:3]:
                hg = (actual_total + int(gd)) // 2
                ag = (actual_total - int(gd)) // 2
                scores.append(f"{hg}:{ag}")
            pred_score.append(scores[0])
            pred_score_top3.append(";".join(scores))
        else:
            pred_score.append("TAIL7+")
            pred_score_top3.append("TAIL7+")
    rows[f"{prefix}_p_home"] = ph
    rows[f"{prefix}_p_draw"] = pd_
    rows[f"{prefix}_p_away"] = pa
    rows[f"{prefix}_pred_result"] = pred_result
    rows[f"{prefix}_pred_score"] = pred_score
    rows[f"{prefix}_pred_score_top3"] = pred_score_top3


def run() -> dict[str, Any]:
    exp = load_experiment()
    config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base_features = build_features(raw)
    feature_names = select_core_features(base_features)
    seasons, excluded = complete_seasons(raw, config)
    test_position = int(exp["test_position_zero_based"])

    base_fold = base_features.copy()
    base_fold["split"] = assign_fold(base_fold, seasons, test_position)
    base_fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
    sample_base, sample_hash = sample_fixed_n(base_fold[base_fold.split == "test"].copy(), int(exp["sample_n"]))

    fold = attach_exact_total(base_fold, raw)
    sample = fold.merge(sample_base[KEYS + ["match_identity", "identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    raw_scores = raw[KEYS + ["home_goals_90", "away_goals_90", "total_goals"]].copy()
    raw_scores["season"] = raw_scores["season"].astype(str)
    sample["season"] = sample["season"].astype(str)
    sample = sample.merge(raw_scores, on=KEYS, how="left", validate="one_to_one")
    if len(sample) != 500:
        raise ResearchError("oracle diagnostic sample mismatch")

    p_flat, _, _ = direct_total_probabilities(fold, sample, feature_names, config)
    branches, branch_receipt = fit_branch_probabilities(fold, sample, feature_names, config)
    p_oracle_parity = oracle_parity_total(sample.exact_parity.to_numpy(int), branches)
    cond_model, _, cond_receipt = conditional_probabilities(fold, sample, feature_names, config)

    flat_joint = assemble_joint(p_flat, cond_model)
    oracle_parity_joint = assemble_joint(p_oracle_parity, cond_model)

    rows = sample[KEYS + ["match_identity", "identity_hash", "home_goals_90", "away_goals_90", "total_goals", "goal_difference"]].copy()
    rows = rows.rename(columns={"total_goals": "actual_total", "goal_difference": "actual_gd"})
    rows["actual_total_class"] = np.minimum(rows.actual_total.astype(int), 7)
    rows["actual_parity"] = rows.actual_total.astype(int) % 2
    rows["actual_score"] = rows.home_goals_90.astype(int).astype(str) + ":" + rows.away_goals_90.astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd > 0, "H", np.where(rows.actual_gd == 0, "D", "A"))
    rows["flat_pred_total_class"] = np.argmax(p_flat, axis=1)
    rows["oracle_parity_pred_total_class"] = np.argmax(p_oracle_parity, axis=1)

    for prefix, joint in (("flat", flat_joint), ("oracle_parity", oracle_parity_joint)):
        jf = pd.DataFrame(joint)
        for column in jf.columns:
            rows[f"{prefix}_{column}"] = jf[column].to_numpy()
    oracle_t_frame(rows, cond_model, "oracle_t")

    flat_hda = hda_metrics(rows, "flat")
    oracle_parity_hda = hda_metrics(rows, "oracle_parity")
    oracle_t_hda = hda_metrics(rows, "oracle_t")
    # hda_metrics exposes per-row LL arrays for bootstrap use in the main evaluator;
    # strip those internal ndarrays from this JSON-only diagnostic receipt.
    flat_hda.pop("_ll_rows", None)
    oracle_parity_hda.pop("_ll_rows", None)
    oracle_t_hda.pop("_ll_rows", None)
    flat_score = score_metrics(rows, "flat")
    oracle_parity_score = score_metrics(rows, "oracle_parity")
    oracle_t_score = score_metrics(rows, "oracle_t")

    draws = rows[rows.actual_result == "D"].copy()
    non_draws = rows[rows.actual_result != "D"].copy()
    draw_breakdown: dict[str, Any] = {}
    for score, part in draws.groupby("actual_score"):
        draw_breakdown[str(score)] = {
            "n": int(len(part)),
            "oracle_parity_total_top1_correct": int(np.sum(part.oracle_parity_pred_total_class == part.actual_total_class)),
            "oracle_parity_draw_calls": int(np.sum(part.oracle_parity_pred_result == "D")),
            "oracle_t_draw_calls": int(np.sum(part.oracle_t_pred_result == "D")),
            "oracle_t_draw_hits": int(np.sum((part.oracle_t_pred_result == "D") & (part.actual_result == "D"))),
        }

    result = {
        "schema_version": "DIRECT_T_PARITY_GD_FIXED500_ORACLES_R1",
        "classification": "POST_RESULT_SAME_SAMPLE_DIAGNOSTIC_ONLY",
        "sample_n": 500,
        "sample_identity_sha256": sample_hash,
        "new_sample_consumed": False,
        "frozen_verdict_unchanged": True,
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "questions": {
            "oracle_parity": "If total-goal parity were known exactly, how much of Direct-T/HDA failure remains?",
            "oracle_t": "If exact total class were known, does existing P(GD|T,X) recover Draw and score structure on this independent fixed500?",
        },
        "branch_receipt": branch_receipt,
        "conditional_gd_receipt": cond_receipt,
        "metrics": {
            "flat": {
                "hda": flat_hda,
                "score": flat_score,
                "total_top1": float(np.mean(rows.flat_pred_total_class == rows.actual_total_class)),
            },
            "oracle_parity": {
                "hda": oracle_parity_hda,
                "score": oracle_parity_score,
                "total_top1": float(np.mean(rows.oracle_parity_pred_total_class == rows.actual_total_class)),
            },
            "oracle_t": {
                "hda": oracle_t_hda,
                "score": oracle_t_score,
            },
        },
        "draw_diagnostics": {
            "actual_draws": int(len(draws)),
            "oracle_parity_draw_calls": int(np.sum(rows.oracle_parity_pred_result == "D")),
            "oracle_parity_draw_hits": int(np.sum((rows.oracle_parity_pred_result == "D") & (rows.actual_result == "D"))),
            "oracle_t_draw_calls": int(np.sum(rows.oracle_t_pred_result == "D")),
            "oracle_t_draw_hits": int(np.sum((rows.oracle_t_pred_result == "D") & (rows.actual_result == "D"))),
            "oracle_t_false_draw_calls": int(np.sum((rows.oracle_t_pred_result == "D") & (rows.actual_result != "D"))),
            "mean_oracle_t_p_draw_actual_draw": float(draws.oracle_t_p_draw.mean()),
            "mean_oracle_t_p_draw_non_draw": float(non_draws.oracle_t_p_draw.mean()),
            "draw_score_breakdown": draw_breakdown,
        },
        "governance": {
            "formal_weight": 0,
            "provider_requests": 0,
            "new_data_collection": False,
            "latest_position4_confirmation_opened": False,
            "formal_asset_changes": 0,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows.to_csv(ROWS_OUT, index=False)
    return result


def main() -> None:
    x = run()
    print(json.dumps({
        "classification": x["classification"],
        "sample_n": x["sample_n"],
        "sample_identity_sha256": x["sample_identity_sha256"],
        "metrics": x["metrics"],
        "draw_diagnostics": x["draw_diagnostics"],
        "governance": x["governance"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
