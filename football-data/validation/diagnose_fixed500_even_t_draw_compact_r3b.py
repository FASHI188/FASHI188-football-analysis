#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from diagnose_fixed500_existing_market_pack_t_r1 import add_identity_key, materialize_market
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import attach_exact_total, load_experiment, paired_bootstrap, sample_fixed_n
from v510_historical_structure_features_r1 import (
    ResearchError, assign_fold, audit_data_identity, build_features, complete_seasons, select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model, select_C

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fixed500_even_t_draw_compact_r3b.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_even_t_draw_compact_r3b_rows.csv"
TARGET_TOTALS = (2, 4, 6)
SIDE = ["mkt_draw_logit", "mkt_home_minus_away"]
AH = ["mkt_ah_line", "mkt_ah_home_logit"]


def binary_components(y: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    y = np.asarray(y, int)
    p = np.clip(np.asarray(p, float), 1e-15, 1 - 1e-15)
    return {
        "logloss": -(y * np.log(p) + (1-y) * np.log(1-p)),
        "brier": (p-y) ** 2,
    }


def binary_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    y = np.asarray(y, int); p = np.asarray(p, float); pred = (p >= 0.5).astype(int)
    c = binary_components(y, p)
    tp = int(np.sum((pred == 1) & (y == 1))); fp = int(np.sum((pred == 1) & (y == 0))); fn = int(np.sum((pred == 0) & (y == 1)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "accuracy": float(np.mean(pred == y)),
        "logloss": float(c["logloss"].mean()),
        "brier": float(c["brier"].mean()),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        "draw_calls": int(pred.sum()), "draw_hits": tp,
        "draw_precision": precision, "draw_recall": recall, "draw_f1": f1,
    }


def support(frame: pd.DataFrame) -> pd.Series:
    return frame.total_class.isin(TARGET_TOTALS) & frame.goal_difference.isin([-2, 0, 2])


def fit_bucketed(fold: pd.DataFrame, target: pd.DataFrame, features: list[str], config: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    out = pd.Series(index=target.index, dtype=float); receipts: dict[str, Any] = {}
    for total in TARGET_TOTALS:
        train = fold[(fold.split == "train") & (fold.total_class == total) & fold.goal_difference.isin([-2,0,2])].copy()
        policy = fold[(fold.split == "policy") & (fold.total_class == total) & fold.goal_difference.isin([-2,0,2])].copy()
        fit = fold[(fold.split.isin(["train","policy"])) & (fold.total_class == total) & fold.goal_difference.isin([-2,0,2])].copy()
        test = target[target.total_class == total].copy()
        if test.empty: continue
        for frame in (train, policy, fit, test): frame["draw_binary"] = (frame.goal_difference == 0).astype(int)
        if train.draw_binary.nunique() < 2 or policy.draw_binary.nunique() < 2:
            raise ResearchError(f"binary classes missing for T={total}")
        C, grid = select_C(train, policy, features, "draw_binary", [0,1], config)
        model = make_model(C, config); model.fit(fit[features], fit.draw_binary)
        p = align_probability(model, test[features], [0,1])[:,1]
        out.loc[test.index] = p
        receipts[str(total)] = {
            "train_rows": int(len(train)), "policy_rows": int(len(policy)), "fit_rows": int(len(fit)), "test_rows": int(len(test)),
            "fit_draw_rate": float(fit.draw_binary.mean()), "selected_C": C, "policy_grid": grid,
        }
    if out.isna().any(): raise ResearchError("missing compact conditional probabilities")
    return out.to_numpy(float), receipts


def compare(fold: pd.DataFrame, target: pd.DataFrame, packs: dict[str,list[str]], config: dict[str,Any]) -> dict[str,Any]:
    y = (target.goal_difference == 0).astype(int).to_numpy()
    probs = {}; metrics = {}; receipts = {}
    for name, feats in packs.items():
        p, r = fit_bucketed(fold, target, feats, config); probs[name] = p; receipts[name] = r; metrics[name] = binary_metrics(y, p)
    boot = {}
    names = list(packs)
    baseline = names[0]
    for challenger in names[1:]:
        cb = binary_components(y, probs[challenger]); bb = binary_components(y, probs[baseline])
        boot[challenger + "_minus_" + baseline] = {
            metric: paired_bootstrap(cb[metric] - bb[metric], 5000, 880100 + i + 10 * names.index(challenger))
            for i, metric in enumerate(("logloss", "brier"))
        }
    return {"n": int(len(target)), "draws": int(y.sum()), "metrics": metrics, "bootstrap": boot, "receipts": receipts, "probabilities": probs}


def run() -> dict[str,Any]:
    exp = load_experiment(); config = load_config(); raw = pd.read_csv(ROOT / str(config["input_ledger"])); data_identity = audit_data_identity(raw, config)
    base = add_identity_key(build_features(raw)); core = select_core_features(base); seasons, excluded = complete_seasons(raw, config)
    pos = int(exp["test_position_zero_based"]); latest = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if pos >= latest: raise ResearchError("must reuse non-latest PR197 fixed500")
    base["split"] = assign_fold(base, seasons, pos); sample_base, sample_hash = sample_fixed_n(base[base.split == "test"].copy(), int(exp["sample_n"]))
    fold = attach_exact_total(base, raw).merge(materialize_market(raw), on="identity_key", how="left", validate="one_to_one")
    sample = fold.merge(sample_base[KEYS + ["match_identity", "identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    if len(sample) != 500: raise ResearchError("fixed500 mismatch")

    side_ok = fold[SIDE].notna().all(axis=1); sync_ok = fold[SIDE + AH].notna().all(axis=1)
    side_target = sample[support(sample) & sample[SIDE].notna().all(axis=1)].copy()
    sync_target = sample[support(sample) & sample[SIDE + AH].notna().all(axis=1)].copy()
    if min(len(side_target), len(sync_target)) < 40: raise ResearchError(f"compact target too small side={len(side_target)} sync={len(sync_target)}")

    side_fold = fold[side_ok].copy(); sync_fold = fold[sync_ok].copy()
    side_result = compare(side_fold, side_target, {"core":core, "core_plus_1x2":core+SIDE}, config)
    sync_result = compare(sync_fold, sync_target, {"core":core, "core_plus_1x2":core+SIDE, "core_plus_1x2_ah":core+SIDE+AH}, config)

    rows = side_target[KEYS + ["match_identity","identity_hash","exact_total","total_class","goal_difference"]].copy(); rows["actual_draw"] = (rows.goal_difference == 0).astype(int)
    for name,p in side_result.pop("probabilities").items(): rows[f"side_{name}_p_draw"] = p
    sync_probs = sync_result.pop("probabilities"); mapping_index = sync_target.index.to_list()
    for name,p in sync_probs.items():
        mp = dict(zip(mapping_index,p)); rows[f"sync_{name}_p_draw"] = [mp.get(i, np.nan) for i in rows.index]

    result = {
        "schema_version":"FIXED500_EVEN_T_DRAW_COMPACT_R3B",
        "classification":"ORACLE_EXACT_T_COMPACT_SIDE_MARKET_DIAGNOSTIC",
        "question":"Given exact T in {2,4,6}, do compact existing 1X2/AH pre-match market references distinguish GD=0 from GD=±2?",
        "sample":{"parent_fixed500_n":500,"parent_fixed500_identity_sha256":sample_hash,"new_sample_consumed":False,"latest_position4_confirmation_opened":False},
        "target_contract":{"exact_total_classes":[2,4,6],"goal_difference_support":[-2,0,2],"draw_label":"GD=0","non_draw_label":"GD=±2","uses_oracle_exact_T":True},
        "side_cohort":side_result,"synchronized_cohort":sync_result,
        "data_identity":data_identity,"excluded_incomplete_latest_seasons":excluded,
        "interpretation_guard":{"retrospective_information_ceiling_only":True,"oracle_T_diagnostic_only":True,"formal_PIT_claim":False,"can_authorize_promotion":False,"same_fixed500_already_viewed":True},
        "governance":{"formal_weight":0,"provider_requests":0,"new_data_collection":False,"new_sample_consumed":False,"latest_position4_confirmation_opened":False,"formal_model_mutation":False,"formal_data_mutation":False,"formal_config_mutation":False,"current_mutation":False,"main_mutation":False},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); rows.to_csv(ROWS_OUT,index=False)
    return result


def main() -> None:
    x=run(); print(json.dumps({"sample":x["sample"],"target":x["target_contract"],"side":x["side_cohort"],"sync":x["synchronized_cohort"]},ensure_ascii=False,indent=2))

if __name__ == "__main__": main()
