from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
from typing import Any

EXPECTED_BASE="ddf8e38a005cc183067a097ed037aa50b6e66df1"
EXPECTED_BRANCH="football3/nextgen-dynamic-gating-prereg-v1"
EXPECTED_FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
EXPECTED_CURRENT_SHA256="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_BLOBS={
"governance/football3/nextgen_current_v2_foundation_contract_v1.json":"897684e9655f7123dd2c338470a64c4837819639",
"governance/football3/nextgen_current_v2_foundation_v1.py":"eebb4aa9747794923a91e26cd36ab6e63b744f6f",
"governance/football3/test_nextgen_current_v2_foundation_v1.py":"e063f42f3820beb23d6b6def1c66543246676567",
"governance/football3/nextgen_market_pit_prereg_contract_v1.json":"863cadbd7743933a888e035f6ed4a6d170e636c4",
"governance/football3/nextgen_dynamic_strength_prereg_contract_v1.json":"e2df5ef84629049a3f346c98da349420de898a1e",
"governance/football3/nextgen_starting_xi_delta_prereg_contract_v1.json":"f8006e500e33cc74ac8c6b9f8fba32cb6ac446e0",
"governance/football3/nextgen_gk_young_transfer_prereg_contract_v1.json":"cc35ba5da918e5fc1438059396284a8ffaac3278",
"governance/football3/nextgen_total_goals_expert_prereg_contract_v1.json":"0d5930d8a58ccc6931a5fddf14b9bc62dc8fbee6",
"governance/football3/nextgen_tactical_matchup_prereg_contract_v1.json":"78fb2c2adda89cae61cf9d5c7ebfe1ac77f57443",
}
EXPECTED_CANDIDATES=(
"V3-C1-PIT-MARKET-TOTALS",
"V3-C2-DYNAMIC-STRENGTH-COLD-START",
"V3-C3-STARTING-XI-OPPONENT-XI-DELTA",
"V3-C4-GK-YOUNG-CROSS-LEAGUE-TRANSFER",
"V3-C5-NONMARKET-TOTAL-GOALS-EXPERT",
"V3-C6-SINGLE-LOW-DIMENSIONAL-TACTICAL-MATCHUP",
)
class ContractError(AssertionError): pass
def req(x:bool,m:str):
    if not x: raise ContractError(m)
def load(p:Path)->dict[str,Any]: return json.loads(p.read_text(encoding="utf-8"))
def validate(c:dict[str,Any])->list[str]:
    out=[]
    def ok(x,n): req(bool(x),n); out.append(n)
    ok(c["schema_version"]=="football3-nextgen-dynamic-gating-prereg-contract-v1","schema")
    ok(c["project_id"]=="football3","project")
    ok(c["architecture_authority"]=="Football3 V3 下一代升级方案","authority")
    ok(c["status"]=="STOP_DATA_COVERAGE","stop")
    ok(c["stop_class"]=="UPSTREAM_ACTIVE_EXPERT_DEPENDENCY_EMPTY","stop_class")
    ok(c["canonical_integration"]["exact_base"]==EXPECTED_BASE,"base")
    ok(c["research_branch"]==EXPECTED_BRANCH,"branch")
    ok(c["formal_baseline"]["head"]==EXPECTED_FORMAL_HEAD,"formal_head")
    ok((c["formal_baseline"]["xg_weight"],c["formal_baseline"]["frozen_v1_weight"])==(0.75,0.25),"formal_weights")
    ok(c["formal_baseline"]["changed"] is False,"formal_unchanged")
    ok(c["current_authority"]["current_sha256"]==EXPECTED_CURRENT_SHA256 and c["current_authority"]["changed"] is False,"current_unchanged")
    x=c["candidate"]
    ok(x["candidate_id"]=="V3-C7-LOW-FREE-DIMENSIONAL-DYNAMIC-GATING","candidate_id")
    ok((x["status"],x["weight"],x["matrix_delta"])==("NOT_AVAILABLE",0,0),"inactive")
    ok(not x["activation_allowed"] and not x["training_allowed"] and not x["tuning_allowed"] and not x["new_target_label_access_allowed"],"candidate_fail_closed")
    ok(not x["legacy_sealed_label_access_allowed"] and not x["gate_may_assign_nonzero_weight_now"] and not x["gate_function_defined_now"],"no_gate_now")
    ok(not x["manual_expert_activation_allowed"] and not x["posthoc_expert_selection_allowed"],"no_manual_posthoc")
    ok(all(v is False for v in c["zero_label_audit"].values()),"zero_label")
    f=c["foundation_dependency"]
    ok(f["contract_blob_sha"]==EXPECTED_BLOBS[f["contract_path"]],"foundation_contract_blob")
    ok(f["implementation_blob_sha"]==EXPECTED_BLOBS[f["implementation_path"]],"foundation_impl_blob")
    ok(f["permanent_test_blob_sha"]==EXPECTED_BLOBS[f["permanent_test_path"]],"foundation_test_blob")
    ok((f["inactive_status"],f["inactive_weight"],f["inactive_matrix_delta"])==("NOT_AVAILABLE",0,0),"foundation_inactive")
    ok(f["dynamic_gate_may_assign_nonzero_weight"] is False,"foundation_gate_zero")
    ok(f["all_experts_inactive_returns_current_v2_object_unchanged"],"foundation_v2_identity")
    ok(f["implementation_rejects_nonzero_weight_for_unactivated_expert"] and f["implementation_rejects_ACTIVE_status_for_unactivated_expert"],"foundation_rejects_activation")
    ok(f["current_candidate_may_not_modify_foundation_activation_semantics"],"foundation_scope_locked")
    regs=c["upstream_candidate_registry"]
    ok(len(regs)==6,"six_upstream")
    ok(tuple(r["candidate_id"] for r in regs)==EXPECTED_CANDIDATES,"candidate_order")
    for i,r in enumerate(regs,1):
        ok(r["blob_sha"]==EXPECTED_BLOBS[r["path"]],f"c{i}_blob")
        ok(r["terminal_status"]=="STOP_DATA_COVERAGE",f"c{i}_stop")
        ok((r["expert_status"],r["weight"],r["matrix_delta"])==("NOT_AVAILABLE",0,0),f"c{i}_inactive")
    d=c["dependency_audit"]
    ok((d["upstream_candidate_count"],d["active_expert_count"],d["nonzero_weight_eligible_expert_count"],d["nonzero_matrix_delta_expert_count"])==(6,0,0,0),"dependency_counts")
    ok(d["minimum_active_experts_required_before_dynamic_gating_research"]==2,"minimum_two")
    ok(d["current_dependency_satisfied"] is False and d["no_training_target_exists_for_relative_expert_weighting"],"dependency_unsatisfied")
    ok(d["baseline_only_output_must_remain_exact_current_v2"],"baseline_identity")
    l=c["legacy_boundary"]
    ok(l["legacy_pr"]==340 and l["classification"]=="LEGACY_POST_VIEW_PROTOTYPE","legacy_boundary")
    ok(l["wholesale_cherry_pick_allowed"] is False and l["promotion_allowed"] is False,"legacy_forbidden")
    failures=set(c["coverage_gate"]["current_failures"])
    required={"UPSTREAM_ACTIVE_EXPERT_COUNT_ZERO","ALL_CANDIDATES_1_TO_6_TERMINAL_STOP_DATA_COVERAGE","FOUNDATION_FORBIDS_NONZERO_WEIGHT_FOR_NOT_AVAILABLE_EXPERTS","FOUNDATION_RUNTIME_REJECTS_ACTIVE_STATUS_FOR_UNACTIVATED_EXPERTS","NO_LEGAL_GATE_TRAINING_TARGET_WITHOUT_ELIGIBLE_EXPERT_PREDICTIONS","LEGACY_POST_VIEW_PROTOTYPE_MAY_NOT_SUPPLY_CURRENT_GATE_LOGIC"}
    ok(required.issubset(failures),"coverage_failures")
    ok(c["coverage_gate"]["result"]=="STOP_DATA_COVERAGE","coverage_result")
    ok(all(c["future_reopen_contract"].values()),"future_reopen")
    s=c["conditional_scientific_preregistration"]
    ok(s["activation_state_now"]=="INACTIVE_STOPPED" and s["family"]=="low_free_dimensional_dynamic_expert_gating","family")
    ok(s["gate_form_now"]=="NOT_DEFINED_BECAUSE_UPSTREAM_EXPERT_SET_IS_EMPTY","gate_undefined")
    ok(s["free_parameter_cap_now"]=="NOT_DEFINED_BECAUSE_UPSTREAM_EXPERT_SET_IS_EMPTY" and s["expert_set_now"]==[],"params_undefined")
    ok(not s["league_specific_gate_allowed"] and not s["manual_expert_override_allowed"],"no_special_gate")
    ok(not s["post_hoc_expert_selection_allowed"] and not s["post_hoc_gate_feature_selection_allowed"],"no_posthoc")
    ok(not s["random_split"] and s["chronological_oos_required_if_reopened"] and s["prospective_confirmation_required_if_reopened"],"future_oos")
    ok(s["unified_score_matrix_only_if_reopened"] and not s["direct_final_1x2_override_allowed"] and not s["optional_stopping"],"matrix_only")
    fb=c["fallback_contract"]
    ok(fb["current_behavior"]=="all experts inactive returns exact current V2 object unchanged" and fb["candidate_gate_effect"]=="NONE","fallback_identity")
    ok(fb["fill_zero_for_missing_expert"] is False and fb["manual_weighting"] is False and fb["silent_v1_switch"] is False and fb["legacy_gate_substitution"] is False and fb["second_provider_chain"] is False,"fallback_fail_closed")
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
    ap=argparse.ArgumentParser(); ap.add_argument("--contract",default="governance/football3/nextgen_dynamic_gating_prereg_contract_v1.json"); ap.add_argument("--repo-root",default="."); ap.add_argument("--skip-repo-blobs",action="store_true"); a=ap.parse_args()
    checks=validate(load(Path(a.contract))); blobs=[] if a.skip_repo_blobs else validate_blobs(Path(a.repo_root))
    print(json.dumps({"status":"STOP_DATA_COVERAGE","stop_class":"UPSTREAM_ACTIVE_EXPERT_DEPENDENCY_EMPTY","contract_checks":len(checks),"repo_blob_checks":len(blobs),"active_expert_count":0,"target_labels_read":False,"training_performed":False,"tuning_performed":False,"candidate_status":"NOT_AVAILABLE","weight":0,"matrix_delta":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
