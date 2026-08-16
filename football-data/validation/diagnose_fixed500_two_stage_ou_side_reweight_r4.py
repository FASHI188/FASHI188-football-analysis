#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from diagnose_fixed500_existing_market_pack_t_r1 import (
    MARKET_FULL,
    MARKET_OU,
    add_identity_key,
    clean_hda,
    fit_total,
    materialize_market,
)
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, assemble_joint, conditional_probabilities, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import (
    attach_exact_total,
    hda_metrics,
    load_experiment,
    paired_bootstrap,
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
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fixed500_two_stage_ou_side_reweight_r4.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_two_stage_ou_side_reweight_r4_rows.csv"
TOTAL_CLASSES = list(range(8))
EVEN_TOTALS = (2, 4, 6)
SIDE_AH = ["mkt_draw_logit", "mkt_home_minus_away", "mkt_ah_line", "mkt_ah_home_logit"]
LABELS = ("H", "D", "A")


def fit_near_balance_draw_models(
    fold_sync: pd.DataFrame,
    sample_sync: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    """Fit T-specific P(GD=0 | T, GD in {-2,0,2}, X), then score every sample row for every T.

    Crucially, inference does not use the row's realized T. Each T-specific model scores all rows;
    the resulting q_T is only used inside that T bucket before the predicted T marginal is applied.
    """
    q_by_total: dict[int, np.ndarray] = {}
    receipt: dict[str, Any] = {}
    for total in EVEN_TOTALS:
        support = fold_sync.goal_difference.isin([-2, 0, 2]) & (fold_sync.total_class == total)
        train = fold_sync[(fold_sync.split == "train") & support].copy()
        policy = fold_sync[(fold_sync.split == "policy") & support].copy()
        fit = fold_sync[fold_sync.split.isin(["train", "policy"]) & support].copy()
        for frame in (train, policy, fit):
            frame["draw_binary"] = (frame.goal_difference == 0).astype(int)
        if train.draw_binary.nunique() < 2 or policy.draw_binary.nunique() < 2:
            raise ResearchError(f"R4 missing binary classes for T={total}")
        selected_C, policy_grid = select_C(train, policy, features, "draw_binary", [0, 1], config)
        model = make_model(selected_C, config)
        model.fit(fit[features], fit.draw_binary)
        q = align_probability(model, sample_sync[features], [0, 1])[:, 1]
        if not np.isfinite(q).all():
            raise ResearchError(f"R4 non-finite q for T={total}")
        q_by_total[total] = q
        receipt[str(total)] = {
            "support": [-2, 0, 2],
            "train_rows": int(len(train)),
            "policy_rows": int(len(policy)),
            "fit_rows": int(len(fit)),
            "fit_draw_rate": float(fit.draw_binary.mean()),
            "selected_C": selected_C,
            "policy_grid": policy_grid,
            "inference_rows_scored": int(len(sample_sync)),
            "inference_uses_realized_T": False,
            "q_mean": float(np.mean(q)),
            "q_min": float(np.min(q)),
            "q_max": float(np.max(q)),
        }
    return q_by_total, receipt


def reweight_near_balance_mass(
    baseline: dict[int, tuple[list[int], np.ndarray]],
    q_by_total: dict[int, np.ndarray],
) -> tuple[dict[int, tuple[list[int], np.ndarray]], dict[str, Any]]:
    out: dict[int, tuple[list[int], np.ndarray]] = {}
    receipt: dict[str, Any] = {}
    for total, (classes, p0) in baseline.items():
        p = np.asarray(p0, dtype=float).copy()
        if total not in EVEN_TOTALS:
            out[total] = (list(classes), p)
            continue
        class_to_idx = {int(gd): i for i, gd in enumerate(classes)}
        if not all(gd in class_to_idx for gd in (-2, 0, 2)):
            raise ResearchError(f"R4 expected -2/0/2 support for T={total}: {classes}")
        im, iz, ip = class_to_idx[-2], class_to_idx[0], class_to_idx[2]
        before_sum = p.sum(axis=1).copy()
        near_mass = p[:, im] + p[:, iz] + p[:, ip]
        side_mass = p[:, im] + p[:, ip]
        plus_share = np.divide(p[:, ip], side_mass, out=np.full(len(p), 0.5), where=side_mass > 1e-15)
        q = np.clip(np.asarray(q_by_total[total], dtype=float), 1e-9, 1 - 1e-9)
        p[:, iz] = near_mass * q
        remaining = near_mass * (1.0 - q)
        p[:, ip] = remaining * plus_share
        p[:, im] = remaining * (1.0 - plus_share)
        residual = float(np.max(np.abs(p.sum(axis=1) - before_sum)))
        if residual > 1e-12 or np.any(p < -1e-14):
            raise ResearchError(f"R4 conditional mass conservation failure T={total}: {residual}")
        out[total] = (list(classes), p)
        receipt[str(total)] = {
            "near_balance_mass_mean": float(np.mean(near_mass)),
            "baseline_draw_share_within_near_mean": float(np.mean(np.divide(p0[:, iz], near_mass, out=np.zeros(len(p)), where=near_mass > 1e-15))),
            "reweighted_draw_share_within_near_mean": float(np.mean(q)),
            "conditional_probability_sum_max_residual": residual,
            "outer_GD_probabilities_unchanged": True,
            "pm2_direction_preserved_from_baseline": True,
        }
    return out, receipt


def hda_components(frame: pd.DataFrame, prefix: str) -> dict[str, np.ndarray]:
    actual = frame["actual_result"].astype(str).to_numpy()
    p = frame[[f"{prefix}_p_home", f"{prefix}_p_draw", f"{prefix}_p_away"]].to_numpy(float)
    idx = np.asarray([LABELS.index(x) for x in actual], dtype=int)
    onehot = np.zeros_like(p)
    onehot[np.arange(len(p)), idx] = 1.0
    return {
        "logloss": -np.log(np.clip(p[np.arange(len(p)), idx], 1e-15, 1.0)),
        "brier": ((p - onehot) ** 2).sum(axis=1),
        "rps": ((np.cumsum(p, axis=1)[:, :-1] - np.cumsum(onehot, axis=1)[:, :-1]) ** 2).sum(axis=1) / 2.0,
    }


def draw_components(frame: pd.DataFrame, prefix: str) -> dict[str, np.ndarray]:
    y = frame.actual_result.eq("D").astype(int).to_numpy()
    p = np.clip(frame[f"{prefix}_p_draw"].to_numpy(float), 1e-15, 1 - 1e-15)
    return {
        "logloss": -(y * np.log(p) + (1-y) * np.log(1-p)),
        "brier": (p-y) ** 2,
    }


def run() -> dict[str, Any]:
    exp = load_experiment()
    config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = add_identity_key(build_features(raw))
    core = select_core_features(base)
    seasons, excluded = complete_seasons(raw, config)
    test_position = int(exp["test_position_zero_based"])
    latest_position = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if test_position >= latest_position:
        raise ResearchError("R4 must reuse PR197 non-latest fixed500")

    base["split"] = assign_fold(base, seasons, test_position)
    sample_base, sample_hash = sample_fixed_n(base[base.split == "test"].copy(), int(exp["sample_n"]))
    fold = attach_exact_total(base, raw)
    fold = fold.merge(materialize_market(raw), on="identity_key", how="left", validate="one_to_one")
    sample = fold.merge(sample_base[KEYS + ["match_identity", "identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    raw_scores = raw[KEYS + ["home_goals_90", "away_goals_90", "total_goals"]].copy()
    raw_scores["season"] = raw_scores["season"].astype(str)
    sample["season"] = sample["season"].astype(str)
    sample = sample.merge(raw_scores, on=KEYS, how="left", validate="one_to_one")
    if len(sample) != 500:
        raise ResearchError("R4 fixed500 reconstruction mismatch")

    sync = fold[MARKET_FULL].notna().all(axis=1)
    sample_sync = sample[sample[MARKET_FULL].notna().all(axis=1)].copy()
    fit_sync = fold[fold.split.isin(["train", "policy"]) & sync].copy()
    train_sync = fit_sync[fit_sync.split == "train"].copy()
    policy_sync = fit_sync[fit_sync.split == "policy"].copy()
    if len(sample_sync) != 220:
        raise ResearchError(f"R4 expected frozen synchronized cohort 220, got {len(sample_sync)}")

    # T layer is deliberately frozen to the robust R1 compact OU pack.
    p_total, total_receipt = fit_total(fit_sync, train_sync, policy_sync, sample_sync, core + MARKET_OU, config)
    y_total = sample_sync.total_class.to_numpy(int)
    total_components = metric_components(y_total, p_total, TOTAL_CLASSES)
    total_metrics = metric_summary(total_components)

    # Baseline conditional GD remains the existing core-only model.
    cond_baseline, _, cond_receipt = conditional_probabilities(fold, sample_sync, core, config)

    # Challenger learns only a targeted near-balance draw share, one model per even T.
    q_by_total, q_receipt = fit_near_balance_draw_models(fit_sync, sample_sync, core + SIDE_AH, config)
    cond_reweighted, reweight_receipt = reweight_near_balance_mass(cond_baseline, q_by_total)

    baseline_joint = pd.DataFrame(assemble_joint(p_total, cond_baseline))
    challenger_joint = pd.DataFrame(assemble_joint(p_total, cond_reweighted))

    rows = sample_sync[KEYS + ["match_identity", "identity_hash", "home_goals_90", "away_goals_90", "total_goals", "goal_difference"] + MARKET_FULL].copy()
    rows = rows.rename(columns={"total_goals": "actual_total", "goal_difference": "actual_gd"})
    rows["actual_total_class"] = np.minimum(rows.actual_total.astype(int), 7)
    rows["actual_score"] = rows.home_goals_90.astype(int).astype(str) + ":" + rows.away_goals_90.astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd > 0, "H", np.where(rows.actual_gd == 0, "D", "A"))
    rows["pred_total_class"] = np.argmax(p_total, axis=1)
    for total in EVEN_TOTALS:
        rows[f"q_draw_given_near_T{total}"] = q_by_total[total]
    for prefix, joint in (("baseline", baseline_joint), ("challenger", challenger_joint)):
        for col in joint.columns:
            rows[f"{prefix}_{col}"] = joint[col].to_numpy()

    baseline_hda_raw = hda_metrics(rows, "baseline")
    challenger_hda_raw = hda_metrics(rows, "challenger")
    baseline_hda = clean_hda(baseline_hda_raw)
    challenger_hda = clean_hda(challenger_hda_raw)
    baseline_score = score_metrics(rows, "baseline")
    challenger_score = score_metrics(rows, "challenger")

    hcomp_b = hda_components(rows, "baseline")
    hcomp_c = hda_components(rows, "challenger")
    dcomp_b = draw_components(rows, "baseline")
    dcomp_c = draw_components(rows, "challenger")
    bootstrap_hda = {
        m: paired_bootstrap(hcomp_c[m] - hcomp_b[m], 5000, 890100 + i)
        for i, m in enumerate(("logloss", "brier", "rps"))
    }
    bootstrap_draw = {
        m: paired_bootstrap(dcomp_c[m] - dcomp_b[m], 5000, 890110 + i)
        for i, m in enumerate(("logloss", "brier"))
    }

    # The same p_total object is used for both joints. Conditional rows must remain normalized.
    conditional_residual = 0.0
    for total in range(8):
        conditional_residual = max(
            conditional_residual,
            float(np.max(np.abs(cond_reweighted[total][1].sum(axis=1) - 1.0))),
        )
    total_marginal_identity_residual = 0.0

    baseline_draw_calls = int(rows.baseline_pred_result.eq("D").sum())
    challenger_draw_calls = int(rows.challenger_pred_result.eq("D").sum())
    actual_draws = int(rows.actual_result.eq("D").sum())
    hda_nonworse = challenger_hda["log_loss"] <= baseline_hda["log_loss"]
    draw_f1_improved = challenger_hda["draw_f1"] > baseline_hda["draw_f1"]
    natural_draws_created = challenger_draw_calls > baseline_draw_calls and challenger_draw_calls > 0
    proper_score_support = bootstrap_hda["logloss"]["p95"] <= 0.0
    if natural_draws_created and hda_nonworse and draw_f1_improved:
        verdict = "PROMISING_TWO_STAGE_END_TO_END_DRAW_SIGNAL_RESEARCH_ONLY"
    elif draw_f1_improved and not hda_nonworse:
        verdict = "REJECT_DRAW_CALLS_BOUGHT_WITH_HDA_DEGRADATION"
    else:
        verdict = "NO_END_TO_END_DRAW_BREAKTHROUGH"

    result = {
        "schema_version": "FIXED500_TWO_STAGE_OU_SIDE_REWEIGHT_R4",
        "status": "COMPLETED_RESEARCH_ONLY",
        "scientific_verdict": verdict,
        "classification": "POST_RESULT_SAME_FIXED500_RETROSPECTIVE_END_TO_END_TWO_STAGE_CHALLENGER",
        "sample": {
            "parent_fixed500_n": 500,
            "parent_fixed500_identity_sha256": sample_hash,
            "synchronized_cohort_n": int(len(rows)),
            "actual_draws": actual_draws,
            "new_sample_consumed": False,
            "latest_position4_confirmation_opened": False,
        },
        "architecture": {
            "T_layer": "core + compact single OU",
            "GD_baseline": "existing core P(GD|T,X)",
            "targeted_reweight": "for T=2/4/6 only, preserve total near-balance mass {-2,0,+2}; replace GD=0 share with compact 1X2+AH q_T; preserve baseline +/-2 direction; outer GD unchanged",
            "inference_uses_realized_T": False,
            "manual_threshold": False,
            "forced_draw": False,
            "T_probability_object_shared_exactly_between_baseline_and_challenger": True,
        },
        "total_layer": {
            "metrics": total_metrics,
            "receipt": total_receipt,
            "baseline_challenger_T_marginal_max_abs_difference": total_marginal_identity_residual,
        },
        "conditional_gd_baseline_receipt": cond_receipt,
        "near_balance_draw_model_receipt": q_receipt,
        "reweight_receipt": reweight_receipt,
        "metrics": {
            "hda": {
                "baseline": baseline_hda,
                "challenger": challenger_hda,
                "delta_challenger_minus_baseline": {
                    "accuracy_pp": float((challenger_hda["accuracy"] - baseline_hda["accuracy"]) * 100),
                    "macro_f1_pp": float((challenger_hda["macro_f1"] - baseline_hda["macro_f1"]) * 100),
                    "log_loss": float(challenger_hda["log_loss"] - baseline_hda["log_loss"]),
                    "brier": float(challenger_hda["brier"] - baseline_hda["brier"]),
                    "rps": float(challenger_hda["rps"] - baseline_hda["rps"]),
                    "draw_f1_pp": float((challenger_hda["draw_f1"] - baseline_hda["draw_f1"]) * 100),
                },
                "bootstrap_delta": bootstrap_hda,
            },
            "draw_probability": {
                "baseline_logloss": float(np.mean(dcomp_b["logloss"])),
                "challenger_logloss": float(np.mean(dcomp_c["logloss"])),
                "baseline_brier": float(np.mean(dcomp_b["brier"])),
                "challenger_brier": float(np.mean(dcomp_c["brier"])),
                "bootstrap_delta": bootstrap_draw,
            },
            "exact_score": {
                "baseline": baseline_score,
                "challenger": challenger_score,
                "delta_top1_pp": float((challenger_score["top1_accuracy"] - baseline_score["top1_accuracy"]) * 100),
                "delta_top3_pp": float((challenger_score["top3_accuracy"] - baseline_score["top3_accuracy"]) * 100),
            },
        },
        "draw_diagnostics": {
            "actual_draws": actual_draws,
            "baseline_draw_calls": baseline_draw_calls,
            "baseline_draw_hits": int(np.sum(rows.baseline_pred_result.eq("D") & rows.actual_result.eq("D"))),
            "challenger_draw_calls": challenger_draw_calls,
            "challenger_draw_hits": int(np.sum(rows.challenger_pred_result.eq("D") & rows.actual_result.eq("D"))),
            "new_draw_calls": int(np.sum(rows.challenger_pred_result.eq("D") & ~rows.baseline_pred_result.eq("D"))),
            "new_draw_hits": int(np.sum(rows.challenger_pred_result.eq("D") & ~rows.baseline_pred_result.eq("D") & rows.actual_result.eq("D"))),
        },
        "decision_checks": {
            "conditional_probability_conservation_max_residual": conditional_residual,
            "T_marginal_identical_by_construction": total_marginal_identity_residual == 0.0,
            "natural_draw_calls_created": natural_draws_created,
            "draw_f1_improved": draw_f1_improved,
            "hda_logloss_nonworse": hda_nonworse,
            "hda_logloss_bootstrap_p95_nonpositive": proper_score_support,
            "post_result_parameter_search": False,
            "manual_threshold": False,
            "forced_draw": False,
        },
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "interpretation_guard": {
            "retrospective_information_ceiling_only": True,
            "formal_PIT_claim": False,
            "end_to_end_inference_uses_true_result_or_true_T": False,
            "can_authorize_promotion": False,
            "same_fixed500_already_viewed": True,
        },
        "governance": {
            "formal_weight": 0,
            "provider_requests": 0,
            "new_data_collection": False,
            "new_sample_consumed": False,
            "latest_position4_confirmation_opened": False,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows.to_csv(ROWS_OUT, index=False)
    return result


def main() -> None:
    x = run()
    print(json.dumps({
        "verdict": x["scientific_verdict"],
        "sample": x["sample"],
        "total": x["total_layer"],
        "hda": x["metrics"]["hda"],
        "draw_probability": x["metrics"]["draw_probability"],
        "exact_score": x["metrics"]["exact_score"],
        "draw_diagnostics": x["draw_diagnostics"],
        "decision_checks": x["decision_checks"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
