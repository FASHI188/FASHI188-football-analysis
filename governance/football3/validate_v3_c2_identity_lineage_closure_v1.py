from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path

BASE="99ed0876818dbbf833f21c4c45e4b9b343af6d0f"
STATUS="STOP_DATA_COVERAGE_IDENTITY_LINEAGE"
EXPECTED_PRIOR={
"governance/football3/v3_c2_promotion_lineage_contract_v1.json":"115799620e4a863bf100c53842a05d66ebafccd3",
"governance/football3/v3_c2_historical_commit_history_manifest_v1.json":"3b962b3af0d73c3eff5b8888052347ad5b350d44",
"governance/football3/v3_c2_historical_cutoff_auditor_v1.py":"5e4d4de9ef2c63e3b4f305f142517800f616001a"
}
class E(AssertionError): pass
def req(x,m):
    if not x: raise E(m)
def load(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def validate(c,i):
    checks=[]
    def ok(x,n): req(x,n); checks.append(n)
    ok(c["schema_version"]=="football3-v3-c2-identity-lineage-closure-contract-v1","schema")
    ok(c["canonical_integration"]["exact_base"]==BASE,"base")
    ok(c["status"]==STATUS,"status")
    cand=c["candidate"]
    ok(cand["status"]=="NOT_AVAILABLE" and cand["weight"]==0 and cand["matrix_delta"]==0 and cand["data_ready"] is False,"candidate_fail_closed")
    z=c["zero_label_audit"]
    ok(not any(z.values()),"zero_label")
    ok(c["first_authoritative_failure"]=="NO_HISTORICAL_CUTOFF_BOUND_STABLE_ID_MEMBERSHIP_SNAPSHOT_WITH_FULL_REQUIRED_SCOPE","first_failure")
    ok(all(c["formal_boundaries"][k] is False for k in c["formal_boundaries"]),"formal_unchanged")
    ok("fuzzy team-name similarity" in c["forbidden_inference"],"no_fuzzy")
    ok(i["schema_version"]=="football3-v3-c2-identity-source-inventory-v1","inventory_schema")
    tm=i["sources"]["transfermarkt_public_dvc"]
    ok(tm["free_public_remote"] and not tm["credentials_required"],"tm_public")
    ok(tm["git_data_policy"]["data_prep_gitignored"] and tm["git_data_policy"]["github_release_count"]==0,"tm_historical_gap")
    ok(not tm["historical_cutoff_safe_membership_snapshot_proven"],"tm_not_pit")
    wd=i["sources"]["wikidata"]
    ok(wd["license_state"]=="CC0_STRUCTURED_DATA" and wd["team_property"]=="P7223" and wd["player_property"]=="P2446","wikidata_ids")
    ok(wd["team_property_expected_completeness"]=="always incomplete","wikidata_incomplete")
    ok(not wd["historical_cutoff_statement_availability_proven"],"wikidata_not_backfilled")
    of=i["sources"]["openfootball_git"]
    ok(of["historical_git_versioning"] and of["cutoff_addressable_commits"],"openfootball_history")
    ok(not of["provider_stable_club_id_in_fixture_files"] and not of["name_only_identity_sufficient"],"openfootball_identity_gap")
    comb=i["combination_rules"]
    ok(comb["fuzzy_name_join"]=="FORBIDDEN" and comb["manual_patch"]=="FORBIDDEN","combination_fail_closed")
    cv=i["coverage_conclusion"]
    ok(not cv["c2_data_ready"] and cv["status"]==STATUS,"coverage_stop")
    return checks
def repo(root):
    out=[]
    for p,e in EXPECTED_PRIOR.items():
        got=subprocess.check_output(["git","-C",str(root),"rev-parse",f"{BASE}:{p}"],text=True).strip()
        req(got==e,f"prior blob {p} {got} != {e}"); out.append(p)
    return out
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--contract",default="governance/football3/v3_c2_identity_lineage_closure_contract_v1.json")
    ap.add_argument("--inventory",default="governance/football3/v3_c2_identity_source_inventory_v1.json")
    ap.add_argument("--repo-root",default=".")
    ap.add_argument("--skip-repo",action="store_true")
    a=ap.parse_args()
    checks=validate(load(a.contract),load(a.inventory)); blobs=[] if a.skip_repo else repo(Path(a.repo_root))
    print(json.dumps({"status":STATUS,"checks":len(checks),"prior_blob_checks":len(blobs),"c2_data_ready":False,"target_labels_read":False,"training_performed":False,"formal_changed":False},sort_keys=True))
if __name__=="__main__": main()
