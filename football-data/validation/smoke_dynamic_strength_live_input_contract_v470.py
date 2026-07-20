#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
from dynamic_strength_live_input_contract_v470 import validate_dynamic_strength_live_input
from platform_core import PlatformError
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"manifests"/"dynamic_strength_live_input_contract_v470_smoke.json"

def source(ts):return {"name":"official-or-public-source","url":"https://example.com/evidence","observed_at_utc":ts}
def team(name,current_manager,prior_manager,roster,weights,promoted=False):
    return {"team_name":name,"promoted_or_relegated":promoted,"current_manager":current_manager,"prior_season_terminal_manager":prior_manager,"manager_source":source("2026-07-20T09:00:00Z"),"roster_source":source("2026-07-20T09:00:00Z"),"transfer_source":source("2026-07-20T09:00:00Z"),"current_roster_player_ids":roster,"prior_season_starter_weights":weights,"prior_season_end_utc":"2026-05-24T23:00:00Z","dated_transfer_events":[{"player_id":"p11","direction":"out","event_at_utc":"2026-07-01T00:00:00Z","observed_at_utc":"2026-07-20T08:00:00Z"}]}
def evidence(cid):
    weights={f"p{i}":float(12-i) for i in range(1,12)}
    return {"competition_id":cid,"target_season":"2026/27","prior_season":"2025/26","observed_at_utc":"2026-07-20T09:30:00Z","teams":{"home":team("Home FC","Coach A","Coach A",[f"p{i}" for i in range(1,11)]+["new1"],weights),"away":team("Away FC","Coach B","Coach C",[f"p{i}" for i in range(1,10)]+["new2","new3"],weights)}}
def context(cid):return {"match_identity":{"competition_id":cid,"season":"2026/27","home_team":"Home FC","away_team":"Away FC","freeze_time_utc":"2026-07-20T10:00:00Z"}}
def main():
    esp=validate_dynamic_strength_live_input(context("ESP_LaLiga"),evidence("ESP_LaLiga"))
    ned=validate_dynamic_strength_live_input(context("NED_Eredivisie"),evidence("NED_Eredivisie"))
    future=evidence("ESP_LaLiga");future["teams"]["home"]["manager_source"]["observed_at_utc"]="2026-07-20T11:00:00Z"
    future_rejected=False
    try:validate_dynamic_strength_live_input(context("ESP_LaLiga"),future)
    except PlatformError:future_rejected=True
    promoted=evidence("ESP_LaLiga");promoted["teams"]["home"]["promoted_or_relegated"]=True;promoted_audit=validate_dynamic_strength_live_input(context("ESP_LaLiga"),promoted)
    wrong_season_rejected=False
    wrong=evidence("NED_Eredivisie");wrong["target_season"]="2027/28"
    try:validate_dynamic_strength_live_input(context("NED_Eredivisie"),wrong)
    except PlatformError:wrong_season_rejected=True
    checks={"esp_frozen_selection_contract_passes":esp["status"]=="通过" and esp["candidate_id"]=="adaptive_6","ned_frozen_selection_contract_passes":ned["status"]=="通过" and ned["candidate_id"]=="adaptive_6","competition_modes_distinct":esp["candidate_mode"]=="full_dynamic_strength" and ned["candidate_mode"]=="allocation_only_preserve_direct_total","live_contract_does_not_mutate_probability":esp["formal_probability_effect_weight"]==0 and ned["formal_probability_effect_weight"]==0 and esp["probability_mutation"] is False and ned["probability_mutation"] is False,"future_dated_evidence_rejected":future_rejected,"wrong_target_season_rejected":wrong_season_rejected,"promoted_team_borrowing_forced_zero":promoted_audit["home"]["borrowing_weight_research_candidate"]==0.0,"manager_change_detected":esp["away"]["coach_continuity"]==0.0,"roster_continuity_derived":0.0<esp["home"]["roster_continuity"]<1.0}
    result={"schema_version":"V4.7.0-dynamic-strength-live-input-smoke-r2","status":"PASS" if all(checks.values()) else "FAIL","checks":checks,"esp_audit":esp,"ned_audit":ned,"promoted_home_borrowing_weight":promoted_audit["home"]["borrowing_weight_research_candidate"],"formal_weight_change":False,"probability_change":False}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result["status"]=="PASS" else 1
if __name__=="__main__":raise SystemExit(main())
