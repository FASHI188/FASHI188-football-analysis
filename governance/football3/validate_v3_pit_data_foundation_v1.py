from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from typing import Any

EXPECTED_BASE="2e2c000902e6c8d6b4e3ff1bf16387eb1c56ebf6"
EXPECTED_BRANCH="football3/v3-pit-data-foundation-audit-v1"
EXPECTED_FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
EXPECTED_CURRENT="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_TM_COMMIT="e44f186d6f06dd8452aaf54c7921ba66c961f637"
EXPECTED_SB_COMMIT="4b73468fc5b0f1950f9f66fada70ad3a4f9327cb"
EXPECTED_OPENFOOTBALL={
"ENG_Championship_2025_26":("openfootball/england","ec25b557eeb0f5cefd233fbeeb43d340ce05142e","2025-26/2-championship.txt","434ca262e93609197795e2bd611114fd72a6da7e"),
"GER_2_Bundesliga_2025_26":("openfootball/deutschland","b3039e75a649a251d75f190eb71a96c5d00576ba","2025-26/2-bundesliga2.txt","b639906648d3ce58de914c0824602b96301bda52"),
"ESP_Segunda_2025_26":("openfootball/espana","35aa00a19cafa72953cc92ccee060352c9ae6e39","2025-26/2-liga2.txt","3d2a182a7b9dc3b1e08ee0a2dcc7d0090f54daa6"),
"ITA_SerieB_2025_26":("openfootball/italy","8ba9b3c44145de7a174b0c98bdaf5808798e14ad","2025-26/2-serieb.txt","653de4eba1d998e2b9b7f0986a5b45d92e1f9804"),
"FRA_Ligue2_2025_26":("openfootball/europe","a475af95da7a3b0b811148d9479efc15f4a8fd65","france/2025-26_fr2.txt","a126221c0c39261cb45815c3db67602e72b02298"),
}
EXPECTED_CANDIDATE_BLOBS={
"governance/football3/nextgen_market_pit_prereg_contract_v1.json":"863cadbd7743933a888e035f6ed4a6d170e636c4",
"governance/football3/nextgen_dynamic_strength_prereg_contract_v1.json":"e2df5ef84629049a3f346c98da349420de898a1e",
"governance/football3/nextgen_starting_xi_delta_prereg_contract_v1.json":"f8006e500e33cc74ac8c6b9f8fba32cb6ac446e0",
"governance/football3/nextgen_gk_young_transfer_prereg_contract_v1.json":"cc35ba5da918e5fc1438059396284a8ffaac3278",
"governance/football3/nextgen_total_goals_expert_prereg_contract_v1.json":"0d5930d8a58ccc6931a5fddf14b9bc62dc8fbee6",
"governance/football3/nextgen_tactical_matchup_prereg_contract_v1.json":"78fb2c2adda89cae61cf9d5c7ebfe1ac77f57443",
"governance/football3/nextgen_dynamic_gating_prereg_contract_v1.json":"215015a7664fda4b7b788513302d29e9e280bed8",
"governance/football3/nextgen_selected_publishing_prereg_contract_v1.json":"bb2a9b8987ae9bce8edaf1ed697cf4d0bf3e72b5",
}
IMMUTABLE={
"governance/football3/nextgen_current_v2_foundation_contract_v1.json":"897684e9655f7123dd2c338470a64c4837819639",
"governance/football3/nextgen_current_v2_foundation_v1.py":"eebb4aa9747794923a91e26cd36ab6e63b744f6f",
"governance/football3/test_nextgen_current_v2_foundation_v1.py":"e063f42f3820beb23d6b6def1c66543246676567",
"football-data/new_engine_v1/formal_fusion_v2.py":"a5ed26d5ffd4a2875cb9c658cfaa28665a8b7871",
"football-data/new_engine_v1/test_formal_fusion_v2.py":"61b25cd403fa1d9efa0dfcbc1643e0f17621944e",
"football-data/config/formal_model_pointer_historical_xg_fusion_v2.json":"f07743f9c873d415fad9612f3225f64c64b10542",
"football-data/formal_gpt_gateway_v1/entry.py":"b41b0ede2db99c116b90270a32cd04dd240eb655",
}
class ContractError(AssertionError): pass
def req(x:bool,m:str):
    if not x: raise ContractError(m)
def load(p:Path)->dict[str,Any]: return json.loads(p.read_text(encoding="utf-8"))

def validate(contract:dict[str,Any], inventory:dict[str,Any])->list[str]:
    out=[]
    def ok(x,n): req(bool(x),n); out.append(n)
    ok(contract["schema_version"]=="football3-v3-pit-data-foundation-contract-v1","schema")
    ok(contract["status"]=="PUBLIC_FOUNDATION_READY_EXTERNAL_SOURCE_DECISION_REQUIRED","status")
    ok(contract["canonical_integration"]["exact_base"]==EXPECTED_BASE,"base")
    ok(contract["research_branch"]==EXPECTED_BRANCH,"branch")
    ok(contract["formal_baseline"]["head"]==EXPECTED_FORMAL_HEAD and contract["formal_baseline"]["changed"] is False,"formal")
    ok(contract["current_authority"]["current_sha256"]==EXPECTED_CURRENT and contract["current_authority"]["changed"] is False,"current")
    ok((contract["formal_baseline"]["xg_weight"],contract["formal_baseline"]["frozen_v1_weight"])==(0.75,0.25),"weights")
    ce=contract["candidate_effect"]
    ok(not ce["c1_to_c7_activation_changed"] and ce["expert_status_changes"]==0 and ce["weight_changes"]==0 and ce["matrix_delta_changes"]==0,"no_candidate_effect")
    ok(not ce["training_allowed"] and not ce["tuning_allowed"] and not ce["new_target_label_access_allowed"] and not ce["legacy_sealed_label_access_allowed"],"zero_label")
    pd=contract["public_phase_deliverables"]
    ok(pd["common_pit_envelope"] and pd["kickoff_revision_aware_fixture_identity"] and pd["deterministic_content_identity"],"p0")
    ok(pd["transfermarkt_17_domain_route_mapping"] and pd["openfootball_big5_lower_division_prior_routes"] and pd["statsbomb_open_coverage_probe_contract"],"public_sources")
    ok(not pd["network_calls_in_runtime_interface"] and not pd["data_rows_acquired_or_persisted"] and not pd["formal_runtime_wiring"],"research_only")
    pit=contract["pit_envelope_contract"]
    ok(pit["timezone_aware_required"] and pit["observed_at_lte_available_at"] and pit["available_at_lte_freeze_at_for_eligibility"],"time_semantics")
    ok(pit["event_time_not_substitute_for_available_at"] and pit["retrieval_time_not_substitute_for_historical_available_at"],"no_time_proxy")
    ok(pit["silent_source_switch_forbidden"] and pit["fixture_identity_conflict_fail_closed"],"fail_closed")
    tm=contract["public_sources"]["transfermarkt"]
    ok(tm["pinned_commit"]==EXPECTED_TM_COMMIT and tm["license_state"]=="CC0_DECLARED_IN_DATASET_METADATA","tm_pin")
    ok(tm["updates_paused"] and tm["snapshot_current_through"]=="2026-07-06" and tm["formal_domain_route_mapping_count"]==17,"tm_scope")
    sb=contract["public_sources"]["statsbomb_open"]
    ok(sb["pinned_commit"]==EXPECTED_SB_COMMIT and sb["coverage_claim"]=="SELECTED_COMPETITIONS_AND_SEASONS_ONLY","sb_pin")
    of=contract["public_sources"]["openfootball_big5_lower_division"]
    ok(of["license_state"]=="CC0_PUBLIC_DOMAIN" and of["secret_required"] is False and of["fee_required"] is False,"of_license")
    ok({k:(x["repository"],x["commit"],x["path"],x["blob_sha"]) for k,x in of["pins"].items()}==EXPECTED_OPENFOOTBALL,"of_pins")
    ok("FUZZY_ALIAS_GUESSING_FORBIDDEN" in of["identity_constraint"],"of_identity_fail_closed")
    order=contract["execution_order"]
    ok([x["priority"] for x in order]==list(range(8)),"priority_order")
    ok(order[0]["workstream"]=="COMMON_PIT_ENVELOPE_AND_IDENTITY_CONTRACT" and order[1]["workstream"]=="TRANSFERMARKT_CC0_IDENTITY_AND_LINEAGE_SEED" and order[2]["workstream"]=="OPENFOOTBALL_CC0_BIG5_LOWER_DIVISION_PRIOR","shared_first")
    ok(order[4]["external_decision_required"] and order[5]["external_decision_required"] and order[6]["external_decision_required"],"external_boundary")
    ok(order[7]["workstream"]=="DYNAMIC_GATING" and order[7]["candidates"]==["C7"],"c7_last")
    ok(all(contract["external_stop_boundaries"][x] for x in ("payment_required","login_required","secret_required","manual_download_required","license_choice_required")),"stop_boundaries")
    ok(all(contract["forbidden_changes"].values()),"forbidden")
    ok(inventory["schema_version"]=="football3-v3-pit-data-source-inventory-v1" and inventory["canonical_integration_exact_base"]==EXPECTED_BASE,"inventory_schema")
    ok(set(inventory["candidate_root_causes"])=={f"C{i}" for i in range(1,8)},"c1_c7")
    common={x["root_id"]:x for x in inventory["common_root_causes"]}
    ok(set(common)=={"COMMON-PIT-TIMING","COMMON-GLOBAL-IDENTITY","COMMON-COVERAGE","COMMON-FRESHNESS"},"common_roots")
    pub=inventory["public_sources"]
    ok(pub["transfermarkt_datasets"]["pinned_commit"]==EXPECTED_TM_COMMIT and pub["transfermarkt_datasets"]["formal_domain_mapping_count"]==17,"inventory_tm")
    ok(len(pub["transfermarkt_datasets"]["formal_domain_mapping"])==17,"tm_17")
    ok(pub["transfermarkt_datasets"]["dataset_freshness"]=="CURRENT_TO_2026-07-06_UPDATES_PAUSED","tm_freshness")
    ok(pub["statsbomb_hudl_open_data"]["pinned_commit"]==EXPECTED_SB_COMMIT and pub["statsbomb_hudl_open_data"]["coverage_state"]=="SELECTED_COMPETITIONS_AND_SEASONS_ONLY","inventory_sb")
    ofi=pub["openfootball_big5_lower_division"]
    ok(ofi["coverage_state"]=="BIG5_SECOND_TIERS_2025_26_MECHANICALLY_PINNED" and ofi["identity_rule"].endswith("FUZZY_ALIAS_FORBIDDEN"),"inventory_of")
    ok({k:(x["repository"],x["commit"],x["path"],x["blob_sha"]) for k,x in ofi["pins"].items()}==EXPECTED_OPENFOOTBALL,"inventory_of_pins")
    ok(pub["football_data_uk"]["blocked_new_durable_use"] is True,"fd_license_guard")
    prio=inventory["execution_priority"]
    ok([x["priority"] for x in prio]==list(range(8)),"inventory_priority")
    ok(all(prio[x]["requires_secret_or_payment"] is False for x in (0,1,2,3)),"public_first")
    ok(prio[4]["requires_secret_or_payment"] is True and prio[6]["requires_secret_or_payment"] is True,"paid_stop")
    ok(inventory["candidate_root_causes"]["C7"]["cost_or_credential_boundary"]=="NO_DIRECT_PURCHASE_RECOMMENDED","c7_no_purchase")
    return out

def validate_repo(root:Path)->list[str]:
    req((root/".git").exists(),"git repo required")
    out=[]
    for p,e in {**EXPECTED_CANDIDATE_BLOBS,**IMMUTABLE}.items():
        got=subprocess.check_output(["git","-C",str(root),"rev-parse",f"{EXPECTED_BASE}:{p}"],text=True).strip()
        req(got==e,f"blob {p}: {got} != {e}")
        if p in IMMUTABLE:
            work=subprocess.check_output(["git","-C",str(root),"hash-object",str(root/p)],text=True).strip()
            req(work==e,f"working immutable {p}: {work} != {e}")
        out.append(p)
    return out

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',default='governance/football3/v3_pit_data_foundation_contract_v1.json'); ap.add_argument('--inventory',default='governance/football3/v3_pit_data_source_inventory_v1.json'); ap.add_argument('--repo-root',default='.'); ap.add_argument('--skip-repo-blobs',action='store_true'); a=ap.parse_args()
    checks=validate(load(Path(a.contract)),load(Path(a.inventory))); blobs=[] if a.skip_repo_blobs else validate_repo(Path(a.repo_root))
    print(json.dumps({'status':'PUBLIC_FOUNDATION_READY_EXTERNAL_SOURCE_DECISION_REQUIRED','contract_checks':len(checks),'repo_blob_checks':len(blobs),'new_target_labels_read':False,'training_performed':False,'tuning_performed':False,'formal_v2_changed':False,'current_changed':False,'production_changed':False,'public_workstreams_completed':[0,1,2,3],'next_boundary':'CURRENT_PERSONNEL_AND_T15_LINEUP_SOURCE'},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
