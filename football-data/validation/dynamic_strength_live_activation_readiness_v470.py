#!/usr/bin/env python3
"""Separate historical validation from current-season dynamic-strength activation.

A historically validated challenger is not live-ready by itself. This gate audits
next-season candidate freezing, target-season formal parameter availability,
current-season sample status, executable PIT evidence contract and runtime effect
wiring. No formal weight is changed.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"manifests"/"dynamic_strength_live_activation_readiness_v470_status.json"
SELECTION=ROOT/"manifests"/"dynamic_strength_next_season_selection_v470_status.json"
LIVE_SMOKE=ROOT/"manifests"/"dynamic_strength_live_input_contract_v470_smoke.json"
REGISTRY=ROOT/"config"/"platform_registry.json"
TARGETS={
 "ESP_LaLiga":{"target_season":"2026/27","final_path":ROOT/"manifests"/"dynamic_strength_final_chain_replay_v470"/"ESP_LaLiga.json","expected_final_status":"FINAL_CHAIN_LIVE_INPUT_REVIEW_REQUIRED","mode":"full_dynamic_strength","smoke_check":"esp_frozen_selection_contract_passes"},
 "NED_Eredivisie":{"target_season":"2026/27","final_path":ROOT/"manifests"/"dynamic_strength_allocation_only_final_chain_v470"/"NED_Eredivisie.json","expected_final_status":"ALLOCATION_ONLY_FINAL_CHAIN_LIVE_INPUT_REVIEW_REQUIRED","mode":"allocation_only_preserve_direct_total","smoke_check":"ned_frozen_selection_contract_passes"},
}
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def main()->int:
    registry=load(REGISTRY);reg={r["competition_id"]:r for r in registry["competitions"]};selection=load(SELECTION) if SELECTION.exists() else {};smoke=load(LIVE_SMOKE) if LIVE_SMOKE.exists() else {};smoke_checks=smoke.get("checks") or {}
    reports={}
    for cid,spec in TARGETS.items():
        final=load(spec["final_path"]) if spec["final_path"].exists() else {};model_path=ROOT/"models"/"formal_core_v460"/cid/"model.json";model=load(model_path) if model_path.exists() else {};sel=(selection.get("reports") or {}).get(cid,{})
        current_status=str(reg.get(cid,{}).get("current_season_status") or "");target=spec["target_season"]
        checks={
          "historical_final_chain_passed":final.get("status")==spec["expected_final_status"],
          "next_season_candidate_frozen_from_completed_prior_season":sel.get("status")=="NEXT_SEASON_CANDIDATE_FROZEN_RESEARCH_ONLY" and sel.get("target_season")==target,
          "formal_core_live_target_season_matches":model.get("live_target_season")==target,
          "formal_core_target_season_parameters_present":target in (model.get("point_in_time_parameters") or {}),
          "current_season_not_preseason_zero_sample":"preseason_no_completed_matches" not in current_status,
          "live_input_contract_smoke_passed":smoke.get("status")=="PASS",
          "live_input_contract_supports_competition":bool(smoke_checks.get(spec["smoke_check"])),
          "question_time_live_input_audit_wired":True,
          "actionable_runtime_dynamic_strength_effect_wired":False,
          "hash_bound_competition_season_promotion_receipt_present":False,
        }
        activation_ready=all(checks.values())
        blockers=[key for key,value in checks.items() if not value]
        reports[cid]={"competition_id":cid,"target_season":target,"mode":spec["mode"],"status":"LIVE_ACTIVATION_READY" if activation_ready else "LIVE_ACTIVATION_BLOCKED","formal_weight":0,"probability_change":False,"current_season_status":current_status,"live_target_season_in_formal_artifact":model.get("live_target_season"),"next_season_selection":sel or None,"checks":checks,"blockers":blockers,"policy":"Historical validation, live evidence audit and live probability activation are separate. Any blocker keeps dynamic-strength formal effect at zero."}
    out={"schema_version":"V4.7.0-dynamic-strength-live-activation-readiness-r2","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_weight_change":False,"automatic_promotion":False,"probability_change":False,"reports":reports,"live_ready_competitions":[cid for cid,r in reports.items() if r["status"]=="LIVE_ACTIVATION_READY"],"blocked_competitions":[cid for cid,r in reports.items() if r["status"]!="LIVE_ACTIVATION_READY"],"policy":"This governance receipt is authoritative for live readiness only; it never overrides CURRENT or creates a promotion."}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
