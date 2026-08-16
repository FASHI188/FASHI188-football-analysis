#!/usr/bin/env python3
"""Frozen disjoint-200 test of an explicit High-T hurdle for Direct-T.

The candidate is developed only on train/policy rows:
    P(T>=4|X) * P(Tclass|low/high branch,X)
then uses the same existing P(GD|Tclass,X) head as the flat Direct-T baseline.

The 200 evaluation identities are label-blind SHA-256 ranks 801..1000 from the
same rolling test-position-3 pool. Ranks 1..500 and 501..800 remain reserved for
the earlier fixed500 and disjoint300 tests. Latest rolling position 4 labels are
not opened. Research-only, formal_weight=0.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import evaluate_direct_t_parity_gd_fixed500_r1 as base

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_CONFIG = ROOT / "config" / "direct_t_hight_hurdle_disjoint200_r1.json"
OUT = ROOT / "manifests" / "direct_t_hight_hurdle_disjoint200_r1_status.json"
ROWS_OUT = ROOT / "manifests" / "direct_t_hight_hurdle_disjoint200_r1_rows.csv"
TOTAL_CLASSES = list(range(8))


def digest(identities) -> str:
    payload = "\n".join(sorted(str(x) for x in identities)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_experiment() -> dict[str, Any]:
    value = json.loads(EXPERIMENT_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise base.ResearchError("experiment config root must be object")
    return value


def freeze_sample(test: pd.DataFrame, n: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = 800 + n
    if len(test) < required:
        raise base.ResearchError(f"target fold has only {len(test)} rows; need at least {required}")
    ranked = test.copy()
    ranked["match_identity"] = ranked.apply(base.row_identity, axis=1)
    ranked["identity_hash"] = ranked["match_identity"].map(base.identity_hash)
    ranked = ranked.sort_values(["identity_hash", "match_identity"]).reset_index(drop=True)

    parent500 = ranked.iloc[:500].copy()
    prior300 = ranked.iloc[500:800].copy()
    sample = ranked.iloc[800:800 + n].copy()
    if sample.match_identity.nunique() != n:
        raise base.ResearchError("disjoint200 identity uniqueness failure")
    overlap500 = len(set(sample.match_identity) & set(parent500.match_identity))
    overlap300 = len(set(sample.match_identity) & set(prior300.match_identity))
    if overlap500 or overlap300:
        raise base.ResearchError(f"reserved-sample overlap: fixed500={overlap500}, disjoint300={overlap300}")
    receipt = {
        "n": int(n),
        "selection": "sha256_identity_hash_ranks_801_to_1000_after_parent_fixed500_and_disjoint300",
        "sample_identity_sha256": digest(sample.match_identity),
        "parent_fixed500_identity_sha256_reproduced": digest(parent500.match_identity),
        "prior_disjoint300_identity_sha256_reproduced": digest(prior300.match_identity),
        "overlap_with_parent_fixed500": overlap500,
        "overlap_with_prior_disjoint300": overlap300,
        "hash_rank_slice_one_based": [801, 1000],
        "labels_used_for_identity_selection": False,
    }
    return sample, receipt


def fit_high_t_hurdle(
    fold: pd.DataFrame,
    sample: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    train = fold[fold.split == "train"]
    policy = fold[fold.split == "policy"]
    fit = fold[fold.split.isin(["train", "policy"])]

    gate_classes = [0, 1]
    gate_C, gate_grid = base.select_C(train, policy, features, "high_t", gate_classes, config)
    gate_model = base.make_model(gate_C, config)
    gate_model.fit(fit[features], fit.high_t)
    p_gate = base.align_probability(gate_model, sample[features], gate_classes)

    p_total = np.zeros((len(sample), 8), dtype=float)
    branch_receipts: dict[str, Any] = {}
    branch_classes = {0: [0, 1, 2, 3], 1: [4, 5, 6, 7]}
    for high_t in gate_classes:
        classes = branch_classes[high_t]
        train_b = train[train.high_t == high_t]
        policy_b = policy[policy.high_t == high_t]
        fit_b = fit[fit.high_t == high_t]
        if train_b.total_class.nunique() < 2 or policy_b.total_class.nunique() < 2:
            raise base.ResearchError(f"insufficient High-T branch classes for high_t={high_t}")
        selected_C, policy_grid = base.select_C(train_b, policy_b, features, "total_class", classes, config)
        model = base.make_model(selected_C, config)
        model.fit(fit_b[features], fit_b.total_class)
        p_branch = base.align_probability(model, sample[features], classes)
        for j, total_class in enumerate(classes):
            p_total[:, total_class] = p_gate[:, high_t] * p_branch[:, j]
        branch_receipts[str(high_t)] = {
            "support": classes,
            "train_rows": int(len(train_b)),
            "policy_rows": int(len(policy_b)),
            "fit_rows": int(len(fit_b)),
            "selected_C": selected_C,
            "policy_grid": policy_grid,
        }

    residual = float(np.max(np.abs(p_total.sum(axis=1) - 1.0)))
    if residual > 1e-10:
        raise base.ResearchError(f"High-T hurdle probability conservation failure: {residual}")
    return p_total, p_gate, {
        "gate_target": "T>=4",
        "gate_selected_C": gate_C,
        "gate_policy_grid": gate_grid,
        "branch_receipts": branch_receipts,
        "probability_sum_max_residual": residual,
        "forced_routing": False,
        "manual_threshold": False,
        "class_weight_override": False,
    }


def binary_metrics(y: np.ndarray, p_high: np.ndarray) -> dict[str, Any]:
    y = y.astype(int)
    p = np.clip(p_high.astype(float), 1e-15, 1.0 - 1e-15)
    ll_rows = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    pred = p >= 0.5
    truth = y == 1
    tp = int(np.sum(pred & truth))
    fp = int(np.sum(pred & ~truth))
    fn = int(np.sum(~pred & truth))
    tn = int(np.sum(~pred & ~truth))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "log_loss": float(ll_rows.mean()),
        "brier": float(np.mean((p - y) ** 2)),
        "mean_p_high": float(p.mean()),
        "threshold_0_5_predicted_high": int(pred.sum()),
        "accuracy_at_0_5": float(np.mean(pred == truth)),
        "precision_at_0_5": precision,
        "recall_at_0_5": recall,
        "f1_at_0_5": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "_ll_rows": ll_rows,
    }


def total_top1_diagnostics(actual_t: np.ndarray, probabilities: np.ndarray) -> dict[str, Any]:
    actual_class = np.minimum(actual_t.astype(int), 7)
    pred = np.argmax(probabilities, axis=1)
    high = actual_t >= 4
    return {
        "exact_total_class_top1": float(np.mean(pred == actual_class)),
        "within_one_goal_class_top1": float(np.mean(np.abs(pred - actual_class) <= 1)),
        "mean_absolute_total_class_error": float(np.mean(np.abs(pred - actual_class))),
        "T4plus_actual_rows": int(high.sum()),
        "T4plus_predicted_rows": int(np.sum(pred >= 4)),
        "T4plus_top1_recall": float(np.mean(pred[high] >= 4)) if np.any(high) else None,
        "T4plus_top1_precision": (
            float(np.mean(high[pred >= 4])) if np.any(pred >= 4) else 0.0
        ),
    }


def draw_breakdown(rows: pd.DataFrame) -> dict[str, Any]:
    draws = rows[rows.actual_result == "D"].copy()
    out: dict[str, Any] = {}
    for score, part in draws.groupby("actual_score"):
        out[str(score)] = {
            "n": int(len(part)),
            "flat_total_top1_correct": int(np.sum(part.flat_pred_total_class == part.actual_total_class)),
            "hurdle_total_top1_correct": int(np.sum(part.hurdle_pred_total_class == part.actual_total_class)),
            "flat_draw_calls": int(np.sum(part.flat_pred_result == "D")),
            "hurdle_draw_calls": int(np.sum(part.hurdle_pred_result == "D")),
        }
    return out


def run() -> dict[str, Any]:
    exp = load_experiment()
    config = base.load_config()
    ledger = ROOT / str(config["input_ledger"])
    if not ledger.is_file():
        raise base.ResearchError(f"ledger missing: {ledger.relative_to(ROOT)}")

    raw = pd.read_csv(ledger)
    data_identity = base.audit_data_identity(raw, config)
    base_features = base.build_features(raw)
    feature_names = base.select_core_features(base_features)
    seasons, excluded = base.complete_seasons(raw, config)

    test_position = int(exp["test_position_zero_based"])
    latest_position = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if test_position >= latest_position:
        raise base.ResearchError("disjoint200 must not open latest rolling confirmation position")

    base_fold = base_features.copy()
    base_fold["split"] = base.assign_fold(base_fold, seasons, test_position)
    base_fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
    test = base_fold[base_fold.split == "test"].copy()
    sample_base, sample_receipt = freeze_sample(test, int(exp["sample_n"]))

    latest_fold = base_features.copy()
    latest_fold["split"] = base.assign_fold(latest_fold, seasons, latest_position)
    prior200, prior200_hash = base.sample_fixed200(latest_fold[latest_fold.split == "test"].copy())
    overlap_pr196 = len(set(sample_base.match_identity) & set(prior200.match_identity))
    if overlap_pr196 != 0:
        raise base.ResearchError(f"disjoint200 overlaps PR196 fixed200: {overlap_pr196}")

    fold = base.attach_exact_total(base_fold, raw)
    fold["high_t"] = (fold.exact_total >= 4).astype(int)
    sample = fold.merge(
        sample_base[base.KEYS + ["match_identity", "identity_hash"]],
        on=base.KEYS,
        how="inner",
        validate="one_to_one",
    )
    if len(sample) != int(exp["sample_n"]):
        raise base.ResearchError("disjoint200 label attachment changed sample size")

    raw_scores = raw[base.KEYS + ["home_goals_90", "away_goals_90", "total_goals"]].copy()
    raw_scores["season"] = raw_scores["season"].astype(str)
    sample["season"] = sample["season"].astype(str)
    sample = sample.merge(raw_scores, on=base.KEYS, how="left", validate="one_to_one")
    if sample[["home_goals_90", "away_goals_90", "total_goals"]].isna().any().any():
        raise base.ResearchError("final-score attachment failure")

    p_flat, _, flat_receipt = base.direct_total_probabilities(fold, sample, feature_names, config)
    p_hurdle, p_gate, hurdle_receipt = fit_high_t_hurdle(fold, sample, feature_names, config)
    cond_model, _, cond_receipt = base.conditional_probabilities(fold, sample, feature_names, config)
    flat_joint = base.assemble_joint(p_flat, cond_model)
    hurdle_joint = base.assemble_joint(p_hurdle, cond_model)

    rows = sample[base.KEYS + ["match_identity", "identity_hash", "home_goals_90", "away_goals_90", "total_goals", "goal_difference"]].copy()
    rows = rows.rename(columns={"total_goals": "actual_total", "goal_difference": "actual_gd"})
    rows["actual_total_class"] = np.minimum(rows.actual_total.astype(int), 7)
    rows["actual_high_t"] = (rows.actual_total.astype(int) >= 4).astype(int)
    rows["actual_score"] = rows.home_goals_90.astype(int).astype(str) + ":" + rows.away_goals_90.astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd > 0, "H", np.where(rows.actual_gd == 0, "D", "A"))
    rows["flat_pred_total_class"] = np.argmax(p_flat, axis=1)
    rows["hurdle_pred_total_class"] = np.argmax(p_hurdle, axis=1)
    rows["flat_p_high_t"] = p_flat[:, 4:].sum(axis=1)
    rows["hurdle_p_high_t"] = p_gate[:, 1]
    rows["hurdle_gate_pred_high_0_5"] = (p_gate[:, 1] >= 0.5).astype(int)
    for total_class in TOTAL_CLASSES:
        rows[f"flat_p_T{total_class}"] = p_flat[:, total_class]
        rows[f"hurdle_p_T{total_class}"] = p_hurdle[:, total_class]
    for prefix, joint in (("flat", flat_joint), ("hurdle", hurdle_joint)):
        jf = pd.DataFrame(joint)
        for column in jf.columns:
            rows[f"{prefix}_{column}"] = jf[column].to_numpy()
    rows.to_csv(ROWS_OUT, index=False)

    yT = rows.actual_total_class.to_numpy(int)
    flat_T_components = base.metric_components(yT, p_flat, TOTAL_CLASSES)
    hurdle_T_components = base.metric_components(yT, p_hurdle, TOTAL_CLASSES)
    flat_T = base.metric_summary(flat_T_components)
    hurdle_T = base.metric_summary(hurdle_T_components)

    y_high = rows.actual_high_t.to_numpy(int)
    flat_high = binary_metrics(y_high, rows.flat_p_high_t.to_numpy(float))
    hurdle_high = binary_metrics(y_high, rows.hurdle_p_high_t.to_numpy(float))
    flat_high_ll = np.asarray(flat_high.pop("_ll_rows"), dtype=float)
    hurdle_high_ll = np.asarray(hurdle_high.pop("_ll_rows"), dtype=float)

    flat_hda = base.hda_metrics(rows, "flat")
    hurdle_hda = base.hda_metrics(rows, "hurdle")
    flat_hda_ll = np.asarray(flat_hda.pop("_ll_rows"), dtype=float)
    hurdle_hda_ll = np.asarray(hurdle_hda.pop("_ll_rows"), dtype=float)

    flat_score = base.score_metrics(rows, "flat")
    hurdle_score = base.score_metrics(rows, "hurdle")
    flat_top1 = total_top1_diagnostics(rows.actual_total.to_numpy(int), p_flat)
    hurdle_top1 = total_top1_diagnostics(rows.actual_total.to_numpy(int), p_hurdle)

    nboot = int(exp["decision_contract"]["bootstrap_samples"])
    seed = int(exp["decision_contract"]["bootstrap_seed"])
    boot_high_ll = base.paired_bootstrap(hurdle_high_ll - flat_high_ll, nboot, seed)
    boot_T_ll = base.paired_bootstrap(
        hurdle_T_components.logloss.to_numpy(float) - flat_T_components.logloss.to_numpy(float), nboot, seed + 1
    )
    boot_hda_ll = base.paired_bootstrap(hurdle_hda_ll - flat_hda_ll, nboot, seed + 2)

    high_binary_pass = boot_high_ll["p95"] < 0.0
    direct_t_pass = boot_T_ll["p95"] < 0.0
    hda_nonworse = hurdle_hda["log_loss"] <= flat_hda["log_loss"]
    high_top1_improved = hurdle_top1["T4plus_top1_recall"] > flat_top1["T4plus_top1_recall"]
    verdict = (
        "PASS_HIGH_T_HURDLE_DISJOINT200_CLEAN_INCREMENT"
        if high_binary_pass and direct_t_pass and hda_nonworse
        else "FAIL_OR_MIXED_HIGH_T_HURDLE_DISJOINT200_NO_PROMOTION"
    )

    sample_receipt.update({
        "test_position_zero_based": test_position,
        "fold": f"window_{test_position - 1}_to_{test_position}",
        "test_pool_rows": int(len(test)),
        "overlap_with_PR196_fixed200": overlap_pr196,
        "PR196_fixed200_identity_sha256": prior200_hash,
        "latest_position4_confirmation_opened": False,
        "project_never_viewed_claim": False,
    })

    result = {
        "schema_version": exp["schema_version"],
        "status": "COMPLETED_RESEARCH_ONLY",
        "scientific_verdict": verdict,
        "formal_weight": 0,
        "sample": sample_receipt,
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "feature_count": len(feature_names),
        "architecture": exp["architecture"],
        "flat_direct_total_receipt": flat_receipt,
        "high_t_hurdle_receipt": hurdle_receipt,
        "conditional_gd_receipt": cond_receipt,
        "metrics": {
            "high_t_binary": {
                "flat_implied": flat_high,
                "hurdle_gate": hurdle_high,
                "delta_logloss_hurdle_minus_flat": float(hurdle_high["log_loss"] - flat_high["log_loss"]),
                "delta_brier_hurdle_minus_flat": float(hurdle_high["brier"] - flat_high["brier"]),
                "bootstrap_logloss_delta": boot_high_ll,
            },
            "direct_total": {
                "flat": flat_T,
                "hurdle": hurdle_T,
                "delta_hurdle_minus_flat": {k: float(hurdle_T[k] - flat_T[k]) for k in flat_T},
                "bootstrap_logloss_delta": boot_T_ll,
                "flat_top1_diagnostics": flat_top1,
                "hurdle_top1_diagnostics": hurdle_top1,
            },
            "hda": {
                "flat": flat_hda,
                "hurdle": hurdle_hda,
                "delta_logloss": float(hurdle_hda["log_loss"] - flat_hda["log_loss"]),
                "delta_brier": float(hurdle_hda["brier"] - flat_hda["brier"]),
                "delta_rps": float(hurdle_hda["rps"] - flat_hda["rps"]),
                "delta_accuracy_pp": float((hurdle_hda["accuracy"] - flat_hda["accuracy"]) * 100.0),
                "delta_draw_f1_pp": float((hurdle_hda["draw_f1"] - flat_hda["draw_f1"]) * 100.0),
                "bootstrap_logloss_delta": boot_hda_ll,
            },
            "exact_score": {
                "flat": flat_score,
                "hurdle": hurdle_score,
                "delta_top1_pp": float((hurdle_score["top1_accuracy"] - flat_score["top1_accuracy"]) * 100.0),
                "delta_top3_pp": float((hurdle_score["top3_accuracy"] - flat_score["top3_accuracy"]) * 100.0),
            },
        },
        "draw_diagnostics": {
            "actual_draws": int(np.sum(rows.actual_result == "D")),
            "score_breakdown": draw_breakdown(rows),
        },
        "decision_checks": {
            "high_t_binary_logloss_bootstrap_p95_below_zero": high_binary_pass,
            "direct_t_logloss_bootstrap_p95_below_zero": direct_t_pass,
            "hda_logloss_nonworse": hda_nonworse,
            "high_t_top1_recall_improved_diagnostic": high_top1_improved,
            "post_result_parameter_search": False,
            "forced_draw": False,
            "manual_threshold_for_model_probabilities": False,
        },
        "governance": exp["governance"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    result = run()
    compact = {
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "sample": result["sample"],
        "high_t_binary": result["metrics"]["high_t_binary"],
        "direct_total": result["metrics"]["direct_total"],
        "hda": result["metrics"]["hda"],
        "exact_score": result["metrics"]["exact_score"],
        "draw_diagnostics": result["draw_diagnostics"],
        "decision_checks": result["decision_checks"],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
