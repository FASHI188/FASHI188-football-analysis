#!/usr/bin/env python3
"""Frozen retrospective fixed500 test of hierarchical Direct-T.

Architecture under test:
    P(parity|X) * P(Tclass|parity,X) * existing P(GD|Tclass,X)

The target 500 is selected by identity hash only from rolling test position 3,
so the latest position-4 season is not opened. The method is frozen before
result visibility. This is research-only, formal_weight=0.
"""
from __future__ import annotations

import hashlib
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
    identity_hash,
    load_config,
    row_identity,
    sample_fixed200,
)
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
    make_model,
    metric_components,
    metric_summary,
    select_C,
)

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_CONFIG = ROOT / "config" / "direct_t_parity_gd_fixed500_r1.json"
OUT = ROOT / "manifests" / "direct_t_parity_gd_fixed500_r1_status.json"
ROWS_OUT = ROOT / "manifests" / "direct_t_parity_gd_fixed500_r1_rows.csv"
TOTAL_CLASSES = list(range(8))


def load_experiment() -> dict[str, Any]:
    value = json.loads(EXPERIMENT_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("experiment config root must be object")
    return value


def sample_fixed_n(test: pd.DataFrame, n: int) -> tuple[pd.DataFrame, str]:
    if len(test) < n:
        raise ResearchError(f"target fold has only {len(test)} rows; need {n}")
    sample = test.copy()
    sample["match_identity"] = sample.apply(row_identity, axis=1)
    sample["identity_hash"] = sample["match_identity"].map(identity_hash)
    sample = sample.sort_values(["identity_hash", "match_identity"]).head(n).copy()
    if sample["match_identity"].nunique() != n:
        raise ResearchError("fixed sample identity uniqueness failure")
    digest = hashlib.sha256(("\n".join(sorted(sample["match_identity"])) + "\n").encode("utf-8")).hexdigest()
    return sample, digest


def attach_exact_total(features: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    left = features.copy()
    right = raw[KEYS + ["total_goals"]].copy()
    left["season"] = left["season"].astype(str)
    right["season"] = right["season"].astype(str)
    out = left.merge(right, on=KEYS, how="left", validate="one_to_one")
    if out["total_goals"].isna().any():
        raise ResearchError("exact total label join failure")
    out["exact_total"] = out["total_goals"].astype(int)
    out["exact_parity"] = out["exact_total"] % 2
    return out.drop(columns=["total_goals"])


def fit_hierarchical_total(
    fold: pd.DataFrame,
    sample: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    train = fold[fold.split == "train"]
    policy = fold[fold.split == "policy"]
    fit = fold[fold.split.isin(["train", "policy"])]

    parity_classes = [0, 1]
    parity_C, parity_grid = select_C(train, policy, features, "exact_parity", parity_classes, config)
    parity_model = make_model(parity_C, config)
    parity_model.fit(fit[features], fit.exact_parity)
    p_parity = align_probability(parity_model, sample[features], parity_classes)

    p_total = np.zeros((len(sample), 8), dtype=float)
    branch_receipts: dict[str, Any] = {}
    branch_classes = {0: [0, 2, 4, 6, 7], 1: [1, 3, 5, 7]}

    for parity in parity_classes:
        classes = branch_classes[parity]
        train_b = train[train.exact_parity == parity]
        policy_b = policy[policy.exact_parity == parity]
        fit_b = fit[fit.exact_parity == parity]
        if train_b.total_class.nunique() < 2 or policy_b.total_class.nunique() < 2:
            raise ResearchError(f"insufficient branch classes for parity={parity}")
        selected_C, policy_grid = select_C(train_b, policy_b, features, "total_class", classes, config)
        model = make_model(selected_C, config)
        model.fit(fit_b[features], fit_b.total_class)
        p_branch = align_probability(model, sample[features], classes)
        parity_mass = p_parity[:, parity]
        for j, total_class in enumerate(classes):
            p_total[:, total_class] += parity_mass * p_branch[:, j]
        branch_receipts[str(parity)] = {
            "support": classes,
            "train_rows": int(len(train_b)),
            "policy_rows": int(len(policy_b)),
            "fit_rows": int(len(fit_b)),
            "selected_C": selected_C,
            "policy_grid": policy_grid,
        }

    residual = float(np.max(np.abs(p_total.sum(axis=1) - 1.0)))
    if residual > 1e-10:
        raise ResearchError(f"hierarchical Direct-T probability conservation failure: {residual}")
    receipt = {
        "parity_selected_C": parity_C,
        "parity_policy_grid": parity_grid,
        "branch_receipts": branch_receipts,
        "probability_sum_max_residual": residual,
        "tail_rule": "even>=8 and odd>=7 are fit separately then merged into legacy Tclass=7+ before conditional-GD",
    }
    return p_total, p_parity, receipt


def hda_metrics(frame: pd.DataFrame, prefix: str) -> dict[str, Any]:
    actual = frame["actual_result"].astype(str).to_numpy()
    pred = frame[f"{prefix}_pred_result"].astype(str).to_numpy()
    p = frame[[f"{prefix}_p_home", f"{prefix}_p_draw", f"{prefix}_p_away"]].to_numpy(float)
    idx = np.asarray([LABELS.index(x) for x in actual], dtype=int)
    onehot = np.zeros_like(p)
    onehot[np.arange(len(p)), idx] = 1.0
    ll_rows = -np.log(np.clip(p[np.arange(len(p)), idx], 1e-15, 1.0))
    brier_rows = ((p - onehot) ** 2).sum(axis=1)
    rps_rows = ((np.cumsum(p, axis=1)[:, :-1] - np.cumsum(onehot, axis=1)[:, :-1]) ** 2).sum(axis=1) / 2.0
    per_class_f1 = []
    for label in LABELS:
        tp = int(np.sum((pred == label) & (actual == label)))
        fp = int(np.sum((pred == label) & (actual != label)))
        fn = int(np.sum((pred != label) & (actual == label)))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        per_class_f1.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    tp_d = int(np.sum((pred == "D") & (actual == "D")))
    fp_d = int(np.sum((pred == "D") & (actual != "D")))
    fn_d = int(np.sum((pred != "D") & (actual == "D")))
    precision_d = tp_d / (tp_d + fp_d) if tp_d + fp_d else 0.0
    recall_d = tp_d / (tp_d + fn_d) if tp_d + fn_d else 0.0
    f1_d = 2 * precision_d * recall_d / (precision_d + recall_d) if precision_d + recall_d else 0.0
    return {
        "accuracy": float(np.mean(pred == actual)),
        "macro_f1": float(np.mean(per_class_f1)),
        "log_loss": float(ll_rows.mean()),
        "brier": float(brier_rows.mean()),
        "rps": float(rps_rows.mean()),
        "predicted_counts": {label: int(np.sum(pred == label)) for label in LABELS},
        "actual_counts": {label: int(np.sum(actual == label)) for label in LABELS},
        "draw_precision": precision_d,
        "draw_recall": recall_d,
        "draw_f1": f1_d,
        "draw_hits": tp_d,
        "_ll_rows": ll_rows,
    }


def score_metrics(frame: pd.DataFrame, prefix: str) -> dict[str, Any]:
    actual = frame["actual_score"].astype(str).to_numpy()
    top1 = frame[f"{prefix}_pred_score"].astype(str).to_numpy()
    top3 = frame[f"{prefix}_pred_score_top3"].astype(str).str.split(";").to_list()
    eligible = frame["actual_total"].to_numpy(int) <= 6
    return {
        "top1_accuracy": float(np.mean(top1 == actual)),
        "top3_accuracy": float(np.mean([a in xs for a, xs in zip(actual, top3)])),
        "actual_T0_6_rows": int(eligible.sum()),
        "top1_accuracy_T0_6": float(np.mean(top1[eligible] == actual[eligible])) if eligible.any() else None,
        "top3_accuracy_T0_6": float(np.mean([a in xs for a, xs in zip(actual[eligible], np.asarray(top3, dtype=object)[eligible])])) if eligible.any() else None,
    }


def paired_bootstrap(delta_rows: np.ndarray, samples: int, seed: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    n = len(delta_rows)
    picks = rng.integers(0, n, size=(samples, n))
    means = delta_rows[picks].mean(axis=1)
    return {
        "mean": float(means.mean()),
        "p05": float(np.quantile(means, 0.05)),
        "median": float(np.quantile(means, 0.50)),
        "p95": float(np.quantile(means, 0.95)),
        "p_delta_lt_0": float(np.mean(means < 0)),
    }


def run() -> dict[str, Any]:
    exp = load_experiment()
    config = load_config()
    ledger = ROOT / str(config["input_ledger"])
    raw = pd.read_csv(ledger)
    data_identity = audit_data_identity(raw, config)
    base_features = build_features(raw)
    feature_names = select_core_features(base_features)
    seasons, excluded = complete_seasons(raw, config)

    test_position = int(exp["test_position_zero_based"])
    if test_position >= max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"]):
        raise ResearchError("fixed500 must not open the latest rolling confirmation position")

    base_fold = base_features.copy()
    base_fold["split"] = assign_fold(base_fold, seasons, test_position)
    base_fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
    test = base_fold[base_fold.split == "test"].copy()
    sample_base, sample_hash = sample_fixed_n(test, int(exp["sample_n"]))

    # Explicitly prove disjointness from PR #196 fixed200, which came from latest position 4.
    latest_position = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    latest_fold = base_features.copy()
    latest_fold["split"] = assign_fold(latest_fold, seasons, latest_position)
    prior200, prior200_hash = sample_fixed200(latest_fold[latest_fold.split == "test"].copy())
    overlap_prior200 = len(set(sample_base.match_identity) & set(prior200.match_identity))
    if overlap_prior200 != 0:
        raise ResearchError("fixed500 overlaps PR196 fixed200")

    # Labels are attached only after the identity-only fixed500 is frozen.
    fold = attach_exact_total(base_fold, raw)
    sample = fold.merge(sample_base[KEYS + ["match_identity", "identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    if len(sample) != int(exp["sample_n"]):
        raise ResearchError("fixed500 label attachment changed sample size")

    # goal_difference already exists on the frozen feature frame; attach only score/total fields here.
    raw_scores = raw[KEYS + ["home_goals_90", "away_goals_90", "total_goals"]].copy()
    raw_scores["season"] = raw_scores["season"].astype(str)
    sample["season"] = sample["season"].astype(str)
    sample = sample.merge(raw_scores, on=KEYS, how="left", validate="one_to_one")
    if sample[["home_goals_90", "away_goals_90", "total_goals"]].isna().any().any():
        raise ResearchError("final-score attachment failure")

    p_flat, _, flat_receipt = direct_total_probabilities(fold, sample, feature_names, config)
    p_hier, p_parity, hier_receipt = fit_hierarchical_total(fold, sample, feature_names, config)
    cond_model, _, cond_receipt = conditional_probabilities(fold, sample, feature_names, config)
    flat_joint = assemble_joint(p_flat, cond_model)
    hier_joint = assemble_joint(p_hier, cond_model)

    rows = sample[KEYS + ["match_identity", "identity_hash", "home_goals_90", "away_goals_90", "total_goals", "goal_difference"]].copy()
    rows = rows.rename(columns={"total_goals": "actual_total", "goal_difference": "actual_gd"})
    rows["actual_total_class"] = np.minimum(rows.actual_total.astype(int), 7)
    rows["actual_parity"] = rows.actual_total.astype(int) % 2
    rows["actual_score"] = rows.home_goals_90.astype(int).astype(str) + ":" + rows.away_goals_90.astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd > 0, "H", np.where(rows.actual_gd == 0, "D", "A"))
    rows["flat_pred_total_class"] = np.argmax(p_flat, axis=1)
    rows["hier_pred_total_class"] = np.argmax(p_hier, axis=1)
    rows["hier_pred_parity"] = np.argmax(p_parity, axis=1)
    rows["hier_p_even"] = p_parity[:, 0]
    rows["hier_p_odd"] = p_parity[:, 1]

    for prefix, joint in (("flat", flat_joint), ("hier", hier_joint)):
        jf = pd.DataFrame(joint)
        for column in jf.columns:
            rows[f"{prefix}_{column}"] = jf[column].to_numpy()

    yT = rows.actual_total_class.to_numpy(int)
    flat_T_components = metric_components(yT, p_flat, TOTAL_CLASSES)
    hier_T_components = metric_components(yT, p_hier, TOTAL_CLASSES)
    flat_T = metric_summary(flat_T_components)
    hier_T = metric_summary(hier_T_components)
    T_delta = {k: float(hier_T[k] - flat_T[k]) for k in flat_T}

    parity_truth = rows.actual_parity.to_numpy(int)
    parity_idx = parity_truth
    parity_ll_rows = -np.log(np.clip(p_parity[np.arange(len(rows)), parity_idx], 1e-15, 1.0))
    parity_onehot = np.zeros_like(p_parity)
    parity_onehot[np.arange(len(rows)), parity_idx] = 1.0
    parity_metrics = {
        "accuracy": float(np.mean(rows.hier_pred_parity.to_numpy(int) == parity_truth)),
        "log_loss": float(parity_ll_rows.mean()),
        "brier": float(((p_parity - parity_onehot) ** 2).sum(axis=1).mean()),
        "actual_even": int(np.sum(parity_truth == 0)),
        "actual_odd": int(np.sum(parity_truth == 1)),
        "pred_even": int(np.sum(rows.hier_pred_parity == 0)),
        "pred_odd": int(np.sum(rows.hier_pred_parity == 1)),
    }

    flat_hda = hda_metrics(rows, "flat")
    hier_hda = hda_metrics(rows, "hier")
    flat_ll_rows = np.asarray(flat_hda.pop("_ll_rows"), dtype=float)
    hier_ll_rows = np.asarray(hier_hda.pop("_ll_rows"), dtype=float)
    flat_score = score_metrics(rows, "flat")
    hier_score = score_metrics(rows, "hier")

    draws = rows[rows.actual_result == "D"].copy()
    non_draw_even = rows[(rows.actual_result != "D") & (rows.actual_parity == 0)].copy()
    draw_breakdown: dict[str, Any] = {}
    for score, part in draws.groupby("actual_score"):
        draw_breakdown[str(score)] = {
            "n": int(len(part)),
            "parity_top1_correct": int(np.sum(part.hier_pred_parity == 0)),
            "hier_total_top1_correct": int(np.sum(part.hier_pred_total_class == part.actual_total_class)),
            "flat_total_top1_correct": int(np.sum(part.flat_pred_total_class == part.actual_total_class)),
            "hier_draw_calls": int(np.sum(part.hier_pred_result == "D")),
            "flat_draw_calls": int(np.sum(part.flat_pred_result == "D")),
        }

    nboot = int(exp["decision_contract"]["bootstrap_samples"])
    seed = int(exp["decision_contract"]["bootstrap_seed"])
    boot_T_ll = paired_bootstrap(
        hier_T_components.logloss.to_numpy(float) - flat_T_components.logloss.to_numpy(float), nboot, seed
    )
    boot_hda_ll = paired_bootstrap(hier_ll_rows - flat_ll_rows, nboot, seed + 1)

    primary_pass = boot_T_ll["p95"] < 0.0
    hda_nonworse = hier_hda["log_loss"] <= flat_hda["log_loss"]
    draw_improved = hier_hda["draw_f1"] > flat_hda["draw_f1"]
    verdict = (
        "PASS_PARITY_T_FIXED500_CLEAN_INCREMENT"
        if primary_pass and hda_nonworse and draw_improved
        else "FAIL_OR_MIXED_PARITY_T_FIXED500_NO_PROMOTION"
    )

    result = {
        "schema_version": exp["schema_version"],
        "status": "COMPLETED_RESEARCH_ONLY",
        "scientific_verdict": verdict,
        "formal_weight": 0,
        "sample": {
            "n": int(len(rows)),
            "selection": exp["sample_selection"],
            "sample_identity_sha256": sample_hash,
            "test_position_zero_based": test_position,
            "fold": f"window_{test_position - 1}_to_{test_position}",
            "test_pool_rows": int(len(test)),
            "labels_used_for_identity_selection": False,
            "overlap_with_PR196_fixed200": overlap_prior200,
            "PR196_fixed200_identity_sha256": prior200_hash,
            "project_never_viewed_claim": False,
            "latest_position4_confirmation_opened": False,
        },
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "feature_count": len(feature_names),
        "architecture": exp["architecture"],
        "flat_direct_total_receipt": flat_receipt,
        "hierarchical_direct_total_receipt": hier_receipt,
        "conditional_gd_receipt": cond_receipt,
        "metrics": {
            "direct_total": {
                "flat": flat_T,
                "hierarchical": hier_T,
                "delta_hier_minus_flat": T_delta,
                "bootstrap_logloss_delta": boot_T_ll,
            },
            "parity_head": parity_metrics,
            "hda": {
                "flat": flat_hda,
                "hierarchical": hier_hda,
                "delta_logloss": float(hier_hda["log_loss"] - flat_hda["log_loss"]),
                "delta_brier": float(hier_hda["brier"] - flat_hda["brier"]),
                "delta_rps": float(hier_hda["rps"] - flat_hda["rps"]),
                "delta_accuracy_pp": float((hier_hda["accuracy"] - flat_hda["accuracy"]) * 100.0),
                "delta_draw_f1_pp": float((hier_hda["draw_f1"] - flat_hda["draw_f1"]) * 100.0),
                "bootstrap_logloss_delta": boot_hda_ll,
            },
            "exact_score": {
                "flat": flat_score,
                "hierarchical": hier_score,
                "delta_top1_pp": float((hier_score["top1_accuracy"] - flat_score["top1_accuracy"]) * 100.0),
                "delta_top3_pp": float((hier_score["top3_accuracy"] - flat_score["top3_accuracy"]) * 100.0),
            },
        },
        "draw_diagnostics": {
            "actual_draws": int(len(draws)),
            "draws_parity_top1_correct": int(np.sum(draws.hier_pred_parity == 0)),
            "draws_hier_total_top1_correct": int(np.sum(draws.hier_pred_total_class == draws.actual_total_class)),
            "draws_flat_total_top1_correct": int(np.sum(draws.flat_pred_total_class == draws.actual_total_class)),
            "draw_score_breakdown": draw_breakdown,
            "even_total_non_draws": int(len(non_draw_even)),
            "even_total_non_draws_hier_draw_calls": int(np.sum(non_draw_even.hier_pred_result == "D")),
            "even_total_non_draws_flat_draw_calls": int(np.sum(non_draw_even.flat_pred_result == "D")),
        },
        "decision_checks": {
            "direct_t_logloss_bootstrap_p95_below_zero": primary_pass,
            "hda_logloss_nonworse": hda_nonworse,
            "natural_draw_f1_improved": draw_improved,
            "post_result_parameter_search": False,
            "forced_draw": False,
            "manual_threshold": False,
        },
        "governance": exp["governance"],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows.to_csv(ROWS_OUT, index=False)
    return result


def main() -> None:
    result = run()
    compact = {
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "sample": result["sample"],
        "direct_total": result["metrics"]["direct_total"],
        "parity_head": result["metrics"]["parity_head"],
        "hda": result["metrics"]["hda"],
        "exact_score": result["metrics"]["exact_score"],
        "draw_diagnostics": result["draw_diagnostics"],
        "decision_checks": result["decision_checks"],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
