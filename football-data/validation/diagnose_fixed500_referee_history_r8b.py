#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_fixed500_referee_history_r8 as r8
from diagnose_fixed500_existing_market_pack_t_r1 import MARKET_OU, add_identity_key, fit_parity, fit_total, materialize_market, parity_metrics
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import attach_exact_total, load_experiment, paired_bootstrap, sample_fixed_n
from v510_historical_structure_features_r1 import ResearchError, assign_fold, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import metric_components, metric_summary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fixed500_referee_history_r8b.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_referee_history_r8b_rows.csv"
TOTAL_CLASSES = list(range(8))
TIERS = (3, 5)

COMPACT = [
    "ref_n_log","ref_mean_total","ref_std_total","ref_draw_rate","ref_zero_zero_rate",
    "ref_even_rate","ref_high_total_rate","ref_home_win_rate",
    "ref_recent_mean_total","ref_recent_draw_rate","ref_recent_zero_zero_rate",
    "ref_recent_even_rate","ref_recent_high_total_rate","ref_recent_home_win_rate",
]
FULL = COMPACT + [
    "ref_mean_fouls","ref_mean_yellows","ref_mean_reds",
    "ref_recent_mean_fouls","ref_recent_mean_yellows","ref_recent_mean_reds",
]


def parity_components(y: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    y = np.asarray(y, int); p = np.asarray(p, float)
    p_even = np.clip(p[:,0], 1e-15, 1-1e-15); y_even = (y == 0).astype(int)
    return {
        "logloss": -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0)),
        "brier": (p_even-y_even)**2 + ((1-p_even)-(1-y_even))**2,
    }


def prior_count_from_log(series: pd.Series) -> pd.Series:
    return np.rint(np.expm1(series.astype(float))).astype(int)


def evaluate(
    fold_c: pd.DataFrame,
    sample_c: pd.DataFrame,
    core: list[str],
    config: dict[str, Any],
    with_ou: bool,
    seed: int,
) -> dict[str, Any]:
    train = fold_c[fold_c.split == "train"].copy()
    policy = fold_c[fold_c.split == "policy"].copy()
    fit = fold_c[fold_c.split.isin(["train","policy"])].copy()
    if min(len(train), len(policy)) < 60:
        return {"status":"INSUFFICIENT_FIT_COVERAGE","n":int(len(sample_c)),"train_n":int(len(train)),"policy_n":int(len(policy))}
    if len(sample_c) < 30:
        return {"status":"INSUFFICIENT_TEST_COVERAGE","n":int(len(sample_c)),"train_n":int(len(train)),"policy_n":int(len(policy))}

    packs: dict[str, list[str]] = {
        "core": core,
        "core_plus_ref_compact": core + COMPACT,
        "core_plus_ref_full": core + FULL,
    }
    baseline = "core"
    if with_ou:
        packs = {
            "core_plus_single_ou": core + MARKET_OU,
            "core_plus_single_ou_ref_compact": core + MARKET_OU + COMPACT,
            "core_plus_single_ou_ref_full": core + MARKET_OU + FULL,
        }
        baseline = "core_plus_single_ou"

    yT = sample_c.total_class.to_numpy(int); yP = sample_c.exact_parity.to_numpy(int)
    tm: dict[str,Any] = {}; pm: dict[str,Any] = {}; tc: dict[str,Any] = {}; pc: dict[str,Any] = {}; receipts: dict[str,Any] = {}
    tprob: dict[str,np.ndarray] = {}; pprob: dict[str,np.ndarray] = {}
    for name, feats in packs.items():
        pt, tr = fit_total(fit, train, policy, sample_c, feats, config)
        pp, pr = fit_parity(fit, train, policy, sample_c, feats, config)
        tprob[name] = pt; pprob[name] = pp; receipts[name] = {"total":tr,"parity":pr}
        c = metric_components(yT, pt, TOTAL_CLASSES); tc[name] = c; tm[name] = metric_summary(c)
        pc[name] = parity_components(yP, pp); pm[name] = parity_metrics(yP, pp)

    boot = {"direct_t":{},"parity":{}}
    challengers = [n for n in packs if n != baseline]
    for k, challenger in enumerate(challengers):
        boot["direct_t"][challenger] = {
            m: paired_bootstrap(tc[challenger][m].to_numpy(float)-tc[baseline][m].to_numpy(float),5000,930100+seed+k*20+i)
            for i,m in enumerate(("logloss","brier","rps"))
        }
        boot["parity"][challenger] = {
            m: paired_bootstrap(pc[challenger][m]-pc[baseline][m],5000,930500+seed+k*20+i)
            for i,m in enumerate(("logloss","brier"))
        }

    best_t = min(challengers,key=lambda n:tm[n]["logloss"])
    best_p = min(challengers,key=lambda n:pm[n]["log_loss"])
    stable_t = tm[best_t]["logloss"] < tm[baseline]["logloss"] and boot["direct_t"][best_t]["logloss"]["p95"] <= 0.0
    stable_p = pm[best_p]["log_loss"] < pm[baseline]["log_loss"] and boot["parity"][best_p]["logloss"]["p95"] <= 0.0
    return {
        "status":"EVALUATED",
        "n":int(len(sample_c)),
        "train_n":int(len(train)),"policy_n":int(len(policy)),
        "actual_draws":int(np.sum(sample_c.goal_difference.to_numpy(int)==0)),
        "actual_even":int(np.sum(yP==0)),"actual_odd":int(np.sum(yP==1)),
        "baseline":baseline,
        "direct_t":tm,"parity":pm,"bootstrap_vs_baseline":boot,"receipts":receipts,
        "best":{"direct_t":best_t,"parity":best_p},
        "stable_signal":{"direct_t":bool(stable_t),"parity":bool(stable_p)},
        "probabilities":{"T":tprob,"P":pprob},
    }


def run() -> dict[str, Any]:
    exp = load_experiment(); config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = build_features(raw)
    referee, coverage5 = r8.build_referee_features(raw)
    base = base.merge(referee,on="row_id",how="left",validate="one_to_one")
    base["ref_prior_n"] = prior_count_from_log(base.ref_n_log.fillna(0.0))
    base = add_identity_key(base); base = attach_exact_total(base,raw)
    base = base.merge(materialize_market(raw),on="identity_key",how="left",validate="one_to_one")
    core = select_core_features(base)
    seasons, excluded = complete_seasons(raw,config)
    pos = int(exp["test_position_zero_based"]); latest=max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if pos >= latest: raise ResearchError("R8B must reuse PR197 non-latest fixed500")
    base["split"] = assign_fold(base,seasons,pos)
    sample_base,sample_hash = sample_fixed_n(base[base.split=="test"].copy(),int(exp["sample_n"]))
    sample = base.merge(sample_base[KEYS+["match_identity","identity_hash"]],on=KEYS,how="inner",validate="one_to_one")
    if len(sample)!=500: raise ResearchError("R8B fixed500 mismatch")

    row_export = sample[KEYS+["match_identity","identity_hash","exact_total","exact_parity","ref_identity_available","ref_prior_n"]+MARKET_OU+FULL].copy()
    tiers: dict[str,Any] = {}
    identity_available = base.ref_identity_available.eq(1.0)
    sample_identity_available = sample.ref_identity_available.eq(1.0)
    for ti, threshold in enumerate(TIERS):
        base_ready = identity_available & base.ref_prior_n.ge(threshold)
        sample_ready = sample_identity_available & sample.ref_prior_n.ge(threshold)
        base_ou = base_ready & base[MARKET_OU].notna().all(axis=1)
        sample_ou = sample_ready & sample[MARKET_OU].notna().all(axis=1)
        no_ou = evaluate(base[base_ready].copy(),sample[sample_ready].copy(),core,config,False,ti*1000)
        yes_ou = evaluate(base[base_ou].copy(),sample[sample_ou].copy(),core,config,True,ti*1000+500)
        tier = {
            "minimum_prior_referee_matches":threshold,
            "fixed500_ref_ready_n":int(sample_ready.sum()),
            "fixed500_ref_ready_ou_n":int(sample_ou.sum()),
            "without_ou":no_ou,
            "with_ou":yes_ou,
        }
        tiers[f"ge{threshold}"] = tier
        for cohort_label, ev, idxs in (("no_ou",no_ou,sample[sample_ready].index.to_list()),("ou",yes_ou,sample[sample_ou].index.to_list())):
            if ev.get("status") != "EVALUATED": continue
            probsT = ev.pop("probabilities")["T"] if "probabilities" in ev else {}
            # probabilities key was popped; preserve parity export via local absence is acceptable for evidence rows.
            for name,p in probsT.items():
                mp=dict(zip(idxs,np.argmax(p,axis=1)))
                row_export[f"ge{threshold}_{cohort_label}_{name}_pred_T"]=[mp.get(i,np.nan) for i in row_export.index]

    evaluated_ou = [t["with_ou"] for t in tiers.values() if t["with_ou"].get("status")=="EVALUATED"]
    stable_t_any = any(x["stable_signal"]["direct_t"] for x in evaluated_ou)
    stable_p_any = any(x["stable_signal"]["parity"] for x in evaluated_ou)
    # Do not select the best tier post hoc. A stable signal must reproduce at BOTH predeclared tiers.
    stable_t_all = len(evaluated_ou)==len(TIERS) and all(x["stable_signal"]["direct_t"] for x in evaluated_ou)
    stable_p_all = len(evaluated_ou)==len(TIERS) and all(x["stable_signal"]["parity"] for x in evaluated_ou)
    if stable_t_all and stable_p_all:
        verdict="REFEREE_HISTORY_REPRODUCES_STABLE_T_AND_PARITY_SIGNAL_ACROSS_GE3_GE5"
    elif stable_t_all:
        verdict="REFEREE_HISTORY_REPRODUCES_STABLE_T_SIGNAL_ACROSS_GE3_GE5"
    elif stable_p_all:
        verdict="REFEREE_HISTORY_REPRODUCES_STABLE_PARITY_SIGNAL_ACROSS_GE3_GE5"
    elif stable_t_any or stable_p_any:
        verdict="REFEREE_HISTORY_SINGLE_TIER_SIGNAL_NOT_REPRODUCED_DO_NOT_ACCEPT"
    else:
        verdict="REFEREE_HISTORY_NO_STABLE_SIGNAL_OR_COVERAGE_LIMITED"

    result={
        "schema_version":"FIXED500_REFEREE_HISTORY_R8B",
        "status":"COMPLETED_RESEARCH_ONLY",
        "scientific_verdict":verdict,
        "question":"Does prior referee history add T/parity signal, evaluated at two coverage tiers chosen before any R8 performance result was observed?",
        "sample":{"parent_fixed500_n":500,"parent_fixed500_identity_sha256":sample_hash,"new_sample_consumed":False,"latest_position4_confirmation_opened":False},
        "coverage":{"original_ge5_gate_fixed500_n":48,"base_referee_coverage":coverage5,"tiers":[3,5]},
        "feature_contract":{"compact_features":COMPACT,"full_features":FULL,"coverage_tiers":[3,5],"tier_selection_rule":"report both; no post-result tier selection","same_day_freeze_before_updates":True,"current_match_result_used_as_current_feature":False,"only_prior_referee_matches_update_state":True,"model_family_unchanged":True,"manual_threshold":False},
        "tiers":tiers,
        "decision":{"stable_t_any_tier":stable_t_any,"stable_parity_any_tier":stable_p_any,"stable_t_both_tiers":stable_t_all,"stable_parity_both_tiers":stable_p_all},
        "data_identity":data_identity,"excluded_incomplete_latest_seasons":excluded,
        "interpretation_guard":{"R8_initial_failure_revealed_only_coverage_not_performance":True,"referee_assignment_archive_has_no_original_observation_timestamp":True,"formal_PIT_claim":False,"can_authorize_promotion":False,"no_current_match_result_leakage":True,"do_not_cherry_pick_coverage_tier":True},
        "governance":{"formal_weight":0,"provider_requests":0,"new_data_collection":False,"new_sample_consumed":False,"latest_position4_confirmation_opened":False,"post_result_threshold_search":False,"formal_model_mutation":False,"formal_data_mutation":False,"formal_config_mutation":False,"current_mutation":False,"main_mutation":False},
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); row_export.to_csv(ROWS_OUT,index=False)
    return result


def main()->None:
    x=run(); print(json.dumps({"verdict":x["scientific_verdict"],"sample":x["sample"],"coverage":x["coverage"],"decision":x["decision"],"tiers":x["tiers"]},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
