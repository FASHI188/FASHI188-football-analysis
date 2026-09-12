from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
from typing import Any

EXPECTED_BASE="102c5cf6ff7b98bd0be0715643c7f936900a756a"
EXPECTED_BRANCH="football3/nextgen-selected-publishing-prereg-v1"
EXPECTED_FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
EXPECTED_CURRENT_SHA256="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_RECEIPT_DISTRIBUTION_BLOB="2c5be3fa953cc9848f331cf8a2bb3600f93b420e"
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
"governance/football3/nextgen_dynamic_gating_prereg_contract_v1.json":"215015a7664fda4b7b788513302d29e9e280bed8",
}
EXPECTED_CANDIDATES=(
"V3-C1-PIT-MARKET-TOTALS","V3-C2-DYNAMIC-STRENGTH-COLD-START","V3-C3-STARTING-XI-OPPONENT-XI-DELTA","V3-C4-GK-YOUNG-CROSS-LEAGUE-TRANSFER","V3-C5-NONMARKET-TOTAL-GOALS-EXPERT","V3-C6-SINGLE-LOW-DIMENSIONAL-TACTICAL-MATCHUP","V3-C7-LOW-FREE-DIMENSIONAL-DYNAMIC-GATING")
EXPECTED_RESERVED=("nextgen_foundation_schema","nextgen_research_status","nextgen_expert_statuses","nextgen_ablation","nextgen_capability","nextgen_data_quality","nextgen_matrix_delta_summary")
class ContractError(AssertionError): pass
def req(x:bool,m:str):
    if not x: raise ContractError(m)
def load(p:Path)->dict[str,Any]: return json.loads(p.read_text(encoding="utf-8"))
def validate(c:dict[str,Any])->list[str]:
    out=[]
    def ok(x,n): req(bool(x),n); out.append(n)
    ok(c["schema_version"]=="football3-nextgen-selected-publishing-prereg-contract-v1","schema")
    ok(c["project_id"]=="football3","project")
    ok(c["architecture_authority"]=="Football3 V3 下一代升级方案","authority")
    ok(c["phase"]=="CANDIDATE_8_ZERO_LABEL_SELECTED_PUBLISHING_LAYER_PREREGISTRATION","phase")
    ok(c["status"]=="RESEARCH_INFRASTRUCTURE_READY_BASELINE_ONLY","status")
    ok(c["canonical_integration"]["exact_base"]==EXPECTED_BASE,"base")
    ok(c["research_branch"]==EXPECTED_BRANCH,"branch")
    ok(c["formal_baseline"]["head"]==EXPECTED_FORMAL_HEAD,"formal_head")
    ok((c["formal_baseline"]["xg_weight"],c["formal_baseline"]["frozen_v1_weight"])==(0.75,0.25),"formal_weights")
    ok(c["formal_baseline"]["changed"] is False,"formal_unchanged")
    ok(c["current_authority"]["current_sha256"]==EXPECTED_CURRENT_SHA256 and c["current_authority"]["changed"] is False,"current_unchanged")
    p=c["publishing_layer"]
    ok(p["candidate_id"]=="V3-C8-SELECTED-PUBLISHING-LAYER" and p["is_expert"] is False,"layer_id")
    ok((p["candidate_status"],p["weight"],p["matrix_delta"])==("NOT_AVAILABLE",0,0),"inactive")
    ok(not p["activation_allowed"] and not p["training_allowed"] and not p["tuning_allowed"] and not p["new_target_label_access_allowed"] and not p["legacy_sealed_label_access_allowed"],"fail_closed")
    ok(p["publication_mode"]=="BASELINE_ONLY_RESEARCH_NOOP","mode")
    ok(p["selected_experts"]==[] and p["active_expert_count"]==0 and p["selected_expert_count"]==0,"empty_selection")
    ok(p["research_governance_receipt_allowed"] and not p["formal_production_publish_allowed"],"research_only")
    ok(not p["formal_receipt_mutation_allowed"] and not p["formal_distribution_mutation_allowed"],"formal_receipt_locked")
    ok(not p["formal_output_mutated"] and not p["production_chain_created"],"no_production_chain")
    ok(all(v is False for v in c["zero_label_audit"].values()),"zero_label")
    f=c["foundation_dependency"]
    ok(f["contract_blob_sha"]==EXPECTED_BLOBS[f["contract_path"]],"foundation_contract_blob")
    ok(f["implementation_blob_sha"]==EXPECTED_BLOBS[f["implementation_path"]],"foundation_impl_blob")
    ok(f["permanent_test_blob_sha"]==EXPECTED_BLOBS[f["permanent_test_path"]],"foundation_test_blob")
    ok((f["inactive_status"],f["inactive_weight"],f["inactive_matrix_delta"])==("NOT_AVAILABLE",0,0),"foundation_inactive")
    ok(f["dynamic_gate_may_assign_nonzero_weight"] is False and f["all_experts_inactive_returns_current_v2_object_unchanged"],"foundation_fallback")
    ok(f["complete_score_matrix_unchanged"] and f["one_x_two_unchanged"] and f["prediction_sha_semantics_unchanged"],"foundation_output_identity")
    ok(f["route_unchanged"] and f["fallback_unchanged"] and f["selector_unchanged"] and f["state_unchanged"],"foundation_route_identity")
    ok(f["second_production_chain"] is False,"foundation_no_second_chain")
    b=c["formal_receipt_distribution_boundary"]
    ok(b["protected_blob_sha"]==EXPECTED_RECEIPT_DISTRIBUTION_BLOB,"receipt_blob")
    ok(b["binding_authority"]=="FOUNDATION_IMMUTABLE_FORMAL_SURFACES" and b["object_type"]=="blob","receipt_binding")
    ok(b["working_tree_path_required"] is False and b["schema_marker"]=="football3-formal-receipt-distribution-contract-v1","receipt_object_semantics")
    ok(b["additive_receipt_contract_only"] and b["prediction_sha_preserved"] and b["prediction_probability_object_changed"] is False,"receipt_prediction_identity")
    ok(b["formal_distribution_fields_derived_from_existing_formal_distribution_only"] and b["candidate8_may_edit_or_replace"] is False,"receipt_distribution_locked")
    regs=c["upstream_candidate_registry"]
    ok(len(regs)==7 and tuple(r["candidate_id"] for r in regs)==EXPECTED_CANDIDATES,"seven_upstream")
    for i,r in enumerate(regs,1):
        ok(r["blob_sha"]==EXPECTED_BLOBS[r["path"]],f"c{i}_blob")
        ok(r["terminal_status"]=="STOP_DATA_COVERAGE",f"c{i}_stop")
        ok((r["expert_status"],r["weight"],r["matrix_delta"])==("NOT_AVAILABLE",0,0),f"c{i}_inactive")
    s=c["selection_audit"]
    ok((s["upstream_candidate_count"],s["active_expert_count"],s["selected_expert_count"])==(7,0,0),"selection_counts")
    ok(s["selected_experts"]==[] and s["selection_performed"] is False,"no_selection")
    ok(s["baseline_only_required"] and s["baseline_only_output_must_remain_exact_current_v2"],"baseline_only")
    ok(s["zero_is_missing_evidence_not_negative_evidence"],"zero_semantics")
    r=c["research_publication_contract"]
    ok(r["surface"]=="SEPARATE_RESEARCH_GOVERNANCE_RECEIPT_ONLY","research_surface")
    ok(r["formal_receipt_is_not_the_publication_surface"] and r["formal_distribution_extension_is_not_the_publication_surface"],"formal_surface_excluded")
    ok(r["selected_experts"]==[] and r["fallback_to_current_v2"],"research_empty_selection")
    identity=("prediction_object_must_equal_current_v2","prediction_sha_must_equal_current_v2","score_matrix_must_equal_current_v2","one_x_two_must_equal_current_v2","route_must_equal_current_v2","fallback_must_equal_current_v2","selector_must_equal_current_v2","state_must_equal_current_v2","formal_weights_must_equal_current_v2","research_metadata_out_of_band_only")
    ok(all(r[k] for k in identity),"research_identity")
    ok(tuple(r["allowed_foundation_reserved_fields"])==EXPECTED_RESERVED,"reserved_fields")
    ok(r["must_not_claim_formal_production_receipt"] and r["must_not_claim_nextgen_activation"],"claim_boundary")
    ok(all(c["future_reopen_contract"].values()),"future_reopen")
    ok(all(c["forbidden_changes"].values()),"forbidden")
    return out

def validate_blobs(root:Path)->list[str]:
    req((root/".git").exists(),"git repo required")
    out=[]
    for p,e in EXPECTED_BLOBS.items():
        got=subprocess.check_output(["git","-C",str(root),"rev-parse",f"{EXPECTED_BASE}:{p}"],text=True).strip()
        req(got==e,f"blob {p}: {got} != {e}"); out.append(p)
    subprocess.check_call(["git","-C",str(root),"cat-file","-e",f"{EXPECTED_RECEIPT_DISTRIBUTION_BLOB}^{{blob}}"])
    text=subprocess.check_output(["git","-C",str(root),"cat-file","blob",EXPECTED_RECEIPT_DISTRIBUTION_BLOB],text=True)
    req('football3-formal-receipt-distribution-contract-v1' in text,"receipt distribution schema marker missing")
    req('prediction_sha_preserved' in text and 'prediction_probability_object_changed' in text,"receipt distribution preservation markers missing")
    out.append(EXPECTED_RECEIPT_DISTRIBUTION_BLOB)
    return out

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--contract",default="governance/football3/nextgen_selected_publishing_prereg_contract_v1.json"); ap.add_argument("--repo-root",default="."); ap.add_argument("--skip-repo-blobs",action="store_true"); a=ap.parse_args()
    checks=validate(load(Path(a.contract))); blobs=[] if a.skip_repo_blobs else validate_blobs(Path(a.repo_root))
    print(json.dumps({"status":"RESEARCH_INFRASTRUCTURE_READY_BASELINE_ONLY","contract_checks":len(checks),"repo_blob_checks":len(blobs),"candidate_status":"NOT_AVAILABLE","is_expert":False,"selected_experts":[],"active_expert_count":0,"weight":0,"matrix_delta":0,"target_labels_read":False,"training_performed":False,"tuning_performed":False,"formal_output_mutated":False,"production_chain_created":False},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
