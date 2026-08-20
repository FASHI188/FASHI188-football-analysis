from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

ROOT_SHA = 'e3e73c998020beef585cc459a69ea5b73b44ddb3'
REQUIRED_METRICS = {'LogLoss', 'Brier', 'RPS'}
SEALED_NAMES = {'C070-F Confirmation1597', 'N17 reserve266', 'N18C confirmation150'}
FORBIDDEN_CALLS = {
    'train_test_split',
    'ShuffleSplit',
    'StratifiedShuffleSplit',
    'KFold',
    'StratifiedKFold',
    'RepeatedKFold',
    'RepeatedStratifiedKFold',
}
FORBIDDEN_SCIENCE_TOKENS = {
    'manual_draw_boost', 'manual_0_0_boost', 'manual_1_1_boost',
    'posthoc_draw_threshold', 'posthoc_draw_weight', 'class_weight_for_draw',
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


def validate_contract(c: dict) -> None:
    if require(c, 'schema_version') != 1:
        fail('unsupported contract schema_version')
    if require(c, 'project_id') != 'football3':
        fail('project_id must be football3')
    root = require(c, 'scientific_root')
    if root.get('experiment') != 'C072-C' or root.get('sha') != ROOT_SHA:
        fail('scientific root mismatch')
    branch = str(require(c, 'branch'))
    if not branch.startswith('football3/'):
        fail('branch must use football3/ prefix')
    if any(x in branch.upper() for x in ('C073', 'C074', 'C075', 'C076', 'C077')):
        fail('quarantined lineage token in football3 branch')

    q = require(c, 'scientific_question')
    if q.get('primary_target') != 'P(T=0,1,2,3,4,5,6,7+)':
        fail('primary target must be complete collapsed P(T)')
    if q.get('direct_draw_optimization') is not False:
        fail('direct Draw optimization is forbidden')

    cutoff = require(c, 'prediction_cutoff')
    if str(cutoff.get('baseline')).strip().lower() != str(cutoff.get('candidate')).strip().lower():
        fail('baseline and candidate must use identical prediction cutoff')
    if not cutoff.get('pit_definition'):
        fail('PIT definition must be explicit')

    baseline = require(c, 'baseline')
    candidate = require(c, 'candidate')
    if not baseline.get('description') or not candidate.get('description'):
        fail('baseline/candidate descriptions required')
    if candidate.get('post_view_neighbor_of_parked_hypothesis') is True:
        fail('neighboring repair of a parked hypothesis is forbidden')

    data = require(c, 'data_plan')
    if data.get('identity_lock_before_labels') is not True:
        fail('identity lock must precede label access')
    if data.get('global_consumption_audit') is not True:
        fail('global consumption audit required')
    if data.get('random_split') not in (False, None):
        fail('random split forbidden')
    if data.get('target_identity_overlap_with_consumed', 0) != 0 and data.get('evidence_class') not in {'REPLICATION', 'REPRODUCTION'}:
        fail('consumed target overlap must be explicitly classified replication/reproduction')

    split = require(c, 'oos_design')
    if split.get('temporal') is not True:
        fail('OOS design must be temporal')
    if split.get('shuffle') is not False:
        fail('OOS shuffle must be false')

    metrics = require(c, 'metrics')
    names = set(metrics.get('proper_scores', []))
    if not REQUIRED_METRICS.issubset(names):
        fail('LogLoss/Brier/RPS all required')
    if metrics.get('top1_primary') is not False:
        fail('Top1 cannot be primary')
    if metrics.get('implementation') != 'football3_core':
        fail('proper-score implementation must be football3_core')

    boot = require(c, 'bootstrap')
    if boot.get('paired_match') is not True or int(boot.get('resamples', 0)) < 1000 or boot.get('seed') is None:
        fail('paired match bootstrap >=1000 with frozen seed required')

    sample = require(c, 'sample_plan')
    if sample.get('confirmation') is True and sample.get('power_or_precision_plan_frozen') is not True:
        fail('confirmation requires frozen power/precision plan')
    if sample.get('optional_stopping') is True:
        fail('optional stopping forbidden')

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

    for node in ast.walk(tree):
        # DataFrame.T caused target-column corruption in N20. Make any transpose explicit via .transpose().
        if isinstance(node, ast.Attribute) and node.attr == 'T':
            fail(f'attribute .T is forbidden in football3 runners; use ["T"] for target or .transpose() explicitly (line {node.lineno})')
        if isinstance(node, ast.Call):
            name = dotted_name(node.func).split('.')[-1]
            if name in FORBIDDEN_CALLS:
                fail(f'non-temporal/random split primitive forbidden: {name} line {node.lineno}')
    low = text.lower()
    for token in FORBIDDEN_SCIENCE_TOKENS:
        if token in low:
            fail(f'forbidden downstream optimization token: {token}')
    # Common accidental randomization patterns.
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
    validate_runner(args.runner)
    print(json.dumps({
        'status': 'FOOTBALL3_PREFLIGHT_PASS',
        'contract': str(args.contract),
        'runner': str(args.runner),
        'root_sha': ROOT_SHA,
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
