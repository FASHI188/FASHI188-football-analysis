#!/usr/bin/env python3
"""Strict forward-season second stage for allocation-only dynamic strength.

The candidate is selected on the fully completed immediately preceding season and
frozen before the target season.  The CURRENT Champion direct total-goals mean and
NB track are preserved exactly.  Research only; no formal weight change.
"""
from __future__ import annotations
import argparse,json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from dynamic_strength_allocation_only_oof_v470 import allocation_only_matrix
from dynamic_strength_oof_screen_v470 import CANDIDATES,MODEL_ROOT,bootstrap_diff,build_season_indexes,date_windows,load_domain_data,score_metrics,team_features,to_match,utc_now,write_json
from football_v460_engine import _merge_parameters,build_score_matrix,expected_goals,fit_current_season_state,load_config,low_score_factors
from platform_core import PlatformError,load_json
ROOT=Path(__file__).resolve().parents[1]
STAGE1_ROOT=ROOT/"manifests"/"dynamic_strength_allocation_only_oof_v470"
OUT_ROOT=ROOT/"manifests"/"dynamic_strength_allocation_only_second_stage_v470"

def season_predictions(cid:str,season:str,selected_params:dict[str,Any],data:dict[str,Any],indexes:dict[str,Any]):
    config=load_config();games=indexes["by_season"].get(season,[]);previous=indexes["previous"].get(season)
    if not games or not previous or previous not in indexes["by_season"]:return {},{c["id"]:{} for c in CANDIDATES}
    params=_merge_parameters(config,selected_params);prior_rows=[to_match(g,cid) for g in indexes["by_season"][previous]];prior_cutoff=max(g["date"] for g in indexes["by_season"][previous])+timedelta(days=1)
    try:prior_state=fit_current_season_state(prior_rows,prior_cutoff,params,config)
    except PlatformError:prior_state=None
    baseline={};candidates={c["id"]:{} for c in CANDIDATES}
    for target in games:
        history=[to_match(g,cid) for g in games if g["date"]<target["date"]]
        try:
            state=fit_current_season_state(history,target["date"],params,config);means=expected_goals(state,f"club_{target['home_id']}",f"club_{target['away_id']}",params,config)
            base_matrix=build_score_matrix(float(means["mu_home"]),float(means["mu_away"]),state["nb_dispersion_k"],params["beta_binomial_concentration"],int(config["max_total_goals_exact"]),low_score_factors(state,params))
        except PlatformError:continue
        hf=team_features(target["home_id"],season,target["date"],indexes,data["transfers"]);af=team_features(target["away_id"],season,target["date"],indexes,data["transfers"])
        if not hf.get("feature_complete") or not af.get("feature_complete"):continue
        key=f"{cid}:{season}:{target['game_id']}";block=f"{season}:{target['date'].year}-{target['date'].month:02d}"
        baseline[key]={"match_key":key,"date":target["date"].date().isoformat(),"season":season,"block_id":block,**score_metrics(base_matrix,target["home_goals"],target["away_goals"])}
        for candidate in CANDIDATES:
            matrix,audit=allocation_only_matrix(state,prior_state,target["home_id"],target["away_id"],hf,af,candidate,params,config,float(means["mu_total"]))
            candidates[candidate["id"]][key]={"match_key":key,"date":target["date"].date().isoformat(),"season":season,"block_id":block,"candidate_id":candidate["id"],**score_metrics(matrix,target["home_goals"],target["away_goals"]),**audit}
    return baseline,candidates

def validate(cid:str,cache:Path)->dict[str,Any]:
    stage1=load_json(STAGE1_ROOT/f"{cid}.json")
    if stage1.get("status")!="ALLOCATION_ONLY_DYNAMIC_STRENGTH_REVIEW_CANDIDATE":raise PlatformError("allocation-only second stage requires passing stage-1 receipt")
    data=load_domain_data(cid,cache);indexes=build_season_indexes(data);model=load_json(MODEL_ROOT/cid/"model.json");pmap=model["point_in_time_parameters"]
    selected_model=[];selected_base=[];folds=[];selections=[]
    for target_season,params in pmap.items():
        selection_season=indexes["previous"].get(target_season)
        if not selection_season:continue
        sb,sc=season_predictions(cid,selection_season,params,data,indexes);scored=[]
        for candidate in CANDIDATES:
            cmap=sc[candidate["id"]];keys=[k for k in sb if k in cmap]
            if len(keys)<100:continue
            scored.append((mean(cmap[k]["one_x_two_rps"] for k in keys),mean(cmap[k]["joint_log"] for k in keys),candidate["id"],len(keys)))
        if not scored:continue
        scored.sort();selected_id=scored[0][2];tb,tc=season_predictions(cid,target_season,params,data,indexes);cmap=tc[selected_id];keys=[k for k in tb if k in cmap]
        if not keys:continue
        records=sorted((tb[k] for k in keys),key=lambda r:(r["date"],r["match_key"]));selections.append({"target_season":target_season,"selection_season":selection_season,"selected_candidate":selected_id,"selection_predictions":scored[0][3]})
        for wi,dates in enumerate(date_windows(records,2),start=1):
            fold_keys=[r["match_key"] for r in records if r["date"] in dates]
            for key in fold_keys:selected_model.append(cmap[key]);selected_base.append(tb[key])
            folds.append({"fold_id":f"{target_season}:OUTER{wi}","target_season":target_season,"selection_season":selection_season,"candidate_frozen_before_target_season":selected_id,"test_start":min(dates),"test_end":max(dates),"outer_predictions":len(fold_keys)})
    pairs=list(zip(selected_model,selected_base))
    if not pairs:raise PlatformError("no paired allocation-only second-stage predictions")
    cis={m:bootstrap_diff(pairs,m) for m in ("joint_log","one_x_two_brier","one_x_two_rps","total_goals_rps")}
    def avg(rows,key):return mean(r[key] for r in rows)
    coverage={key:{"current":avg(selected_base,key),"candidate":avg(selected_model,key)} for key in ("top1","top3","top5","score80","score90")};counts=Counter(s["selected_candidate"] for s in selections)
    checks={"minimum_outer_predictions":len(pairs)>=200,"minimum_independent_forward_time_folds":len(folds)>=8,"candidate_frozen_before_each_target_season":all(f["selection_season"]!=f["target_season"] for f in folds),"one_x_two_rps_ci_improves":cis["one_x_two_rps"]["ci95_upper"]<0.0,"joint_log_ci_improves":cis["joint_log"]["ci95_upper"]<0.0,"one_x_two_brier_ci_nonworse":cis["one_x_two_brier"]["ci95_upper"]<=0.0,"total_goals_rps_preserved":abs(cis["total_goals_rps"]["mean_difference"])<=1e-12 and abs(cis["total_goals_rps"]["ci95_upper"])<=1e-12,"top1_nonworse":coverage["top1"]["candidate"]+1e-12>=coverage["top1"]["current"],"top3_nonworse":coverage["top3"]["candidate"]+1e-12>=coverage["top3"]["current"],"top5_nonworse":coverage["top5"]["candidate"]+1e-12>=coverage["top5"]["current"],"score80_calibrated":0.76<=coverage["score80"]["candidate"]<=0.84,"score90_calibrated":0.86<=coverage["score90"]["candidate"]<=0.94,"probability_conservation":max(r["probability_sum_error"] for r in selected_model)<=1e-8,"non_identity_selected":sum(v for k,v in counts.items() if k!="identity_no_borrow")>0}
    status="ALLOCATION_ONLY_SECOND_STAGE_FINAL_CHAIN_REVIEW_CANDIDATE" if all(checks.values()) else "ALLOCATION_ONLY_SECOND_STAGE_NOT_PROMOTED"
    report={"schema_version":"V4.7.0-dynamic-strength-allocation-only-second-stage-r1","generated_at_utc":utc_now(),"competition_id":cid,"status":status,"formal_weight":0,"automatic_promotion":False,"probability_change":False,"total_goals_marginal_policy":"preserve_current_champion_mu_total_and_NB_track","outer_predictions":len(pairs),"independent_forward_time_folds":len(folds),"season_candidate_selections":selections,"selected_candidate_counts":dict(counts),"confidence_intervals":cis,"coverage":coverage,"checks":checks,"folds":folds,"policy":"Strict forward-season allocation-only research. Passing still requires final OOF calibration-chain replay and live-input governance review."}
    write_json(OUT_ROOT/f"{cid}.json",report);return report

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--competition",default="NED_Eredivisie");parser.add_argument("--cache-dir",default="/tmp/football-dynamic-strength-allocation-second-cache");args=parser.parse_args()
    try:r=validate(args.competition,Path(args.cache_dir))
    except Exception as exc:
        r={"schema_version":"V4.7.0-dynamic-strength-allocation-only-second-stage-r1","generated_at_utc":utc_now(),"competition_id":args.competition,"status":"FAILED","formal_weight":0,"automatic_promotion":False,"probability_change":False,"reason":str(exc)};write_json(OUT_ROOT/f"{args.competition}.json",r);print(json.dumps(r,ensure_ascii=False,indent=2));return 1
    print(json.dumps({"competition_id":args.competition,"status":r["status"],"outer_predictions":r["outer_predictions"],"folds":r["independent_forward_time_folds"]},ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
