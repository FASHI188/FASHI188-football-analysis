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
REQUIRED_SCORING_CALLS = {'evaluate_frozen_experiment','assert_feature_pit','assert_temporal_oos','assert_master_prediction_cutoff'}
HEX64 = re.compile(r'^[0-9a-f]{64}$')
REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_GITHUB_REPO = 'FASHI188/FASHI188-football-analysis'
EXPECTED_AIRTABLE_BASE = 'appLXF9IBvSCEUjJV'


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


def validate_contract(c: dict) -> None:
    if require(c, 'schema_version') != 2:
        fail('unsupported contract schema_version; new football3 science requires v2 contract')
    if require(c, 'project_id') != 'football3':
        fail('project_id must be football3')
    root = require(c, 'scientific_root')
    if root.get('experiment') != 'C072-C' or root.get('sha') != ROOT_SHA:
        fail('scientific root mismatch')
    branch = str(require(c, 'branch'))
    if not branch.startswith('football3/') or not _nonplaceholder(branch):
        fail('branch must be a concrete football3/ branch')
    if any(x in branch.upper() for x in ('C073','C074','C075','C076','C077')):
        fail('quarantined lineage token in football3 branch')

    q = require(c, 'scientific_question')
    if q.get('primary_target') != 'P(T=0,1,2,3,4,5,6,7+)':
        fail('primary target must be complete collapsed P(T)')
    if q.get('direct_draw_optimization') is not False:
        fail('direct Draw optimization is forbidden')
    if q.get('materially_new_pt_hypothesis') is not True:
        fail('new science must declare a materially new P(T) hypothesis')
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
    for key in ('market_anchor','same_cutoff','latest_snapshot_at_or_before_cutoff','devigged','representation_frozen_before_labels'):
        if baseline.get(key) is not True:
            fail(f'strong market baseline gate missing: {key}')
    if not _nonplaceholder(baseline.get('representation')):
        fail('baseline representation must be frozen and explicit')
    if not _nonplaceholder(baseline.get('quote_timestamp_column')):
        fail('baseline quote timestamp column must be explicit')
    if candidate.get('post_view_neighbor_of_parked_hypothesis') is True:
        fail('neighboring repair of a parked hypothesis is forbidden')

    data = require(c, 'data_plan')
    if not _nonplaceholder(data.get('source_revision')):
        fail('exact source revision required')
    if data.get('identity_lock_before_labels') is not True:
        fail('identity lock must precede label access')
    if data.get('identity_lock_format') != 'sha256_csv_v1':
        fail('identity_lock_format must be sha256_csv_v1')
    if not _nonplaceholder(data.get('identity_lock_artifact')):
        fail('zero-label identity lock artifact required')
    _sha64(data.get('identity_lock_sha256'), 'identity_lock_sha256')
    _positive_int(data.get('identity_count'), 'identity_count')
    _sha64(data.get('ordered_identity_sha256'), 'ordered_identity_sha256')

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
    eclass = data.get('evidence_class')
    if eclass not in FRESH_CLASSES | REUSE_CLASSES:
        fail('invalid evidence_class')
    if (overlap > 0 or gaps > 0) and eclass not in REUSE_CLASSES:
        fail('overlap or unresolved history forbids fresh evidence classification')
    if eclass in FRESH_CLASSES and (overlap != 0 or gaps != 0):
        fail('fresh evidence requires zero overlap and zero unresolved history')
    if data.get('random_split') not in (False, None):
        fail('random split forbidden')
    if data.get('target_labels_before_contract', 0) != 0:
        fail('target labels before frozen contract must be zero')

    split = require(c, 'oos_design')
    if split.get('temporal') is not True:
        fail('OOS design must be temporal')
    if split.get('shuffle') is not False:
        fail('OOS shuffle must be false')
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
    if sample.get('optional_stopping') is not False:
        fail('optional stopping forbidden')
    if sample.get('confirmation') is True:
        if sample.get('power_or_precision_plan_frozen') is not True:
            fail('confirmation requires frozen power/precision plan')
        if eclass != 'CONFIRMATION_FRESH':
            fail('confirmation must be classified CONFIRMATION_FRESH')
        if overlap != 0 or gaps != 0:
            fail('confirmation requires zero consumed overlap and zero unresolved historical gaps')
        _positive_int(sample.get('minimum_n'), 'confirmation minimum_n')
        planned_power = _number(sample.get('planned_power'), 'planned_power')
        if not (0.80 <= planned_power < 1.0):
            fail('confirmation planned_power must be in [0.80,1)')
        alpha = _number(sample.get('alpha'), 'confirmation alpha')
        if not (0 < alpha <= 0.20):
            fail('confirmation alpha must be in (0,0.20]')
        if sample.get('planning_basis') not in {'DEVELOPMENT_ONLY','EXTERNAL_PRIOR'}:
            fail('confirmation planning_basis must be DEVELOPMENT_ONLY or EXTERNAL_PRIOR')
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
    ids: list[str] = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) != 1 or not HEX64.fullmatch(row[0]):
            fail(f'identity lock row {i} must contain exactly one lowercase sha256')
        ids.append(row[0])
    if not ids:
        fail('identity lock must contain at least one identity')
    if len(ids) != len(set(ids)):
        fail('identity lock contains duplicate identities')
    if len(ids) != data['identity_count']:
        fail('identity lock row count does not match contract identity_count')
    if _ordered_digest(ids) != data['ordered_identity_sha256']:
        fail('identity lock ordered identity digest mismatch')


def _validate_github_receipt(r: object) -> None:
    if not isinstance(r, dict):
        fail('github_receipt must be structured object')
    if r.get('repository') != EXPECTED_GITHUB_REPO:
        fail('github_receipt repository mismatch')
    _aware_iso_timestamp(r.get('checked_at'), 'github_receipt.checked_at')
    scope = r.get('query_scope')
    if not isinstance(scope, list) or not scope or any(not _nonplaceholder(x) for x in scope):
        fail('github_receipt query_scope must be nonempty list')
    _sha64(r.get('result_digest_sha256'), 'github_receipt.result_digest_sha256')


def _validate_airtable_receipt(r: object) -> None:
    if not isinstance(r, dict):
        fail('airtable_receipt must be structured object')
    if r.get('base_id') != EXPECTED_AIRTABLE_BASE:
        fail('airtable_receipt base_id mismatch')
    _aware_iso_timestamp(r.get('checked_at'), 'airtable_receipt.checked_at')
    tables = r.get('tables_checked')
    if not isinstance(tables, list) or not {'当前状态','维护日志'}.issubset(set(tables)):
        fail('airtable_receipt must include 当前状态 and 维护日志')
    _sha64(r.get('result_digest_sha256'), 'airtable_receipt.result_digest_sha256')


def validate_external_audit_artifacts(c: dict, contract_path: Path) -> None:
    base = contract_path.resolve().parent
    data = c['data_plan']
    lock = _resolve_bound_artifact(base, data['identity_lock_artifact'], 'identity lock artifact')
    if _file_sha256(lock) != data['identity_lock_sha256']:
        fail('identity lock artifact sha256 mismatch')
    _validate_identity_lock(lock, data)

    audit_cfg = data['global_consumption_audit']
    audit_path = _resolve_bound_artifact(base, audit_cfg['artifact'], 'global consumption audit artifact')
    if _file_sha256(audit_path) != audit_cfg['artifact_sha256']:
        fail('global consumption audit artifact sha256 mismatch')
    try:
        a = json.loads(audit_path.read_text(encoding='utf-8'))
    except Exception as e:
        fail(f'consumption audit artifact unreadable: {e}')
    if a.get('schema_version') != 1 or a.get('project_id') != 'football3':
        fail('invalid consumption audit artifact schema/project')
    _aware_iso_timestamp(a.get('audited_at'), 'consumption audit audited_at')
    if a.get('identity_lock_sha256') != data['identity_lock_sha256']:
        fail('audit artifact identity lock file digest mismatch')
    if a.get('identity_count') != data['identity_count']:
        fail('audit artifact identity_count mismatch')
    if a.get('ordered_identity_sha256') != data['ordered_identity_sha256']:
        fail('audit artifact ordered identity digest mismatch')
    if a.get('source_revision') != data['source_revision']:
        fail('audit artifact source revision mismatch')
    if a.get('real_target_values_read') != 0:
        fail('consumption audit must be zero-label')
    for key in ('registry_checked','github_history_checked','airtable_history_checked'):
        if a.get(key) is not True or a.get(key) != audit_cfg.get(key):
            fail(f'audit artifact missing/mismatched external check: {key}')
    if a.get('connected_audit_status') != 'VERIFIED_ZERO_LABEL' or a.get('connected_audit_status') != audit_cfg.get('connected_audit_status'):
        fail('consumption audit connected verification status mismatch')
    for key in ('target_identity_overlap_with_consumed','unresolved_historical_identity_gaps'):
        if a.get(key) != audit_cfg.get(key):
            fail(f'audit artifact/contract mismatch: {key}')
    if a.get('evidence_class') != data.get('evidence_class'):
        fail('audit artifact evidence_class mismatch')
    _validate_github_receipt(a.get('github_receipt'))
    _validate_airtable_receipt(a.get('airtable_receipt'))

    sample = c['sample_plan']
    if sample.get('confirmation') is True:
        p = _resolve_bound_artifact(base, sample['planning_artifact'], 'confirmation planning artifact')
        if _file_sha256(p) != sample['planning_artifact_sha256']:
            fail('planning artifact sha256 mismatch')


def dotted_name(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return '.'.join(reversed(parts))


def _module_string_constant(tree: ast.AST, name: str) -> str | None:
    for n in getattr(tree, 'body', []):
        if not isinstance(n, (ast.Assign, ast.AnnAssign)):
            continue
        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        v = n.value
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
    return None


def validate_runner(path: Path, contract_path: Path | None = None) -> None:
    text = path.read_text(encoding='utf-8')
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        fail(f'runner syntax error: {e}')

    imported_core = False
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_core |= any(a.name == 'football3_core' for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_core |= node.module == 'football3_core'
        elif isinstance(node, ast.Call):
            calls.add(dotted_name(node.func).split('.')[-1])
    if not imported_core:
        fail('runner must actually import football3_core')

    missing_calls = REQUIRED_SCORING_CALLS - calls
    if missing_calls:
        fail(f'runner missing mandatory canonical runtime calls: {sorted(missing_calls)}')

    if contract_path is not None:
        declared = _module_string_constant(tree, 'FOOTBALL3_EXPERIMENT_CONTRACT')
        if not declared:
            fail('runner missing FOOTBALL3_EXPERIMENT_CONTRACT constant')
        declared_path = Path(declared)
        if declared_path.is_absolute() or '..' in declared_path.parts:
            fail('runner experiment contract path must be repo-relative and non-traversing')
        if (REPO_ROOT / declared_path).resolve() != contract_path.resolve():
            fail('runner declared contract does not match validator --contract path')

    low = text.lower()
    for tok in SEALED_RUNNER_TOKENS:
        if tok in low:
            fail(f'sealed-pool token forbidden in scientific runner: {tok}')
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == 'T':
            fail(f'attribute .T is forbidden in football3 runners; use ["T"] for target or .transpose() explicitly (line {node.lineno})')
        if isinstance(node, ast.Call):
            name = dotted_name(node.func).split('.')[-1]
            if name in FORBIDDEN_CALLS:
                fail(f'non-temporal/random split primitive forbidden: {name} line {node.lineno}')
    for token in FORBIDDEN_SCIENCE_TOKENS:
        if token in low:
            fail(f'forbidden downstream optimization token: {token}')
    if re.search(r'\.sample\s*\([^\)]*frac\s*=\s*1', text):
        fail('full-row random shuffle via sample(frac=1) forbidden')
    if re.search(r'shuffle\s*=\s*True', text, flags=re.I):
        fail('shuffle=True forbidden')


def validate_runtime_branch_binding(c: dict) -> None:
    runtime = os.environ.get('GITHUB_HEAD_REF') or os.environ.get('GITHUB_REF_NAME') or ''
    runtime = runtime.strip()
    if runtime and runtime != c['branch']:
        fail(f"contract branch {c['branch']!r} does not match runtime branch {runtime!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--contract', type=Path, required=True)
    ap.add_argument('--runner', type=Path, required=True)
    args = ap.parse_args()
    c = load_contract(args.contract)
    validate_contract(c)
    validate_runtime_branch_binding(c)
    validate_external_audit_artifacts(c, args.contract)
    validate_runner(args.runner, args.contract)
    print(json.dumps({'status':'FOOTBALL3_PREFLIGHT_PASS','contract':str(args.contract),'runner':str(args.runner),'root_sha':ROOT_SHA,'master_cutoff':MASTER_CUTOFF,'runtime_branch_bound':True}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
