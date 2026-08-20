from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from statistics import NormalDist

ROOT_SHA = 'e3e73c998020beef585cc459a69ea5b73b44ddb3'
MASTER_CUTOFF = 'T-15m'
REQUIRED_METRICS = {'LogLoss', 'Brier', 'RPS'}
REQUIRED_CALIBRATION = {'Top1ECE', 'ClasswiseECE'}
SEALED_NAMES = {'C070-F Confirmation1597', 'N17 reserve266', 'N18C confirmation150'}
SEALED_RUNNER_TOKENS = {'confirmation1597', 'reserve266', 'confirmation150'}
FRESH_CLASSES = {'DEVELOPMENT_FRESH', 'CONFIRMATION_FRESH'}
REUSE_CLASSES = {'REPLICATION', 'REPRODUCTION'}
FORBIDDEN_CALLS = {'train_test_split','ShuffleSplit','StratifiedShuffleSplit','KFold','StratifiedKFold','RepeatedKFold','RepeatedStratifiedKFold'}
FORBIDDEN_SCIENCE_TOKENS = {'manual_draw_boost','manual_0_0_boost','manual_1_1_boost','posthoc_draw_threshold','posthoc_draw_weight','class_weight_for_draw'}
REQUIRED_SCORING_CALLS = {
    'evaluate_frozen_experiment','assert_feature_pit','assert_temporal_oos',
    'assert_master_prediction_cutoff','assert_exact_one_to_one_join','assert_sealed_boundaries',
}
HEX64 = re.compile(r'^[0-9a-f]{64}$')
REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_GITHUB_REPO = 'FASHI188/FASHI188-football-analysis'
EXPECTED_AIRTABLE_BASE = 'appLXF9IBvSCEUjJV'
REQUIRED_GITHUB_AUDIT_SCOPE = {
    'branches','pull_requests','commits','scientific-hypothesis-history',
    'source-revision-history','market-field-history',
}


class PreflightError(RuntimeError):
    pass


def fail(msg: str) -> None:
    raise PreflightError(msg)


def load_contract(path: Path) -> dict:
    try:
        c = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        fail(f'contract JSON unreadable: {e}')
    if not isinstance(c, dict):
        fail('contract must be a JSON object')
    return c


def require(c: dict, key: str):
    if key not in c:
        fail(f'missing contract key: {key}')
    return c[key]


def _norm(s: object) -> str:
    return ''.join(str(s).lower().split())


def _nonplaceholder(x: object) -> bool:
    s = str(x or '').strip()
    return bool(s) and 'REPLACE_ME' not in s


def _number(x: object, name: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        fail(f'{name} must be numeric')
    v = float(x)
    if not math.isfinite(v):
        fail(f'{name} must be finite')
    return v


def _positive_int(x: object, name: str) -> int:
    if isinstance(x, bool) or not isinstance(x, int) or x <= 0:
        fail(f'{name} must be positive integer')
    return int(x)


def _nonnegative_int(x: object, name: str) -> int:
    if isinstance(x, bool) or not isinstance(x, int) or x < 0:
        fail(f'{name} must be integer >=0')
    return int(x)


def _fraction(x: object, name: str) -> float:
    v = _number(x, name)
    if not (0.0 <= v <= 1.0):
        fail(f'{name} must be in [0,1]')
    return v


def _bool(x: object, name: str) -> bool:
    if not isinstance(x, bool):
        fail(f'{name} must be boolean')
    return x


def _sha64(x: object, name: str) -> str:
    s = str(x or '')
    if s != s.lower() or not HEX64.fullmatch(s):
        fail(f'{name} must be lowercase sha256 hex')
    return s


def _aware_iso_timestamp(x: object, name: str) -> str:
    s = str(x or '').strip()
    if not s or 'REPLACE_ME' in s:
        fail(f'{name} must be a timezone-aware timestamp')
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception as e:
        fail(f'{name} invalid timestamp: {e}')
    if d.tzinfo is None or d.utcoffset() is None:
        fail(f'{name} must include timezone information')
    return s


def _feature_manifest(candidate: dict) -> tuple[list[str], dict[str,str]]:
    cols=candidate.get('feature_columns')
    fmap=candidate.get('feature_timestamp_map')
    if not isinstance(cols,list) or not cols or any(not _nonplaceholder(x) for x in cols):
        fail('candidate.feature_columns must be a nonempty concrete list')
    cols=[str(x) for x in cols]
    if len(cols)!=len(set(cols)):
        fail('candidate.feature_columns contains duplicates')
    if not isinstance(fmap,dict) or set(fmap)!=set(cols):
        fail('candidate.feature_timestamp_map keys must exactly equal feature_columns')
    clean={str(k):str(v) for k,v in fmap.items()}
    if any(not _nonplaceholder(v) for v in clean.values()):
        fail('every candidate feature must have a concrete timestamp column')
    if candidate.get('all_model_features_must_be_declared') is not True:
        fail('all model features must be declared in contract')
    return cols,clean


def validate_contract(c: dict) -> None:
    if require(c, 'schema_version') != 2:
        fail('new football3 science requires schema_version 2')
    if require(c, 'project_id') != 'football3':
        fail('project_id must be football3')
    root = require(c, 'scientific_root')
    if root.get('experiment') != 'C072-C' or root.get('sha') != ROOT_SHA:
        fail('scientific root mismatch')
    branch = str(require(c, 'branch'))
    if not branch.startswith('football3/') or not _nonplaceholder(branch):
        fail('branch must be a concrete football3/ branch')
    if any(x in branch.upper() for x in ('C073','C074','C075','C076','C077','C078','C079')):
        fail('quarantined/derived lineage token in football3 branch')

    q = require(c, 'scientific_question')
    if q.get('primary_target') != 'P(T=0,1,2,3,4,5,6,7+)':
        fail('primary target must be complete collapsed P(T)')
    if q.get('direct_draw_optimization') is not False:
        fail('direct Draw optimization is forbidden')
    if not isinstance(q.get('materially_new_pt_hypothesis'),bool):
        fail('materially_new_pt_hypothesis must be boolean')
    if not _nonplaceholder(q.get('question')):
        fail('scientific question must be explicit')

    cutoff = require(c, 'prediction_cutoff')
    vals = [cutoff.get('master'), cutoff.get('baseline'), cutoff.get('candidate')]
    if any(_norm(x) != _norm(MASTER_CUTOFF) for x in vals):
        fail(f'football3 master product cutoff is frozen at {MASTER_CUTOFF}')
    if cutoff.get('change_requires_new_product_contract') is not True:
        fail('master-cutoff change must require a new product contract')
    if not _nonplaceholder(cutoff.get('pit_definition')):
        fail('PIT definition must be explicit')

    baseline = require(c, 'baseline')
    candidate = require(c, 'candidate')
    if not _nonplaceholder(baseline.get('description')) or not _nonplaceholder(candidate.get('description')):
        fail('baseline/candidate descriptions required')
    for key in ('market_anchor','same_cutoff','latest_snapshot_at_or_before_cutoff','devigged','representation_frozen_before_labels','market_semantics_zero_label_audit_required'):
        if baseline.get(key) is not True:
            fail(f'strong market baseline gate missing: {key}')
    if not _nonplaceholder(baseline.get('representation')):
        fail('baseline representation must be frozen and explicit')
    if not _nonplaceholder(baseline.get('quote_timestamp_column')):
        fail('baseline quote timestamp column must be explicit')
    if candidate.get('post_view_neighbor_of_parked_hypothesis') is True:
        fail('neighboring repair of a parked hypothesis is forbidden')
    _feature_manifest(candidate)

    data = require(c, 'data_plan')
    if not _nonplaceholder(data.get('source_revision')):
        fail('exact source revision required')
    if data.get('identity_lock_before_labels') is not True or data.get('scored_identity_must_equal_lock') is not True:
        fail('frozen identity lock must precede labels and equal scored identities')
    if data.get('identity_lock_format') != 'sha256_csv_v1':
        fail('identity_lock_format must be sha256_csv_v1')
    if not _nonplaceholder(data.get('identity_lock_artifact')):
        fail('zero-label identity lock artifact required')
    _sha64(data.get('identity_lock_sha256'), 'identity_lock_sha256')
    _positive_int(data.get('identity_count'), 'identity_count')
    _sha64(data.get('ordered_identity_sha256'), 'ordered_identity_sha256')

    sem=require(data,'input_semantics_audit')
    if sem.get('required') is not True or sem.get('connected_audit_status')!='VERIFIED_ZERO_LABEL':
        fail('connected zero-label input semantics audit required')
    if not _nonplaceholder(sem.get('artifact')):
        fail('input semantics audit artifact required')
    _sha64(sem.get('artifact_sha256'),'input_semantics_audit.artifact_sha256')
    for key in ('latest_market_snapshot_verified','devig_representation_verified','all_candidate_features_timestamp_mapped'):
        if sem.get(key) is not True:
            fail(f'input semantics audit gate missing: {key}')

    audit = require(data, 'global_consumption_audit')
    if audit.get('required') is not True:
        fail('global consumption audit required')
    if not _nonplaceholder(audit.get('artifact')):
        fail('global consumption audit artifact required')
    _sha64(audit.get('artifact_sha256'), 'global_consumption_audit.artifact_sha256')
    for key in ('registry_checked','github_history_checked','airtable_history_checked'):
        if audit.get(key) is not True:
            fail(f'global consumption audit missing {key}')
    if audit.get('connected_audit_status') != 'VERIFIED_ZERO_LABEL':
        fail('global consumption audit must be VERIFIED_ZERO_LABEL')
    overlap = _nonnegative_int(audit.get('target_identity_overlap_with_consumed'), 'target_identity_overlap_with_consumed')
    gaps = _nonnegative_int(audit.get('unresolved_historical_identity_gaps'), 'unresolved_historical_identity_gaps')
    dup=require(audit,'cross_project_duplication')
    same_hyp=_bool(dup.get('same_scientific_hypothesis_previously_viewed'),'cross_project_duplication.same_scientific_hypothesis_previously_viewed')
    exact_reuse=_bool(dup.get('exact_replication_or_reproduction_condition_met'),'cross_project_duplication.exact_replication_or_reproduction_condition_met')
    eclass = data.get('evidence_class')
    if eclass not in FRESH_CLASSES | REUSE_CLASSES:
        fail('invalid evidence_class')
    reuse_required = overlap > 0 or gaps > 0 or same_hyp or exact_reuse
    if reuse_required and eclass not in REUSE_CLASSES:
        fail('consumption/duplication audit requires REPLICATION or REPRODUCTION classification')
    if eclass in FRESH_CLASSES and (overlap != 0 or gaps != 0 or same_hyp or exact_reuse):
        fail('fresh evidence requires zero overlap/gaps and no previously viewed same hypothesis/reuse condition')
    if eclass in FRESH_CLASSES and q.get('materially_new_pt_hypothesis') is not True:
        fail('fresh football3 science requires a materially new P(T) hypothesis')
    if data.get('random_split') not in (False, None):
        fail('random split forbidden')
    if data.get('target_labels_before_contract', 0) != 0:
        fail('target labels before frozen contract must be zero')

    split = require(c, 'oos_design')
    if split.get('temporal') is not True or split.get('shuffle') is not False:
        fail('OOS design must be temporal and unshuffled')
    if not _nonplaceholder(split.get('folds')):
        fail('temporal fold plan must be frozen')
    _positive_int(split.get('minimum_test_rows_per_fold'), 'minimum_test_rows_per_fold')

    metrics = require(c, 'metrics')
    if not REQUIRED_METRICS.issubset(set(metrics.get('proper_scores', []))):
        fail('LogLoss/Brier/RPS all required')
    if metrics.get('top1_primary') is not False:
        fail('Top1 cannot be primary')
    if metrics.get('implementation') != 'football3_core.evaluate_frozen_experiment':
        fail('new science must use football3_core.evaluate_frozen_experiment')
    cal = require(metrics, 'calibration')
    if cal.get('required') is not True or not REQUIRED_CALIBRATION.issubset(set(cal.get('metrics', []))):
        fail('Top1ECE and ClasswiseECE calibration metrics required')
    _positive_int(cal.get('bins'), 'calibration bins')
    if cal['bins'] < 5:
        fail('calibration bins must be >=5')

    gates = require(c, 'success_gates')
    primary = require(gates, 'primary')
    if primary.get('metric') != 'LogLoss':
        fail('primary success metric must be LogLoss')
    if _number(primary.get('delta_max'), 'success_gates.primary.delta_max') > 0:
        fail('primary delta_max cannot permit LogLoss worsening')
    if _number(primary.get('bootstrap_ci_high_max'), 'success_gates.primary.bootstrap_ci_high_max') > 0:
        fail('bootstrap CI gate cannot permit LogLoss worsening')
    sec = require(gates, 'secondary_noninferiority')
    for key in ('Brier_delta_max','RPS_delta_max','Top1ECE_delta_max','ClasswiseECE_delta_max'):
        if _number(sec.get(key), f'success_gates.secondary_noninferiority.{key}') > 0:
            fail(f'{key} cannot permit worsening')
    tc = require(gates, 'temporal_consistency')
    _fraction(tc.get('minimum_fold_win_fraction'), 'minimum_fold_win_fraction')
    dc = require(gates, 'domain_consistency')
    if not _nonplaceholder(dc.get('domain_field')):
        fail('domain field required')
    if _positive_int(dc.get('minimum_domains'), 'minimum_domains') < 2:
        fail('minimum_domains must be >=2')
    _positive_int(dc.get('minimum_rows_per_domain'), 'minimum_rows_per_domain')
    _fraction(dc.get('minimum_win_fraction'), 'minimum_domain_win_fraction')
    if _number(dc.get('max_domain_logloss_regression'), 'max_domain_logloss_regression') < 0:
        fail('max_domain_logloss_regression must be nonnegative')

    boot = require(c, 'bootstrap')
    if boot.get('paired_match') is not True:
        fail('paired match bootstrap required')
    if isinstance(boot.get('resamples'), bool) or not isinstance(boot.get('resamples'), int) or boot['resamples'] < 1000:
        fail('bootstrap resamples must be integer >=1000')
    if isinstance(boot.get('seed'), bool) or not isinstance(boot.get('seed'), int):
        fail('bootstrap seed must be integer')
    ci = _number(boot.get('ci'), 'bootstrap ci')
    if not (0.80 <= ci < 1.0):
        fail('bootstrap CI must be frozen in [0.80,1)')

    sample = require(c, 'sample_plan')
    _positive_int(sample.get('development_minimum_n'), 'development_minimum_n')
    if sample.get('runtime_minimum_n_enforced') is not True:
        fail('runtime sample minimum enforcement required')
    if sample.get('optional_stopping') is not False:
        fail('optional stopping forbidden')
    if sample.get('confirmation') is True:
        if sample.get('power_or_precision_plan_frozen') is not True:
            fail('confirmation requires frozen power/precision plan')
        if eclass != 'CONFIRMATION_FRESH':
            fail('confirmation must be classified CONFIRMATION_FRESH')
        if reuse_required:
            fail('fresh confirmation requires zero consumed/duplication blockers')
        _positive_int(sample.get('minimum_n'), 'confirmation minimum_n')
        planned_power = _number(sample.get('planned_power'), 'planned_power')
        if not (0.80 <= planned_power < 1.0):
            fail('confirmation planned_power must be in [0.80,1)')
        alpha = _number(sample.get('alpha'), 'confirmation alpha')
        if not (0 < alpha <= 0.20):
            fail('confirmation alpha must be in (0,0.20]')
        if sample.get('planning_basis') not in {'DEVELOPMENT_ONLY','EXTERNAL_PRIOR'}:
            fail('confirmation planning_basis must be DEVELOPMENT_ONLY or EXTERNAL_PRIOR')
        if sample.get('planning_artifact_schema') != 'football3_paired_power_plan_v1':
            fail('confirmation planning_artifact_schema must be football3_paired_power_plan_v1')
        if not _nonplaceholder(sample.get('planning_artifact')):
            fail('confirmation planning artifact required')
        _sha64(sample.get('planning_artifact_sha256'), 'planning_artifact_sha256')

    sealed = require(c, 'sealed_boundaries')
    present = {str(x.get('name')) for x in sealed if isinstance(x, dict)}
    if not SEALED_NAMES.issubset(present):
        fail('known sealed pools must be declared')
    for x in sealed:
        if x.get('authorized_access_count', 0) != 0:
            fail(f"sealed access nonzero in contract: {x.get('name')}")

    method = require(c, 'method_shopping')
    if method.get('same_labels_rescue_allowed') is not False:
        fail('same-label rescue must be false')
    if not isinstance(method.get('frozen_dimensions'), list) or not method['frozen_dimensions']:
        fail('frozen method dimensions must be listed')
    auth = require(c, 'authorization')
    if auth.get('new_target_access_requires_explicit_user_authorization') is not True:
        fail('explicit user target-access authorization must remain mandatory')


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _ordered_digest(ids: list[str]) -> str:
    raw = '\n'.join(ids) + ('\n' if ids else '')
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _resolve_bound_artifact(base: Path, value: object, name: str) -> Path:
    raw = str(value or '').strip()
    if not raw or 'REPLACE_ME' in raw:
        fail(f'{name} path missing')
    p = Path(raw)
    if p.is_absolute() or '..' in p.parts:
        fail(f'{name} must be a relative non-traversing path')
    base_real = base.resolve()
    candidate_unresolved = base / p
    if candidate_unresolved.is_symlink():
        fail(f'{name} may not be a symlink')
    try:
        candidate = candidate_unresolved.resolve(strict=True)
    except FileNotFoundError:
        fail(f'{name} missing: {candidate_unresolved}')
    try:
        candidate.relative_to(base_real)
    except ValueError:
        fail(f'{name} escapes contract directory')
    if not candidate.is_file():
        fail(f'{name} must be a regular file')
    return candidate


def _validate_identity_lock(path: Path, data: dict) -> None:
    try:
        with path.open('r', encoding='utf-8', newline='') as f:
            rows = list(csv.reader(f))
    except Exception as e:
        fail(f'identity lock unreadable: {e}')
    if not rows or rows[0] != ['identity_sha256']:
        fail('identity lock must have exact single-column header identity_sha256')
    ids=[]
    for i,row in enumerate(rows[1:],start=2):
        if len(row)!=1 or not HEX64.fullmatch(row[0]):
            fail(f'identity lock row {i} must contain exactly one lowercase sha256')
        ids.append(row[0])
    if not ids or len(ids)!=len(set(ids)):
        fail('identity lock must be nonempty and duplicate-free')
    if len(ids)!=data['identity_count'] or _ordered_digest(ids)!=data['ordered_identity_sha256']:
        fail('identity lock count/order digest mismatch')


def _validate_github_receipt(r: object) -> None:
    if not isinstance(r, dict) or r.get('repository') != EXPECTED_GITHUB_REPO:
        fail('github_receipt repository/structure mismatch')
    _aware_iso_timestamp(r.get('checked_at'), 'github_receipt.checked_at')
    scope=r.get('query_scope')
    if not isinstance(scope,list) or not REQUIRED_GITHUB_AUDIT_SCOPE.issubset(set(scope)):
        fail(f'github_receipt query_scope missing required dimensions: {sorted(REQUIRED_GITHUB_AUDIT_SCOPE)}')
    _sha64(r.get('result_digest_sha256'), 'github_receipt.result_digest_sha256')


def _validate_airtable_receipt(r: object) -> None:
    if not isinstance(r, dict) or r.get('base_id') != EXPECTED_AIRTABLE_BASE:
        fail('airtable_receipt base/structure mismatch')
    _aware_iso_timestamp(r.get('checked_at'), 'airtable_receipt.checked_at')
    tables=r.get('tables_checked')
    if not isinstance(tables,list) or not {'当前状态','维护日志'}.issubset(set(tables)):
        fail('airtable_receipt must include 当前状态 and 维护日志')
    _sha64(r.get('result_digest_sha256'), 'airtable_receipt.result_digest_sha256')


def _validate_cross_project_duplication(x: object) -> dict:
    if not isinstance(x,dict):
        fail('cross_project_duplication must be structured object')
    for k in ('same_repository_previously_used','same_revision_previously_used','same_market_fields_previously_used','same_label_definition_previously_used','same_scientific_hypothesis_previously_viewed','exact_replication_or_reproduction_condition_met'):
        _bool(x.get(k),f'cross_project_duplication.{k}')
    _nonnegative_int(x.get('same_season_overlap_count'),'cross_project_duplication.same_season_overlap_count')
    _nonnegative_int(x.get('same_match_identity_overlap_count'),'cross_project_duplication.same_match_identity_overlap_count')
    if not _nonplaceholder(x.get('evidence_notes')):
        fail('cross_project_duplication.evidence_notes required')
    return x


def _validate_input_semantics_artifact(a: object, c: dict) -> None:
    if not isinstance(a,dict) or a.get('schema_version')!=1 or a.get('project_id')!='football3':
        fail('invalid input semantics audit schema/project')
    _aware_iso_timestamp(a.get('audited_at'),'input semantics audited_at')
    data=c['data_plan']; baseline=c['baseline']; candidate=c['candidate']
    if a.get('source_revision')!=data['source_revision'] or a.get('identity_lock_sha256')!=data['identity_lock_sha256']:
        fail('input semantics audit source/identity binding mismatch')
    if a.get('identity_count')!=data['identity_count'] or a.get('ordered_identity_sha256')!=data['ordered_identity_sha256']:
        fail('input semantics audit identity count/order mismatch')
    if a.get('real_target_values_read')!=0 or a.get('connected_audit_status')!='VERIFIED_ZERO_LABEL':
        fail('input semantics audit must be connected zero-label')
    b=a.get('baseline')
    if not isinstance(b,dict): fail('input semantics baseline audit missing')
    if b.get('representation')!=baseline['representation'] or b.get('quote_timestamp_column')!=baseline['quote_timestamp_column']:
        fail('input semantics baseline representation/timestamp mismatch')
    for k in ('latest_snapshot_at_or_before_cutoff_verified','devig_representation_verified'):
        if b.get(k) is not True: fail(f'input semantics baseline gate missing: {k}')
    for k in ('quote_timestamp_missing_count','post_cutoff_quote_count'):
        if _nonnegative_int(b.get(k),f'input semantics baseline {k}')!=0:
            fail(f'input semantics baseline {k} must be zero')
    cand=a.get('candidate')
    if not isinstance(cand,dict): fail('input semantics candidate audit missing')
    cols,fmap=_feature_manifest(candidate)
    if cand.get('feature_columns')!=cols or cand.get('feature_timestamp_map')!=fmap:
        fail('input semantics candidate feature/timestamp manifest mismatch')
    if cand.get('all_model_features_timestamp_mapped') is not True:
        fail('input semantics must verify all model features timestamp mapped')
    for k in ('feature_timestamp_missing_count','post_cutoff_feature_value_count'):
        if _nonnegative_int(cand.get(k),f'input semantics candidate {k}')!=0:
            fail(f'input semantics candidate {k} must be zero')


def _validate_power_plan_artifact(a: object, c: dict) -> None:
    if not isinstance(a,dict) or a.get('schema_version')!=1 or a.get('schema')!='football3_paired_power_plan_v1':
        fail('invalid confirmation power-plan schema')
    sample=c['sample_plan']
    if a.get('planning_basis')!=sample['planning_basis']:
        fail('power plan planning_basis mismatch')
    effect=_number(a.get('effect_abs'),'power plan effect_abs')
    sd=_number(a.get('paired_delta_sd'),'power plan paired_delta_sd')
    if effect<=0 or sd<=0: fail('power plan effect_abs and paired_delta_sd must be >0')
    alpha=_number(a.get('alpha'),'power plan alpha')
    power=_number(a.get('planned_power'),'power plan planned_power')
    multiplier=_number(a.get('conservative_multiplier'),'power plan conservative_multiplier')
    if alpha!=float(sample['alpha']) or power!=float(sample['planned_power']):
        fail('power plan alpha/power mismatch contract')
    if multiplier<1.0: fail('power plan conservative_multiplier must be >=1')
    development_n=_positive_int(a.get('development_or_prior_n'),'power plan development_or_prior_n')
    if sample['planning_basis']=='DEVELOPMENT_ONLY' and development_n < c['sample_plan']['development_minimum_n']:
        fail('DEVELOPMENT_ONLY power plan uses fewer rows than frozen development minimum')
    if not _nonplaceholder(a.get('effect_source')):
        fail('power plan effect_source required')
    z_alpha=NormalDist().inv_cdf(1.0-alpha/2.0)
    z_power=NormalDist().inv_cdf(power)
    required_n=int(math.ceil(((z_alpha+z_power)*sd/effect)**2*multiplier))
    if a.get('required_n')!=required_n:
        fail(f'power plan required_n mismatch: artifact={a.get("required_n")} calculated={required_n}')
    if int(sample['minimum_n']) < required_n:
        fail(f'confirmation minimum_n {sample["minimum_n"]} below mathematically required {required_n}')


def validate_external_audit_artifacts(c: dict, contract_path: Path) -> None:
    base=contract_path.resolve().parent; data=c['data_plan']
    lock=_resolve_bound_artifact(base,data['identity_lock_artifact'],'identity lock artifact')
    if _file_sha256(lock)!=data['identity_lock_sha256']: fail('identity lock artifact sha256 mismatch')
    _validate_identity_lock(lock,data)

    sem_cfg=data['input_semantics_audit']
    sem_path=_resolve_bound_artifact(base,sem_cfg['artifact'],'input semantics audit artifact')
    if _file_sha256(sem_path)!=sem_cfg['artifact_sha256']: fail('input semantics audit sha256 mismatch')
    try: sem=json.loads(sem_path.read_text(encoding='utf-8'))
    except Exception as e: fail(f'input semantics audit unreadable: {e}')
    _validate_input_semantics_artifact(sem,c)

    audit_cfg=data['global_consumption_audit']
    audit_path=_resolve_bound_artifact(base,audit_cfg['artifact'],'global consumption audit artifact')
    if _file_sha256(audit_path)!=audit_cfg['artifact_sha256']: fail('global consumption audit sha256 mismatch')
    try: a=json.loads(audit_path.read_text(encoding='utf-8'))
    except Exception as e: fail(f'consumption audit artifact unreadable: {e}')
    if a.get('schema_version')!=1 or a.get('project_id')!='football3': fail('invalid consumption audit artifact schema/project')
    _aware_iso_timestamp(a.get('audited_at'),'consumption audit audited_at')
    for k in ('identity_lock_sha256','identity_count','ordered_identity_sha256','source_revision'):
        expected=data[k] if k!='source_revision' else data['source_revision']
        if a.get(k)!=expected: fail(f'consumption audit binding mismatch: {k}')
    if a.get('real_target_values_read')!=0: fail('consumption audit must be zero-label')
    for k in ('registry_checked','github_history_checked','airtable_history_checked'):
        if a.get(k) is not True or a.get(k)!=audit_cfg.get(k): fail(f'consumption audit external check mismatch: {k}')
    if a.get('connected_audit_status')!='VERIFIED_ZERO_LABEL' or a.get('connected_audit_status')!=audit_cfg.get('connected_audit_status'):
        fail('consumption audit connected verification mismatch')
    for k in ('target_identity_overlap_with_consumed','unresolved_historical_identity_gaps'):
        if a.get(k)!=audit_cfg.get(k): fail(f'consumption audit/contract mismatch: {k}')
    if a.get('evidence_class')!=data.get('evidence_class'): fail('consumption audit evidence_class mismatch')
    dup=_validate_cross_project_duplication(a.get('cross_project_duplication'))
    if dup['same_match_identity_overlap_count']!=a['target_identity_overlap_with_consumed']:
        fail('cross-project same_match_identity_overlap_count must equal consumed target overlap count')
    cdup=audit_cfg['cross_project_duplication']
    for k in ('same_scientific_hypothesis_previously_viewed','exact_replication_or_reproduction_condition_met'):
        if dup[k]!=cdup[k]: fail(f'cross-project audit/contract mismatch: {k}')
    reuse_required=(a['target_identity_overlap_with_consumed']>0 or a['unresolved_historical_identity_gaps']>0 or dup['same_scientific_hypothesis_previously_viewed'] or dup['exact_replication_or_reproduction_condition_met'])
    if reuse_required and data['evidence_class'] not in REUSE_CLASSES: fail('external audit requires reuse classification')
    _validate_github_receipt(a.get('github_receipt')); _validate_airtable_receipt(a.get('airtable_receipt'))

    sample=c['sample_plan']
    if sample.get('confirmation') is True:
        p=_resolve_bound_artifact(base,sample['planning_artifact'],'confirmation planning artifact')
        if _file_sha256(p)!=sample['planning_artifact_sha256']: fail('planning artifact sha256 mismatch')
        try: plan=json.loads(p.read_text(encoding='utf-8'))
        except Exception as e: fail(f'power plan unreadable: {e}')
        _validate_power_plan_artifact(plan,c)


def dotted_name(node: ast.AST) -> str:
    parts=[]
    while isinstance(node,ast.Attribute): parts.append(node.attr); node=node.value
    if isinstance(node,ast.Name): parts.append(node.id)
    return '.'.join(reversed(parts))


def _module_string_constant(tree: ast.AST, name: str) -> str | None:
    for n in getattr(tree,'body',[]):
        if not isinstance(n,(ast.Assign,ast.AnnAssign)): continue
        targets=n.targets if isinstance(n,ast.Assign) else [n.target]
        if not any(isinstance(t,ast.Name) and t.id==name for t in targets): continue
        v=n.value
        if isinstance(v,ast.Constant) and isinstance(v.value,str): return v.value
    return None


def _literal_strings(node: ast.AST | None) -> list[str] | None:
    if not isinstance(node,(ast.List,ast.Tuple,ast.Set)): return None
    vals=[]
    for e in node.elts:
        if not isinstance(e,ast.Constant) or not isinstance(e.value,str): return None
        vals.append(e.value)
    return vals


def _reachable_nodes(tree: ast.Module) -> list[ast.AST]:
    funcs={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    roots=[n for n in tree.body if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    out=list(roots); seen=set(); queue=[]
    for n in roots:
        for x in ast.walk(n):
            if isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id in funcs:
                queue.append(x.func.id)
    while queue:
        name=queue.pop()
        if name in seen: continue
        seen.add(name); fn=funcs[name]; out.append(fn)
        for x in ast.walk(fn):
            if isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id in funcs and x.func.id not in seen:
                queue.append(x.func.id)
    return out


def validate_runner(path: Path, contract_path: Path | None = None) -> None:
    text=path.read_text(encoding='utf-8')
    try: tree=ast.parse(text,filename=str(path))
    except SyntaxError as e: fail(f'runner syntax error: {e}')
    c=load_contract(contract_path) if contract_path is not None else None

    imported_core=False
    for node in ast.walk(tree):
        if isinstance(node,ast.Import): imported_core |= any(a.name=='football3_core' for a in node.names)
        elif isinstance(node,ast.ImportFrom): imported_core |= node.module=='football3_core'
    if not imported_core: fail('runner must actually import football3_core')

    reachable=_reachable_nodes(tree)
    calls=[]
    for root in reachable:
        for node in ast.walk(root):
            if isinstance(node,ast.Call): calls.append(node)
    names={dotted_name(n.func).split('.')[-1] for n in calls}
    missing=REQUIRED_SCORING_CALLS-names
    if missing: fail(f'runner reachable execution path missing mandatory canonical calls: {sorted(missing)}')

    eval_calls=[n for n in calls if dotted_name(n.func).split('.')[-1]=='evaluate_frozen_experiment']
    if not eval_calls: fail('reachable canonical evaluator call missing')
    for n in eval_calls:
        keys={k.arg for k in n.keywords if k.arg}
        for req in ('identity_sha256','fold_ids','domain_ids','contract'):
            if req not in keys: fail(f'evaluate_frozen_experiment must bind runtime keyword {req}')

    if c is not None:
        declared=_module_string_constant(tree,'FOOTBALL3_EXPERIMENT_CONTRACT')
        if not declared: fail('runner missing FOOTBALL3_EXPERIMENT_CONTRACT constant')
        declared_path=Path(declared)
        if declared_path.is_absolute() or '..' in declared_path.parts: fail('runner experiment contract path must be repo-relative and non-traversing')
        if (REPO_ROOT/declared_path).resolve()!=contract_path.resolve(): fail('runner declared contract does not match validator --contract path')
        expected_ts=set(_feature_manifest(c['candidate'])[1].values()) | {str(c['baseline']['quote_timestamp_column'])}
        pit_calls=[n for n in calls if dotted_name(n.func).split('.')[-1]=='assert_feature_pit']
        if len(pit_calls)!=1: fail('runner must have exactly one reachable assert_feature_pit call')
        kw={k.arg:k.value for k in pit_calls[0].keywords if k.arg}
        literal=_literal_strings(kw.get('feature_timestamp_cols'))
        if literal is None: fail('assert_feature_pit feature_timestamp_cols must be a frozen literal list/tuple/set')
        if set(literal)!=expected_ts: fail(f'assert_feature_pit timestamp columns must exactly match frozen feature manifest + baseline quote timestamp: {sorted(expected_ts)}')

    low=text.lower()
    for tok in SEALED_RUNNER_TOKENS:
        if tok in low: fail(f'sealed-pool token forbidden in scientific runner: {tok}')
    for node in ast.walk(tree):
        if isinstance(node,ast.Attribute) and node.attr=='T': fail(f'attribute .T forbidden; use ["T"] or .transpose() explicitly (line {node.lineno})')
        if isinstance(node,ast.Call):
            name=dotted_name(node.func).split('.')[-1]
            if name in FORBIDDEN_CALLS: fail(f'non-temporal/random split primitive forbidden: {name} line {node.lineno}')
    for token in FORBIDDEN_SCIENCE_TOKENS:
        if token in low: fail(f'forbidden downstream optimization token: {token}')
    if re.search(r'\.sample\s*\([^\)]*frac\s*=\s*1',text): fail('full-row random shuffle via sample(frac=1) forbidden')
    if re.search(r'shuffle\s*=\s*True',text,flags=re.I): fail('shuffle=True forbidden')


def validate_runtime_branch_binding(c: dict) -> None:
    runtime=(os.environ.get('GITHUB_HEAD_REF') or os.environ.get('GITHUB_REF_NAME') or '').strip()
    if runtime and runtime!=c['branch']: fail(f"contract branch {c['branch']!r} does not match runtime branch {runtime!r}")


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=Path,required=True); ap.add_argument('--runner',type=Path,required=True); args=ap.parse_args()
    c=load_contract(args.contract)
    validate_contract(c)
    validate_runtime_branch_binding(c)
    validate_external_audit_artifacts(c,args.contract)
    validate_runner(args.runner,args.contract)
    print(json.dumps({'status':'FOOTBALL3_PREFLIGHT_PASS','contract':str(args.contract),'runner':str(args.runner),'root_sha':ROOT_SHA,'master_cutoff':MASTER_CUTOFF,'runtime_branch_bound':True,'external_semantics_truth_proven_by_ci':False},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
