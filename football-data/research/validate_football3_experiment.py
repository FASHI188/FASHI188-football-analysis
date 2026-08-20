from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
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
HEX64 = re.compile(r'^[0-9a-f]{64}$')


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
    return float(x)


def _fraction(x: object, name: str) -> float:
    v = _number(x, name)
    if not (0.0 <= v <= 1.0):
        fail(f'{name} must be in [0,1]')
    return v


def _sha64(x: object, name: str) -> str:
    s = str(x or '').lower()
    if not HEX64.match(s):
        fail(f'{name} must be lowercase sha256 hex')
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
    if not branch.startswith('football3/'):
        fail('branch must use football3/ prefix')
    if any(x in branch.upper() for x in ('C073','C074','C075','C076','C077')):
        fail('quarantined lineage token in football3 branch')

    q = require(c, 'scientific_question')
    if q.get('primary_target') != 'P(T=0,1,2,3,4,5,6,7+)':
        fail('primary target must be complete collapsed P(T)')
    if q.get('direct_draw_optimization') is not False:
        fail('direct Draw optimization is forbidden')
    if q.get('materially_new_pt_hypothesis') is not True:
        fail('new science must declare a materially new P(T) hypothesis')

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
    if candidate.get('post_view_neighbor_of_parked_hypothesis') is True:
        fail('neighboring repair of a parked hypothesis is forbidden')

    data = require(c, 'data_plan')
    if not _nonplaceholder(data.get('source_revision')):
        fail('exact source revision required')
    if data.get('identity_lock_before_labels') is not True:
        fail('identity lock must precede label access')
    if not _nonplaceholder(data.get('identity_lock_artifact')):
        fail('zero-label identity lock artifact required')
    _sha64(data.get('identity_lock_sha256'), 'identity_lock_sha256')
    audit = require(data, 'global_consumption_audit')
    if audit.get('required') is not True:
        fail('global consumption audit required')
    if not _nonplaceholder(audit.get('artifact')):
        fail('global consumption audit artifact required')
    _sha64(audit.get('artifact_sha256'), 'global_consumption_audit.artifact_sha256')
    for key in ('registry_checked','github_history_checked','airtable_history_checked'):
        if audit.get(key) is not True:
            fail(f'global consumption audit missing {key}')
    overlap = audit.get('target_identity_overlap_with_consumed')
    gaps = audit.get('unresolved_historical_identity_gaps')
    if not isinstance(overlap, int) or overlap < 0:
        fail('target_identity_overlap_with_consumed must be integer >=0')
    if not isinstance(gaps, int) or gaps < 0:
        fail('unresolved_historical_identity_gaps must be integer >=0')
    eclass = data.get('evidence_class')
    if eclass not in FRESH_CLASSES | REUSE_CLASSES:
        fail('invalid evidence_class')
    if (overlap > 0 or gaps > 0) and eclass not in REUSE_CLASSES:
        fail('overlap or unresolved history forbids fresh evidence classification')
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
    if not isinstance(split.get('minimum_test_rows_per_fold'), int) or split['minimum_test_rows_per_fold'] <= 0:
        fail('minimum_test_rows_per_fold must be positive integer')

    metrics = require(c, 'metrics')
    if not REQUIRED_METRICS.issubset(set(metrics.get('proper_scores', []))):
        fail('LogLoss/Brier/RPS all required')
    if metrics.get('top1_primary') is not False:
        fail('Top1 cannot be primary')
    if metrics.get('implementation') != 'football3_core':
        fail('proper-score implementation must be football3_core')
    cal = require(metrics, 'calibration')
    if cal.get('required') is not True or not REQUIRED_CALIBRATION.issubset(set(cal.get('metrics', []))):
        fail('Top1ECE and ClasswiseECE calibration metrics required')
    if not isinstance(cal.get('bins'), int) or cal['bins'] < 5:
        fail('calibration bins must be integer >=5')

    gates = require(c, 'success_gates')
    primary = require(gates, 'primary')
    if primary.get('metric') != 'LogLoss':
        fail('primary success metric must be LogLoss')
    if _number(primary.get('delta_max'), 'success_gates.primary.delta_max') > 0:
        fail('primary delta_max cannot permit LogLoss worsening')
    if _number(primary.get('bootstrap_ci_high_max'), 'success_gates.primary.bootstrap_ci_high_max') > 0:
        fail('bootstrap CI gate cannot permit LogLoss worsening')
    sec = require(gates, 'secondary_noninferiority')
    for key in ('Brier_delta_max','RPS_delta_max','ClasswiseECE_delta_max'):
        if _number(sec.get(key), f'success_gates.secondary_noninferiority.{key}') > 0:
            fail(f'{key} cannot permit worsening')
    tc = require(gates, 'temporal_consistency')
    _fraction(tc.get('minimum_fold_win_fraction'), 'minimum_fold_win_fraction')
    dc = require(gates, 'domain_consistency')
    if not _nonplaceholder(dc.get('domain_field')):
        fail('domain field required')
    if not isinstance(dc.get('minimum_domains'), int) or dc['minimum_domains'] < 2:
        fail('minimum_domains must be integer >=2')
    _fraction(dc.get('minimum_win_fraction'), 'minimum_domain_win_fraction')
    if _number(dc.get('max_domain_logloss_regression'), 'max_domain_logloss_regression') < 0:
        fail('max_domain_logloss_regression must be nonnegative')

    boot = require(c, 'bootstrap')
    if boot.get('paired_match') is not True or int(boot.get('resamples', 0)) < 1000 or boot.get('seed') is None:
        fail('paired match bootstrap >=1000 with frozen seed required')
    if not (0.80 <= float(boot.get('ci', 0)) < 1.0):
        fail('bootstrap CI must be frozen in [0.80,1)')

    sample = require(c, 'sample_plan')
    if not isinstance(sample.get('development_minimum_n'), int) or sample['development_minimum_n'] <= 0:
        fail('development_minimum_n must be positive integer')
    if sample.get('optional_stopping') is not False:
        fail('optional stopping forbidden')
    if sample.get('confirmation') is True:
        if sample.get('power_or_precision_plan_frozen') is not True:
            fail('confirmation requires frozen power/precision plan')
        if eclass != 'CONFIRMATION_FRESH':
            fail('confirmation must be classified CONFIRMATION_FRESH')
        if overlap != 0 or gaps != 0:
            fail('confirmation requires zero consumed overlap and zero unresolved historical gaps')
        if not isinstance(sample.get('minimum_n'), int) or sample['minimum_n'] <= 0:
            fail('confirmation minimum_n must be positive integer')
        if _number(sample.get('planned_power'), 'planned_power') < 0.80:
            fail('confirmation planned_power must be >=0.80')
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
    if not method.get('frozen_dimensions'):
        fail('frozen method dimensions must be listed')


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_external_audit_artifacts(c: dict, contract_path: Path) -> None:
    base = contract_path.parent
    data = c['data_plan']
    lock = (base / data['identity_lock_artifact']).resolve() if not Path(data['identity_lock_artifact']).is_absolute() else Path(data['identity_lock_artifact'])
    if not lock.exists():
        fail(f'identity lock artifact missing: {lock}')
    if _file_sha256(lock) != data['identity_lock_sha256']:
        fail('identity lock artifact sha256 mismatch')
    audit_cfg = data['global_consumption_audit']
    audit_path = (base / audit_cfg['artifact']).resolve() if not Path(audit_cfg['artifact']).is_absolute() else Path(audit_cfg['artifact'])
    if not audit_path.exists():
        fail(f'global consumption audit artifact missing: {audit_path}')
    if _file_sha256(audit_path) != audit_cfg['artifact_sha256']:
        fail('global consumption audit artifact sha256 mismatch')
    try:
        a = json.loads(audit_path.read_text(encoding='utf-8'))
    except Exception as e:
        fail(f'consumption audit artifact unreadable: {e}')
    if a.get('schema_version') != 1 or a.get('project_id') != 'football3':
        fail('invalid consumption audit artifact schema/project')
    if a.get('identity_lock_sha256') != data['identity_lock_sha256']:
        fail('audit artifact identity lock digest mismatch')
    if a.get('source_revision') != data['source_revision']:
        fail('audit artifact source revision mismatch')
    if a.get('real_target_values_read') != 0:
        fail('consumption audit must be zero-label')
    for key in ('registry_checked','github_history_checked','airtable_history_checked'):
        if a.get(key) is not True:
            fail(f'audit artifact missing external check: {key}')
        if a.get(key) != audit_cfg.get(key):
            fail(f'audit artifact/contract mismatch: {key}')
    for key in ('target_identity_overlap_with_consumed','unresolved_historical_identity_gaps'):
        if a.get(key) != audit_cfg.get(key):
            fail(f'audit artifact/contract mismatch: {key}')
    if a.get('evidence_class') != data.get('evidence_class'):
        fail('audit artifact evidence_class mismatch')
    if not _nonplaceholder(a.get('github_receipt')) or not _nonplaceholder(a.get('airtable_receipt')):
        fail('external GitHub/Airtable audit receipts required')

    sample = c['sample_plan']
    if sample.get('confirmation') is True:
        p = (base / sample['planning_artifact']).resolve() if not Path(sample['planning_artifact']).is_absolute() else Path(sample['planning_artifact'])
        if not p.exists():
            fail(f'planning artifact missing: {p}')
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


def validate_runner(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        fail(f'runner syntax error: {e}')
    if 'football3_core' not in text:
        fail('runner must import/use football3_core for shared scientific primitives')
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--contract', type=Path, required=True)
    ap.add_argument('--runner', type=Path, required=True)
    args = ap.parse_args()
    c = load_contract(args.contract)
    validate_contract(c)
    validate_external_audit_artifacts(c, args.contract)
    validate_runner(args.runner)
    print(json.dumps({'status':'FOOTBALL3_PREFLIGHT_PASS','contract':str(args.contract),'runner':str(args.runner),'root_sha':ROOT_SHA,'master_cutoff':MASTER_CUTOFF}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
