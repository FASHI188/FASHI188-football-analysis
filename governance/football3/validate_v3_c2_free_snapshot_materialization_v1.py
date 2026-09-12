from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path

BASE="5c60aa6f94976f2cd6fc07a285b763e7fefc528b"
FORMAL="e12f5d1193be5d81f60301cf34ab2140e11712a9"
CURRENT="71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
class E(AssertionError): pass
def req(v,m):
    if not v: raise E(m)
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))

def validate(c,m,i):
    checks=[]
    def ok(v,n): req(v,n); checks.append(n)
    ok(c['schema_version']=='football3-v3-c2-free-snapshot-materialization-contract-v1','schema')
    ok(c['canonical_integration']['exact_base']==BASE,'base')
    ok(c['status']=='PARTIAL_DATA_FOUNDATION_READY_STOP_DATA_COVERAGE','status')
    cand=c['candidate']; ok(cand['status']=='NOT_AVAILABLE' and cand['weight']==0 and cand['matrix_delta']==0 and not cand['data_ready'],'inactive')
    ok(not cand['training_allowed'] and not cand['tuning_allowed'] and not cand['target_label_access_allowed'],'zero_label')
    ok(c['strict_pit_rules']['snapshot_selector'].startswith('latest source commit'),'selector')
    ok(c['strict_pit_rules']['latest_current_commit_backfill_forbidden'],'no_backfill')
    ok(c['strict_pit_rules']['fuzzy_alias_forbidden'],'no_fuzzy')
    ok(len(c['remaining_c2_data_ready_failures'])>=5,'remaining_failures')
    ok(all(c['forbidden_changes'].values()),'forbidden')
    ok(m['schema_version']=='football3-v3-c2-free-source-snapshot-manifest-v1','manifest_schema')
    pins=m['openfootball_big5_second_tier_latest_pins']; ok(len(pins)==5,'five_pins')
    for name,p in pins.items():
        ok(len(p['commit'])==40 and len(p['blob_sha'])==40,f'{name}_sha')
        ok(p['commit_published_at'].endswith('Z'),f'{name}_time')
        ok(p['license_state']=='CC0_PUBLIC_DOMAIN',f'{name}_license')
    tm=m['transfermarkt_identity_snapshot']; ok(tm['commit']=='e44f186d6f06dd8452aaf54c7921ba66c961f637','tm_commit')
    ok(tm['updates_paused'] and tm['role']=='IDENTITY_AND_LINEAGE_SEED_ONLY','tm_role')
    ok(m['historical_selector_contract']['latest_pin_as_earlier_history']=='FORBIDDEN','history_guard')
    ok(i['schema_version']=='football3-v3-c2-club-identity-bridge-contract-v1','identity_schema')
    ok(i['canonical_identity_strategy']['fuzzy_name_match']=='FORBIDDEN' and i['canonical_identity_strategy']['name_only_match']=='FORBIDDEN','identity_guard')
    ok(i['wikidata_identity_only']['sports_feature_role']=='NONE' and i['wikidata_identity_only']['target_label_role']=='NONE','wikidata_identity_only')
    ok(i['current_gap'].endswith('C2 stays NOT_AVAILABLE'),'identity_gap')
    return checks

def repo_checks(root:Path):
    out=[]
    formal=subprocess.check_output(['git','-C',str(root),'rev-parse','football3/historical-xg-fusion-v2-formal-activation-v1'],text=True).strip()
    req(formal==FORMAL,'formal head drift'); out.append('formal_head')
    changed=set(subprocess.check_output(['git','-C',str(root),'diff','--name-only',f'{BASE}...HEAD'],text=True).splitlines())
    expected={
      '.github/workflows/football3-v3-c2-free-snapshot-materialization-v1.yml',
      'governance/football3/v3_c2_free_snapshot_materialization_contract_v1.json',
      'governance/football3/v3_c2_free_source_snapshot_manifest_v1.json',
      'governance/football3/v3_c2_club_identity_bridge_contract_v1.json',
      'governance/football3/v3_c2_free_snapshot_materializer_v1.py',
      'governance/football3/validate_v3_c2_free_snapshot_materialization_v1.py',
      'governance/football3/test_v3_c2_free_snapshot_materialization_v1.py'
    }
    req(changed==expected,f'exact seven-file diff required: {sorted(changed)}'); out.append('exact_diff')
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo-root',default='.'); ap.add_argument('--skip-repo-blobs',action='store_true'); a=ap.parse_args()
    c=load('governance/football3/v3_c2_free_snapshot_materialization_contract_v1.json'); m=load('governance/football3/v3_c2_free_source_snapshot_manifest_v1.json'); i=load('governance/football3/v3_c2_club_identity_bridge_contract_v1.json')
    checks=validate(c,m,i); repo=[] if a.skip_repo_blobs else repo_checks(Path(a.repo_root))
    print(json.dumps({'status':c['status'],'contract_checks':len(checks),'repo_checks':len(repo),'formal_head':FORMAL,'current_sha256':CURRENT,'target_labels_read':False,'training':False,'tuning':False,'activation':False},sort_keys=True))
if __name__=='__main__': main()
