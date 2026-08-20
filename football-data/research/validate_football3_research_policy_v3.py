#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT_SHA='e3e73c998020beef585cc459a69ea5b73b44ddb3'
N20_CHECKPOINT_SHA='a8cef08fd57315c16a9adc23935b871c98ef575a'
POLICY_PATH=pathlib.Path('football-data/research/FOOTBALL3_RESEARCH_POLICY_V3.json')
CURRENT_PATH=pathlib.Path('football-data/research/FOOTBALL3_INDEPENDENT_CURRENT.md')
REGISTRY_PATH=pathlib.Path('football-data/research/FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json')
CONTRACT_TEMPLATE=pathlib.Path('football-data/research/FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json')
AUDIT_TEMPLATE=pathlib.Path('football-data/research/FOOTBALL3_GLOBAL_CONSUMPTION_AUDIT_TEMPLATE_V1.json')
EXECUTION_STANDARD=pathlib.Path('football-data/research/FOOTBALL3_EXECUTION_STANDARD_V2.md')
STALE_AUTHORITY_PATHS=[
    pathlib.Path('football-data/research/FOOTBALL3_RESEARCH_POLICY_V2.json'),
    pathlib.Path('football-data/research/validate_football3_research_policy_v2.py'),
    pathlib.Path('football-data/research/FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V1.json'),
    pathlib.Path('football-data/research/FOOTBALL3_EXECUTION_STANDARD_V1.md'),
]


def fail(msg:str)->None:
    print(f'FAIL: {msg}',file=sys.stderr); raise SystemExit(1)


def git_ok(*args:str)->bool:
    return subprocess.run(['git',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE).returncode==0


def main()->None:
    for p in (POLICY_PATH,CURRENT_PATH,REGISTRY_PATH,CONTRACT_TEMPLATE,AUDIT_TEMPLATE,EXECUTION_STANDARD):
        if not p.exists(): fail(f'missing {p}')
    stale=[str(p) for p in STALE_AUTHORITY_PATHS if p.exists()]
    if stale: fail(f'stale authority files still present: {stale}')

    p=json.loads(POLICY_PATH.read_text(encoding='utf-8'))
    r=json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    t=json.loads(CONTRACT_TEMPLATE.read_text(encoding='utf-8'))
    a=json.loads(AUDIT_TEMPLATE.read_text(encoding='utf-8'))
    c=CURRENT_PATH.read_text(encoding='utf-8')

    if p.get('schema')!='FOOTBALL3_RESEARCH_POLICY_V3': fail('policy schema')
    if p.get('project_id')!='football3': fail('project_id')
    if p.get('scientific_root',{}).get('sha')!=ROOT_SHA: fail('root SHA')
    if p.get('scientific_root',{}).get('continuation_prefix')!='football3/': fail('branch prefix')
    if p.get('primary_optimization',{}).get('direct_draw_top1') is not False: fail('direct Draw gate')
    pred=p.get('prediction_contract',{})
    if pred.get('master_prediction_cutoff')!='T-15m': fail('master cutoff')
    if pred.get('master_cutoff_change_requires_new_product_contract') is not True: fail('cutoff drift gate')
    if pred.get('strong_same_cutoff_market_baseline_required') is not True: fail('strong baseline gate')
    if pred.get('missing_or_invalid_feature_timestamp_fails_closed') is not True: fail('strict PIT timestamp gate')
    core=p.get('execution_core',{})
    if core.get('mandatory_proper_scores')!=['LogLoss','Brier','RPS']: fail('proper scores')
    if core.get('mandatory_calibration_metrics')!=['Top1ECE','ClasswiseECE']: fail('calibration metrics')
    if core.get('random_split') is not False: fail('random split gate')
    if p.get('prelabel_protocol',{}).get('synthetic_smoke_real_target_count')!=0: fail('synthetic prelabel boundary')
    val=p.get('validation',{})
    if val.get('calibration_required') is not True or val.get('domain_consistency_required') is not True: fail('calibration/domain gates')
    if val.get('numerical_success_gates_frozen_before_labels') is not True: fail('success gate freeze')
    if val.get('confirmation_minimum_planned_power',0)<0.8: fail('confirmation power')
    if val.get('optional_stopping') is not False: fail('optional stopping')
    if p.get('method_shopping',{}).get('same_viewed_labels_rescue') is not False: fail('method shopping')
    gc=p.get('global_consumption',{})
    if gc.get('fresh_evidence_requires_identity_lock_and_audit_artifact_sha256') is not True: fail('consumption artifact gate')
    if gc.get('fresh_evidence_requires_zero_unresolved_historical_identity_gaps') is not True: fail('unresolved-history gate')
    if gc.get('any_overlap_or_unresolved_gap_forces_replication_or_reproduction') is not True: fail('reuse classification gate')
    if r.get('rules',{}).get('viewed_target_labels_are_globally_consumed') is not True: fail('global consumption registry')
    if not all(x.get('authorized_access') is False for x in r.get('sealed',[])): fail('sealed registry access')

    if t.get('schema_version')!=2: fail('contract template schema')
    if t.get('prediction_cutoff',{}).get('master')!='T-15m': fail('contract template master cutoff')
    if t.get('baseline',{}).get('market_anchor') is not True: fail('contract template market anchor')
    if t.get('metrics',{}).get('calibration',{}).get('required') is not True: fail('contract template calibration')
    if 'success_gates' not in t: fail('contract template success gates')
    if a.get('real_target_values_read')!=0: fail('audit template must be zero-label')
    if a.get('github_history_checked') is not True or a.get('airtable_history_checked') is not True: fail('audit template external history checks')

    for token in ('C072-C',ROOT_SHA,'C073-C077','C072N20_P1000_PILOT_NO_SIGNAL','P(T=0,1,2,3,4,5,6,7+)','T-15m','C070-F Confirmation1597','N17 reserve266','N18C confirmation150','FULL_STACK_ROOT_CAUSE_REMEDIATION_V2_COMPLETE_NO_NEW_SCIENCE'):
        if token not in c: fail(f'current missing {token}')

    if pathlib.Path('.git').exists():
        if not git_ok('merge-base','--is-ancestor',ROOT_SHA,'HEAD'): fail('HEAD outside C072-C lineage')
        if not git_ok('merge-base','--is-ancestor',N20_CHECKPOINT_SHA,'HEAD'): fail('HEAD does not descend from N20 remediated scientific checkpoint')
    ref=os.environ.get('GITHUB_HEAD_REF') or os.environ.get('GITHUB_REF_NAME') or ''
    if ref and not ref.startswith('football3/'): fail(f'non-football3 ref: {ref}')
    print('PASS: football3 V3 policy + V2 execution contract, T-15m cutoff, consumption receipts and single-authority boundary validated')


if __name__=='__main__': main()
