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
INPUT_AUDIT_TEMPLATE=ROOT/'FOOTBALL3_INPUT_SEMANTICS_AUDIT_TEMPLATE_V1.json'
CONSUMPTION_AUDIT_TEMPLATE=ROOT/'FOOTBALL3_GLOBAL_CONSUMPTION_AUDIT_TEMPLATE_V1.json'
EXECUTION_STANDARD=ROOT/'FOOTBALL3_EXECUTION_STANDARD_V2.md'
CORE_PATH=ROOT/'football3_core.py'
LINEAGE_AUDIT=ROOT/'audit_football3_lineage.py'
CHANGED_AUDIT=ROOT/'audit_football3_changed_scientific_files.py'
PR_SCOPE_AUDIT=ROOT/'audit_football3_pr_scope.py'
FULLSTACK_WF=pathlib.Path('.github/workflows/football3-full-stack-remediation.yml')
POLICY_WF=pathlib.Path('.github/workflows/football3-research-policy-integrity.yml')
STALE_AUTHORITY_PATHS=[
    ROOT/'FOOTBALL3_RESEARCH_POLICY_V2.json', ROOT/'validate_football3_research_policy_v2.py',
    ROOT/'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V1.json', ROOT/'FOOTBALL3_EXECUTION_STANDARD_V1.md',
]


def fail(msg:str)->None:
    print(f'FAIL: {msg}',file=sys.stderr); raise SystemExit(1)


def git_ok(*args:str)->bool:
    return subprocess.run(['git',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE).returncode==0


def main()->None:
    required=(POLICY_PATH,CURRENT_PATH,REGISTRY_PATH,CONTRACT_TEMPLATE,INPUT_AUDIT_TEMPLATE,CONSUMPTION_AUDIT_TEMPLATE,EXECUTION_STANDARD,CORE_PATH,LINEAGE_AUDIT,CHANGED_AUDIT,PR_SCOPE_AUDIT,FULLSTACK_WF,POLICY_WF)
    for path in required:
        if not path.exists(): fail(f'missing {path}')
    stale=[str(path) for path in STALE_AUTHORITY_PATHS if path.exists()]
    if stale: fail(f'stale authority files still present: {stale}')

    p=json.loads(POLICY_PATH.read_text(encoding='utf-8'))
    r=json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    t=json.loads(CONTRACT_TEMPLATE.read_text(encoding='utf-8'))
    ia=json.loads(INPUT_AUDIT_TEMPLATE.read_text(encoding='utf-8'))
    ga=json.loads(CONSUMPTION_AUDIT_TEMPLATE.read_text(encoding='utf-8'))
    current=CURRENT_PATH.read_text(encoding='utf-8')
    core=CORE_PATH.read_text(encoding='utf-8')
    lineage=LINEAGE_AUDIT.read_text(encoding='utf-8')
    changed=CHANGED_AUDIT.read_text(encoding='utf-8')
    fullwf=FULLSTACK_WF.read_text(encoding='utf-8')
    policywf=POLICY_WF.read_text(encoding='utf-8')

    if p.get('schema')!='FOOTBALL3_RESEARCH_POLICY_V3' or p.get('project_id')!='football3': fail('policy schema/project')
    if p.get('scientific_root',{}).get('sha')!=ROOT_SHA or p.get('scientific_root',{}).get('continuation_prefix')!='football3/': fail('root/prefix')
    q=p.get('quarantine',{})
    if q.get('seed_experiments')!=['C073','C074','C075','C076','C077']: fail('quarantine seed set')
    if q.get('later_derived_research_also_quarantined') is not True or q.get('quarantine_is_lineage_not_name_based') is not True: fail('derived quarantine rule')
    if q.get('direct_merge_ancestry_forbidden') is not True or q.get('scientific_cherry_pick_patch_id_overlap_forbidden') is not True: fail('quarantine lineage gates')
    if q.get('missing_quarantine_refs_fails_closed') is not True: fail('quarantine missing-ref gate')
    if 'all_research_refs' not in lineage or 'derived_quarantine_refs' not in lineage: fail('derived-lineage implementation missing')
    if p.get('primary_optimization',{}).get('direct_draw_top1') is not False: fail('direct Draw gate')

    pred=p.get('prediction_contract',{})
    for k in ('master_cutoff_change_requires_new_product_contract','strong_same_cutoff_market_baseline_required','candidate_feature_timestamp_manifest_required','all_model_features_must_be_declared','timezone_aware_timestamp_required','missing_invalid_or_timezone_naive_timestamp_fails_closed','runtime_branch_must_equal_contract_branch','zero_label_input_semantics_audit_required','external_market_semantics_not_claimed_as_cryptographically_proven_by_ci'):
        if pred.get(k) is not True: fail(f'prediction contract gate {k}')
    if pred.get('master_prediction_cutoff')!='T-15m': fail('master cutoff')

    ex=p.get('execution_core',{})
    if ex.get('canonical_evaluator')!='football3_core.evaluate_frozen_experiment': fail('canonical evaluator')
    if ex.get('mandatory_proper_scores')!=['LogLoss','Brier','RPS'] or ex.get('mandatory_calibration_metrics')!=['Top1ECE','ClasswiseECE']: fail('metric bundle')
    for k in ('scored_identity_must_equal_frozen_identity_lock','runtime_sample_minimum_enforced','all_active_v2_contracts_revalidated_each_scientific_pr','changed_python_requires_exact_contract_or_helper_binding','prefix_based_infrastructure_exemption_forbidden','alternate_executable_surface_fails_closed','static_ast_guard_is_not_claimed_as_full_control_flow_proof'):
        if ex.get(k) is not True: fail(f'execution gate {k}')
    if 'def evaluate_frozen_experiment(' not in core or 'assert_scoring_identities_match_contract' not in core or '_enforce_runtime_sample_plan' not in core: fail('canonical identity/sample runtime implementation missing')
    if 'EXEMPT_PREFIXES' in changed: fail('prefix-based infrastructure exemption reintroduced')
    if "SCIENTIFIC_CODE_PREFIXES = ('football-data/', 'scripts/')" not in changed: fail('repo-wide changed-code surface missing')

    ci=p.get('ci_trigger_contract',{})
    for k in ('football3_head_to_main_must_trigger_full_stack','full_stack_paths_cover_football_data_scripts_all_workflows','football3_branch_may_only_modify_football3_named_workflows','football3_owned_authority_modified_from_nonfootball3_head_fails'):
        if ci.get(k) is not True: fail(f'CI contract {k}')
    if 'pull_request:\n    branches:' in fullwf or 'pull_request:\n    branches:' in policywf: fail('wrong pull_request base-branch filter reintroduced')
    for path_token in ("- 'football-data/**'","- 'scripts/**'","- '.github/workflows/**'"):
        if path_token not in fullwf: fail(f'full-stack trigger missing {path_token}')
    if "startsWith(github.head_ref, 'football3/')" not in fullwf: fail('full-stack head gate missing')
    if 'audit_football3_lineage.py' not in fullwf or 'audit_football3_lineage.py' not in policywf: fail('lineage audit missing from CI')

    val=p.get('validation',{})
    for k in ('calibration_required','domain_consistency_required','numerical_success_gates_frozen_before_labels','canonical_evaluator_applies_success_gates','bootstrap_all_proper_scores_reported','canonical_evaluator_checks_scored_identity_lock','canonical_evaluator_checks_runtime_sample_minimum','confirmation_requires_frozen_power_plan','confirmation_power_required_n_recomputed_by_validator'):
        if val.get(k) is not True: fail(f'validation gate {k}')
    if val.get('confirmation_minimum_planned_power',0)<0.8 or val.get('optional_stopping') is not False: fail('power/stopping')

    gc=p.get('global_consumption',{})
    if gc.get('identity_lock_format')!='sha256_csv_v1': fail('identity format')
    for k in ('identity_lock_content_semantics_verified','scored_identity_ordered_digest_must_match_contract','fresh_evidence_requires_structured_connected_audit_receipts','fresh_evidence_requires_zero_unresolved_historical_identity_gaps','same_scientific_hypothesis_previously_viewed_forces_reuse_classification','external_audit_reuse_condition_forces_replication_or_reproduction','github_ci_verifies_receipt_structure_not_external_source_truth'):
        if gc.get(k) is not True: fail(f'global consumption gate {k}')
    required_dims={'repository','revision','season','match identity','market fields','label definition','scientific hypothesis'}
    if not required_dims.issubset(set(gc.get('audit_dimensions',[]))): fail('global audit dimensions incomplete')
    if r.get('rules',{}).get('viewed_target_labels_are_globally_consumed') is not True: fail('global registry rule')
    if not all(x.get('authorized_access') is False for x in r.get('sealed',[])): fail('sealed registry access')

    if t.get('schema_version')!=2 or t.get('prediction_cutoff',{}).get('master')!='T-15m': fail('contract template root fields')
    if t.get('data_plan',{}).get('identity_lock_format')!='sha256_csv_v1' or t.get('data_plan',{}).get('scored_identity_must_equal_lock') is not True: fail('contract identity binding')
    if t.get('data_plan',{}).get('input_semantics_audit',{}).get('required') is not True: fail('contract input semantics audit')
    if t.get('candidate',{}).get('all_model_features_must_be_declared') is not True or not isinstance(t.get('candidate',{}).get('feature_timestamp_map'),dict): fail('contract feature manifest')
    if t.get('sample_plan',{}).get('runtime_minimum_n_enforced') is not True: fail('contract runtime N')
    if t.get('metrics',{}).get('implementation')!='football3_core.evaluate_frozen_experiment': fail('contract canonical evaluator')
    if ia.get('real_target_values_read')!=0 or ia.get('connected_audit_status')!='VERIFIED_ZERO_LABEL': fail('input semantics template boundary')
    if ga.get('real_target_values_read')!=0 or ga.get('connected_audit_status')!='VERIFIED_ZERO_LABEL': fail('global audit template boundary')
    if not isinstance(ga.get('cross_project_duplication'),dict): fail('cross-project duplication template missing')

    for token in ('C072-C',ROOT_SHA,'C073-C077','C072N20_P1000_PILOT_NO_SIGNAL','P(T=0,1,2,3,4,5,6,7+)','T-15m','C070-F Confirmation1597','N17 reserve266','N18C confirmation150','FULL_STACK_ROOT_CAUSE_REMEDIATION_V4_CROSS_LAYER_AUDIT'):
        if token not in current: fail(f'current missing {token}')

    if pathlib.Path('.git').exists():
        if not git_ok('merge-base','--is-ancestor',ROOT_SHA,'HEAD'): fail('HEAD outside C072-C lineage')
        if not git_ok('merge-base','--is-ancestor',N20_CHECKPOINT_SHA,'HEAD'): fail('HEAD does not descend from N20 checkpoint')
    ref=os.environ.get('GITHUB_HEAD_REF') or os.environ.get('GITHUB_REF_NAME') or ''
    if ref and not ref.startswith('football3/'): fail(f'non-football3 ref: {ref}')
    print('PASS: football3 V4 cross-layer policy/execution/identity/PIT/consumption/sample/lineage controls validated')


if __name__=='__main__': main()
