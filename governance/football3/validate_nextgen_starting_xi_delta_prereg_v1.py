from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from typing import Any

EXPECTED_BASE="52f860d325f5d981dc665ace88ad9dc98267faaf"
EXPECTED_BRANCH="football3/nextgen-starting-xi-delta-prereg-v1"
EXPECTED_FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
EXPECTED_CURRENT_SHA256="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_BLOBS={
"football-data/engine/ingest_transfermarkt_lineups_v502.py":"5b478976011e59b586538f581c7683f8c38a858e",
"football-data/validation/probable_lineup_route_v502.py":"66f348d5a104e4d995549c539ef8b6e7c1c0cb2a",
"football-data/manifests/player_xi_data_readiness_v502_status.json":"a7491655ff1ead4d64e289b8743b8d1f013e8e01",
"football-data/manifests/probable_lineup_v502_status.json":"4b414d78d3dc8bb858a2ffef4f9767a76eea6c09",
"football-data/manifests/lineup_match_identity_v502_status.json":"10075f09eb04f6eea6b29d15897759d6ae8d08da",
"football-data/config/player_xi_layer_final_registry_v505.json":"1617070aa44ada5cf08ed444c6842cf6716ae05a",
"football-data/manifests/player_xi_gate_adjudication_v503_status.json":"cd1cbe783cac4d8e129d6a2e4e6665a3e296f835",
}
class ContractError(AssertionError): pass
def req(x:bool,m:str):
    if not x: raise ContractError(m)
def load(p:Path)->dict[str,Any]: return json.loads(p.read_text(encoding="utf-8"))
def validate(c:dict[str,Any])->list[str]:
    out=[]
    def ok(x,n): req(x,n); out.append(n)
    ok(c["schema_version"]=="football3-nextgen-starting-xi-delta-prereg-contract-v1","schema")
    ok(c["project_id"]=="football3","project")
    ok(c["status"]=="STOP_DATA_COVERAGE","stop")
    ok(c["canonical_integration"]["exact_base"]==EXPECTED_BASE,"base")
    ok(c["research_branch"]==EXPECTED_BRANCH,"branch")
    ok(c["formal_baseline"]["head"]==EXPECTED_FORMAL_HEAD,"formal_head")
    ok((c["formal_baseline"]["xg_weight"],c["formal_baseline"]["frozen_v1_weight"])==(0.75,0.25),"formal_weights")
    ok(c["current_authority"]["current_sha256"]==EXPECTED_CURRENT_SHA256,"current_sha")
    x=c["candidate"]
    ok(x["candidate_id"]=="V3-C3-STARTING-XI-OPPONENT-XI-DELTA","candidate_id")
    ok((x["status"],x["weight"],x["matrix_delta"])==("NOT_AVAILABLE",0,0),"inactive")
    ok(not x["activation_allowed"] and not x["training_allowed"] and not x["tuning_allowed"] and not x["target_label_access_allowed"],"candidate_fail_closed")
    ok(x["post_cutoff_actual_xi_allowed_as_input"] is False,"no_post_cutoff_actual_xi")
    z=c["zero_label_audit"]
    for k,vv in z.items(): ok(vv is False,f"zero_{k}")
    e=c["repository_evidence"]
    li=e["lineup_ingest"]
    ok(li["blob_sha"]==EXPECTED_BLOBS[li["path"]],"lineup_ingest_blob")
    ok(li["upstream_has_exact_kickoff"] is False and li["upstream_has_lineup_publication_timestamp"] is False,"source_timestamp_gap")
    ok(li["own_match_lineup_pre_kickoff_known_claimed"] is False,"no_false_prematch_claim")
    ok("lagged observed history" in li["permitted_role"],"lagged_history_only")
    lr=e["lineup_shadow_route"]
    ok(lr["blob_sha"]==EXPECTED_BLOBS[lr["path"]],"shadow_route_blob")
    ok(lr["formal_weight"]==0 and lr["target_actual_xi_used_as_input"] is False,"shadow_zero_weight_no_target_xi")
    ok(lr["prior_same_season_lineups_only"] is True,"same_season_prior_only")
    ok(lr["current_target_freeze_semantics"]=="target kickoff, not canonical T-15 cutoff","freeze_not_t15")
    ok(lr["sufficient_as_current_candidate_pit_input_contract"] is False,"shadow_not_candidate_pit")
    dr=e["data_readiness"]
    ok(dr["blob_sha"]==EXPECTED_BLOBS[dr["path"]],"readiness_blob")
    ok(dr["status"]=="PARTIAL_READY_FOR_LINEUP_ONLY_SHADOW_TRAINING","readiness_status")
    ok(dr["registered_competition_count"]==17 and dr["trainable_lineup_domain_count"]==5,"lineup_domain_counts")
    ok(dr["availability_domain_count"]==0 and dr["full_shadow_candidate_domain_count"]==0,"availability_zero")
    ok(set(dr["trainable_lineup_domains"])=={"ENG_PremierLeague","GER_Bundesliga","ITA_SerieA","FRA_Ligue1","ESP_LaLiga"},"five_lineup_domains")
    ps=e["probable_lineup_shadow_status"]
    ok(ps["blob_sha"]==EXPECTED_BLOBS[ps["path"]],"probable_status_blob")
    ok(ps["validated_shadow_route_count"]==5,"shadow_count")
    ok(ps["formal_weight_change"] is False and ps["probability_change"] is False and ps["automatic_promotion"] is False,"shadow_no_formal_effect")
    ident=e["lineup_match_identity"]
    ok(ident["blob_sha"]==EXPECTED_BLOBS[ident["path"]],"identity_blob")
    ok(len(ident["passed_domains"])==5,"identity_five")
    ok(ident["identity_bridge_is_not_pre_cutoff_availability_evidence"] is True,"identity_not_availability")
    legacy=e["legacy_player_xi_final_registry"]
    ok(legacy["blob_sha"]==EXPECTED_BLOBS[legacy["path"]],"legacy_registry_blob")
    ok(legacy["status"]=="RESEARCH_LAYER_CLOSED_KEEP_FORMAL_WEIGHT_0" and legacy["formal_weight"]==0,"legacy_closed_zero")
    ok(legacy["pit_availability_domains_ready"]==[] and legacy["full_player_xi_latent_domains_ready"]==[],"legacy_no_full_pit")
    ok(legacy["viewed_outer_seasons"]==["2024/25","2025/26"],"viewed_outer_seasons")
    ok(legacy["post_result_projection_scale_search_prohibited_for_promotion"] is True and legacy["reuse_as_fresh_confirmation"] is False,"anti_phacking_freeze")
    ga=e["legacy_gate_adjudication"]
    ok(ga["blob_sha"]==EXPECTED_BLOBS[ga["path"]],"legacy_gate_blob")
    ok(ga["continuity_discovery_domains_passed"]==[] and ga["old_player_signal_not_authorized_for_new_promotion"] is True,"legacy_signal_not_promotable")
    failures=set(c["coverage_gate"]["current_failures"])
    required_failures={"NO_PIT_AVAILABILITY_DOMAIN_READY","HISTORICAL_LINEUP_SOURCE_HAS_NO_EXACT_KICKOFF_OR_PUBLICATION_TIMESTAMP","CURRENT_LINEUP_BACKFILL_IS_LAGGED_OBSERVED_LABEL_EVIDENCE_NOT_TARGET_PREMATCH_INPUT","SHADOW_ROUTE_FREEZE_IS_KICKOFF_NOT_CANONICAL_T_MINUS_15","ONLY_5_OF_17_DOMAINS_HAVE_LINEUP_SHADOW_DATA","LEGACY_PLAYER_XI_OUTER_SEASONS_ALREADY_VIEWED_NOT_FRESH_CONFIRMATION"}
    ok(required_failures.issubset(failures),"coverage_failures")
    ok(c["coverage_gate"]["historical_actual_xi_volume_cannot_substitute_for_pit_current_xi"] is True,"volume_not_pit")
    ok(c["coverage_gate"]["partial_five_league_scope_selection_after_old_results_forbidden"] is True,"no_posthoc_five_league")
    ok(all(c["coverage_gate"]["required"].values()),"required_gate_all_true")
    pit=c["pit_contract_if_future_data_ready"]
    ok(pit["master_cutoff"]=="T-15m","t15")
    ok(set(pit["required_time_fields"])=={"kickoff","cutoff","source_observed_at","available_at","freeze_at"},"time_fields")
    ok(pit["timezone_aware_required"] and pit["observed_at_lte_cutoff"] and pit["available_at_lte_cutoff"],"time_fail_closed")
    ok(pit["official_xi_after_cutoff"]=="reject_as_input","reject_late_official")
    ok("audit only" in pit["final_actual_xi"],"actual_xi_audit_only")
    ok("never relabel as observed" in pit["predicted_xi"],"predicted_not_observed")
    ok("both sides" in pit["home_away_symmetry"],"home_away_symmetry")
    ok("NOT_AVAILABLE" in pit["missing_or_unsafe_input"] and "V2 fallback" in pit["missing_or_unsafe_input"],"exact_v2_fallback")
    s=c["conditional_scientific_preregistration"]
    ok(s["parameter_count"]==1 and s["parameter"]=="gamma" and s["parameter_range"]==[0.0,0.4],"low_freedom")
    ok(s["signal_clip"]==[-2.0,2.0],"signal_clip")
    ok(s["total_goals_constraint"]=="P_C3(T) equals current V2 P(T) exactly","preserve_total")
    ok(not s["league_specific_gamma_allowed"] and not s["player_specific_manual_adjustment_allowed"],"no_special_patches")
    ok(not s["post_hoc_xi_confidence_threshold_selection_allowed"] and not s["post_hoc_league_selection_allowed"],"no_posthoc")
    ok(not s["old_viewed_2024_25_2025_26_seasons_allowed_as_fresh_confirmation"],"old_seasons_not_fresh")
    ok(s["chronological_oos_required"] and not s["random_split"],"temporal_oos")
    ok(s["paired_bootstrap"]=={"resamples":5000,"seed":74000,"ci":0.9},"bootstrap")
    ok(not s["optional_stopping"],"no_optional_stopping")
    ok(all(c["reopen_contract"].values()),"reopen_contract")
    ok(all(c["forbidden_changes"].values()),"forbidden")
    return out

def validate_blobs(root:Path)->list[str]:
    req((root/".git").exists(),"git repo required")
    out=[]
    for p,e in EXPECTED_BLOBS.items():
        g=subprocess.check_output(["git","-C",str(root),"rev-parse",f"{EXPECTED_BASE}:{p}"],text=True).strip(); req(g==e,f"blob {p}: {g} != {e}"); out.append(p)
    return out

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--contract",default="governance/football3/nextgen_starting_xi_delta_prereg_contract_v1.json"); ap.add_argument("--repo-root",default="."); ap.add_argument("--skip-repo-blobs",action="store_true"); a=ap.parse_args()
    checks=validate(load(Path(a.contract))); blobs=[] if a.skip_repo_blobs else validate_blobs(Path(a.repo_root))
    print(json.dumps({"status":"STOP_DATA_COVERAGE","contract_checks":len(checks),"repo_blob_checks":len(blobs),"target_labels_read":False,"training_performed":False,"tuning_performed":False,"candidate_status":"NOT_AVAILABLE","weight":0,"matrix_delta":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
