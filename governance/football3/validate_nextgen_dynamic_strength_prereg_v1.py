from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from typing import Any

EXPECTED_BASE="14411ade968a908de37f53c2a2a24aa7c1c116b0"
EXPECTED_BRANCH="football3/nextgen-dynamic-strength-prereg-v1"
EXPECTED_FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
EXPECTED_CURRENT_SHA256="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_BLOBS={
"football-data/engine/build_team_strengths.py":"f88ce7e76e5f63dab011ea0067061295e6e443aa",
"football-data/config/team_strength_config.json":"063acaeddf0a1db189950c9b363ba28d7f8d9d13",
"football-data/manifests/latest_team_strengths.json":"e7b855bd62f9c01637f574274131189d057a7b0c",
"football-data/engine/platform_core.py":"0a2f3d20ea2746245552653984c6092c253e8272",
"football-data/config/platform_registry.json":"e250d165956393d871684b28bd1251f90a95aaea",
"football-data/config/active_domain_identity_registry_v5532.json":"1f2f0a3ea3da4d6da799b14faafeb6d720c35aef",
"football-data/config/bayesian_dynamic_state_shadow_registry_v501.json":"8c8b1db6e3e243d135e26a03b9fb765708035046",
"football-data/config/clubelo_residual_challenger_v515.json":"8256001fe37aa5b2a7a7599c73703b20a193b070",
"football-data/manifests/clubelo_history_ingest_v515_status.json":"f5d05ef28f1b1fc428caf86b16ba9bed5b823341",
}
class ContractError(AssertionError): pass
def req(x:bool,m:str):
    if not x: raise ContractError(m)
def load(p:Path)->dict[str,Any]: return json.loads(p.read_text(encoding="utf-8"))
def validate(c:dict[str,Any])->list[str]:
    out=[]
    def ok(x,n): req(x,n); out.append(n)
    ok(c["schema_version"]=="football3-nextgen-dynamic-strength-prereg-contract-v1","schema")
    ok(c["status"]=="STOP_DATA_COVERAGE","stop")
    ok(c["canonical_integration"]["exact_base"]==EXPECTED_BASE,"base")
    ok(c["research_branch"]==EXPECTED_BRANCH,"branch")
    ok(c["formal_baseline"]["head"]==EXPECTED_FORMAL_HEAD,"formal_head")
    ok((c["formal_baseline"]["xg_weight"],c["formal_baseline"]["frozen_v1_weight"])==(0.75,0.25),"formal_weights")
    ok(c["current_authority"]["current_sha256"]==EXPECTED_CURRENT_SHA256,"current_sha")
    x=c["candidate"]
    ok((x["status"],x["weight"],x["matrix_delta"])==("NOT_AVAILABLE",0,0),"inactive")
    ok(not x["partial_established_team_only_scope_accepted"],"no_scope_downgrade")
    ok(not x["activation_allowed"] and not x["training_allowed"] and not x["tuning_allowed"] and not x["target_label_access_allowed"],"candidate_fail_closed")
    z=c["zero_label_audit"]
    for k,vv in z.items(): ok(vv is False,f"zero_{k}")
    e=c["repository_evidence"]
    ok(e["team_strength_manifest"]["competition_count_built"]==17 and e["team_strength_manifest"]["total_matches"]==27616,"descriptive_inventory")
    ok(e["team_strength_builder"]["historical_fixture_level_replay_state_present"] is False,"no_fixture_replay")
    ok(e["platform_core"]["cross_competition_club_identity_preserved"] is False,"no_cross_comp_identity")
    ok(e["processed_inventory"]["lower_division_directories_for_big5_cold_start"] is False,"no_lower_divisions")
    ok(e["active_identity_registry"]["cross_competition_promotion_lineage_registry"] is False,"no_promotion_lineage")
    ok(e["legacy_clubelo_ingest"]["fully_passed_domains"]==["ITA_SerieA"],"clubelo_only_italy_pass")
    ok(e["legacy_clubelo_ingest"]["sufficient_as_universal_promoted_cold_start_source"] is False,"clubelo_insufficient")
    failures=set(c["coverage_gate"]["current_failures"])
    for f in ["NO_FIXTURE_LEVEL_HISTORICAL_PIT_TEAM_STRENGTH_STATE","TEAM_ID_IS_COMPETITION_SCOPED_NO_CROSS_COMPETITION_CLUB_ID","NO_GENERAL_PROMOTION_RELEGATION_LINEAGE_REGISTRY","NO_INTERNAL_BIG5_LOWER_DIVISION_PROCESSED_HISTORY","PROMOTED_TEAM_DEFAULT_1500_IS_NOT_EVIDENCE_BASED_COLD_START","LEGACY_CLUBELO_HISTORY_NOT_RELIABLE_OR_COMPLETE_ENOUGH_FOR_TARGET_SCOPE"]: ok(f in failures,"failure_"+f)
    ok(c["coverage_gate"]["partial_established_team_only_reduction_forbidden"] is True,"no_partial_goal_rewrite")
    pit=c["pit_contract_if_future_data_ready"]
    ok(pit["reject_timezone_naive"] and pit["reject_state_cutoff_after_target_cutoff"] and pit["reject_target_match_in_state"] and pit["reject_future_match_in_state"],"pit_fail_closed")
    ok(pit["competition_scoped_hash_alone_sufficient"] is False,"global_club_id_required")
    ok("never fixed zero/1500" in pit["promoted_team_prior"],"no_synthetic_promoted_prior")
    s=c["conditional_scientific_preregistration"]
    ok(s["parameter_count"]==1 and s["parameter"]=="beta" and s["parameter_range"]==[0.0,0.5],"low_freedom")
    ok(s["signal_clip"]==[-2.0,2.0],"signal_clip")
    ok("P_C2(T) equals current V2 P(T) exactly"==s["total_goals_constraint"],"preserve_total")
    ok(not s["league_specific_beta_allowed"] and not s["promoted_team_special_beta_allowed"] and not s["manual_team_patch_allowed"],"no_special_patches")
    ok(not s["post_hoc_competition_selection_allowed"] and not s["post_hoc_cold_start_classification_allowed"],"no_posthoc")
    ok(not s["random_split"] and s["chronological_oos_required"],"temporal_oos")
    ok(s["paired_bootstrap"]=={"resamples":5000,"seed":73000,"ci":0.9},"bootstrap")
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
    ap=argparse.ArgumentParser(); ap.add_argument("--contract",default="governance/football3/nextgen_dynamic_strength_prereg_contract_v1.json"); ap.add_argument("--repo-root",default="."); ap.add_argument("--skip-repo-blobs",action="store_true"); a=ap.parse_args()
    checks=validate(load(Path(a.contract))); blobs=[] if a.skip_repo_blobs else validate_blobs(Path(a.repo_root))
    print(json.dumps({"status":"STOP_DATA_COVERAGE","contract_checks":len(checks),"repo_blob_checks":len(blobs),"target_labels_read":False,"training_performed":False,"tuning_performed":False,"candidate_status":"NOT_AVAILABLE","weight":0,"matrix_delta":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
