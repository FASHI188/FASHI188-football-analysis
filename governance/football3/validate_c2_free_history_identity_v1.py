from __future__ import annotations
import argparse, json, re, subprocess
from pathlib import Path

BASE="5c60aa6f94976f2cd6fc07a285b763e7fefc528b"
BRANCH="football3/v3-c2-free-history-identity-v1"
FORMAL="e12f5d1193be5d81f60301cf34ab2140e11712a9"
CURRENT="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
EXPECTED_COUNTS={"ENG":3,"GER":3,"ESP":3,"ITA":3,"FRA":2,"total":14}
SHARED={
"governance/football3/v3_git_snapshot_adapter_v1.py":"0ed11017f7335aced6064e3473e038b087e0bd74",
"governance/football3/v3_pit_data_provider_interface_v1.py":"8b9bba7b548e61de7f3b79d96881ebe9c3a41b51",
"governance/football3/v3_free_source_registry_v1.json":"0a7782620557be98e8a2882409ff8028f96a8d27",
}
ALLOWED_BASIS={"EXACT_CANONICAL_NAME","SOURCE_DECLARED_EXACT_ALIAS"}
class ContractError(AssertionError): pass
def req(v,m):
    if not v: raise ContractError(m)
def load(p): return json.loads(Path(p).read_text(encoding="utf-8"))

def validate(c,s,l):
    n=[]
    def ok(v,name): req(v,name); n.append(name)
    ok(c["schema_version"]=="football3-v3-c2-free-history-identity-contract-v1","schema")
    ok(c["canonical_integration"]["exact_base"]==BASE,"base")
    ok(c["research_branch"]==BRANCH,"branch")
    ok(c["formal_baseline"]["head"]==FORMAL and c["formal_baseline"]["changed"] is False,"formal")
    ok(c["current_authority"]["current_sha256"]==CURRENT and c["current_authority"]["changed"] is False,"current")
    cand=c["candidate"]
    ok(cand["status"]=="NOT_AVAILABLE" and cand["weight"]==0 and cand["matrix_delta"]==0,"candidate_zero")
    ok(not any(cand[k] for k in ("activation_allowed","training_allowed","tuning_allowed","new_target_label_access_allowed")),"candidate_forbidden")
    z=c["zero_label_boundary"]
    ok(z["fixture_scores_may_be_present_in_source_files"] and not z["fixture_score_tokens_may_be_parsed_or_used"] and z["membership_names_only"],"zero_label_reader")
    ok(not any(z[k] for k in ("target_results_read","training_performed","tuning_performed","sealed_pools_opened")),"zero_label_actions")
    ok(c["mechanical_scope"]["expected_promotion_lineage_count"]==14,"expected_14")
    ok(c["mechanical_scope"]["fuzzy_matching_allowed"] is False and c["mechanical_scope"]["manual_team_alias_patch_allowed"] is False,"no_fuzzy_manual")
    ok(c["source_time_semantics"]["latest_archive_backfill_before_publication_forbidden"],"no_backfill")
    ok(len(c["residual_blockers_before_data_ready"])>=5,"residual_blockers")
    ok(c["completion_decision"]["data_ready_requires_all_original_c2_coverage_gate_items"],"data_ready_full_gate")
    ok(not c["completion_decision"]["this_batch_authorizes_training"] and not c["completion_decision"]["this_batch_authorizes_target_label_access"],"no_science_exec")
    ok(all(c["forbidden_changes"].values()),"forbidden")
    ok(s["schema_version"]=="football3-v3-c2-openfootball-snapshot-manifest-v1","snapshot_schema")
    ok(s["availability_basis"]=="ARCHIVE_RELEASE_AT" and s["license"]=="CC0-1.0","snapshot_policy")
    rows=s["season_snapshots"]
    ok(len(rows)==10,"ten_season_snapshots")
    ok({(x["country"],x["tier"]) for x in rows}=={(cc,t) for cc in ("ENG","GER","ESP","ITA","FRA") for t in ("SECOND","TOP")},"big5_two_tiers")
    ok(all(re.fullmatch(r"[0-9a-f]{40}",x["commit"]) and re.fullmatch(r"[0-9a-f]{40}",x["blob_sha"]) for x in rows),"snapshot_sha_shapes")
    ok(len(s["club_registries"])==5,"five_club_registries")
    ok(all(x["commit"]=="ae3800227c449447b3a337fc0aac79a8f02f4c8b" for x in s["club_registries"]),"club_registry_pin")
    tm=s["transfermarkt_public_capture"]
    ok(tm["pinned_repository_commit"]=="e44f186d6f06dd8452aaf54c7921ba66c961f637","tm_commit")
    ok(tm["license"]=="CC0_DECLARED_IN_DATASET_METADATA" and tm["role"].startswith("identity-only"),"tm_identity_only")
    ok(l["schema_version"]=="football3-v3-c2-openfootball-identity-lineage-v1","lineage_schema")
    ok(l["counts"]==EXPECTED_COUNTS,"counts")
    lines=l["promotion_lineage"]
    ok(len(lines)==14,"lineage_14")
    ok(all(x["basis"] in ALLOWED_BASIS for x in lines),"basis_only")
    ok(len({(x["country"],x["canonical_name"]) for x in lines})==14,"lineage_unique")
    ok(sum(x["basis"]=="SOURCE_DECLARED_EXACT_ALIAS" for x in lines)==3,"declared_alias_count")
    ok(l["identity_policy"]["fuzzy_matching"] is False and l["identity_policy"]["manual_alias"] is False,"lineage_no_guess")
    ok(l["cross_provider_binding"]["fuzzy_or_manual_resolution_forbidden"] is True,"cross_provider_fail_closed")
    expected_alias={("ESP","Racing Santander","Real Racing Club de Santander","Real Racing Santander"),("ESP","Deportivo La Coruña","RC Deportivo La Coruña","RCD La Coruña"),("FRA","ESTAC Troyes","ES Troyes AC","ES Troyes AC")}
    got_alias={(x["country"],x["prior_name"],x["current_name"],x["canonical_name"]) for x in lines if x["basis"]=="SOURCE_DECLARED_EXACT_ALIAS"}
    ok(got_alias==expected_alias,"declared_alias_exact_set")
    return n

def validate_repo(root:Path):
    out=[]
    for path,sha in SHARED.items():
        got=subprocess.check_output(["git","-C",str(root),"rev-parse",f"{BASE}:{path}"],text=True).strip()
        req(got==sha,f"shared blob drift {path}: {got}")
        out.append(path)
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',default='governance/football3/c2_free_history_identity_contract_v1.json'); ap.add_argument('--snapshots',default='governance/football3/c2_openfootball_snapshot_manifest_v1.json'); ap.add_argument('--lineage',default='governance/football3/c2_openfootball_identity_lineage_v1.json'); ap.add_argument('--repo-root',default='.'); ap.add_argument('--skip-repo-blobs',action='store_true'); a=ap.parse_args()
    checks=validate(load(a.contract),load(a.snapshots),load(a.lineage)); blobs=[] if a.skip_repo_blobs else validate_repo(Path(a.repo_root))
    print(json.dumps({'status':'MATERIALIZATION_READY_KEEP_STOP_DATA_COVERAGE','contract_checks':len(checks),'shared_blob_checks':len(blobs),'promotion_lineage_count':14,'candidate_status':'NOT_AVAILABLE','weight':0,'matrix_delta':0,'new_target_labels_read':False,'training_performed':False,'tuning_performed':False},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
