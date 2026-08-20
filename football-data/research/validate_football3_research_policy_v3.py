#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT_SHA='e3e73c998020beef585cc459a69ea5b73b44ddb3'
N20_CHECKPOINT_SHA='a8cef08fd57315c16a9adc23935b871c98ef575a'
ROOT=pathlib.Path('football-data/research')
POLICY_PATH=ROOT/'FOOTBALL3_RESEARCH_POLICY_V3.json'
CURRENT_PATH=ROOT/'FOOTBALL3_INDEPENDENT_CURRENT.md'
REGISTRY_PATH=ROOT/'FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json'
CONTRACT_TEMPLATE=ROOT/'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json'
AUDIT_TEMPLATE=ROOT/'FOOTBALL3_GLOBAL_CONSUMPTION_AUDIT_TEMPLATE_V1.json'
EXECUTION_STANDARD=ROOT/'FOOTBALL3_EXECUTION_STANDARD_V2.md'
CORE_PATH=ROOT/'football3_core.py'
LINEAGE_AUDIT=ROOT/'audit_football3_lineage.py'
CHANGED_AUDIT=ROOT/'audit_football3_changed_scientific_files.py'
PR_SCOPE_AUDIT=ROOT/'audit_football3_pr_scope.py'
FULLSTACK_WF=pathlib.Path('.github/workflows/football3-full-stack-remediation.yml')
POLICY_WF=pathlib.Path('.github/workflows/football3-research-policy-integrity.yml')
STALE_AUTHORITY_PATHS=[
    ROOT/'FOOTBALL3_RESEARCH_POLICY_V2.json',
    ROOT/'validate_football3_research_policy_v2.py',
    ROOT/'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V1.json',
    ROOT/'FOOTBALL3_EXECUTION_STANDARD_V1.md',
]


def fail(msg:str)->None:
    print(f'FAIL: {msg}',file=sys.stderr); raise SystemExit(1)


def git_ok(*args:str)->bool:
    return subprocess.run(['git',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE).returncode==0


def main()->None:
    required=(POLICY_PATH,CURRENT_PATH,REGISTRY_PATH,CONTRACT_TEMPLATE,AUDIT_TEMPLATE,EXECUTION_STANDARD,CORE_PATH,LINEAGE_AUDIT,CHANGED_AUDIT,PR_SCOPE_AUDIT,FULLSTACK_WF,POLICY_WF)
    for path in required:
        if not path.exists(): fail(f'missing {path}')
    stale=[str(path) for path in STALE_AUTHORITY_PATHS if path.exists()]
    if stale: fail(f'stale authority files still present: {stale}')

    p=json.loads(POLICY_PATH.read_text(encoding='utf-8'))
    r=json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    t=json.loads(CONTRACT_TEMPLATE.read_text(encoding='utf-8'))
    a=json.loads(AUDIT_TEMPLATE.read_text(encoding='utf-8'))
    c=CURRENT_PATH.read_text(encoding='utf-8')
    core=CORE_PATH.read_text(encoding='utf-8')
    fullwf=FULLSTACK_WF.read_text(encoding='utf-8')
    policywf=POLICY_WF.read_text(encoding='utf-8')

    if p.get('schema')!='FOOTBALL3_RESEARCH_POLICY_V3': fail('policy schema')
    if p.get('project_id')!='football3': fail('project_id')
    if p.get('scientific_root',{}).get('sha')!=ROOT_SHA: fail('root SHA')
    if p.get('scientific_root',{}).get('continuation_prefix')!='football3/': fail('branch prefix')
    q=p.get('quarantine',{})
    if q.get('direct_merge_ancestry_forbidden') is not True or q.get('scientific_cherry_pick_patch_id_overlap_forbidden') is not True: fail('quarantine lineage gates')
    if q.get('missing_quarantine_refs_fails_closed') is not True: fail('quarantine missing-ref fail-closed gate')
    if p.get('primary_optimization',{}).get('direct_draw_top1') is not False: fail('direct Draw gate')

    pred=p.get('prediction_contract',{})
    if pred.get('master_prediction_cutoff')!='T-15m': fail('master cutoff')
    if pred.get('master_cutoff_change_requires_new_product_contract') is not True: fail('cutoff drift gate')
    if pred.get('strong_same_cutoff_market_baseline_required') is not True: fail('strong baseline gate')
    if pred.get('timezone_aware_timestamp_required') is not True: fail('timezone-aware PIT gate')
    if pred.get('missing_invalid_or_timezone_naive_timestamp_fails_closed') is not True: fail('strict PIT timestamp gate')
    if pred.get('runtime_branch_must_equal_contract_branch') is not True: fail('runtime branch binding gate')

    execution=p.get('execution_core',{})
    if execution.get('canonical_evaluator')!='football3_core.evaluate_frozen_experiment': fail('canonical evaluator')
    if execution.get('mandatory_proper_scores')!=['LogLoss','Brier','RPS']: fail('proper scores')
    if execution.get('mandatory_calibration_metrics')!=['Top1ECE','ClasswiseECE']: fail('calibration metrics')
    if execution.get('paired_bootstrap_reports_all_proper_scores') is not True: fail('proper-score bootstrap reporting')
    if execution.get('random_split') is not False: fail('random split gate')
    if execution.get('all_active_v2_contracts_revalidated_each_scientific_pr') is not True: fail('active-contract revalidation gate')
    if execution.get('changed_research_python_requires_contract_or_helper_binding') is not True: fail('changed-code binding gate')
    if execution.get('alternate_notebook_shell_r_execution_surface_fails_closed') is not True: fail('alternate executable gate')
    if 'def evaluate_frozen_experiment(' not in core: fail('canonical evaluator implementation missing')
    if 'timezone-naive timestamp forbidden' not in core: fail('timezone-naive timestamp runtime guard missing')

    ci=p.get('ci_trigger_contract',{})
    if ci.get('football3_head_to_main_must_trigger_full_stack') is not True: fail('head-to-main CI trigger contract')
    if ci.get('football3_owned_authority_modified_from_nonfootball3_head_fails') is not True: fail('cross-project authority mutation guard')
    if 'pull_request:\n    branches:' in fullwf or 'pull_request:\n    branches:' in policywf: fail('wrong pull_request base-branch filter reintroduced')
    if "startsWith(github.head_ref, 'football3/')" not in fullwf: fail('full-stack head-branch gate missing')
    if 'audit_football3_lineage.py' not in fullwf or 'audit_football3_lineage.py' not in policywf: fail('lineage audit missing from CI')
    if 'audit_football3_pr_scope.py' not in policywf: fail('non-football3 authority scope guard missing')

    if p.get('prelabel_protocol',{}).get('synthetic_smoke_real_target_count')!=0: fail('synthetic prelabel boundary')
    val=p.get('validation',{})
    if val.get('calibration_required') is not True or val.get('domain_consistency_required') is not True: fail('calibration/domain gates')
    if val.get('numerical_success_gates_frozen_before_labels') is not True: fail('success gate freeze')
    if val.get('canonical_evaluator_applies_success_gates') is not True: fail('runtime success gate application')
    if val.get('bootstrap_all_proper_scores_reported') is not True: fail('bootstrap proper-score coverage')
    if val.get('confirmation_minimum_planned_power',0)<0.8: fail('confirmation power')
    if val.get('optional_stopping') is not False: fail('optional stopping')
    if p.get('method_shopping',{}).get('same_viewed_labels_rescue') is not False: fail('method shopping')

    gc=p.get('global_consumption',{})
    if gc.get('identity_lock_format')!='sha256_csv_v1': fail('semantic identity lock format')
    if gc.get('identity_lock_content_semantics_verified') is not True: fail('identity content semantics gate')
    if gc.get('fresh_evidence_requires_structured_connected_audit_receipts') is not True: fail('structured audit receipts gate')
    if gc.get('fresh_evidence_requires_zero_unresolved_historical_identity_gaps') is not True: fail('unresolved-history gate')
    if gc.get('any_overlap_or_unresolved_gap_forces_replication_or_reproduction') is not True: fail('reuse classification gate')
    if r.get('rules',{}).get('viewed_target_labels_are_globally_consumed') is not True: fail('global consumption registry')
    if not all(x.get('authorized_access') is False for x in r.get('sealed',[])): fail('sealed registry access')

    if t.get('schema_version')!=2: fail('contract template schema')
    pc=t.get('prediction_cutoff',{})
    if not (pc.get('master')==pc.get('baseline')==pc.get('candidate')=='T-15m'): fail('contract template cutoff')
    if t.get('data_plan',{}).get('identity_lock_format')!='sha256_csv_v1': fail('contract identity format')
    if t.get('metrics',{}).get('implementation')!='football3_core.evaluate_frozen_experiment': fail('contract canonical evaluator')
    if t.get('metrics',{}).get('calibration',{}).get('required') is not True: fail('contract template calibration')
    if 'Top1ECE_delta_max' not in t.get('success_gates',{}).get('secondary_noninferiority',{}): fail('Top1ECE noninferiority gate')
    if 'minimum_rows_per_domain' not in t.get('success_gates',{}).get('domain_consistency',{}): fail('domain minimum rows gate')
    if a.get('real_target_values_read')!=0 or a.get('connected_audit_status')!='VERIFIED_ZERO_LABEL': fail('audit template zero-label connected status')
    if not isinstance(a.get('github_receipt'),dict) or not isinstance(a.get('airtable_receipt'),dict): fail('structured external audit receipts')

    for token in ('C072-C',ROOT_SHA,'C073-C077','C072N20_P1000_PILOT_NO_SIGNAL','P(T=0,1,2,3,4,5,6,7+)','T-15m','C070-F Confirmation1597','N17 reserve266','N18C confirmation150','FULL_STACK_ROOT_CAUSE_REMEDIATION_V3_REAUDIT_COMPLETE_NO_NEW_SCIENCE'):
        if token not in c: fail(f'current missing {token}')

    if pathlib.Path('.git').exists():
        if not git_ok('merge-base','--is-ancestor',ROOT_SHA,'HEAD'): fail('HEAD outside C072-C lineage')
        if not git_ok('merge-base','--is-ancestor',N20_CHECKPOINT_SHA,'HEAD'): fail('HEAD does not descend from N20 remediated scientific checkpoint')
    ref=os.environ.get('GITHUB_HEAD_REF') or os.environ.get('GITHUB_REF_NAME') or ''
    if ref and not ref.startswith('football3/'): fail(f'non-football3 ref: {ref}')
    print('PASS: football3 third-pass V3 policy, V2 runtime contract, CI trigger, lineage, consumption and single-authority boundary validated')


if __name__=='__main__': main()
