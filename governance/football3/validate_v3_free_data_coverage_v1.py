from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from typing import Any
from v3_free_data_coverage_probe_v1 import REQUIRED_CATEGORIES, full_probe

EXACT_BASE="02343081400618660958cdb4d960d014046aa41b"
BRANCH="football3/v3-free-data-coverage-v1"
FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
CURRENT_SHA="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
ALLOWED={
".github/workflows/football3-v3-free-data-coverage-v1.yml",
"governance/football3/v3_free_data_coverage_contract_v1.json",
"governance/football3/v3_free_source_registry_v1.json",
"governance/football3/v3_git_snapshot_adapter_v1.py",
"governance/football3/v3_free_data_coverage_probe_v1.py",
"governance/football3/validate_v3_free_data_coverage_v1.py",
"governance/football3/test_v3_free_data_coverage_v1.py",
}
IMMUTABLE={
"governance/football3/v3_pit_data_foundation_contract_v1.json":"778487281a4cf90b7a739d77c63da6c2259b0241",
"governance/football3/v3_pit_data_source_inventory_v1.json":"3d053ef8c0a0c0c47173d599e140a1e075f51f0a",
"governance/football3/v3_pit_data_provider_interface_v1.py":"8b9bba7b548e61de7f3b79d96881ebe9c3a41b51",
"governance/football3/validate_v3_pit_data_foundation_v1.py":"bf42df4ede376465f13ae36f52c850aa25a608df",
"governance/football3/test_validate_v3_pit_data_foundation_v1.py":"c92b9225bd96212af7260b6321e0feb33457ff5c",
".github/workflows/football3-v3-pit-data-foundation-audit-v1.yml":"88906b67509ebabd285ded31646406520119045a",
"governance/football3/nextgen_current_v2_foundation_contract_v1.json":"897684e9655f7123dd2c338470a64c4837819639",
"governance/football3/nextgen_current_v2_foundation_v1.py":"eebb4aa9747794923a91e26cd36ab6e63b744f6f",
"governance/football3/test_nextgen_current_v2_foundation_v1.py":"e063f42f3820beb23d6b6def1c66543246676567",
"football-data/new_engine_v1/formal_fusion_v2.py":"a5ed26d5ffd4a2875cb9c658cfaa28665a8b7871",
"football-data/new_engine_v1/test_formal_fusion_v2.py":"61b25cd403fa1d9efa0dfcbc1643e0f17621944e",
"football-data/config/formal_model_pointer_historical_xg_fusion_v2.json":"f07743f9c873d415fad9612f3225f64c64b10542",
"football-data/formal_gpt_gateway_v1/entry.py":"b41b0ede2db99c116b90270a32cd04dd240eb655",
}
CANDIDATE_CONTRACTS={
"C1":"governance/football3/nextgen_market_pit_prereg_contract_v1.json",
"C2":"governance/football3/nextgen_dynamic_strength_prereg_contract_v1.json",
"C3":"governance/football3/nextgen_starting_xi_delta_prereg_contract_v1.json",
"C4":"governance/football3/nextgen_gk_young_transfer_prereg_contract_v1.json",
"C5":"governance/football3/nextgen_total_goals_expert_prereg_contract_v1.json",
"C6":"governance/football3/nextgen_tactical_matchup_prereg_contract_v1.json",
"C7":"governance/football3/nextgen_dynamic_gating_prereg_contract_v1.json",
}
class ContractError(AssertionError): pass
def req(x:bool,m:str):
    if not x: raise ContractError(m)
def load(p:Path)->dict[str,Any]: return json.loads(p.read_text(encoding="utf-8"))

def validate(contract:dict[str,Any], registry:dict[str,Any])->list[str]:
    checks=[]
    def ok(x,n): req(bool(x),n); checks.append(n)
    ok(contract["schema_version"]=="football3-v3-free-data-coverage-contract-v1","schema")
    ok(contract["architecture_authority"]=="Football3 V3 下一代升级方案","authority")
    ok(contract["canonical_integration"]["exact_base"]==EXACT_BASE,"base")
    ok(contract["research_branch"]==BRANCH,"branch")
    ok(set(contract["allowed_files"])==ALLOWED and len(contract["allowed_files"])==7,"allowlist")
    fb=contract["formal_baseline"]
    ok(fb["head"]==FORMAL_HEAD and fb["changed"] is False and (fb["xg_weight"],fb["frozen_v1_weight"])==(0.75,0.25),"formal")
    ok(contract["current_authority"]["sha256"]==CURRENT_SHA and contract["current_authority"]["changed"] is False,"current")
    sd=contract["shared_design"]
    ok(sd["second_loader_chain_allowed"] is False and sd["runtime_network_calls_allowed"] is False,"no_second_chain")
    ok(sd["retrieval_time_backfill_forbidden"] and sd["event_time_as_availability_forbidden"] and sd["fuzzy_identity_forbidden"],"pit_failclosed")
    cs=contract["candidate_state"]
    ok(set(cs)=={f"C{i}" for i in range(1,8)},"c1c7")
    ok(all(x["status"]=="NOT_AVAILABLE" and x["weight"]==0 and x["matrix_delta"]==0 for x in cs.values()),"all_inactive")
    ok(cs["C1"]["coverage_status"]=="FREE_SOURCE_EXHAUSTED_FOR_REQUIRED_SURFACE","c1_closed")
    ok(cs["C7"]["coverage_status"]=="BLOCKED_UPSTREAM_ACTIVE_EXPERT_COUNT_0","c7_blocked")
    dg=contract["data_ready_gate"]
    ok(not dg["training_before_data_ready"] and not dg["tuning_before_data_ready"] and not dg["new_target_labels_before_prereg_freeze"],"no_training_labels")
    ok(all(contract["forbidden_changes"].values()),"forbidden")
    ok(registry["schema_version"]=="football3-v3-free-source-registry-v1" and registry["exact_base"]==EXACT_BASE,"registry")
    ok(len(registry["formal_domains"])==17 and len(set(registry["formal_domains"]))==17,"17domains")
    sp=registry["selection_policy"]
    ok(all(sp[x] for x in ("legal_public_free_only","no_login","no_secret","no_paywall","no_manual_per_match_download","strict_pit_required","latest_snapshot_may_not_be_used_for_earlier_cutoff","license_unknown_means_not_selected")),"source_policy")
    src=registry["sources"]
    of=src["openfootball"]
    ok(of["selected"] and of["license"]=="CC0-1.0" and of["license_blob_sha"]=="670154e3538863b2d9891fd5483160fbdfc89164","of_license")
    ok(of["current_direct_path_count"]==8 and of["current_gap_count"]==9,"of_count")
    ok(set(of["current_formal_domain_paths"])|set(of["historical_or_missing_formal_domains"])==set(registry["formal_domains"]),"of_partition")
    ok(all(len(x["commit"])==40 and len(x["blob_sha"])==40 and x["published_at"].endswith("Z") for x in of["current_formal_domain_paths"].values()),"of_pins")
    tm=src["transfermarkt_public_dataset"]
    ok(tm["selected"] and tm["updates_paused"] and tm["snapshot_current_through"]=="2026-07-06","tm_guard")
    ok(src["wikidata_identity"]["selected"] and src["wikidata_identity"]["use"]=="IDENTITY_ONLY_NOT_PERFORMANCE_FEATURE","wikidata_guard")
    sb=src["statsbomb_hudl_open_data"]
    ok(sb["selected"] and sb["coverage"]=="SELECTED_COMPETITIONS_AND_SEASONS_ONLY","sb_guard")
    ok(src["football_data_uk"]["selected"] is False and "LICENSE" in src["football_data_uk"]["reason"],"fd_not_selected")
    matrix=registry["candidate_coverage_matrix"]
    ok(set(matrix)=={f"C{i}" for i in range(1,8)},"matrix_c1c7")
    ok(registry["candidate_coverage_matrix_categories"]==REQUIRED_CATEGORIES,"matrix_categories")
    ok(all(all(k in row for k in REQUIRED_CATEGORIES) for row in matrix.values()),"matrix_complete")
    probe=full_probe(registry)
    ok(probe["data_ready_candidates"]==[] and probe["active_experts"]==[] and probe["selected_experts"]==[],"none_ready")
    ok(probe["status"]=="FREE_DATA_SHARED_INFRA_READY_NO_CANDIDATE_DATA_READY","probe_status")
    ok(not any(probe[k] for k in ("new_target_labels_read","training_performed","tuning_performed","formal_v2_changed","current_changed","production_changed")),"probe_invariants")
    return checks

def validate_repo(root:Path)->list[str]:
    req((root/".git").exists(),"git repo required")
    out=[]
    for p,e in IMMUTABLE.items():
        got=subprocess.check_output(["git","-C",str(root),"rev-parse",f"{EXACT_BASE}:{p}"],text=True).strip()
        req(got==e,f"immutable base blob {p}: {got} != {e}")
        work=subprocess.check_output(["git","-C",str(root),"hash-object",str(root/p)],text=True).strip()
        req(work==e,f"immutable working blob {p}: {work} != {e}")
        out.append(p)
    for cid,p in CANDIDATE_CONTRACTS.items():
        j=load(root/p)
        req(j["status"]=="STOP_DATA_COVERAGE",f"{cid} prior status")
        req(j["candidate"]["status"]=="NOT_AVAILABLE" and j["candidate"]["weight"]==0 and j["candidate"]["matrix_delta"]==0,f"{cid} inactive")
        out.append(p)
    return out

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--contract",default="governance/football3/v3_free_data_coverage_contract_v1.json")
    ap.add_argument("--registry",default="governance/football3/v3_free_source_registry_v1.json")
    ap.add_argument("--repo-root",default=".")
    ap.add_argument("--skip-repo-blobs",action="store_true")
    a=ap.parse_args()
    checks=validate(load(Path(a.contract)),load(Path(a.registry)))
    repo=[] if a.skip_repo_blobs else validate_repo(Path(a.repo_root))
    print(json.dumps({"status":"FREE_DATA_SHARED_INFRA_READY_NO_CANDIDATE_DATA_READY","contract_checks":len(checks),"repo_checks":len(repo),"data_ready_candidates":[],"active_experts":[],"selected_experts":[],"new_target_labels_read":False,"training_performed":False,"tuning_performed":False,"formal_v2_changed":False,"current_changed":False,"production_changed":False},sort_keys=True))
    return 0
if __name__=="__main__":
    raise SystemExit(main())
