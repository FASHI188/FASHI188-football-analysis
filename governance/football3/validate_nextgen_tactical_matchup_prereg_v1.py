from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
from typing import Any

EXPECTED_BASE="de4423d75dead888a4fdd9f2bd0a0fbe89e47fba"
EXPECTED_BRANCH="football3/nextgen-tactical-matchup-prereg-v1"
EXPECTED_FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
EXPECTED_CURRENT_SHA256="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_BLOBS={
"governance/football3/nextgen_current_v2_foundation_contract_v1.json":"897684e9655f7123dd2c338470a64c4837819639",
"football-data/validation/v6_understat_xg_feature_panel_freeze_v6189.py":"b2efe24692b8c60991255e14cf0a8373c93a43a5",
"football-data/config/evidence_sources_v470.json":"f9241dd14cbb89cc31ada2facfeb6d3620d0afff",
}
class ContractError(AssertionError): pass
def req(x:bool,m:str):
    if not x: raise ContractError(m)
def load(p:Path)->dict[str,Any]: return json.loads(p.read_text(encoding="utf-8"))
def validate(c:dict[str,Any])->list[str]:
    out=[]
    def ok(x,n): req(bool(x),n); out.append(n)
    ok(c["schema_version"]=="football3-nextgen-tactical-matchup-prereg-contract-v1","schema")
    ok(c["project_id"]=="football3","project")
    ok(c["architecture_authority"]=="Football3 V3 下一代升级方案","authority")
    ok(c["status"]=="STOP_DATA_COVERAGE","stop")
    ok(c["canonical_integration"]["exact_base"]==EXPECTED_BASE,"base")
    ok(c["research_branch"]==EXPECTED_BRANCH,"branch")
    ok(c["formal_baseline"]["head"]==EXPECTED_FORMAL_HEAD,"formal_head")
    ok((c["formal_baseline"]["xg_weight"],c["formal_baseline"]["frozen_v1_weight"])==(0.75,0.25),"formal_weights")
    ok(c["formal_baseline"]["changed"] is False,"formal_unchanged")
    ok(c["current_authority"]["current_sha256"]==EXPECTED_CURRENT_SHA256 and c["current_authority"]["changed"] is False,"current_unchanged")
    x=c["candidate"]
    ok(x["candidate_id"]=="V3-C6-SINGLE-LOW-DIMENSIONAL-TACTICAL-MATCHUP","candidate_id")
    ok((x["status"],x["weight"],x["matrix_delta"])==("NOT_AVAILABLE",0,0),"inactive")
    ok(not x["activation_allowed"] and not x["training_allowed"] and not x["tuning_allowed"] and not x["new_target_label_access_allowed"],"candidate_fail_closed")
    ok(not x["legacy_prospective_label_access_allowed"] and not x["legacy_receipt_rebinding_allowed"],"legacy_sealed")
    ok(not x["multiple_tactical_experts_allowed"] and not x["posthoc_axis_selection_allowed"] and not x["partial_big5_rescue_allowed"],"single_axis_no_posthoc")
    ok(all(v is False for v in c["zero_label_audit"].values()),"zero_label")
    f=c["current_foundation_evidence"]
    ok(f["blob_sha"]==EXPECTED_BLOBS[f["path"]],"foundation_blob")
    ok(f["formal_baseline_head"]==EXPECTED_FORMAL_HEAD and f["tactical_matchup_interface_reserved"],"foundation_interface")
    ok((f["inactive_contract"]["status"],f["inactive_contract"]["weight"],f["inactive_contract"]["matrix_delta"])==("NOT_AVAILABLE",0,0),"foundation_inactive")
    ok(set(f["required_time_fields"])=={"as_of","cutoff","observed_at","available_at"},"foundation_time")
    ok(set(f["required_data_quality_fields"])=={"source","schema","identity","coverage","timing","missingness"},"foundation_quality")
    r=c["current_repository_tactical_evidence"]
    b=r["understat_panel_builder"]
    ok(b["blob_sha"]==EXPECTED_BLOBS[b["path"]],"panel_builder_blob")
    ok(b["strict_prior_match_state"] and b["downstream_web_refetch_forbidden"],"panel_builder_pit")
    ok({"PPDA","deep"}.issubset(set(b["feature_family"])),"tactical_fields")
    s=r["understat_panel_status"]
    ok(s["formal_current_version"]=="V5.0.1","panel_old_baseline")
    ok(s["panel_sha256"]=="a7627827006bb8f1eade4f8e1acc933e69ae2c006ad32e527525f0711af2c0d3","panel_sha")
    ok((s["panel_rows"],s["input_rows"])==(6247,6249) and s["attach_rate"]>0.999,"panel_coverage")
    ok(s["covered_domain_count"]==5 and s["registered_formal_domain_count"]==17 and s["all_17_covered"] is False,"five_of_seventeen")
    ok(s["labels_in_panel"] is False and s["formal_weight"]==0,"panel_research_only")
    p=r["source_policy"]
    ok(p["blob_sha"]==EXPECTED_BLOBS[p["path"]],"source_policy_blob")
    ok(p["route_defined_is_not_data_acquired"],"route_not_acquired")
    d=c["legacy_stage6_b_development_evidence"]
    ok(d["exact_head"]=="9cb97764de1250fd08c6d0880559192e66352ce3","legacy_dev_head")
    ok(d["contract_blob_sha"]=="3f5bf16e6ffe7853bbcfdcf408859df8b7c1be0b","legacy_dev_contract")
    ok((d["run_id"],d["artifact_id"])==(33859953006,9931718950),"legacy_dev_run_artifact")
    ok(d["artifact_digest"]=="sha256:4eaee5a8b8b62e4a75cf7f5c2c7beca94bcb909c7b7ccfb1d2ecba21c5cd2c96","legacy_dev_digest")
    ok(d["classification"]=="POST_VIEW_DEVELOPMENT_NOT_FRESH_CONFIRMATION" and d["baseline"]=="Frozen V3.1.1","legacy_dev_class")
    ok((d["development_n"],d["active_n"],d["fallback_n"])==(5478,5463,15),"legacy_dev_counts")
    ok(d["mechanism"]["half_life_matches"]==16.0 and d["mechanism"]["bridge_coefficient"]==0.10 and d["mechanism"]["no_model_fit"],"legacy_fixed_mechanism")
    ok(d["development_result"]["status"]=="STAGE6_PRE_B_DEVELOPMENT_PASS_PROSPECTIVE_CONFIRMATION_REQUIRED" and d["development_result"]["all_pass"],"legacy_dev_pass")
    ok(d["may_define_current_axis_because_positive"] is False and d["may_be_reused_as_fresh_confirmation"] is False,"legacy_dev_not_current")
    q=c["legacy_stage6_b_prospective_evidence"]
    ok(q["exact_head"]=="5abb056d6d88bb42809e1114ad642c3c9f414acb","legacy_pros_head")
    ok((q["run_id"],q["artifact_id"])==(33884264610,9941451940),"legacy_pros_run_artifact")
    ok(q["artifact_digest"]=="sha256:3dc33bad08091c8353cf3df2dbbcf71166620910dedd352562ece972fc1658e8","legacy_pros_digest")
    ok(q["status"]=="STAGE6_PRE_B_FRESH_CONFIRMATION_RECEIPTS_FULLY_ENROLLED_LABELS_SEALED","legacy_pros_status")
    ok(q["baseline"]=="Frozen V3.1.1" and q["formal_v2_reference_only_head"]==EXPECTED_FORMAL_HEAD,"legacy_pros_baseline")
    ok(len(q["target_population"])==5 and q["target_season"]=="2026/27","legacy_pros_scope")
    ok((q["required_n"],q["receipt_n"],q["active_n"],q["fallback_n"])==(1335,1335,1335,0),"legacy_pros_counts")
    ok(q["target_labels_opened"] is False and q["target_result_or_goal_values_read"]==0 and q["interim_scoring"] is False,"legacy_labels_sealed")
    ok(q["post_enrollment_action"]=="WAIT_ALL_1335_FIXTURES_COMPLETE_THEN_ONE_SHOT_LABEL_REVEAL_AND_SCORE","legacy_wait")
    ok(q["must_remain_untouched_by_current_candidate"] and q["may_not_be_rebound_to_current_nextgen_baseline"] and q["may_not_be_interim_scored"] and q["may_not_be_refit_or_reenrolled"],"legacy_protected")
    failures=set(c["coverage_gate"]["current_failures"])
    required={"LEGACY_DEEP_PPDA_DEVELOPMENT_RESULT_ALREADY_VIEWED_AND_MAY_NOT_SELECT_CURRENT_AXIS","LEGACY_1335_PROSPECTIVE_RECEIPTS_BOUND_TO_FROZEN_V3_1_1_NOT_CURRENT_NEXTGEN_FORMAL_V2_BASELINE","LEGACY_1335_QUEUE_ALREADY_LOCKED_AND_MAY_NOT_BE_REBOUND_REENROLLED_OR_INTERIM_SCORED","CURRENT_UNDERSTAT_TACTICAL_STATE_COVERS_ONLY_5_OF_17_REGISTERED_FORMAL_DOMAINS","CURRENT_REPOSITORY_HAS_NO_ACQUIRED_TIMESTAMP_COMPLETE_17_DOMAIN_TACTICAL_PROVIDER_CONTRACT","BIG5_PARTIAL_SCOPE_MAY_NOT_BE_SELECTED_NOW_FROM_VIEWED_LEGACY_SUCCESS"}
    ok(required.issubset(failures),"coverage_failures")
    ok(c["coverage_gate"]["result"]=="STOP_DATA_COVERAGE","coverage_result")
    ok(all(c["future_reopen_contract"].values()),"future_reopen")
    z=c["conditional_scientific_preregistration"]
    ok(z["activation_state_now"]=="INACTIVE_STOPPED" and z["family"]=="single_low_dimensional_tactical_matchup_matrix_residual","family")
    ok(z["maximum_active_tactical_axes"]==1 and z["maximum_free_parameter_count"]==1,"low_dimension")
    ok(z["axis_definition_now"]=="NOT_SELECTED_DUE_TO_POSTHOC_GUARD" and z["legacy_deep_ppda_is_reference_only"],"axis_not_selected")
    ok(not z["legacy_deep_ppda_may_be_used_as_new_confirmation"] and not z["league_specific_parameter_allowed"] and not z["multiple_expert_ensemble_allowed"] and not z["manual_tactical_override_allowed"],"no_reuse_specials")
    ok(not z["post_hoc_axis_selection_allowed"] and not z["post_hoc_domain_selection_allowed"],"no_posthoc")
    ok(z["chronological_oos_required"] and z["prospective_confirmation_required"] and not z["random_split"],"oos_prospective")
    ok(z["unified_score_matrix_only"] and not z["direct_final_1x2_override_allowed"] and not z["optional_stopping"],"matrix_no_optional")
    fb=c["fallback_contract"]
    ok("NOT_AVAILABLE" in fb["missing_or_unsafe_candidate_input"] and "V2 fallback" in fb["missing_or_unsafe_candidate_input"],"v2_fallback")
    ok(fb["fill_zero_for_missing_signal"] is False and fb["manual_estimation"] is False and fb["silent_v1_switch"] is False and fb["legacy_stage6_b_substitution"] is False and fb["second_provider_chain"] is False,"fallback_fail_closed")
    ok(all(c["forbidden_changes"].values()),"forbidden")
    return out

def validate_blobs(root:Path)->list[str]:
    req((root/".git").exists(),"git repo required")
    out=[]
    for p,e in EXPECTED_BLOBS.items():
        got=subprocess.check_output(["git","-C",str(root),"rev-parse",f"{EXPECTED_BASE}:{p}"],text=True).strip()
        req(got==e,f"blob {p}: {got} != {e}"); out.append(p)
    return out

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--contract",default="governance/football3/nextgen_tactical_matchup_prereg_contract_v1.json"); ap.add_argument("--repo-root",default="."); ap.add_argument("--skip-repo-blobs",action="store_true"); a=ap.parse_args()
    checks=validate(load(Path(a.contract))); blobs=[] if a.skip_repo_blobs else validate_blobs(Path(a.repo_root))
    print(json.dumps({"status":"STOP_DATA_COVERAGE","contract_checks":len(checks),"repo_blob_checks":len(blobs),"target_labels_read":False,"legacy_1335_labels_read":False,"training_performed":False,"tuning_performed":False,"candidate_status":"NOT_AVAILABLE","weight":0,"matrix_delta":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
