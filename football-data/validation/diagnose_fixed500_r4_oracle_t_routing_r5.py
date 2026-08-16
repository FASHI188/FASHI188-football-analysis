#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from diagnose_fixed500_existing_market_pack_t_r1 import MARKET_FULL, MARKET_OU, add_identity_key, fit_total, materialize_market
from diagnose_fixed500_two_stage_ou_side_reweight_r4 import SIDE_AH, fit_near_balance_draw_models, reweight_near_balance_mass
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, assemble_joint, conditional_probabilities, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import attach_exact_total, load_experiment, sample_fixed_n
from v510_historical_structure_features_r1 import ResearchError, assign_fold, audit_data_identity, build_features, complete_seasons, select_core_features

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fixed500_r4_oracle_t_routing_r5.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_r4_oracle_t_routing_r5_rows.csv"


def oracle_parity_total(p_total: np.ndarray, actual_total: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Parity diagnostic only for realized exact totals <=6.

    Tail class 7+ merges odd/even totals, so parity-only routing is not identifiable there.
    We therefore report parity routing only on the T<=6 diagnostic cohort and set those
    rows' tail mass to zero before renormalizing over exact classes of the realized parity.
    """
    p = np.asarray(p_total, float).copy()
    actual = np.asarray(actual_total, int)
    eligible = actual <= 6
    out = np.full_like(p, np.nan)
    for i in np.where(eligible)[0]:
        parity = int(actual[i] % 2)
        keep = np.asarray([(t <= 6 and t % 2 == parity) for t in range(8)], dtype=bool)
        row = np.where(keep, p[i], 0.0)
        s = float(row.sum())
        if s <= 1e-15:
            raise ResearchError("R5 oracle parity renormalization has zero mass")
        out[i] = row / s
    return out, eligible


def oracle_exact_total(actual_total_class: np.ndarray) -> np.ndarray:
    y = np.asarray(actual_total_class, int)
    out = np.zeros((len(y), 8), dtype=float)
    out[np.arange(len(y)), y] = 1.0
    return out


def attach_joint(rows: pd.DataFrame, prefix: str, p_total: np.ndarray, cond: dict[int, tuple[list[int], np.ndarray]], mask: np.ndarray | None = None) -> None:
    if mask is None:
        joint = pd.DataFrame(assemble_joint(p_total, cond), index=rows.index)
        for col in joint.columns:
            rows[f"{prefix}_{col}"] = joint[col].to_numpy()
        return
    idx = np.where(mask)[0]
    sub_total = p_total[idx]
    sub_cond = {t: (classes, probs[idx]) for t, (classes, probs) in cond.items()}
    joint = pd.DataFrame(assemble_joint(sub_total, sub_cond), index=rows.index[idx])
    for col in joint.columns:
        rows[f"{prefix}_{col}"] = np.nan
        rows.loc[joint.index, f"{prefix}_{col}"] = joint[col]


def draw_ranking_summary(rows: pd.DataFrame, prefix: str, mask: np.ndarray | None = None) -> dict[str, Any]:
    part = rows if mask is None else rows.loc[mask]
    p_draw = part[f"{prefix}_p_draw"].to_numpy(float)
    p_side = np.maximum(part[f"{prefix}_p_home"].to_numpy(float), part[f"{prefix}_p_away"].to_numpy(float))
    margin = p_draw - p_side
    actual_draw = part.actual_result.eq("D").to_numpy()
    pred_draw = part[f"{prefix}_pred_result"].eq("D").to_numpy()
    draw_margin = margin[actual_draw]
    return {
        "n": int(len(part)),
        "actual_draws": int(actual_draw.sum()),
        "top1_draw_calls": int(pred_draw.sum()),
        "top1_draw_hits": int(np.sum(pred_draw & actual_draw)),
        "top1_draw_false_positives": int(np.sum(pred_draw & ~actual_draw)),
        "actual_draw_margin_mean": float(draw_margin.mean()) if len(draw_margin) else None,
        "actual_draw_margin_median": float(np.median(draw_margin)) if len(draw_margin) else None,
        "actual_draw_margin_p10": float(np.quantile(draw_margin, 0.10)) if len(draw_margin) else None,
        "actual_draw_margin_p90": float(np.quantile(draw_margin, 0.90)) if len(draw_margin) else None,
        "actual_draws_within_1pp_of_top1": int(np.sum(draw_margin >= -0.01)),
        "actual_draws_within_3pp_of_top1": int(np.sum(draw_margin >= -0.03)),
        "actual_draws_within_5pp_of_top1": int(np.sum(draw_margin >= -0.05)),
        "actual_draws_within_10pp_of_top1": int(np.sum(draw_margin >= -0.10)),
        "actual_draws_top1": int(np.sum(draw_margin >= 0.0)),
    }


def run() -> dict[str, Any]:
    exp = load_experiment(); config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = add_identity_key(build_features(raw)); core = select_core_features(base)
    seasons, excluded = complete_seasons(raw, config)
    pos = int(exp["test_position_zero_based"]); latest = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if pos >= latest:
        raise ResearchError("R5 must reuse PR197 non-latest fixed500")
    base["split"] = assign_fold(base, seasons, pos)
    sample_base, sample_hash = sample_fixed_n(base[base.split == "test"].copy(), int(exp["sample_n"]))
    fold = attach_exact_total(base, raw).merge(materialize_market(raw), on="identity_key", how="left", validate="one_to_one")
    sample = fold.merge(sample_base[KEYS + ["match_identity", "identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    raw_scores = raw[KEYS + ["home_goals_90", "away_goals_90", "total_goals"]].copy(); raw_scores["season"] = raw_scores["season"].astype(str); sample["season"] = sample["season"].astype(str)
    sample = sample.merge(raw_scores, on=KEYS, how="left", validate="one_to_one")
    if len(sample) != 500:
        raise ResearchError("R5 fixed500 mismatch")

    sync = fold[MARKET_FULL].notna().all(axis=1)
    sample_sync = sample[sample[MARKET_FULL].notna().all(axis=1)].copy()
    fit_sync = fold[fold.split.isin(["train", "policy"]) & sync].copy(); train_sync = fit_sync[fit_sync.split == "train"].copy(); policy_sync = fit_sync[fit_sync.split == "policy"].copy()
    if len(sample_sync) != 220:
        raise ResearchError(f"R5 expected 220 synchronized rows, got {len(sample_sync)}")

    p_pred, total_receipt = fit_total(fit_sync, train_sync, policy_sync, sample_sync, core + MARKET_OU, config)
    cond_base, _, cond_receipt = conditional_probabilities(fold, sample_sync, core, config)
    q_by_total, q_receipt = fit_near_balance_draw_models(fit_sync, sample_sync, core + SIDE_AH, config)
    cond_r4, reweight_receipt = reweight_near_balance_mass(cond_base, q_by_total)

    rows = sample_sync[KEYS + ["match_identity", "identity_hash", "home_goals_90", "away_goals_90", "total_goals", "goal_difference", "exact_total", "total_class"]].copy()
    rows = rows.rename(columns={"total_goals":"actual_total","goal_difference":"actual_gd","total_class":"actual_total_class"})
    rows["actual_score"] = rows.home_goals_90.astype(int).astype(str) + ":" + rows.away_goals_90.astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd > 0, "H", np.where(rows.actual_gd == 0, "D", "A"))

    attach_joint(rows, "predicted_T", p_pred, cond_r4)
    p_parity, parity_mask = oracle_parity_total(p_pred, rows.actual_total.to_numpy(int))
    attach_joint(rows, "oracle_parity", p_parity, cond_r4, parity_mask)
    p_exact = oracle_exact_total(rows.actual_total_class.to_numpy(int))
    attach_joint(rows, "oracle_exact_T", p_exact, cond_r4)

    pred_summary = draw_ranking_summary(rows, "predicted_T")
    parity_summary = draw_ranking_summary(rows, "oracle_parity", parity_mask)
    exact_summary = draw_ranking_summary(rows, "oracle_exact_T")

    # Draws are all even exact totals <=6, so all 64 draw rows are eligible for the parity diagnostic.
    draw_rows = rows.actual_result.eq("D")
    if not bool(np.all(parity_mask[draw_rows.to_numpy()])):
        raise ResearchError("R5 unexpected draw outside exact T<=6 parity cohort")
    transition = {
        "actual_draws": int(draw_rows.sum()),
        "predicted_T_draw_hits": pred_summary["top1_draw_hits"],
        "oracle_parity_draw_hits": parity_summary["top1_draw_hits"],
        "oracle_exact_T_draw_hits": exact_summary["top1_draw_hits"],
        "gain_from_parity_routing": int(parity_summary["top1_draw_hits"] - pred_summary["top1_draw_hits"]),
        "additional_gain_from_exact_T_within_parity": int(exact_summary["top1_draw_hits"] - parity_summary["top1_draw_hits"]),
    }
    if parity_summary["top1_draw_hits"] > pred_summary["top1_draw_hits"]:
        if exact_summary["top1_draw_hits"] > parity_summary["top1_draw_hits"]:
            bottleneck = "BOTH_PARITY_AND_WITHIN_PARITY_T_RESOLUTION_MATTER"
        else:
            bottleneck = "PARITY_ROUTING_DOMINANT"
    elif exact_summary["top1_draw_hits"] > pred_summary["top1_draw_hits"]:
        bottleneck = "WITHIN_PARITY_EXACT_T_RESOLUTION_DOMINANT"
    else:
        bottleneck = "CONDITIONAL_GD_STILL_DOMINANT"

    result = {
        "schema_version":"FIXED500_R4_ORACLE_T_ROUTING_R5",
        "status":"COMPLETED_DIAGNOSTIC_ONLY",
        "scientific_verdict":bottleneck,
        "sample":{"parent_fixed500_n":500,"parent_fixed500_identity_sha256":sample_hash,"synchronized_cohort_n":220,"actual_draws":int(draw_rows.sum()),"parity_diagnostic_T0_6_n":int(parity_mask.sum()),"new_sample_consumed":False,"latest_position4_confirmation_opened":False},
        "architecture":{"conditional_layer":"exact same R4 reweighted conditional GD","predicted_T_layer":"core+single OU","oracle_parity":"diagnostic only; on actual T<=6 rows renormalize predicted T probabilities over exact classes of realized parity","oracle_exact_T":"diagnostic one-hot realized Tclass","manual_threshold":False,"forced_draw":False},
        "draw_ranking":{"predicted_T":pred_summary,"oracle_parity_T0_6":parity_summary,"oracle_exact_T":exact_summary,"transition":transition},
        "bottleneck_interpretation":{"classification":bottleneck,"rule":"compare natural Top1 draw hits as progressively more T information is revealed; no threshold tuning"},
        "receipts":{"total":total_receipt,"conditional_gd":cond_receipt,"near_balance_draw_models":q_receipt,"reweight":reweight_receipt},
        "data_identity":data_identity,"excluded_incomplete_latest_seasons":excluded,
        "interpretation_guard":{"oracle_information_used":True,"formal_performance_claim":False,"retrospective_information_ceiling_only":True,"formal_PIT_claim":False,"can_authorize_promotion":False,"same_fixed500_already_viewed":True},
        "governance":{"formal_weight":0,"provider_requests":0,"new_data_collection":False,"new_sample_consumed":False,"latest_position4_confirmation_opened":False,"post_result_threshold_search":False,"formal_model_mutation":False,"formal_data_mutation":False,"formal_config_mutation":False,"current_mutation":False,"main_mutation":False},
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); rows.to_csv(ROWS_OUT,index=False)
    return result


def main() -> None:
    x=run(); print(json.dumps({"verdict":x["scientific_verdict"],"sample":x["sample"],"draw_ranking":x["draw_ranking"],"guard":x["interpretation_guard"]},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
