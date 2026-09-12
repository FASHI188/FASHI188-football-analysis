from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
from typing import Any

EXPECTED_BASE="3b084ede10a6c53f91322915987ebbbfee3d1d60"
EXPECTED_BRANCH="football3/nextgen-total-goals-expert-prereg-v1"
EXPECTED_FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
EXPECTED_CURRENT_SHA256="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_BLOBS={
"football-data/manifests/total_goals_dynamic_v464_status.json":"4703a012037f9a1f3d953d865b63dbb50a730ad0",
"football-data/manifests/total_goals_round2_v465_status.json":"57a7cbf5b196108d0f02840e4515de8021b9249f",
"football-data/manifests/total_goals_joint_integration_v466_status.json":"b3bdd81242b7c637fa96a4ae1f00f26dd5d164fa",
"football-data/validation/v6_total_shot_residual_v6181.py":"fe47fc3fac638dcc48e80f40731eb338faabbd72",
"football-data/manifests/v6_strict_daily_pit_total_v6181c_status.json":"a3408e1127f5d4e95c3112038bd881acf8e5939e",
"football-data/manifests/v6_total_shot_prospective_freeze_v6183r3_status.json":"f3e8d0c1327f608a389a7c2daa656fcd499e5fc0",
"football-data/config/league_sources_batch_001.json":"5e85e44725228333ea2dd4911755e07002f1514c",
"football-data/config/multisource_evidence_v463.json":"dc1d85bd9b51595246b65659aee48f95ff7359ab",
}
class ContractError(AssertionError): pass
def req(x:bool,m:str):
    if not x: raise ContractError(m)
def load(p:Path)->dict[str,Any]: return json.loads(p.read_text(encoding="utf-8"))
def validate(c:dict[str,Any])->list[str]:
    out=[]
    def ok(x,n): req(bool(x),n); out.append(n)
    ok(c["schema_version"]=="football3-nextgen-total-goals-expert-prereg-contract-v1","schema")
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
    ok(x["candidate_id"]=="V3-C5-NONMARKET-TOTAL-GOALS-EXPERT","candidate_id")
    ok((x["status"],x["weight"],x["matrix_delta"])==("NOT_AVAILABLE",0,0),"inactive")
    ok(not x["activation_allowed"] and not x["training_allowed"] and not x["tuning_allowed"] and not x["new_target_label_access_allowed"],"candidate_fail_closed")
    ok(x["market_input_allowed"] is False and x["candidate1_market_totals_reuse_allowed"] is False,"nonmarket_only")
    ok(x["postmatch_current_fixture_stats_allowed"] is False and x["posthoc_domain_selection_allowed"] is False,"no_leak_no_posthoc")
    ok(all(v is False for v in c["zero_label_audit"].values()),"zero_label")
    d=c["legacy_direct_total_evidence"]
    r1=d["round1"]
    ok(r1["blob_sha"]==EXPECTED_BLOBS[r1["path"]],"round1_blob")
    ok((r1["competition_count_requested"],r1["competition_count_built"],r1["passed_count"])==(17,17,7),"round1_counts")
    ok(r1["strictly_prior_selection"] is True,"round1_prior")
    r2=d["round2"]
    ok(r2["blob_sha"]==EXPECTED_BLOBS[r2["path"]],"round2_blob")
    ok((r2["competition_count_requested"],r2["passed_count"])==(7,3),"round2_counts")
    jm=d["joint_matrix"]
    ok(jm["blob_sha"]==EXPECTED_BLOBS[jm["path"]],"joint_blob")
    ok((jm["competition_count_requested"],jm["ready_for_promotion_review_count"])==(10,1),"joint_counts")
    ok(jm["only_ready_domain"]=="JPN_J1" and jm["old_single_domain_success_may_not_define_new_scope"],"no_jpn_rescue")
    ok(jm["formal_weight"]==0,"joint_zero_weight")
    s=c["legacy_shot_total_evidence"]
    impl=s["implementation"]
    ok(impl["blob_sha"]==EXPECTED_BLOBS[impl["path"]],"shot_impl_blob")
    ok(impl["target"]=="P(T=0..6,7+)" and impl["current_match_event_stats_used"] is False,"shot_target_pit")
    pit=s["strict_daily_pit_repair"]
    ok(pit["blob_sha"]==EXPECTED_BLOBS[pit["path"]],"strict_blob")
    ok(pit["status"]=="PASS" and pit["classification"]=="STRICT_DAILY_PIT_RETROSPECTIVE_OOS_REPAIR","strict_status")
    ok(pit["formal_current_version"]=="V5.0.1","strict_old_baseline")
    ok((pit["all_rows"],pit["train_rows"],pit["validation_rows"],pit["test_rows"])==(9184,4626,2279,2279),"strict_rows")
    ok(pit["same_date_history_frozen"] and pit["2025_26_is_viewed_retrospective_evidence"] and pit["promotion_grade_prospective_evidence"] is False,"strict_not_prospective")
    fr=s["prospective_freeze"]
    ok(fr["blob_sha"]==EXPECTED_BLOBS[fr["path"]],"freeze_blob")
    ok(fr["status"]=="PASS" and fr["formal_current_version"]=="V5.0.1","freeze_old_baseline")
    ok(fr["target_season"]=="2026/27" and fr["frozen_domain_count"]==8 and fr["frozen_C"]==0.01,"freeze_scope")
    ok(fr["no_backfill"] and fr["model_refit_after_freeze_forbidden"],"freeze_no_backfill_refit")
    ok(fr["bound_to_current_formal_v2"] is False and fr["may_be_relabelled_as_current_v3_candidate"] is False,"freeze_not_current")
    src=c["source_contract_audit"]
    reg=src["source_registry"]
    ok(reg["blob_sha"]==EXPECTED_BLOBS[reg["path"]],"source_registry_blob")
    ok(reg["primary_source"]=="Football-Data.co.uk" and reg["historical_match_statistics_available"],"source_identity")
    ok(reg["records_download_timestamp_utc"] and reg["records_source_sha256"],"source_snapshot_metadata")
    ok(reg["row_level_source_observed_at"] is False and reg["row_level_available_at"] is False,"row_time_gap")
    mg=src["multisource_governance"]
    ok(mg["blob_sha"]==EXPECTED_BLOBS[mg["path"]],"multisource_blob")
    ok(mg["football_data_historical_snapshots"] is False and mg["point_in_time_market_use"]=="retrospective_reference_only","no_historical_snapshots")
    ok(set(src["v3_required_per_source_fields"])=={"source","schema","source_identity","observed_at","available_at","event_time","content_sha","coverage","revision_lineage"},"v3_source_fields")
    ok(src["current_historical_shot_event_time"]=="match calendar date" and src["current_historical_shot_observed_at"] is None and src["current_historical_shot_available_at"] is None,"shot_time_gap")
    ok(src["download_time_may_not_be_backfilled_as_historical_available_at"],"no_download_backfill")
    ok(src["strict_calendar_day_freeze_reduces_same_day_leakage_but_does_not_create_missing_source_availability_metadata"],"strict_freeze_not_timestamp")
    failures=set(c["coverage_gate"]["current_failures"])
    required={"HISTORICAL_SHOT_STAT_SOURCE_OBSERVED_AT_NOT_PROVEN","HISTORICAL_SHOT_STAT_SOURCE_AVAILABLE_AT_NOT_PROVEN","LEGACY_PROSPECTIVE_FREEZE_BOUND_TO_FORMAL_CURRENT_V5_0_1_NOT_CURRENT_FORMAL_V2","LEGACY_FREEZE_CANNOT_BE_REFIT_AND_STILL_CLAIM_SAME_PRISTINE_FREEZE","PAST_2026_27_FIXTURES_MAY_NOT_BE_BACKFILLED","LEGACY_DIRECT_TOTAL_ROUND1_ROUND2_AND_JOINT_RESULTS_ALREADY_VIEWED","JPN_J1_OLD_SINGLE_DOMAIN_SUCCESS_MAY_NOT_DEFINE_NEW_SCOPE","MARKET_OU_TOTAL_ROUTE_BELONGS_TO_STOPPED_CANDIDATE1_AND_IS_NOT_AN_ORTHOGONAL_CANDIDATE5_INPUT"}
    ok(required.issubset(failures),"coverage_failures")
    ok(c["coverage_gate"]["result"]=="STOP_DATA_COVERAGE","coverage_result")
    rr=c["future_reopen_contract"]
    ok(all(rr.values()),"reopen_all")
    cs=c["conditional_scientific_preregistration"]
    ok(cs["activation_state_now"]=="INACTIVE_STOPPED" and cs["family"]=="bounded_nonmarket_direct_total_residual","family")
    ok(cs["target"]=="P(T=0..6,7+)" and cs["preferred_orthogonal_signal_family"]=="strictly lagged nonmarket shots/SOT/corners","orthogonal_family")
    ok(cs["maximum_free_parameter_count"]==1 and cs["single_regularization_or_scale_parameter_must_be_frozen_before_labels"],"low_freedom")
    ok(not cs["league_specific_parameter_allowed"] and not cs["manual_domain_weight_allowed"],"no_special_weights")
    ok(not cs["post_hoc_feature_selection_allowed"] and not cs["post_hoc_domain_selection_allowed"],"no_posthoc")
    ok(not cs["old_round1_round2_joint_results_allowed_as_fresh_confirmation"] and not cs["old_2025_26_shot_total_result_allowed_as_fresh_confirmation"],"old_not_fresh")
    ok(not cs["legacy_v5_0_1_frozen_model_allowed_as_current_v2_model_without_refit"],"old_model_not_current")
    ok(cs["chronological_oos_required"] and cs["prospective_confirmation_required"] and not cs["random_split"],"oos_prospective")
    ok(cs["unified_score_matrix_only"] and not cs["direct_final_1x2_override_allowed"],"matrix_only")
    ok(not cs["optional_stopping"],"no_optional_stopping")
    fb=c["fallback_contract"]
    ok("NOT_AVAILABLE" in fb["missing_or_unsafe_candidate_input"] and "V2 fallback" in fb["missing_or_unsafe_candidate_input"],"v2_fallback")
    ok(fb["fill_zero_for_missing_signal"] is False and fb["manual_estimation"] is False and fb["silent_v1_switch"] is False and fb["market_substitution"] is False and fb["second_provider_chain"] is False,"fallback_fail_closed")
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
    ap=argparse.ArgumentParser(); ap.add_argument("--contract",default="governance/football3/nextgen_total_goals_expert_prereg_contract_v1.json"); ap.add_argument("--repo-root",default="."); ap.add_argument("--skip-repo-blobs",action="store_true"); a=ap.parse_args()
    checks=validate(load(Path(a.contract))); blobs=[] if a.skip_repo_blobs else validate_blobs(Path(a.repo_root))
    print(json.dumps({"status":"STOP_DATA_COVERAGE","contract_checks":len(checks),"repo_blob_checks":len(blobs),"target_labels_read":False,"training_performed":False,"tuning_performed":False,"candidate_status":"NOT_AVAILABLE","weight":0,"matrix_delta":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
