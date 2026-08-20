from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path('football-data/research')
N20_WORKFLOW = Path('.github/workflows/football3-c072n20-p1000-evaluation.yml')
REQUIRED = [
    ROOT / 'football3_core.py',
    ROOT / 'validate_football3_experiment.py',
    ROOT / 'validate_football3_research_policy_v3.py',
    ROOT / 'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json',
    ROOT / 'FOOTBALL3_GLOBAL_CONSUMPTION_AUDIT_TEMPLATE_V1.json',
    ROOT / 'FOOTBALL3_EXECUTION_STANDARD_V2.md',
    ROOT / 'FOOTBALL3_RESEARCH_POLICY_V3.json',
    ROOT / 'FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json',
    ROOT / 'run_football3_synthetic_prelabel_smoke.py',
    ROOT / 'FOOTBALL3_HISTORICAL_PASS_ENGINEERING_AUDIT_20260820.md',
    ROOT / 'run_c072n20_p1000_evaluation_replay.py',
    N20_WORKFLOW,
]
STALE_AUTHORITIES = [
    ROOT / 'FOOTBALL3_RESEARCH_POLICY_V2.json',
    ROOT / 'validate_football3_research_policy_v2.py',
    ROOT / 'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V1.json',
    ROOT / 'FOOTBALL3_EXECUTION_STANDARD_V1.md',
]

KNOWN_HISTORICAL = {
    'F2': ROOT / 'evaluate_c072f2_ou25_movement_forward_confirm.py',
    'I2_RAW': ROOT / 'evaluate_c072i2_dgiven_t_forward_confirm.py',
    'I2_WRAPPER': ROOT / 'run_c072i2_dgiven_t_forward_confirm.py',
    'K2': ROOT / 'evaluate_c072k2_joint_low_score_confirm.py',
    'N20_RAW': ROOT / 'run_c072n20_p1000_evaluation.py',
    'N20_REPLAY': ROOT / 'run_c072n20_p1000_evaluation_replay.py',
}

FORBIDDEN_SPLIT_NAMES = {
    'train_test_split', 'ShuffleSplit', 'StratifiedShuffleSplit',
    'KFold', 'StratifiedKFold', 'RepeatedKFold', 'RepeatedStratifiedKFold',
}


def text(path: Path) -> str:
    if not path.exists():
        raise RuntimeError(f'missing required file: {path}')
    return path.read_text(encoding='utf-8')


def parse(path: Path) -> ast.AST:
    return ast.parse(text(path), filename=str(path))


def attr_T_lines(path: Path) -> list[int]:
    return sorted({n.lineno for n in ast.walk(parse(path)) if isinstance(n, ast.Attribute) and n.attr == 'T'})


def forbidden_split_calls(path: Path) -> list[dict]:
    out=[]
    for n in ast.walk(parse(path)):
        if not isinstance(n, ast.Call):
            continue
        fn=n.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ''
        if name in FORBIDDEN_SPLIT_NAMES:
            out.append({'name':name,'line':n.lineno})
    return out


def main() -> int:
    findings=[]
    blockers=[]
    warnings=[]

    for p in REQUIRED:
        if not p.exists():
            blockers.append(f'missing remediation file {p}')
    stale=[str(p) for p in STALE_AUTHORITIES if p.exists()]
    if stale:
        blockers.append(f'stale football3 authority files present: {stale}')
    if blockers:
        raise SystemExit(json.dumps({'status':'BLOCK','blockers':blockers},indent=2))

    policy=json.loads(text(ROOT/'FOOTBALL3_RESEARCH_POLICY_V3.json'))
    registry=json.loads(text(ROOT/'FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json'))
    template=json.loads(text(ROOT/'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json'))
    audit_template=json.loads(text(ROOT/'FOOTBALL3_GLOBAL_CONSUMPTION_AUDIT_TEMPLATE_V1.json'))
    if policy.get('project_id')!='football3': blockers.append('policy project_id')
    if policy.get('scientific_root',{}).get('sha')!='e3e73c998020beef585cc459a69ea5b73b44ddb3': blockers.append('policy root sha')
    if policy.get('prediction_contract',{}).get('master_prediction_cutoff')!='T-15m': blockers.append('policy master cutoff')
    if policy.get('prediction_contract',{}).get('strong_same_cutoff_market_baseline_required') is not True: blockers.append('strong same-cutoff market baseline rule')
    if policy.get('prediction_contract',{}).get('missing_or_invalid_feature_timestamp_fails_closed') is not True: blockers.append('strict PIT timestamp rule')
    if registry.get('rules',{}).get('viewed_target_labels_are_globally_consumed') is not True: blockers.append('global consumption rule')
    if template.get('schema_version')!=2: blockers.append('V2 experiment contract template schema')
    pc=template.get('prediction_cutoff',{})
    if not (pc.get('master')==pc.get('baseline')==pc.get('candidate')=='T-15m'):
        blockers.append('template master/same-cutoff mismatch')
    baseline=template.get('baseline',{})
    for key in ('market_anchor','same_cutoff','latest_snapshot_at_or_before_cutoff','devigged','representation_frozen_before_labels'):
        if baseline.get(key) is not True: blockers.append(f'template strong baseline gate missing: {key}')
    if template.get('metrics',{}).get('calibration',{}).get('required') is not True: blockers.append('template calibration gate')
    if 'success_gates' not in template: blockers.append('template numerical success gates')
    if audit_template.get('real_target_values_read') != 0: blockers.append('consumption audit template must be zero-label')
    for key in ('registry_checked','github_history_checked','airtable_history_checked'):
        if audit_template.get(key) is not True: blockers.append(f'consumption audit template missing {key}')

    # F2: technically executable, but explicitly only coarse opening/closing PIT semantics.
    f2=KNOWN_HISTORICAL['F2']
    if f2.exists():
        f2t=text(f2)
        if attr_T_lines(f2): blockers.append('unexpected .T in F2')
        if forbidden_split_calls(f2): blockers.append('random/non-temporal split primitive in F2')
        if 'COARSE_OPEN_CLOSE_SEMANTICS_ONLY_NO_IMMUTABLE_QUOTE_TIMESTAMPS' not in f2t:
            warnings.append('F2 PIT limitation marker not found directly in evaluator; retained in audit document')
        findings.append({'experiment':'C072-F2','status':'BOUND','reason':'opening->closing information; not same-cutoff T-15m alpha proof'})
    else:
        warnings.append('F2 evaluator not present on current branch path')

    # I2 raw contains the known pandas .T bug, but its one-shot workflow executed a committed wrapper that fixed it first.
    raw=KNOWN_HISTORICAL['I2_RAW']; wrapper=KNOWN_HISTORICAL['I2_WRAPPER']
    if raw.exists() and wrapper.exists():
        raw_lines=attr_T_lines(raw)
        wt=text(wrapper)
        if not raw_lines:
            warnings.append('I2 raw evaluator no longer exposes historical .T issue')
        required=["scored['T'].map", "even['T']//2"]
        if not all(x in wt for x in required): blockers.append('I2 historical engineering wrapper no longer proves exact .T corrections')
        findings.append({'experiment':'C072-I2','status':'REMEDIATED_HISTORICAL','raw_dot_T_lines':raw_lines,'reason':'executed wrapper corrected known pandas target-column ambiguity before one-shot run'})
    else:
        warnings.append('I2 raw/wrapper files not both present')

    # K2: no known .T or random split, but its joint metric gate lacked RPS.
    k2=KNOWN_HISTORICAL['K2']
    if k2.exists():
        kt=text(k2)
        if attr_T_lines(k2): blockers.append('unexpected .T in K2')
        if forbidden_split_calls(k2): blockers.append('random/non-temporal split primitive in K2')
        has_rps='RPS' in kt or 'rps' in kt.lower()
        findings.append({'experiment':'C072-K2','status':'BOUND','rps_in_script':has_rps,'reason':'historical LL/Brier evidence only under current contract if joint RPS was absent'})
        if has_rps:
            warnings.append('K2 now contains RPS token; historical audit should be manually reconciled if code changed')
    else:
        warnings.append('K2 evaluator not present on current branch path')

    # N20 raw file is preserved as immutable failed-precursor provenance and must never execute directly.
    n20=KNOWN_HISTORICAL['N20_RAW']; replay=KNOWN_HISTORICAL['N20_REPLAY']
    if n20.exists() and replay.exists():
        raw_lines=attr_T_lines(n20)
        nt=text(n20); rt=text(replay); wf=text(N20_WORKFLOW)
        if 'y=test.T.to_numpy(int)' not in nt:
            warnings.append('N20 raw precursor no longer contains historical failing expression; verify provenance if intentionally migrated')
        if 'old="y=test.T.to_numpy(int)"' not in rt or 'new="y=test[\'T\'].to_numpy(int)"' not in rt:
            blockers.append('N20 replay wrapper no longer proves exact one-line target-column correction')
        if 'python football-data/research/run_c072n20_p1000_evaluation_replay.py' not in wf:
            blockers.append('N20 workflow does not execute replay wrapper')
        for line in wf.splitlines():
            if line.strip().startswith('python football-data/research/run_c072n20_p1000_evaluation.py '):
                blockers.append('N20 workflow can still execute defective raw precursor directly')
        if forbidden_split_calls(n20): blockers.append('random/non-temporal split primitive in N20 raw')
        findings.append({'experiment':'C072-N20','status':'REMEDIATED_EXECUTION_PATH_RAW_FROZEN','raw_dot_T_lines':raw_lines,'reason':'raw precursor retained for audit; workflow executes exact one-line replay correction only'})
    else:
        warnings.append('N20 raw/replay files not both present')

    # New mandatory infrastructure itself must never use DataFrame.T or random split primitives.
    for p in [ROOT/'football3_core.py', ROOT/'validate_football3_experiment.py', ROOT/'run_football3_synthetic_prelabel_smoke.py']:
        if attr_T_lines(p): blockers.append(f'.T present in remediation infrastructure: {p}')
        if forbidden_split_calls(p): blockers.append(f'forbidden split primitive called in remediation infrastructure: {p}')

    status='FOOTBALL3_EXECUTION_SURFACE_AUDIT_PASS' if not blockers else 'FOOTBALL3_EXECUTION_SURFACE_AUDIT_BLOCK'
    out={
        'status':status,
        'blockers':blockers,
        'warnings':warnings,
        'findings':findings,
        'master_prediction_cutoff':'T-15m',
        'contract_schema_version':2,
        'real_target_labels_opened':0,
        'models_fit_or_scored':0,
        'sealed_pools_opened':0,
    }
    Path('football-data/research/football3_execution_surface_audit_summary.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(out,indent=2))
    return 0 if not blockers else 2


if __name__=='__main__':
    raise SystemExit(main())
