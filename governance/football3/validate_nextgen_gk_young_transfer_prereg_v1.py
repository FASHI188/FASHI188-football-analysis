from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
from typing import Any

EXPECTED_BASE="40ca77a4602fe2629421610671d61e2ae015c652"
EXPECTED_BRANCH="football3/nextgen-gk-young-transfer-prereg-v1"
EXPECTED_FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
EXPECTED_CURRENT_SHA256="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_BLOBS={
"governance/football3/nextgen_starting_xi_delta_prereg_contract_v1.json":"f8006e500e33cc74ac8c6b9f8fba32cb6ac446e0",
"football-data/validation/validate_1x2_goalkeeper_stability_fast100_v6136.py":"63502f26d5367fb8f37f442ae61e6869c1ad9586",
"football-data/manifests/v6_1x2_goalkeeper_stability_fast100_v6136_status.json":"77cd5e52ac2e732b490b7a910a71432be170d983",
"football-data/manifests/v6_1x2_goalkeeper_stability_replication100_v6137_status.json":"93baa5133552cc6ccc9bd6d30dfbe56e777e5d37",
"football-data/manifests/transfermarkt_value_readiness_v520_status.json":"502929cc88be841a516f2e7cebfe298651584241",
"football-data/config/dynamic_strength_public_evidence_v470.json":"86a5ebc6cad6d2019580baa282c2d69718199a19",
"football-data/validation/dynamic_strength_public_evidence_coverage_v470.py":"328b109ab3c88870245d43b5a81a23880d9cd7ff",
"football-data/manifests/dynamic_strength_public_evidence_v470_status.json":"9440a04218e58862d0b1b75f1e7fb8ebb30ea541",
"football-data/manifests/dynamic_strength_oof_screen_v470_status.json":"d5191305e0c5c5e5eb4a40f2d68e7047e4edfa4d",
}
class ContractError(AssertionError): pass
def req(x:bool,m:str):
    if not x: raise ContractError(m)
def load(p:Path)->dict[str,Any]: return json.loads(p.read_text(encoding="utf-8"))
def validate(c:dict[str,Any])->list[str]:
    out=[]
    def ok(x,n): req(bool(x),n); out.append(n)
    ok(c["schema_version"]=="football3-nextgen-gk-young-transfer-prereg-contract-v1","schema")
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
    ok(x["candidate_id"]=="V3-C4-GK-YOUNG-CROSS-LEAGUE-TRANSFER","candidate_id")
    ok((x["status"],x["weight"],x["matrix_delta"])==("NOT_AVAILABLE",0,0),"inactive")
    ok(not x["activation_allowed"] and not x["training_allowed"] and not x["tuning_allowed"] and not x["target_label_access_allowed"],"candidate_fail_closed")
    ok(x["post_cutoff_player_or_transfer_evidence_allowed"] is False and x["partial_league_rescue_allowed"] is False,"no_late_no_partial_rescue")
    ok(all(v is False for v in c["zero_label_audit"].values()),"zero_label")
    up=c["upstream_dependency"]
    ok(up["blob_sha"]==EXPECTED_BLOBS[up["path"]],"upstream_blob")
    ok(up["candidate_status"]=="STOP_DATA_COVERAGE","upstream_stop")
    e=c["repository_evidence"]
    g=e["goalkeeper_history_implementation"]
    ok(g["blob_sha"]==EXPECTED_BLOBS[g["path"]],"gk_impl_blob")
    ok(g["uses_same_season_prior_lineups_only"] and g["target_actual_lineup_excluded"],"gk_prior_only")
    ok(g["position_source_current_profile_role_only"] and not g["is_current_nextgen_matrix_residual"],"gk_role_limit")
    gd=e["goalkeeper_discovery_receipt"]
    ok(gd["blob_sha"]==EXPECTED_BLOBS[gd["path"]],"gk_discovery_blob")
    ok(gd["viewed_season"]=="2025/26" and gd["formal_weight_change"] is False and gd["fresh_confirmation"] is False,"gk_discovery_not_fresh")
    gr=e["goalkeeper_replication_receipt"]
    ok(gr["blob_sha"]==EXPECTED_BLOBS[gr["path"]],"gk_rep_blob")
    ok(gr["replication_count"]==100 and gr["viewed_season"]=="2025/26" and gr["fresh_confirmation"] is False,"gk_rep_not_fresh")
    pa=e["player_age_and_value_readiness"]
    ok(pa["blob_sha"]==EXPECTED_BLOBS[pa["path"]],"age_blob")
    ok(pa["license"]=="CC0-1.0" and pa["players_rows"]==50149 and "date_of_birth" in pa["player_fields_include"],"age_source")
    ok(pa["status"]=="PARTIAL" and pa["passed_domains"]==["GER_Bundesliga","ITA_SerieA"],"age_value_partial")
    ok(pa["historical_squad_lineup_identity_is_separate_gate"] and pa["current_profile_fields_are_not_historical_availability_proof"],"age_not_availability")
    dt=e["dated_transfer_contract"]
    ok(dt["blob_sha"]==EXPECTED_BLOBS[dt["path"]],"transfer_contract_blob")
    ok(dt["transfer_file"]=="transfers.csv.gz" and "transfer_date strictly before target date" in dt["transfer_rule"],"dated_transfer")
    ok(dt["same_competition_prior_only"] and dt["cross_competition_strength_borrowing"] is False,"cross_league_disabled")
    ok(dt["has_historical_source_observed_at_contract"] is False and dt["has_historical_available_at_contract"] is False,"transfer_time_gap")
    ti=e["dated_transfer_coverage_implementation"]
    ok(ti["blob_sha"]==EXPECTED_BLOBS[ti["path"]],"transfer_impl_blob")
    ok(ti["transfer_timestamp_consumed"]=="transfer_date" and ti["transfer_source_observed_at_consumed"] is False and ti["transfer_available_at_consumed"] is False,"transfer_only_event_time")
    tr=e["dated_transfer_coverage_receipt"]
    ok(tr["blob_sha"]==EXPECTED_BLOBS[tr["path"]],"coverage_blob")
    ok((tr["competition_count_requested"],tr["competition_count_reported"])==(17,17),"coverage_17")
    ok((tr["chronological_oof_ready_count"],tr["stage_adapter_required_count"],tr["partial_or_unavailable_count"])==(10,6,1),"coverage_split")
    ok(tr["partial_or_unavailable"]==["JPN_J1"] and tr["all_17_directly_ready"] is False,"jpn_gap")
    old=e["legacy_dynamic_strength_oof"]
    ok(old["blob_sha"]==EXPECTED_BLOBS[old["path"]],"old_oof_blob")
    ok((old["competition_count_requested"],old["competition_count_built"],old["competition_count_failed"])==(10,8,2),"old_oof_counts")
    ok(old["contains_viewed_2025_26_outer_folds"] and old["reuse_as_fresh_confirmation"] is False,"old_oof_not_fresh")
    p=c["provider_contract_gap"]
    ok(set(p["architecture_requires_per_source"])=={"source","schema","source_identity","observed_at","available_at","event_time","content_sha","coverage","revision_lineage"},"provider_fields")
    ok(p["current_transfer_event_time"]=="transfer_date" and p["current_transfer_observed_at"] is None and p["current_transfer_available_at"] is None,"provider_time_gap")
    ok(p["historical_event_date_may_not_be_backfilled_as_known_at"] and p["current_public_retrievability_does_not_prove_historical_availability"],"no_backfill")
    failures=set(c["coverage_gate"]["current_failures"])
    required={"TRANSFER_SOURCE_OBSERVED_AT_NOT_PROVEN","TRANSFER_AVAILABLE_AT_NOT_PROVEN","CROSS_COMPETITION_STRENGTH_BORROWING_CURRENTLY_DISABLED","ONLY_10_OF_17_DOMAINS_DIRECT_CHRONOLOGICAL_OOF_READY","SIX_DOMAINS_REQUIRE_STAGE_ADAPTER","JPN_J1_PUBLIC_EVIDENCE_PARTIAL_OR_UNAVAILABLE","TARGET_SIDE_T15_PLAYER_XI_AVAILABILITY_NOT_READY_FROM_CANDIDATE3","GK_2025_26_DISCOVERY_AND_REPLICATION_ALREADY_VIEWED_NOT_FRESH_CONFIRMATION","LEGACY_DYNAMIC_STRENGTH_OOF_ALREADY_VIEWED_NOT_FRESH_CONFIRMATION"}
    ok(required.issubset(failures),"coverage_failures")
    ok(c["coverage_gate"]["partial_ready_domains_may_not_be_selected_after_old_results"] and c["coverage_gate"]["legacy_positive_subsets_may_not_define_new_scope"],"no_posthoc_scope")
    ok(c["coverage_gate"]["result"]=="STOP_DATA_COVERAGE","coverage_result")
    r=c["future_reopen_contract"]
    ok(all(r.values()),"future_reopen_all")
    s=c["conditional_scientific_preregistration"]
    ok(s["activation_state_now"]=="INACTIVE_STOPPED" and s["family"]=="bounded_personnel_transition_matrix_residual","family")
    ok(s["maximum_free_parameter_count"]==1 and s["single_scale_parameter_name"]=="kappa","one_parameter_max")
    ok(s["subsignal_definitions_and_fixed_combination_must_be_frozen_in_a_future_zero_label_DATA_READY_amendment"],"future_signal_freeze")
    ok(not s["league_specific_parameter_allowed"] and not s["manual_player_adjustment_allowed"],"no_special_params")
    ok(not s["post_hoc_age_threshold_selection_allowed"] and not s["post_hoc_gk_threshold_selection_allowed"] and not s["post_hoc_transfer_threshold_selection_allowed"] and not s["post_hoc_league_selection_allowed"],"no_posthoc")
    ok(not s["old_viewed_2025_26_samples_allowed_as_fresh_confirmation"],"old_not_fresh")
    ok(s["chronological_oos_required"] and not s["random_split"] and s["unified_score_matrix_only"] and not s["direct_final_1x2_override_allowed"],"matrix_oos")
    ok(s["independent_confirmation_required"] and s["prospective_required_before_promotion"] and not s["optional_stopping"],"confirmation_prospective")
    f=c["fallback_contract"]
    ok("NOT_AVAILABLE" in f["missing_or_unsafe_candidate_input"] and "V2 fallback" in f["missing_or_unsafe_candidate_input"],"exact_v2_fallback")
    ok(f["fill_zero_for_missing_signal"] is False and f["manual_estimation"] is False and f["silent_v1_switch"] is False and f["second_provider_chain"] is False,"fallback_fail_closed")
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
    ap=argparse.ArgumentParser(); ap.add_argument("--contract",default="governance/football3/nextgen_gk_young_transfer_prereg_contract_v1.json"); ap.add_argument("--repo-root",default="."); ap.add_argument("--skip-repo-blobs",action="store_true"); a=ap.parse_args()
    checks=validate(load(Path(a.contract))); blobs=[] if a.skip_repo_blobs else validate_blobs(Path(a.repo_root))
    print(json.dumps({"status":"STOP_DATA_COVERAGE","contract_checks":len(checks),"repo_blob_checks":len(blobs),"target_labels_read":False,"training_performed":False,"tuning_performed":False,"candidate_status":"NOT_AVAILABLE","weight":0,"matrix_delta":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
