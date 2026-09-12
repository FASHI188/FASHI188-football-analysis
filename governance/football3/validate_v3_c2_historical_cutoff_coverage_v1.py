from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path

BASE="af81196bace425707bc976f219733b41d993f532"
FORMAL="e12f5d1193be5d81f60301cf34ab2140e11712a9"
CURRENT="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
class E(AssertionError): pass
def req(v,m):
    if not v: raise E(m)
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))

def validate(c,m,l):
    checks=[]
    def ok(v,n): req(v,n); checks.append(n)
    ok(c['schema_version']=='football3-v3-c2-historical-cutoff-coverage-contract-v1','schema')
    ok(c['canonical_integration']['exact_base']==BASE,'base')
    ok(c['status']=='PARTIAL_HISTORICAL_SNAPSHOT_COVERAGE_READY_STOP_IDENTITY_LINEAGE','status')
    cand=c['candidate']; ok(cand['status']=='NOT_AVAILABLE' and cand['weight']==0 and cand['matrix_delta']==0 and not cand['data_ready'],'inactive')
    ok(not cand['training_allowed'] and not cand['tuning_allowed'] and not cand['target_label_access_allowed'],'zero_label')
    ok(c['audit_scope']['metadata_only'] and not c['audit_scope']['match_file_content_opened'] and not c['audit_scope']['target_result_labels_read'],'metadata_only')
    ok(c['strict_pit_rules']['latest_archive_backfill_forbidden'] and c['strict_pit_rules']['retrieval_time_proxy_forbidden'],'pit_guard')
    ok(c['strict_pit_rules']['minimum_materiality_check_required'],'materiality_guard')
    ok(c['findings']['all_five_paths_have_preseason_git_snapshot'] and c['findings']['italy_earliest_blob_is_materially_tiny'],'findings')
    ok(len(c['c2_data_ready_remaining_failures'])>=6,'remaining_failures')
    ok(all(c['forbidden_changes'].values()),'forbidden')
    ok(m['schema_version']=='football3-v3-c2-historical-commit-history-manifest-v1','manifest_schema')
    src=m['sources']; ok(len(src)==5,'five_sources')
    ok(m['mechanical_summary']['preseason_snapshot_count']==5 and m['mechanical_summary']['materiality_pass_count']==4 and m['mechanical_summary']['materiality_fail_count']==1,'summary')
    ok(all(x['preseason_snapshot_present'] for x in src.values()),'preseason_presence')
    ok(src['ITA_SerieB']['blob_bytes']==65 and src['ITA_SerieB']['minimum_materiality_pass'] is False,'italy_guard')
    ok(all(len(x['preseason_commit'])==40 and len(x['tree_sha'])==40 and len(x['blob_sha'])==40 for x in src.values()),'exact_git_ids')
    ok(not m['content_read'] and not m['target_labels_read'],'no_content_labels')
    ok(l['schema_version']=='football3-v3-c2-promotion-lineage-contract-v1','lineage_schema')
    ok(l['canonical_club_identity'].startswith('global stable club id'),'global_identity')
    ok(all(l['forbidden_inference'].values()),'lineage_no_label_inference')
    ok(l['current_state']['c2_data_ready'] is False and l['current_state']['training_allowed'] is False and l['current_state']['target_labels_allowed'] is False,'lineage_inactive')
    return checks

def repo_checks(root:Path):
    out=[]
    formal=subprocess.check_output(['git','-C',str(root),'rev-parse','refs/remotes/origin/football3/historical-xg-fusion-v2-formal-activation-v1'],text=True).strip(); req(formal==FORMAL,'formal drift'); out.append('formal')
    expected={
      '.github/workflows/football3-v3-c2-historical-cutoff-coverage-v1.yml',
      'governance/football3/v3_c2_historical_cutoff_coverage_contract_v1.json',
      'governance/football3/v3_c2_historical_commit_history_manifest_v1.json',
      'governance/football3/v3_c2_promotion_lineage_contract_v1.json',
      'governance/football3/v3_c2_historical_cutoff_auditor_v1.py',
      'governance/football3/validate_v3_c2_historical_cutoff_coverage_v1.py',
      'governance/football3/test_v3_c2_historical_cutoff_coverage_v1.py'
    }
    got=set(subprocess.check_output(['git','-C',str(root),'diff','--name-only',f'{BASE}...HEAD'],text=True).splitlines()); req(got==expected,f'exact seven-file diff required: {sorted(got)}'); out.append('diff')
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo-root',default='.'); ap.add_argument('--skip-repo-blobs',action='store_true'); a=ap.parse_args()
    c=load('governance/football3/v3_c2_historical_cutoff_coverage_contract_v1.json'); m=load('governance/football3/v3_c2_historical_commit_history_manifest_v1.json'); l=load('governance/football3/v3_c2_promotion_lineage_contract_v1.json')
    checks=validate(c,m,l); repo=[] if a.skip_repo_blobs else repo_checks(Path(a.repo_root))
    print(json.dumps({'status':c['status'],'contract_checks':len(checks),'repo_checks':len(repo),'formal_head':FORMAL,'current_sha256':CURRENT,'match_content_read':False,'target_labels_read':False,'training':False,'tuning':False,'activation':False},sort_keys=True))
if __name__=='__main__': main()
