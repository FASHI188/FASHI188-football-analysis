#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT_SHA = 'e3e73c998020beef585cc459a69ea5b73b44ddb3'
N20_CHECKPOINT_SHA = 'a8cef08fd57315c16a9adc23935b871c98ef575a'
POLICY_PATH = pathlib.Path('football-data/research/FOOTBALL3_RESEARCH_POLICY_V3.json')
CURRENT_PATH = pathlib.Path('football-data/research/FOOTBALL3_INDEPENDENT_CURRENT.md')
REGISTRY_PATH = pathlib.Path('football-data/research/FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json')


def fail(msg: str) -> None:
    print(f'FAIL: {msg}', file=sys.stderr)
    raise SystemExit(1)


def git_ok(*args: str) -> bool:
    return subprocess.run(['git', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0


def main() -> None:
    for p in (POLICY_PATH, CURRENT_PATH, REGISTRY_PATH):
        if not p.exists(): fail(f'missing {p}')
    p=json.loads(POLICY_PATH.read_text(encoding='utf-8'))
    r=json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    c=CURRENT_PATH.read_text(encoding='utf-8')

    if p.get('schema')!='FOOTBALL3_RESEARCH_POLICY_V3': fail('policy schema')
    if p.get('project_id')!='football3': fail('project_id')
    if p.get('scientific_root',{}).get('sha')!=ROOT_SHA: fail('root SHA')
    if p.get('scientific_root',{}).get('continuation_prefix')!='football3/': fail('branch prefix')
    if p.get('primary_optimization',{}).get('direct_draw_top1') is not False: fail('direct Draw gate')
    if p.get('prediction_contract',{}).get('baseline_candidate_same_cutoff_required') is not True: fail('same-cutoff gate')
    if p.get('execution_core',{}).get('mandatory_proper_scores')!=['LogLoss','Brier','RPS']: fail('proper scores')
    if p.get('execution_core',{}).get('random_split') is not False: fail('random split gate')
    if p.get('prelabel_protocol',{}).get('synthetic_smoke_real_target_count')!=0: fail('synthetic prelabel boundary')
    if p.get('validation',{}).get('optional_stopping') is not False: fail('optional stopping')
    if p.get('method_shopping',{}).get('same_viewed_labels_rescue') is not False: fail('method shopping')
    if r.get('rules',{}).get('viewed_target_labels_are_globally_consumed') is not True: fail('global consumption')
    if not all(x.get('authorized_access') is False for x in r.get('sealed',[])): fail('sealed registry access')

    for token in (
        'C072-C', ROOT_SHA, 'C073-C077', 'C072N20_P1000_PILOT_NO_SIGNAL',
        'P(T=0,1,2,3,4,5,6,7+)', 'C070-F Confirmation1597', 'N17 reserve266',
        'N18C confirmation150', 'FULL_STACK_ROOT_CAUSE_REMEDIATION_COMPLETE_NO_NEW_SCIENCE'
    ):
        if token not in c: fail(f'current missing {token}')

    if pathlib.Path('.git').exists():
        if not git_ok('merge-base','--is-ancestor',ROOT_SHA,'HEAD'): fail('HEAD outside C072-C lineage')
        if not git_ok('merge-base','--is-ancestor',N20_CHECKPOINT_SHA,'HEAD'): fail('HEAD does not descend from N20 remediated scientific checkpoint')

    ref=os.environ.get('GITHUB_HEAD_REF') or os.environ.get('GITHUB_REF_NAME') or ''
    if ref and not ref.startswith('football3/'):
        fail(f'non-football3 ref: {ref}')
    print('PASS: football3 V3 policy, N20 checkpoint, lineage, consumption and seals validated')


if __name__=='__main__':
    main()
