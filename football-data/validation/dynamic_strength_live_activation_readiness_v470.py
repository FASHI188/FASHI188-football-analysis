#!/usr/bin/env python3
"""Separate historical validation from current-season dynamic-strength activation.

A historically validated challenger is not live-ready by itself. This gate audits
candidate freezing, next-season formal parameter/calibration routing, current-season
sample status, executable PIT evidence and the still-separate probability-effect
runtime/promotion gates. No formal weight is changed.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"manifests"/"dynamic_strength_live_activation_readiness_v470_status.json"
SELECTION=ROOT/"manifests"/"dynamic_strength_next_season_selection_v470_status.json"
LIVE_SMOKE=ROOT/"manifests"/"dynamic_strength_live_input_contract_v470_smoke.json"
PARAM_ROLL=ROOT/"manifests"/"formal_next_season_parameter_rollforward_v470_status.json"
PARAM_SMOKE=ROOT/"manifests"/"formal_next_season_parameter_runtime_v470_smoke.json"
OOF_ROLL=ROOT/"manifests"/"oof_next_season_rollforward_v470_status.json"
OOF_SMOKE=ROOT/"manifests"/"oof_next_season_runtime_v470_smoke.json"
REGISTRY=ROOT/"config"/"platform_registry.json"
CONFIG=ROOT/"config"/"formal_core_v460.json"
TARGETS={
 "ESP_LaLiga":{"target_season":"2026/27","final_path":ROOT/"manifests"/"dynamic_strength_final_chain_replay_v470"/"ESP_LaLiga.json","expected_final_status":"FINAL_CHAIN_LIVE_INPUT_REVIEW_REQUIRED","mode":"full_dynamic_strength","live_smoke_check":"esp_frozen_selection_contract_passes","param_smoke_check":"ESP_LaLiga_parameter_bridge_passes","zero_smoke_check":"ESP_LaLiga_zero_sample_still_rejected","oof_smoke_check":"ESP_LaLiga_unchanged_calibration_function_passes"},
 "NED_Eredivisie":{"target_season":"2026/27","final_path":ROOT/"manifests"/"dynamic_strength_allocation_only_final_chain_v470"/"NED_Eredivisie.json","expected_final_status":"ALLOCATION_ONLY_FINAL_CHAIN_LIVE_INPUT_REVIEW_REQUIRED","mode":"allocation_only_preserve_direct_total","live_smoke_check":"ned_frozen_selection_contract_passes","param_smoke_check":"NED_Eredivisie_parameter_bridge_passes","zero_smoke_check":"NED_Eredivisie_zero_sample_still_rejected","oof_smoke_check":"NED_Eredivisie_unchanged_calibration_function_passes"},
}
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def main()->int:
    registry=load(REGISTRY);reg={r["competition_id"]:r for r in registry["competitions"]};selection=load(SELECTION) if SELECTION.exists() else {};live_smoke=load(LIVE_SMOKE) if LIVE_SMOKE.exists() else {};param_roll=load(PARAM_ROLL) if PARAM_ROLL.exists() else {};param_smoke=load(PARAM_SMOKE) if PARAM_SMOKE.exists() else {};oof_roll=load(OOF_ROLL) if OOF_ROLL.exists() else {};oof_smoke=load(OOF_SMOKE) if OOF_SMOKE.exists() else {};cfg=load(CONFIG)
    live_checks=live_smoke.get("checks") or {};param_checks=param_smoke.get("checks") or {};oof_checks=oof_smoke.get("checks") or {}
    reports={}
    for cid,spec in TARGETS.items():
        final=load(spec["final_path"]) if spec["final_path"].exists() else {};sel=(selection.get("reports") or {}).get(cid,{});pr=(param_roll.get("reports") or {}).get(cid,{});orr=(oof_roll.get("reports") or {}).get(cid,{})
        current_status=str(reg.get(cid,{}).get("current_season_status") or "");target=spec["target_season"]
        checks={
          "historical_final_chain_passed":final.get("status")==spec["expected_final_status"],
          "next_season_candidate_frozen_from_completed_prior_season":sel.get("status")=="NEXT_SEASON_CANDIDATE_FROZEN_RESEARCH_ONLY" and sel.get("target_season")==target,
          "next_season_parameter_rollforward_passed":pr.get("status")=="NEXT_SEASON_PARAMETERS_FROZEN" and pr.get("target_season")==target and pr.get("team_strength_rollforward") is False,
          "next_season_parameter_runtime_smoke_passed":param_smoke.get("status")=="PASS" and bool(param_checks.get(spec["param_smoke_check"])),
          "zero_sample_hard_gate_preserved":bool(param_checks.get(spec["zero_smoke_check"])),
          "next_season_oof_rollforward_passed":orr.get("status")=="NEXT_SEASON_OOF_CALIBRATOR_FROZEN" and orr.get("target_season")==target and orr.get("guardrail_passed") is True,
          "next_season_oof_runtime_smoke_passed":oof_smoke.get("status")=="PASS" and bool(oof_checks.get(spec["oof_smoke_check"])),
          "current_season_not_preseason_zero_sample":"preseason_no_completed_matches" not in current_status,
          "live_input_contract_smoke_passed":live_smoke.get("status")=="PASS",
          "live_input_contract_supports_competition":bool(live_checks.get(spec["live_smoke_check"])),
          "question_time_parameter_and_oof_bridges_wired":True,
          "question_time_live_input_audit_wired":True,
          "actionable_runtime_dynamic_strength_effect_wired":False,
          "hash_bound_competition_season_promotion_receipt_present":False,
        }
        activation_ready=all(checks.values());blockers=[key for key,value in checks.items() if not value]
        reports[cid]={"competition_id":cid,"target_season":target,"mode":spec["mode"],"status":"LIVE_ACTIVATION_READY" if activation_ready else "LIVE_ACTIVATION_BLOCKED","formal_weight":0,"probability_change":False,"current_season_status":current_status,"base_core_sample_thresholds":{"minimum_competition_history_matches":cfg["minimum_competition_history_matches"],"minimum_team_raw_matches_per_relevant_venue":cfg["minimum_team_raw_matches"]},"next_season_selection":sel or None,"parameter_rollforward":pr or None,"oof_rollforward":orr or None,"checks":checks,"blockers":blockers,"resolved_in_this_phase":["next_season_parameter_rollforward_passed","next_season_parameter_runtime_smoke_passed","next_season_oof_rollforward_passed","next_season_oof_runtime_smoke_passed","question_time_parameter_and_oof_bridges_wired"],"policy":"Historical validation, next-season parameter/calibration routing, same-season sample sufficiency, live evidence and live probability activation are separate. Any blocker keeps dynamic-strength formal effect at zero."}
    out={"schema_version":"V4.7.0-dynamic-strength-live-activation-readiness-r3","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_weight_change":False,"automatic_promotion":False,"probability_change":False,"reports":reports,"live_ready_competitions":[cid for cid,r in reports.items() if r["status"]=="LIVE_ACTIVATION_READY"],"blocked_competitions":[cid for cid,r in reports.items() if r["status"]!="LIVE_ACTIVATION_READY"],"policy":"This governance receipt is authoritative for live readiness only; it never overrides CURRENT or creates a promotion."}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
