#!/usr/bin/env python3
"""Final OOF-calibration chain replay for allocation-only dynamic strength.

The raw challenger preserves the Champion direct total-goals track.  This replay
checks the important interaction with the existing full-matrix temperature
calibrator, which can re-couple score allocation and the total-goals marginal.
Research only; no formal weight change.
"""
from __future__ import annotations
import argparse,json
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from dynamic_strength_allocation_only_oof_v470 import allocation_only_matrix
from dynamic_strength_final_chain_replay_v470 import apply_runtime_equivalent_calibration,verify_calibrator
from dynamic_strength_oof_screen_v470 import CANDIDATES,MODEL_ROOT,bootstrap_diff,build_season_indexes,date_windows,load_domain_data,score_metrics,team_features,to_match,utc_now,write_json
from football_v460_engine import _merge_parameters,build_score_matrix,expected_goals,fit_current_season_state,load_config,low_score_factors
from platform_core import PlatformError,load_json
ROOT=Path(__file__).resolve().parents[1]
SECOND_ROOT=ROOT/"manifests"/"dynamic_strength_allocation_only_second_stage_v470"
OUT_ROOT=ROOT/"manifests"/"dynamic_strength_allocation_only_final_chain_v470"

def raw_rows(cid:str,season:str,selected_params:dict[str,Any],candidate:dict[str,Any],data:dict[str,Any],indexes:dict[str,Any]):
    config=load_config();games=indexes["by_season"].get(season,[]);previous=indexes["previous"].get(season)
    if not games or not previous or previous not in indexes["by_season"]:return []
    params=_merge_parameters(config,selected_params);prior_rows=[to_match(g,cid) for g in indexes["by_season"][previous]];prior_cutoff=max(g["date"] for g in indexes["by_season"][previous])+timedelta(days=1)
    try:prior_state=fit_current_season_state(prior_rows,prior_cutoff,params,config)
    except PlatformError:prior_state=None
    out=[]
    for target in games:
        history=[to_match(g,cid) for g in games if g["date"]<target["date"]]
        try:
            state=fit_current_season_state(history,target["date"],params,config);means=expected_goals(state,f"club_{target['home_id']}",f"club_{target['away_id']}",params,config)
            base=build_score_matrix(float(means["mu_home"]),float(means["mu_away"]),state["nb_dispersion_k"],params["beta_binomial_concentration"],int(config["max_total_goals_exact"]),low_score_factors(state,params))
        except PlatformError:continue
        hf=team_features(target["home_id"],season,target["date"],indexes,data["transfers"]);af=team_features(target["away_id"],season,target["date"],indexes,data["transfers"])
        if not hf.get("feature_complete") or not af.get("feature_complete"):continue
        challenger,audit=allocation_only_matrix(state,prior_state,target["home_id"],target["away_id"],hf,af,candidate,params,config,float(means["mu_total"]))
        out.append({"match_key":f"{cid}:{season}:{target['game_id']}","date":target["date"].date().isoformat(),"season":season,"block_id":f"{season}:{target['date'].year}-{target['date'].month:02d}","home_goals":int(target["home_goals"]),"away_goals":int(target["away_goals"]),"base_matrix":base,"candidate_matrix":challenger,"challenger_audit":audit})
    return out

def validate(cid:str,cache:Path)->dict[str,Any]:
    second=load_json(SECOND_ROOT/f"{cid}.json")
    if second.get("status")!="ALLOCATION_ONLY_SECOND_STAGE_FINAL_CHAIN_REVIEW_CANDIDATE":raise PlatformError("passing allocation-only second-stage receipt required")
    calibrator,integrity=verify_calibrator(cid);model=load_json(MODEL_ROOT/cid/"model.json");pmap=model["point_in_time_parameters"];candidate_by_id={c["id"]:c for c in CANDIDATES};selections={x["target_season"]:x for x in second.get("season_candidate_selections",[])};data=load_domain_data(cid,cache);indexes=build_season_indexes(data)
    model_rows=[];base_rows=[];folds=[];season_cal={}
    for season,selection in selections.items():
        if season not in pmap:continue
        selected_id=selection["selected_candidate"];candidate=candidate_by_id[selected_id];records=raw_rows(cid,season,pmap[season],candidate,data,indexes)
        calibrated=[]
        for row in records:
            base,bc=apply_runtime_equivalent_calibration(row["base_matrix"],season,calibrator);challenger,cc=apply_runtime_equivalent_calibration(row["candidate_matrix"],season,calibrator)
            if bc!=cc:raise PlatformError("baseline/candidate calibration paths diverged")
            bm=score_metrics(base,row["home_goals"],row["away_goals"]);cm=score_metrics(challenger,row["home_goals"],row["away_goals"])
            b={"match_key":row["match_key"],"date":row["date"],"season":season,"block_id":row["block_id"],**bm};c={"match_key":row["match_key"],"date":row["date"],"season":season,"block_id":row["block_id"],**cm,**row["challenger_audit"]};calibrated.append((c,b));season_cal[season]=bc
        dr=[p[1] for p in calibrated]
        for wi,dates in enumerate(date_windows(dr,2),start=1):
            ps=[p for p in calibrated if p[1]["date"] in dates]
            if not ps:continue
            model_rows.extend(p[0] for p in ps);base_rows.extend(p[1] for p in ps);folds.append({"fold_id":f"{season}:FINAL{wi}","target_season":season,"frozen_candidate":selected_id,"calibration_status":season_cal[season]["status"],"calibration_mode":season_cal[season]["mode"],"temperature":season_cal[season]["temperature"],"test_start":min(dates),"test_end":max(dates),"outer_predictions":len(ps)})
    pairs=list(zip(model_rows,base_rows))
    if not pairs:raise PlatformError("no paired allocation-only final-chain predictions")
    cis={m:bootstrap_diff(pairs,m) for m in ("joint_log","one_x_two_brier","one_x_two_rps","total_goals_rps")}
    def avg(rows,key):return mean(r[key] for r in rows)
    coverage={k:{"current":avg(base_rows,k),"candidate":avg(model_rows,k)} for k in ("top1","top3","top5","score80","score90")}
    checks={"calibrator_artifact_integrity":integrity["status"]=="通过","minimum_outer_predictions":len(pairs)>=200,"minimum_independent_forward_time_folds":len(folds)>=8,"one_x_two_rps_ci_improves":cis["one_x_two_rps"]["ci95_upper"]<0.0,"joint_log_ci_improves":cis["joint_log"]["ci95_upper"]<0.0,"one_x_two_brier_ci_nonworse":cis["one_x_two_brier"]["ci95_upper"]<=0.0,"post_calibration_total_goals_rps_nonworse":cis["total_goals_rps"]["ci95_upper"]<=0.0,"top1_nonworse":coverage["top1"]["candidate"]+1e-12>=coverage["top1"]["current"],"top3_nonworse":coverage["top3"]["candidate"]+1e-12>=coverage["top3"]["current"],"top5_nonworse":coverage["top5"]["candidate"]+1e-12>=coverage["top5"]["current"],"score80_calibrated":0.76<=coverage["score80"]["candidate"]<=0.84,"score90_calibrated":0.86<=coverage["score90"]["candidate"]<=0.94,"probability_conservation":max(r["probability_sum_error"] for r in model_rows)<=1e-8}
    status="ALLOCATION_ONLY_FINAL_CHAIN_LIVE_INPUT_REVIEW_REQUIRED" if all(checks.values()) else "ALLOCATION_ONLY_FINAL_CHAIN_NOT_PROMOTED"
    report={"schema_version":"V4.7.0-dynamic-strength-allocation-only-final-chain-r1","generated_at_utc":utc_now(),"competition_id":cid,"status":status,"formal_weight":0,"automatic_promotion":False,"probability_change":False,"raw_total_goals_policy":"Champion direct total-goals marginal preserved before calibration","outer_predictions":len(pairs),"independent_forward_time_folds":len(folds),"calibrator_integrity":integrity,"season_calibration_audit":season_cal,"confidence_intervals":cis,"coverage":coverage,"checks":checks,"folds":folds,"live_input_gate":{"status":"REQUIRED_NOT_YET_PASSED" if status.endswith("LIVE_INPUT_REVIEW_REQUIRED") else "NOT_APPLICABLE"},"policy":"Final calibration-chain research replay only. Full-matrix temperature calibration may re-couple the raw allocation-only challenger to total-goals marginals, so post-calibration total RPS must remain nonworse. No automatic promotion."}
    write_json(OUT_ROOT/f"{cid}.json",report);return report

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--competition",default="NED_Eredivisie");parser.add_argument("--cache-dir",default="/tmp/football-dynamic-strength-allocation-final-cache");args=parser.parse_args()
    try:r=validate(args.competition,Path(args.cache_dir))
    except Exception as exc:
        r={"schema_version":"V4.7.0-dynamic-strength-allocation-only-final-chain-r1","generated_at_utc":utc_now(),"competition_id":args.competition,"status":"FAILED","formal_weight":0,"automatic_promotion":False,"probability_change":False,"reason":str(exc)};write_json(OUT_ROOT/f"{args.competition}.json",r);print(json.dumps(r,ensure_ascii=False,indent=2));return 1
    print(json.dumps({"competition_id":args.competition,"status":r["status"],"outer_predictions":r["outer_predictions"],"folds":r["independent_forward_time_folds"]},ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
