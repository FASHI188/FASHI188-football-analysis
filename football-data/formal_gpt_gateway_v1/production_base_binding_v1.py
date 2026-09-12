#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
from typing import Any
FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT = 'football3-formal-gpt-request-transport-v1'
SCHEMA = 'football3-production-live-base-binding-v1'
EXECUTION_SCHEMA = 'football3-production-execution-binding-receipt-v1'
BLOCKER_SCHEMA = 'football3-production-base-binding-blocker-v1'
CANONICAL_BASE_REF = 'football3/formal-gpt-runner-integration-v1'
AUTHORIZED_CARRIER_REF = 'football3/formal-gpt-runner-request-carrier-v1'
WORKFLOW_CONTRACT_VERSION = 'football3-production-live-base-binding-v1'
WORKFLOW_PATH = '.github/workflows/football3-formal-gpt-runner-integration-v1.yml'
HELPER_PATH = 'football-data/formal_gpt_gateway_v1/production_base_binding_v1.py'
_SHA_RE = re.compile('^[0-9a-f]{40}$')
_SHA256_RE = re.compile('^[0-9a-f]{64}$')

class ProductionBaseBindingError(RuntimeError):
    pass

def canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def parse_live_ref_payload(payload: object, canonical_base_ref: str=CANONICAL_BASE_REF) -> str:
    if type(payload) is not dict:
        raise ProductionBaseBindingError('PRODUCTION_LIVE_BASE_REF_INVALID_RESPONSE')
    if payload.get('ref') != f'refs/heads/{canonical_base_ref}':
        raise ProductionBaseBindingError('PRODUCTION_LIVE_BASE_REF_INVALID_RESPONSE')
    obj = payload.get('object')
    if type(obj) is not dict or obj.get('type') != 'commit':
        raise ProductionBaseBindingError('PRODUCTION_LIVE_BASE_REF_INVALID_RESPONSE')
    sha = obj.get('sha')
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        raise ProductionBaseBindingError('PRODUCTION_LIVE_BASE_REF_INVALID_RESPONSE')
    return sha

def resolve_live_ref(repo: str, token: str, canonical_base_ref: str=CANONICAL_BASE_REF) -> str:
    if not repo or not token:
        raise ProductionBaseBindingError('PRODUCTION_LIVE_BASE_REF_RESOLUTION_FAILED')
    url = f'https://api.github.com/repos/{repo}/git/ref/heads/{canonical_base_ref}'
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
    except Exception as exc:
        raise ProductionBaseBindingError('PRODUCTION_LIVE_BASE_REF_RESOLUTION_FAILED') from exc
    try:
        payload = json.loads(raw.decode('utf-8'))
    except Exception as exc:
        raise ProductionBaseBindingError('PRODUCTION_LIVE_BASE_REF_INVALID_RESPONSE') from exc
    return parse_live_ref_payload(payload, canonical_base_ref)

def validate_pr_authority(event_base_ref: str, carrier_ref: str) -> None:
    if event_base_ref != CANONICAL_BASE_REF:
        raise ProductionBaseBindingError(f'PRODUCTION_UNAUTHORIZED_BASE_REF:event={event_base_ref}:required={CANONICAL_BASE_REF}')
    if carrier_ref != AUTHORIZED_CARRIER_REF:
        raise ProductionBaseBindingError(f'PRODUCTION_UNAUTHORIZED_REQUEST_CARRIER:{carrier_ref}')

def evaluate_start_binding(*, event_name: str, event_base_sha: str | None, canonical_base_ref: str, resolved_live_base_sha: str, checkout_head_sha: str, resolution_timestamp: str, carrier_ref: str | None, carrier_head: str | None) -> dict[str, Any]:
    if not _SHA_RE.fullmatch(resolved_live_base_sha):
        raise ProductionBaseBindingError('PRODUCTION_LIVE_BASE_REF_INVALID_RESPONSE')
    if checkout_head_sha != resolved_live_base_sha:
        raise ProductionBaseBindingError(f'PRODUCTION_CHECKOUT_HEAD_MISMATCH:checkout={checkout_head_sha}:resolved={resolved_live_base_sha}')
    carrier_bound = bool(carrier_ref or carrier_head or event_base_sha)
    if carrier_bound:
        validate_pr_authority(canonical_base_ref, carrier_ref or '')
        if not carrier_head or not _SHA_RE.fullmatch(carrier_head):
            raise ProductionBaseBindingError('PRODUCTION_INVALID_REQUEST_CARRIER_HEAD')
    return {'schema_version': SCHEMA, 'status': 'CHECKOUT_BOUND', 'event_name': event_name, 'event_base_sha': event_base_sha or None, 'event_base_source': 'PULL_REQUEST_EVENT' if event_name == 'pull_request' else ('CARRIER_PR_METADATA' if carrier_bound else None), 'stale_event_metadata': bool(carrier_bound and event_base_sha and event_base_sha != resolved_live_base_sha), 'canonical_base_ref': canonical_base_ref, 'resolved_live_base_sha': resolved_live_base_sha, 'checkout_head_sha': checkout_head_sha, 'resolution_timestamp': resolution_timestamp, 'request_carrier_ref': carrier_ref or None, 'request_carrier_head': carrier_head or None, 'request_id': None, 'request_sha256': None, 'workflow_contract_version': WORKFLOW_CONTRACT_VERSION, 'runner_code_source': 'CANONICAL_INTEGRATION_EXACT_SHA', 'request_transport_source': 'AUTHORIZED_DRAFT_PR_BODY_ONLY' if carrier_bound else 'COMMITTED_OR_DISPATCH_REQUEST', 'request_carrier_code_executed': False, 'repository_run_list_scan_used': False, 'initial_live_ref_query_count': 1 if carrier_bound else 0, 'final_live_ref_query_count': 0, 'total_live_ref_query_count': 1 if carrier_bound else 0, 'final_live_base_sha': None, 'final_resolution_timestamp': None, 'integration_moved_during_run': None}

def _git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()

def record_checkout(args: argparse.Namespace) -> None:
    checkout = _git('rev-parse', 'HEAD')
    binding = evaluate_start_binding(event_name=args.event_name, event_base_sha=args.event_base_sha, canonical_base_ref=args.canonical_base_ref, resolved_live_base_sha=args.resolved_live_base_sha, checkout_head_sha=checkout, resolution_timestamp=args.resolution_timestamp, carrier_ref=args.request_carrier_ref, carrier_head=args.request_carrier_head)
    workflow = Path(WORKFLOW_PATH)
    helper = Path(HELPER_PATH)
    binding.update({'run_id': os.environ.get('GITHUB_RUN_ID'), 'workflow_identity': os.environ.get('GITHUB_WORKFLOW'), 'workflow_ref': os.environ.get('GITHUB_WORKFLOW_REF'), 'workflow_sha': os.environ.get('GITHUB_WORKFLOW_SHA'), 'workflow_git_blob_sha': _git('rev-parse', f'HEAD:{WORKFLOW_PATH}'), 'workflow_file_sha256': hashlib.sha256(workflow.read_bytes()).hexdigest(), 'binding_helper_git_blob_sha': _git('rev-parse', f'HEAD:{HELPER_PATH}'), 'binding_helper_file_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(canonical_bytes(binding) + b'\n')
    print(json.dumps(binding, sort_keys=True))

def bind_request(args: argparse.Namespace) -> None:
    binding_path, request_path, transport_path = map(Path, (args.binding, args.request, args.transport))
    binding = json.loads(binding_path.read_text(encoding='utf-8'))
    request = json.loads(request_path.read_text(encoding='utf-8'))
    transport = json.loads(transport_path.read_text(encoding='utf-8'))
    request_id = request.get('request_id')
    if binding.get('request_carrier_ref') and (not isinstance(request_id, str) or not request_id.strip()):
        raise ProductionBaseBindingError('PRODUCTION_REQUEST_ID_MISSING')
    request_sha = hashlib.sha256(canonical_bytes(request)).hexdigest()
    binding['request_id'] = request_id
    binding['request_sha256'] = request_sha
    binding_path.write_bytes(canonical_bytes(binding) + b'\n')
    transport.update({'request_id': request_id, 'pr_head_sha': binding.get('request_carrier_head'), 'event_base_sha': binding.get('event_base_sha'), 'stale_event_metadata': binding.get('stale_event_metadata'), 'canonical_base_ref': binding.get('canonical_base_ref'), 'resolved_live_base_sha': binding.get('resolved_live_base_sha'), 'checkout_head_sha': binding.get('checkout_head_sha'), 'workflow_contract_version': binding.get('workflow_contract_version'), 'runner_code_source': binding.get('runner_code_source'), 'request_carrier_code_executed': False, 'code_ref': binding.get('checkout_head_sha'), 'event_code_ref': os.environ.get('GITHUB_SHA')})
    transport_path.write_bytes(canonical_bytes(transport) + b'\n')
    print('PRODUCTION_REQUEST_BINDING_PASS', request_id, request_sha)

def assert_no_drift(binding: dict[str, Any]) -> None:
    if binding.get('integration_moved_during_run'):
        raise ProductionBaseBindingError('PRODUCTION_INTEGRATION_HEAD_MOVED_DURING_RUN')

def finalize_evidence(binding: dict[str, Any], final_live_base_sha: str, final_timestamp: str) -> dict[str, Any]:
    result = dict(binding)
    result['final_live_base_sha'] = final_live_base_sha
    result['final_resolution_timestamp'] = final_timestamp
    result['final_live_ref_query_count'] = 1 if result.get('request_carrier_ref') else 0
    result['total_live_ref_query_count'] = int(result.get('initial_live_ref_query_count') or 0) + int(result['final_live_ref_query_count'])
    moved = final_live_base_sha != result.get('resolved_live_base_sha')
    result['integration_moved_during_run'] = moved
    result['status'] = 'INTEGRATION_MOVED_DURING_RUN' if moved else 'PASS'
    return result

def _optional_provenance_value(container: dict[str, Any], key: str, invalid_code: str, pattern: re.Pattern[str]) -> str | None:
    if key not in container:
        return None
    value = container[key]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ProductionBaseBindingError(invalid_code)
    return value

def _resolve_provenance_value(*, authoritative: list[str | None], corroborating: list[str | None], missing_code: str, conflict_code: str) -> str:
    values = [value for value in authoritative if value is not None]
    if not values:
        raise ProductionBaseBindingError(missing_code)
    all_values = values + [value for value in corroborating if value is not None]
    if len(set(all_values)) != 1:
        raise ProductionBaseBindingError(conflict_code)
    return values[0]

def resolve_receipt_provenance(receipt: dict[str, Any], summary: dict[str, Any]) -> tuple[str, str]:
    formal_binding = receipt.get('formal_binding')
    if formal_binding is None:
        formal_binding = {}
    if type(formal_binding) is not dict:
        raise ProductionBaseBindingError('PRODUCTION_FORMAL_BINDING_INVALID')
    formal_head = _resolve_provenance_value(
        authoritative=[
            _optional_provenance_value(receipt, 'formal_head', 'PRODUCTION_FORMAL_HEAD_INVALID', _SHA_RE),
            _optional_provenance_value(receipt, 'formal_model_head', 'PRODUCTION_FORMAL_HEAD_INVALID', _SHA_RE),
            _optional_provenance_value(formal_binding, 'runtime_formal_head', 'PRODUCTION_FORMAL_HEAD_INVALID', _SHA_RE),
        ],
        corroborating=[
            _optional_provenance_value(summary, 'formal_head', 'PRODUCTION_FORMAL_HEAD_INVALID', _SHA_RE),
        ],
        missing_code='PRODUCTION_FORMAL_HEAD_MISSING',
        conflict_code='PRODUCTION_FORMAL_HEAD_CONFLICT',
    )
    current_sha256 = _resolve_provenance_value(
        authoritative=[
            _optional_provenance_value(receipt, 'current_sha256', 'PRODUCTION_CURRENT_SHA_INVALID', _SHA256_RE),
            _optional_provenance_value(receipt, 'actual_current_sha', 'PRODUCTION_CURRENT_SHA_INVALID', _SHA256_RE),
            _optional_provenance_value(formal_binding, 'runtime_current_sha256', 'PRODUCTION_CURRENT_SHA_INVALID', _SHA256_RE),
        ],
        corroborating=[
            _optional_provenance_value(summary, 'formal_current_sha256', 'PRODUCTION_CURRENT_SHA_INVALID', _SHA256_RE),
        ],
        missing_code='PRODUCTION_CURRENT_SHA_MISSING',
        conflict_code='PRODUCTION_CURRENT_SHA_CONFLICT',
    )
    return formal_head, current_sha256

def _preserve_gateway_fail_closed(*, out: Path, binding: dict[str, Any], summary: dict[str, Any]) -> None:
    if summary.get('status') != 'FAIL_CLOSED':
        return
    reason = summary.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise ProductionBaseBindingError('PRODUCTION_GATEWAY_FAIL_CLOSED_OUTPUT_INVALID')
    if summary.get('prediction_sha') is not None or summary.get('receipt_sha') is not None:
        raise ProductionBaseBindingError('PRODUCTION_GATEWAY_FAIL_CLOSED_OUTPUT_INVALID')
    if (out / 'prediction_receipt.json').exists():
        raise ProductionBaseBindingError('PRODUCTION_GATEWAY_FAIL_CLOSED_OUTPUT_INVALID')
    formal_head = summary.get('formal_head')
    current_sha256 = summary.get('formal_current_sha256')
    if not isinstance(formal_head, str) or not _SHA_RE.fullmatch(formal_head):
        raise ProductionBaseBindingError('PRODUCTION_FORMAL_HEAD_INVALID')
    if not isinstance(current_sha256, str) or not _SHA256_RE.fullmatch(current_sha256):
        raise ProductionBaseBindingError('PRODUCTION_CURRENT_SHA_INVALID')
    if not binding.get('resolved_live_base_sha') == binding.get('checkout_head_sha') == binding.get('final_live_base_sha'):
        raise ProductionBaseBindingError('PRODUCTION_EXACT_HEAD_BINDING_MISMATCH')
    summary_path = out / 'summary.json'
    base_binding_path = out / 'production_base_binding.json'
    blocker = {
        'schema_version': BLOCKER_SCHEMA,
        'status': 'FAIL_CLOSED',
        'blocker': reason,
        'gateway_status': 'FAIL_CLOSED',
        'run_id': binding.get('run_id'),
        'request_id': binding.get('request_id'),
        'request_sha256': binding.get('request_sha256'),
        'resolved_live_base_sha': binding.get('resolved_live_base_sha'),
        'checkout_head_sha': binding.get('checkout_head_sha'),
        'final_live_base_sha': binding.get('final_live_base_sha'),
        'integration_moved_during_run': False,
        'formal_head': formal_head,
        'current_sha256': current_sha256,
        'prediction_sha': None,
        'receipt_sha': None,
        'summary_sha256': hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        'production_base_binding_sha256': hashlib.sha256(base_binding_path.read_bytes()).hexdigest(),
    }
    execution = {
        'schema_version': EXECUTION_SCHEMA,
        'status': 'FAIL_CLOSED',
        'reason': reason,
        'run_id': binding.get('run_id'),
        'request_id': binding.get('request_id'),
        'request_sha256': binding.get('request_sha256'),
        'request_carrier_ref': binding.get('request_carrier_ref'),
        'request_carrier_head': binding.get('request_carrier_head'),
        'canonical_base_ref': binding.get('canonical_base_ref'),
        'resolved_live_base_sha': binding.get('resolved_live_base_sha'),
        'checkout_head_sha': binding.get('checkout_head_sha'),
        'final_live_base_sha': binding.get('final_live_base_sha'),
        'integration_moved_during_run': False,
        'resolution_timestamp': binding.get('resolution_timestamp'),
        'final_resolution_timestamp': binding.get('final_resolution_timestamp'),
        'workflow_identity': binding.get('workflow_identity'),
        'workflow_contract_version': binding.get('workflow_contract_version'),
        'workflow_ref': binding.get('workflow_ref'),
        'workflow_sha': binding.get('workflow_sha'),
        'runner_code_source': binding.get('runner_code_source'),
        'request_transport_source': binding.get('request_transport_source'),
        'request_carrier_code_executed': False,
        'repository_run_list_scan_used': False,
        'live_ref_query_count': binding.get('total_live_ref_query_count'),
        'formal_head': formal_head,
        'current_sha256': current_sha256,
        'prediction_sha': None,
        'receipt_sha': None,
        'summary_sha256': blocker['summary_sha256'],
        'production_base_binding_sha256': blocker['production_base_binding_sha256'],
    }
    (out / 'production_base_blocker.json').write_bytes(canonical_bytes(blocker) + b'\n')
    (out / 'production_execution_binding_receipt.json').write_bytes(canonical_bytes(execution) + b'\n')
    print('PRODUCTION_GATEWAY_FAIL_CLOSED', reason)
    raise ProductionBaseBindingError(f'PRODUCTION_GATEWAY_FAIL_CLOSED:{reason}')

def finalize(args: argparse.Namespace) -> None:
    binding_path = Path(args.binding)
    binding = json.loads(binding_path.read_text(encoding='utf-8'))
    if binding.get('request_carrier_ref'):
        final_sha = resolve_live_ref(args.repo, args.token, str(binding['canonical_base_ref']))
    else:
        final_sha = str(binding['resolved_live_base_sha'])
    final_timestamp = args.final_timestamp or os.environ.get('FOOTBALL3_FINAL_RESOLUTION_TIMESTAMP')
    if not final_timestamp:
        import datetime as dt
        final_timestamp = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    binding = finalize_evidence(binding, final_sha, final_timestamp)
    binding_path.write_bytes(canonical_bytes(binding) + b'\n')
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'production_base_binding.json').write_bytes(canonical_bytes(binding) + b'\n')
    stats_path = Path(args.stats) if args.stats else None
    if stats_path and stats_path.exists():
        (out / 'github_api_stats.json').write_bytes(stats_path.read_bytes())
    if binding['integration_moved_during_run']:
        blocker = {'schema_version': BLOCKER_SCHEMA, 'status': 'FAIL_CLOSED', 'blocker': 'PRODUCTION_INTEGRATION_HEAD_MOVED_DURING_RUN', 'run_id': binding.get('run_id'), 'request_id': binding.get('request_id'), 'resolved_live_base_sha': binding.get('resolved_live_base_sha'), 'final_live_base_sha': binding.get('final_live_base_sha')}
        (out / 'production_base_blocker.json').write_bytes(canonical_bytes(blocker) + b'\n')
        assert_no_drift(binding)
    if args.prediction_required != 'true':
        print('PRODUCTION_BASE_BINDING_PASS_NON_PREDICTION', final_sha)
        return
    summary_path = out / 'summary.json'
    if not summary_path.exists():
        raise ProductionBaseBindingError('PRODUCTION_SUMMARY_MISSING')
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    _preserve_gateway_fail_closed(out=out, binding=binding, summary=summary)
    receipt = json.loads((out / 'prediction_receipt.json').read_text(encoding='utf-8'))
    selector = json.loads((out / 'state_recovery.json').read_text(encoding='utf-8'))
    stats = json.loads(stats_path.read_text(encoding='utf-8')) if stats_path and stats_path.exists() else {}
    if summary.get('status') != 'PASS':
        raise ProductionBaseBindingError('PRODUCTION_SUMMARY_NOT_PASS')
    prediction_sha = summary.get('prediction_sha')
    if not isinstance(prediction_sha, str) or not prediction_sha or receipt.get('prediction_sha') != prediction_sha:
        raise ProductionBaseBindingError('PRODUCTION_PREDICTION_SHA_INVALID')
    if receipt.get('state_integrity_guard', {}).get('status') != 'PASS':
        raise ProductionBaseBindingError('PRODUCTION_STATE_INTEGRITY_GUARD_NOT_PASS')
    weights = receipt.get('fusion_weights') or {}
    try:
        xg_weight = float(weights.get('xg'))
        v1_weight = float(weights.get('v1'))
    except (TypeError, ValueError) as exc:
        raise ProductionBaseBindingError('PRODUCTION_FUSION_WEIGHTS_INVALID') from exc
    if xg_weight != 0.75 or v1_weight != 0.25:
        raise ProductionBaseBindingError('PRODUCTION_FUSION_WEIGHTS_DRIFT')
    if not stats:
        raise ProductionBaseBindingError('PRODUCTION_GITHUB_API_STATS_MISSING')
    if stats.get('run_list_pages') != 0:
        raise ProductionBaseBindingError('PRODUCTION_RUN_LIST_SCAN_DETECTED')
    if stats.get('rate_limit_remaining_start') is None or stats.get('rate_limit_remaining_end') is None:
        raise ProductionBaseBindingError('PRODUCTION_RATE_LIMIT_EVIDENCE_MISSING')
    try:
        network_request_count = int(stats.get('network_request_count'))
        api_budget = int(stats.get('api_budget'))
    except (TypeError, ValueError) as exc:
        raise ProductionBaseBindingError('PRODUCTION_API_BUDGET_EVIDENCE_INVALID') from exc
    if network_request_count > api_budget:
        raise ProductionBaseBindingError('PRODUCTION_API_BUDGET_EXCEEDED')
    candidates = selector.get('candidates') or []
    production_inventory_sha = hashlib.sha256(canonical_bytes(candidates)).hexdigest()
    formal_head, current_sha256 = resolve_receipt_provenance(receipt, summary)
    execution = {'schema_version': EXECUTION_SCHEMA, 'status': 'PASS', 'run_id': binding.get('run_id'), 'request_id': binding.get('request_id'), 'request_sha256': binding.get('request_sha256'), 'request_carrier_ref': binding.get('request_carrier_ref'), 'request_carrier_head': binding.get('request_carrier_head'), 'event_base_sha': binding.get('event_base_sha'), 'stale_event_metadata': binding.get('stale_event_metadata'), 'canonical_base_ref': binding.get('canonical_base_ref'), 'resolved_live_base_sha': binding.get('resolved_live_base_sha'), 'checkout_head_sha': binding.get('checkout_head_sha'), 'final_live_base_sha': binding.get('final_live_base_sha'), 'integration_moved_during_run': False, 'resolution_timestamp': binding.get('resolution_timestamp'), 'final_resolution_timestamp': binding.get('final_resolution_timestamp'), 'workflow_identity': binding.get('workflow_identity'), 'workflow_contract_version': binding.get('workflow_contract_version'), 'workflow_ref': binding.get('workflow_ref'), 'workflow_sha': binding.get('workflow_sha'), 'workflow_git_blob_sha': binding.get('workflow_git_blob_sha'), 'workflow_file_sha256': binding.get('workflow_file_sha256'), 'binding_helper_git_blob_sha': binding.get('binding_helper_git_blob_sha'), 'binding_helper_file_sha256': binding.get('binding_helper_file_sha256'), 'runner_code_source': binding.get('runner_code_source'), 'request_transport_source': binding.get('request_transport_source'), 'request_carrier_code_executed': False, 'repository_run_list_scan_used': False, 'live_ref_query_count': binding.get('total_live_ref_query_count'), 'production_selector_inventory_sha256': production_inventory_sha, 'production_selector_candidate_count': len(candidates), 'selection_sha256': selector.get('selection_sha256'), 'github_api_stats': stats, 'summary_sha256': hashlib.sha256((out / 'summary.json').read_bytes()).hexdigest(), 'prediction_receipt_sha256': hashlib.sha256((out / 'prediction_receipt.json').read_bytes()).hexdigest(), 'state_recovery_sha256': hashlib.sha256((out / 'state_recovery.json').read_bytes()).hexdigest(), 'github_api_stats_sha256': hashlib.sha256(stats_path.read_bytes()).hexdigest() if stats_path and stats_path.exists() else None, 'production_base_binding_sha256': hashlib.sha256((out / 'production_base_binding.json').read_bytes()).hexdigest(), 'formal_head': formal_head, 'current_sha256': current_sha256, 'fusion_weights': weights, 'prediction_sha': prediction_sha, 'model_route': receipt.get('model_route'), 'fallback_exact_v1': receipt.get('fallback_exact_v1'), 'state_integrity_guard_status': 'PASS'}
    if not execution['resolved_live_base_sha'] == execution['checkout_head_sha'] == execution['final_live_base_sha']:
        raise ProductionBaseBindingError('PRODUCTION_EXACT_HEAD_BINDING_MISMATCH')
    (out / 'production_execution_binding_receipt.json').write_bytes(canonical_bytes(execution) + b'\n')
    print('PRODUCTION_EXECUTION_BINDING_PASS', prediction_sha, production_inventory_sha)

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    rec = sub.add_parser('record-checkout')
    rec.add_argument('--event-name', required=True)
    rec.add_argument('--event-base-sha', default='')
    rec.add_argument('--canonical-base-ref', required=True)
    rec.add_argument('--resolved-live-base-sha', required=True)
    rec.add_argument('--resolution-timestamp', required=True)
    rec.add_argument('--request-carrier-ref', default='')
    rec.add_argument('--request-carrier-head', default='')
    rec.add_argument('--out', required=True)
    rec.set_defaults(func=record_checkout)
    bind = sub.add_parser('bind-request')
    bind.add_argument('--binding', required=True)
    bind.add_argument('--request', required=True)
    bind.add_argument('--transport', required=True)
    bind.set_defaults(func=bind_request)
    fin = sub.add_parser('finalize')
    fin.add_argument('--binding', required=True)
    fin.add_argument('--repo', required=True)
    fin.add_argument('--token', required=True)
    fin.add_argument('--out-dir', required=True)
    fin.add_argument('--stats', default='')
    fin.add_argument('--prediction-required', required=True)
    fin.add_argument('--final-timestamp', default='')
    fin.set_defaults(func=finalize)
    return ap

def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except ProductionBaseBindingError as exc:
        print(str(exc), file=os.sys.stderr)
        return 2
    return 0
if __name__ == '__main__':
    raise SystemExit(main())