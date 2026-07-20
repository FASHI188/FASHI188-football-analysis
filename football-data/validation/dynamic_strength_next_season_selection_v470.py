#!/usr/bin/env python3
"""Select frozen 2026/27 dynamic-strength candidates using completed 2025/26 only.

This implements the same forward-season policy used by the strict second stage:
the candidate for a target season is selected exclusively from the fully completed
immediately preceding season.  ESP uses the full dynamic-strength variant; NED uses
the allocation-only variant that preserves the Champion direct-total track before
calibration.  Selection alone never changes formal weights.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
from statistics import mean
from dynamic_strength_second_stage_v470 import season_predictions as full_season_predictions
from dynamic_strength_allocation_only_second_stage_v470 import season_predictions as allocation_season_predictions
from dynamic_strength_oof_screen_v470 import CANDIDATES,MODEL_ROOT,build_season_indexes,load_domain_data
from platform_core import load_json
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"manifests"/"dynamic_strength_next_season_selection_v470_status.json"
TARGETS={
 "ESP_LaLiga":{"selection_season":"2025/26","target_season":"2026/27","mode":"full_dynamic_strength","season_predictions":full_season_predictions},
 "NED_Eredivisie":{"selection_season":"2025/26","target_season":"2026/27","mode":"allocation_only_preserve_direct_total","season_predictions":allocation_season_predictions},
}

def main()->int:
    reports={}
    for cid,spec in TARGETS.items():
        data=load_domain_data(cid,Path("/tmp/football-dynamic-strength-next-season-cache"));indexes=build_season_indexes(data);model=load_json(MODEL_ROOT/cid/"model.json");pmap=model.get("point_in_time_parameters") or {}
        selection_season=spec["selection_season"]
        if selection_season not in pmap:
            reports[cid]={"competition_id":cid,"status":"FAILED","reason":"selection-season formal parameters missing","formal_weight":0};continue
        baseline,candidates=spec["season_predictions"](cid,selection_season,pmap[selection_season],data,indexes)
        scored=[]
        for candidate in CANDIDATES:
            cmap=candidates[candidate["id"]];keys=[k for k in baseline if k in cmap]
            if len(keys)<100:continue
            scored.append({"candidate_id":candidate["id"],"predictions":len(keys),"mean_one_x_two_rps":mean(cmap[k]["one_x_two_rps"] for k in keys),"mean_joint_log":mean(cmap[k]["joint_log"] for k in keys),"mean_one_x_two_brier":mean(cmap[k]["one_x_two_brier"] for k in keys),"mean_total_goals_rps":mean(cmap[k]["total_goals_rps"] for k in keys)})
        scored.sort(key=lambda item:(item["mean_one_x_two_rps"],item["mean_joint_log"],item["candidate_id"]))
        if not scored:
            reports[cid]={"competition_id":cid,"status":"FAILED","reason":"no eligible completed-season candidate predictions","formal_weight":0};continue
        selected=scored[0]
        reports[cid]={"competition_id":cid,"status":"NEXT_SEASON_CANDIDATE_FROZEN_RESEARCH_ONLY","selection_season":selection_season,"target_season":spec["target_season"],"mode":spec["mode"],"selected_candidate":selected["candidate_id"],"selection_predictions":selected["predictions"],"selection_metrics":selected,"candidate_ranking":scored,"formal_weight":0,"automatic_promotion":False,"probability_change":False,"policy":"Candidate frozen from the fully completed immediately preceding season only. Live activation still requires current-target-season parameter rollover, sample gates, PIT evidence and a hash-bound promotion receipt."}
    out={"schema_version":"V4.7.0-dynamic-strength-next-season-selection-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS" if all(r.get("status")!="FAILED" for r in reports.values()) else "PARTIAL","formal_weight_change":False,"automatic_promotion":False,"probability_change":False,"reports":reports}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if out["status"]=="PASS" else 1
if __name__=="__main__":raise SystemExit(main())
