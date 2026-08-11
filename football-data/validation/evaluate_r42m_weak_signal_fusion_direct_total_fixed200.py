#!/usr/bin/env python3
"""R42M: second-generation fusion of two prior weak Direct-T feature blocks.

This is explicitly exploratory post-selection: R42F HT->FT response and R42J recovered
all-history pair features were chosen because earlier viewed fixed200 tests had favorable
proper-score point estimates without passing their frozen gates. No parameter, feature or
threshold is selected on the new R42M fixed200. The two blocks are concatenated and their
coefficients are learned only on historical train/policy rows; there is no manual weight.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json, select_fixed_identities, split_for_latest_complete
from evaluate_r42e_shot_direct_total_crossdomain_fixed200 import paired_bootstrap
from evaluate_r42f_htft_response_direct_total_fixed200 import load_ht_rows, build_htft_features
from evaluate_r42g_discipline_referee_direct_total_fixed200 import tail_binary
from evaluate_r42j_all_history_pair_recovery_direct_total_fixed200 import add_recovered_all_pair_features, recovered_feature_names
from evaluate_r42l_team_total_histogram_fixed200 import reproduce_prior3400, build_histogram_features, histogram_feature_names
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_CONFIG=ROOT/"config"/"r42m_weak_signal_fusion_direct_total_fixed200.json"
DEFAULT_OUT=ROOT/"manifests"/"r42m_weak_signal_fusion_direct_total_fixed200_status.json"
TOTAL_CLASSES=list(range(8))


def reproduce_prior3600(raw: pd.DataFrame, seasons: dict[str,list[str]], base_features: pd.DataFrame, cfg: dict[str,Any]) -> tuple[set[str],dict[str,str]]:
    r42l_cfg=load_json(ROOT/"config"/"r42l_team_total_histogram_fixed200.json")
    excluded3400, hashes=reproduce_prior3400(raw,seasons,base_features,r42l_cfg)
    if len(excluded3400)!=3400: raise ResearchError(f"expected prior3400, got {len(excluded3400)}")
    f=base_features.copy(); f["split_r42l"]=split_for_latest_complete(f,seasons,r42l_cfg)
    hist,_=build_histogram_features(f,r42l_cfg); names=histogram_feature_names(r42l_cfg)
    m=f.merge(hist,on="identity_key",how="left",validate="one_to_one")
    target=m[(m.split_r42l=="target_pool") & (m.histogram_history_ok.fillna(0).astype(int)==1) & m[names].notna().all(axis=1)].copy()
    fresh=target[~target.identity_key.astype(str).isin(excluded3400)].copy()
    ids,sha=select_fixed_identities(fresh,int(r42l_cfg["sample_contract"]["sample_size"]),int(r42l_cfg["sample_contract"]["seed"]))
    expected=str(cfg["sample_contract"]["exclude_R42L_identity_sha256"])
    if sha!=expected: raise ResearchError(f"R42L identity mismatch {sha} != {expected}")
    excluded3600=excluded3400|set(ids)
    if len(excluded3600)!=int(cfg["sample_contract"]["prior_consumed_rows_before_R42M"]): raise ResearchError(f"expected prior3600, got {len(excluded3600)}")
    out=dict(hashes); out["R42L"]=sha
    return excluded3600,out


def run(cfg:dict[str,Any], out_path:Path)->dict[str,Any]:
    base_cfg=load_json(ROOT/str(cfg["base_model_config"])); raw=pd.read_csv(ROOT/str(cfg["input_ledger"]))
    identity=audit_data_identity(raw,base_cfg); seasons,excluded_latest=complete_seasons(raw,base_cfg)
    features=add_identity_key(build_features(raw)); features["split"]=split_for_latest_complete(features,seasons,cfg)
    excluded3600,prior_hashes=reproduce_prior3600(raw,seasons,features,cfg)

    r42j_cfg=load_json(ROOT/str(cfg["feature_contract"]["all_pair_source_config"])); frame=add_recovered_all_pair_features(features,r42j_cfg); pair_names=recovered_feature_names(r42j_cfg)
    r42f_cfg=load_json(ROOT/str(cfg["feature_contract"]["htft_source_config"])); frame["date_norm"]=pd.to_datetime(frame["date_key"],errors="raise").dt.date.astype(str)
    ht_rows,source_cov=load_ht_rows(set(frame.competition_id.astype(str))); htft,ht_audit=build_htft_features(ht_rows,r42f_cfg); ht_names=[str(x) for x in r42f_cfg["feature_contract"]["feature_names"]]
    keep=["competition_id","season","date_norm","home_team","away_team","home_state_trials_total","away_state_trials_total"]+ht_names
    frame=frame.merge(htft[keep],on=["competition_id","season","date_norm","home_team","away_team"],how="left",validate="one_to_one")
    names=ht_names+pair_names
    if len(ht_names)!=int(cfg["feature_contract"]["htft_feature_count"]) or len(pair_names)!=int(cfg["feature_contract"]["all_pair_feature_count"]) or len(names)!=int(cfg["feature_contract"]["combined_feature_count"]): raise ResearchError("R42M feature-count contract mismatch")
    min_trials=float(cfg["coverage_gate"]["minimum_prior_state_trials_per_team_any_state"])
    target=frame[(frame.split=="target_pool") & frame[names].notna().all(axis=1) & (frame.home_state_trials_total.fillna(0)>=min_trials) & (frame.away_state_trials_total.fillna(0)>=min_trials)].copy()
    fresh=target[~target.identity_key.astype(str).isin(excluded3600)].copy(); minimum=int(cfg["coverage_gate"]["minimum_fresh_target_rows_after_prior3600_exclusion"])
    coverage_by_comp={str(k):int(v) for k,v in fresh.groupby("competition_id").size().sort_index().items()}
    base_receipt={"schema_version":cfg["schema_version"],"data_identity":identity,"excluded_incomplete_latest_seasons":excluded_latest,"prior_fixed200_exclusion":{"rows":len(excluded3600),"hashes":prior_hashes},"selection_history_disclosure":cfg["feature_contract"]["selection_history_disclosure"],"coverage":{"raw_htft_source_by_competition":source_cov,"htft_feature_build":ht_audit,"fresh_target_rows_after_prior3600_exclusion":int(len(fresh)),"fresh_target_rows_by_competition":coverage_by_comp,"minimum_required":minimum},"zero_test_selection_receipt":{"target_labels_used_for_coverage_gate":False,"target_labels_used_for_identity_selection":False,"current_match_htft_used_in_own_features":0,"all_pair_features_strictly_prior":True,"manual_feature_weight":False,"model_fits_before_coverage_gate":0},"governance":cfg["governance"]}
    if len(fresh)<minimum:
        result={**base_receipt,"status":"STOP_R42M_FUSION_COVERAGE_LT200","scientific_verdict":"DO_NOT_CONSUME_FIXED200_FUSION_COVERAGE_INSUFFICIENT","sample":None,"model_fits":0}; out_path.parent.mkdir(parents=True,exist_ok=True); out_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); return result

    ids,sample_sha=select_fixed_identities(fresh,int(cfg["sample_contract"]["sample_size"]),int(cfg["sample_contract"]["seed"])); sample=fresh[fresh.identity_key.astype(str).isin(set(ids))].sort_values("identity_key").copy()
    if len(sample)!=200 or set(sample.identity_key.astype(str))&excluded3600: raise ResearchError("R42M sample identity contract failed")
    fit_rows=frame[frame.split.isin(["train","policy"]) & frame[names].notna().all(axis=1) & (frame.home_state_trials_total.fillna(0)>=min_trials) & (frame.away_state_trials_total.fillna(0)>=min_trials)].copy(); train=fit_rows[fit_rows.split=="train"].copy(); policy=fit_rows[fit_rows.split=="policy"].copy()
    if min(len(train),len(policy))==0: raise ResearchError("empty R42M fit split")
    core=select_core_features(frame); selected_C,policy_grid=select_C(train,policy,core,"total_class",TOTAL_CLASSES,base_cfg); allowed=[float(x) for x in cfg["fit_contract"]["baseline_C_grid"]]
    if float(selected_C) not in allowed: raise ResearchError(f"R42M selected C outside grid: {selected_C}")
    challenger_features=core+names; baseline=make_model(float(selected_C),base_cfg); challenger=make_model(float(selected_C),base_cfg); baseline.fit(fit_rows[core],fit_rows.total_class); challenger.fit(fit_rows[challenger_features],fit_rows.total_class)
    p_base=align_probability(baseline,sample[core],TOTAL_CLASSES); p_ch=align_probability(challenger,sample[challenger_features],TOTAL_CLASSES)
    y=sample.total_class.to_numpy(int); bc=metric_components(y,p_base,TOTAL_CLASSES); cc=metric_components(y,p_ch,TOTAL_CLASSES); bm=metric_summary(bc); cm=metric_summary(cc); boot=paired_bootstrap(bc,cc,cfg)
    gate={"logloss_p95_below_zero":bool(boot["logloss"]["p95"]<0),"brier_nonworse":bool(cm["brier"]<=bm["brier"]),"rps_nonworse":bool(cm["rps"]<=bm["rps"])}; gate["all_required"]=bool(all(gate.values()))
    draw_mask=sample.goal_difference.to_numpy(int)==0; draw_diag=None
    if np.any(draw_mask): draw_diag={"rows":int(draw_mask.sum()),"baseline_total_logloss":float(bc.loc[draw_mask,"logloss"].mean()),"challenger_total_logloss":float(cc.loc[draw_mask,"logloss"].mean()),"delta":float(cc.loc[draw_mask,"logloss"].mean()-bc.loc[draw_mask,"logloss"].mean())}
    result={**base_receipt,"status":"PASS_R42M_FIXED200_EXECUTION_COMPLETE","scientific_verdict":"PASS_R42M_WEAK_SIGNAL_FUSION_FIXED200" if gate["all_required"] else "FAIL_R42M_WEAK_SIGNAL_FUSION_NO_INCREMENT_FIXED200","sample":{"rows":200,"seed":int(cfg["sample_contract"]["seed"]),"identity_sha256":sample_sha,"overlap_with_prior_3600":0,"competitions_represented":int(sample.competition_id.nunique()),"competition_counts":{str(k):int(v) for k,v in sample.groupby("competition_id").size().sort_index().items()},"date_min":str(sample.date_key.min()),"date_max":str(sample.date_key.max()),"actual_total_bucket_counts":{str(k):int(v) for k,v in sample.total_class.value_counts().sort_index().items()},"actual_draw_rows":int(draw_mask.sum()),"labels_used_for_identity_selection":False,"blind_claim":False},"model_contract":{"baseline_policy_selected_C":float(selected_C),"baseline_policy_grid":policy_grid,"same_C_used_by_challenger":True,"baseline_feature_count":len(core),"htft_feature_count":len(ht_names),"all_pair_feature_count":len(pair_names),"fusion_feature_count":len(names),"challenger_feature_count":len(challenger_features),"scientific_parameters_selected_on_fixed200":0,"manual_feature_weight":False,"baseline_max_solver_iterations":int(np.max(baseline.named_steps["model"].n_iter_)),"challenger_max_solver_iterations":int(np.max(challenger.named_steps["model"].n_iter_)),"baseline_probability_sum_max_residual":float(np.max(np.abs(p_base.sum(axis=1)-1))),"challenger_probability_sum_max_residual":float(np.max(np.abs(p_ch.sum(axis=1)-1)))},"metrics":{"baseline":bm,"challenger":cm,"delta_challenger_minus_baseline":{k:float(cm[k]-bm[k]) for k in bm},"paired_bootstrap":boot,"tail_T_ge_4":{"baseline":tail_binary(y,p_base),"challenger":tail_binary(y,p_ch)},"actual_draw_subset_total_logloss":draw_diag,"gate":gate},"interpretation_limits":["This candidate is post-selection exploratory because its two feature blocks were chosen after earlier viewed tests.","No coefficient/feature/threshold is selected on the R42M fixed200; a PASS can authorize a new disjoint replication only.","A FAIL closes the historical-score weak-signal fusion path under the current contract."]}
    out_path.parent.mkdir(parents=True,exist_ok=True); out_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); return result


def self_test()->None:
    cfg=load_json(DEFAULT_CONFIG); assert int(cfg["feature_contract"]["htft_feature_count"])+int(cfg["feature_contract"]["all_pair_feature_count"])==36; assert cfg["method_contract"]["manual_feature_weight"] is False; print(json.dumps({"status":"PASS_R42M_SELF_TEST","fusion_feature_count":36,"post_selection_disclosed":True,"manual_feature_weight":False}))

def main()->None:
    p=argparse.ArgumentParser(); p.add_argument("--config",type=Path,default=DEFAULT_CONFIG); p.add_argument("--out",type=Path,default=DEFAULT_OUT); p.add_argument("--self-test",action="store_true"); a=p.parse_args();
    if a.self_test: self_test(); return
    r=run(load_json(a.config),a.out); print(json.dumps({"status":r["status"],"scientific_verdict":r["scientific_verdict"],"coverage":r["coverage"],"sample":r.get("sample"),"model_contract":r.get("model_contract"),"metrics":r.get("metrics")},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
