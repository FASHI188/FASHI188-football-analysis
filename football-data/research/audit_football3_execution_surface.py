from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT=Path('football-data/research')
N20_WORKFLOW=Path('.github/workflows/football3-c072n20-p1000-evaluation.yml')
FULLSTACK_WORKFLOW=Path('.github/workflows/football3-full-stack-remediation.yml')
POLICY_WORKFLOW=Path('.github/workflows/football3-research-policy-integrity.yml')
REQUIRED=[
    ROOT/'football3_core.py', ROOT/'validate_football3_experiment.py', ROOT/'validate_football3_research_policy_v3.py',
    ROOT/'audit_football3_lineage.py', ROOT/'audit_football3_pr_scope.py', ROOT/'audit_football3_changed_scientific_files.py',
    ROOT/'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json', ROOT/'FOOTBALL3_GLOBAL_CONSUMPTION_AUDIT_TEMPLATE_V1.json',
    ROOT/'FOOTBALL3_EXECUTION_STANDARD_V2.md', ROOT/'FOOTBALL3_RESEARCH_POLICY_V3.json', ROOT/'FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json',
    ROOT/'run_football3_synthetic_prelabel_smoke.py', ROOT/'FOOTBALL3_HISTORICAL_PASS_ENGINEERING_AUDIT_20260820.md',
    ROOT/'run_c072n20_p1000_evaluation_replay.py', N20_WORKFLOW, FULLSTACK_WORKFLOW, POLICY_WORKFLOW,
]
STALE_AUTHORITIES=[ROOT/'FOOTBALL3_RESEARCH_POLICY_V2.json',ROOT/'validate_football3_research_policy_v2.py',ROOT/'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V1.json',ROOT/'FOOTBALL3_EXECUTION_STANDARD_V1.md']
KNOWN_HISTORICAL={
    'F2':ROOT/'evaluate_c072f2_ou25_movement_forward_confirm.py',
    'I2_RAW':ROOT/'evaluate_c072i2_dgiven_t_forward_confirm.py',
    'I2_WRAPPER':ROOT/'run_c072i2_dgiven_t_forward_confirm.py',
    'K2':ROOT/'evaluate_c072k2_joint_low_score_confirm.py',
    'N20_RAW':ROOT/'run_c072n20_p1000_evaluation.py',
    'N20_REPLAY':ROOT/'run_c072n20_p1000_evaluation_replay.py',
}
FORBIDDEN_SPLIT_NAMES={'train_test_split','ShuffleSplit','StratifiedShuffleSplit','KFold','StratifiedKFold','RepeatedKFold','RepeatedStratifiedKFold'}


def text(path:Path)->str:
    if not path.exists(): raise RuntimeError(f'missing required file: {path}')
    return path.read_text(encoding='utf-8')


def parse(path:Path)->ast.AST: return ast.parse(text(path),filename=str(path))

def attr_T_lines(path:Path)->list[int]: return sorted({n.lineno for n in ast.walk(parse(path)) if isinstance(n,ast.Attribute) and n.attr=='T'})

def forbidden_split_calls(path:Path)->list[dict]:
    out=[]
    for n in ast.walk(parse(path)):
        if isinstance(n,ast.Call):
            fn=n.func; name=fn.id if isinstance(fn,ast.Name) else fn.attr if isinstance(fn,ast.Attribute) else ''
            if name in FORBIDDEN_SPLIT_NAMES: out.append({'name':name,'line':n.lineno})
    return out


def main()->int:
    findings=[]; blockers=[]; warnings=[]
    for path in REQUIRED:
        if not path.exists(): blockers.append(f'missing remediation file {path}')
    stale=[str(path) for path in STALE_AUTHORITIES if path.exists()]
    if stale: blockers.append(f'stale football3 authority files present: {stale}')
    if blockers:
        print(json.dumps({'status':'BLOCK','blockers':blockers},indent=2)); return 2

    policy=json.loads(text(ROOT/'FOOTBALL3_RESEARCH_POLICY_V3.json'))
    registry=json.loads(text(ROOT/'FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json'))
    template=json.loads(text(ROOT/'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json'))
    audit=json.loads(text(ROOT/'FOOTBALL3_GLOBAL_CONSUMPTION_AUDIT_TEMPLATE_V1.json'))
    core=text(ROOT/'football3_core.py'); validator=text(ROOT/'validate_football3_experiment.py')
    fullwf=text(FULLSTACK_WORKFLOW); policywf=text(POLICY_WORKFLOW)

    if policy.get('project_id')!='football3': blockers.append('policy project_id')
    if policy.get('scientific_root',{}).get('sha')!='e3e73c998020beef585cc459a69ea5b73b44ddb3': blockers.append('policy root sha')
    pred=policy.get('prediction_contract',{})
    if pred.get('master_prediction_cutoff')!='T-15m': blockers.append('policy master cutoff')
    if pred.get('timezone_aware_timestamp_required') is not True: blockers.append('timezone-aware timestamp rule')
    if pred.get('runtime_branch_must_equal_contract_branch') is not True: blockers.append('runtime branch binding rule')
    execution=policy.get('execution_core',{})
    if execution.get('canonical_evaluator')!='football3_core.evaluate_frozen_experiment': blockers.append('canonical evaluator policy')
    if execution.get('all_active_v2_contracts_revalidated_each_scientific_pr') is not True: blockers.append('active V2 contract scan policy')
    if registry.get('rules',{}).get('viewed_target_labels_are_globally_consumed') is not True: blockers.append('global consumption rule')
    if template.get('schema_version')!=2: blockers.append('V2 contract schema')
    pc=template.get('prediction_cutoff',{})
    if not (pc.get('master')==pc.get('baseline')==pc.get('candidate')=='T-15m'): blockers.append('template cutoff mismatch')
    if template.get('data_plan',{}).get('identity_lock_format')!='sha256_csv_v1': blockers.append('semantic identity lock format')
    if template.get('metrics',{}).get('implementation')!='football3_core.evaluate_frozen_experiment': blockers.append('template canonical evaluator')
    if 'Top1ECE_delta_max' not in template.get('success_gates',{}).get('secondary_noninferiority',{}): blockers.append('Top1ECE gate')
    if audit.get('real_target_values_read')!=0 or audit.get('connected_audit_status')!='VERIFIED_ZERO_LABEL': blockers.append('connected zero-label audit template')
    if not isinstance(audit.get('github_receipt'),dict) or not isinstance(audit.get('airtable_receipt'),dict): blockers.append('structured external receipts')
    for token in ('def evaluate_frozen_experiment(','timezone-naive timestamp forbidden','paired_bootstrap_proper_score_deltas'):
        if token not in core: blockers.append(f'core missing {token}')
    for token in ('REQUIRED_SCORING_CALLS','identity lock must have exact single-column header','contract branch'):
        if token not in validator: blockers.append(f'validator missing {token}')
    if 'pull_request:\n    branches:' in fullwf or 'pull_request:\n    branches:' in policywf: blockers.append('wrong PR base-branch filter present')
    if "startsWith(github.head_ref, 'football3/')" not in fullwf: blockers.append('football3 head-branch full-stack trigger missing')
    if 'audit_football3_lineage.py' not in fullwf or 'audit_football3_lineage.py' not in policywf: blockers.append('lineage CI missing')

    f2=KNOWN_HISTORICAL['F2']
    if f2.exists():
        f2t=text(f2)
        if attr_T_lines(f2): blockers.append('unexpected .T in F2')
        if forbidden_split_calls(f2): blockers.append('random/non-temporal split primitive in F2')
        if 'COARSE_OPEN_CLOSE_SEMANTICS_ONLY_NO_IMMUTABLE_QUOTE_TIMESTAMPS' not in f2t: warnings.append('F2 PIT limitation marker remains only in audit document')
        findings.append({'experiment':'C072-F2','status':'BOUND','reason':'opening->closing information; not same-cutoff T-15m alpha proof'})
    else: warnings.append('F2 evaluator not present')

    raw=KNOWN_HISTORICAL['I2_RAW']; wrapper=KNOWN_HISTORICAL['I2_WRAPPER']
    if raw.exists() and wrapper.exists():
        raw_lines=attr_T_lines(raw); wt=text(wrapper)
        if not all(x in wt for x in ("scored['T'].map","even['T']//2")): blockers.append('I2 historical .T wrapper proof missing')
        findings.append({'experiment':'C072-I2','status':'REMEDIATED_HISTORICAL','raw_dot_T_lines':raw_lines})
    else: warnings.append('I2 raw/wrapper files not both present')

    k2=KNOWN_HISTORICAL['K2']
    if k2.exists():
        if attr_T_lines(k2): blockers.append('unexpected .T in K2')
        if forbidden_split_calls(k2): blockers.append('random/non-temporal split primitive in K2')
        findings.append({'experiment':'C072-K2','status':'BOUND','reason':'historical LL/Brier only; not full current contract'})
    else: warnings.append('K2 evaluator not present')

    n20=KNOWN_HISTORICAL['N20_RAW']; replay=KNOWN_HISTORICAL['N20_REPLAY']
    if n20.exists() and replay.exists():
        nt=text(n20); rt=text(replay); wf=text(N20_WORKFLOW); raw_lines=attr_T_lines(n20)
        if 'y=test.T.to_numpy(int)' not in nt: warnings.append('N20 raw historical failing expression changed')
        if 'old="y=test.T.to_numpy(int)"' not in rt or 'new="y=test[\'T\'].to_numpy(int)"' not in rt: blockers.append('N20 exact replay correction proof missing')
        if 'python football-data/research/run_c072n20_p1000_evaluation_replay.py' not in wf: blockers.append('N20 workflow replay path missing')
        findings.append({'experiment':'C072-N20','status':'RAW_PROVENANCE_EXECUTION_PATH_REMEDIATED','raw_dot_T_lines':raw_lines})
    else: warnings.append('N20 raw/replay files not both present')

    for path in (ROOT/'football3_core.py',ROOT/'validate_football3_experiment.py',ROOT/'run_football3_synthetic_prelabel_smoke.py'):
        if attr_T_lines(path): blockers.append(f'.T present in remediation infrastructure: {path}')
        if forbidden_split_calls(path): blockers.append(f'forbidden split primitive in remediation infrastructure: {path}')

    status='FOOTBALL3_EXECUTION_SURFACE_AUDIT_PASS' if not blockers else 'FOOTBALL3_EXECUTION_SURFACE_AUDIT_BLOCK'
    out={'status':status,'blockers':blockers,'warnings':warnings,'findings':findings,'master_prediction_cutoff':'T-15m','contract_schema_version':2,'third_pass_runtime_hardening':True,'real_target_labels_opened':0,'models_fit_or_scored':0,'sealed_pools_opened':0}
    Path('football-data/research/football3_execution_surface_audit_summary.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(out,indent=2)); return 0 if not blockers else 2


if __name__=='__main__': raise SystemExit(main())
