#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
R3A = Path(__file__).with_name('diagnose_coverage_r3a.py')
PREREG_SHA = '6eea5dde15835bd0d8fcf2792d8e6915d8e526fb9f5c45dfae06e148a39222ec'
LOCK_RECEIPT_SHA = '33d572c4a3b7803e72b2839ba59c085b4bd6a2eafc053ca6e952c31b4b71c431'
LOCK_CANONICAL_SHA = 'cae1d81f80bb5af0c65cf502ac18c335840d61e96ac2168899d85db229c6fe29'
IDENTITY_SET_SHA = 'ad5679c194d7c3fa5a3691e01605ff234373e190cade90a65a17a3f08031d523'
SOURCE_COMMIT = '90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff'
ELIGIBLE_COUNT = 63
CANDIDATE = 'DRAW_T5_PLUS_HALF_T30_MOVE'
BASELINE = 'DRAW_FAIR_T5'
BOOTSTRAP_REPS = 5000
BOOTSTRAP_SEED = 53005


class PilotError(RuntimeError):
    pass


@dataclass(frozen=True)
class Row:
    identity_hash: str
    source_path: Path
    baseline: float
    candidate: float


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise PilotError(f'JSON object required: {path}')
    return value


def dump(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    if load(path) != value:
        raise PilotError(f'persist/reload mismatch: {path}')


def canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def ordered_sha(values: Sequence[str]) -> str:
    return hashlib.sha256(''.join(value + '\n' for value in values).encode('utf-8')).hexdigest()


def clip(value: float) -> float:
    return min(0.999999, max(0.000001, float(value)))


def qdraw(prices: Sequence[float]) -> float:
    if len(prices) != 3 or any(not math.isfinite(value) or value < 1.01 for value in prices):
        raise PilotError('invalid H/D/A prices')
    inverse = [1.0 / value for value in prices]
    return inverse[1] / sum(inverse)


def r3a_module() -> Any:
    spec = importlib.util.spec_from_file_location('r3_execution_helper', R3A)
    if spec is None or spec.loader is None:
        raise PilotError('R3A helper unavailable')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def verify_preregistration(prereg_path: Path, lock_receipt_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    prereg = load(prereg_path)
    receipt = load(lock_receipt_path)
    if canonical_sha(prereg) != PREREG_SHA:
        raise PilotError('preregistration canonical SHA mismatch')
    if canonical_sha(receipt) != LOCK_RECEIPT_SHA:
        raise PilotError('identity-lock receipt canonical SHA mismatch')
    if prereg.get('schema_version') != 'BETFAIR-DRAW-TRAJECTORY-PILOT-PREREG-R3':
        raise PilotError('preregistration schema mismatch')
    if prereg.get('status') != 'PRE_REGISTERED_NOT_AUTHORIZED_NOT_RUN':
        raise PilotError('preregistration status mismatch')
    if prereg.get('source_commit') != SOURCE_COMMIT:
        raise PilotError('source commit mismatch')
    identity = prereg['identity_lock']
    if identity.get('eligible_count') != ELIGIBLE_COUNT:
        raise PilotError('eligible count mismatch')
    if identity.get('ordered_identity_hashes_sha256') != IDENTITY_SET_SHA:
        raise PilotError('identity-set SHA mismatch')
    if identity.get('identity_lock_canonical_sha256') != LOCK_CANONICAL_SHA:
        raise PilotError('identity-lock canonical SHA mismatch')
    if receipt.get('eligible_count') != ELIGIBLE_COUNT or receipt.get('ordered_identity_hashes_sha256') != IDENTITY_SET_SHA:
        raise PilotError('identity-lock receipt binding mismatch')
    probability = prereg['probability_contract']
    if probability.get('candidate_count') != 1:
        raise PilotError('candidate count mismatch')
    if probability.get('baseline') != {'id': BASELINE, 'formula': 'qD_T5'}:
        raise PilotError('baseline mismatch')
    expected_candidate = {
        'id': CANDIDATE,
        'coefficient': 0.5,
        'formula': 'clip(qD_T5 + 0.5 * (qD_T5 - qD_T30))',
        'source_cutoffs_minutes_before_kickoff': [30, 5],
    }
    if probability.get('fixed_candidate') != expected_candidate:
        raise PilotError('candidate mismatch')
    if probability.get('model_fit_allowed') is not False or probability.get('threshold_selection_allowed') is not False:
        raise PilotError('fit/threshold boundary mismatch')
    bootstrap = prereg['bootstrap']
    if bootstrap.get('replicates') != BOOTSTRAP_REPS or bootstrap.get('seed') != BOOTSTRAP_SEED:
        raise PilotError('bootstrap mismatch')
    hard = prereg['hard_limits']
    if hard.get('formal_weight') != 0:
        raise PilotError('formal weight mismatch')
    for key in ('formal_promotion_allowed', 'current_match_use_allowed', 'formal_ev_allowed', 'formal_model_mutation_allowed', 'formal_data_mutation_allowed', 'formal_config_mutation_allowed', 'CURRENT_mutation_allowed'):
        if hard.get(key) is not False:
            raise PilotError(f'hard limit mismatch: {key}')
    return prereg, receipt


def snapshot_prices(module: Any, parsed: dict[str, Any], cutoff_minutes: int, max_stale: int, max_span: int) -> tuple[float, float, float] | None:
    target = parsed['market_time'] - timedelta(minutes=cutoff_minutes)
    selected = []
    for runner_id in parsed['runner_ids']:
        observation = module.latest(parsed['histories'].get(runner_id, []), target)
        if observation is None:
            return None
        age = (target - observation.t).total_seconds()
        if age < 0 or age > max_stale:
            return None
        selected.append(observation)
    span = (max(item.t for item in selected) - min(item.t for item in selected)).total_seconds()
    if span > max_span:
        return None
    return tuple(item.p for item in selected)


def reconstruct(checkout: Path, prereg: dict[str, Any]) -> list[Row]:
    module = r3a_module()
    helper = module.helper()
    cfg = module.load(module.HELPER_CFG)
    start = prereg['snapshot_contract']['start']
    end = prereg['snapshot_contract']['end']
    rows: list[Row] = []
    for path in module.candidate_files(checkout):
        try:
            parsed = module.parse_market(path, helper, cfg)
        except Exception:
            continue
        prices_30 = snapshot_prices(
            module,
            parsed,
            int(start['cutoff_minutes']),
            int(start['maximum_single_runner_staleness_seconds']),
            int(start['maximum_home_draw_away_observation_span_seconds']),
        )
        prices_5 = snapshot_prices(
            module,
            parsed,
            int(end['cutoff_minutes']),
            int(end['maximum_single_runner_staleness_seconds']),
            int(end['maximum_home_draw_away_observation_span_seconds']),
        )
        if prices_30 is None or prices_5 is None:
            continue
        relative = path.relative_to(checkout).as_posix()
        identity_hash = hashlib.sha256(relative.encode('utf-8')).hexdigest()
        q30 = qdraw(prices_30)
        q5 = qdraw(prices_5)
        rows.append(Row(identity_hash, path, clip(q5), clip(q5 + 0.5 * (q5 - q30))))
    rows.sort(key=lambda row: row.identity_hash)
    hashes = [row.identity_hash for row in rows]
    if len(rows) != ELIGIBLE_COUNT or len(set(hashes)) != ELIGIBLE_COUNT:
        raise PilotError(f'identity reconstruction count mismatch: {len(rows)}')
    if ordered_sha(hashes) != IDENTITY_SET_SHA:
        raise PilotError('identity reconstruction SHA mismatch')
    return rows


def read_draw_label(path: Path, helper: Any, cfg: dict[str, Any]) -> int:
    final_definition: dict[str, Any] | None = None
    with path.open('r', encoding='utf-8-sig') as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            message = json.loads(raw)
            for change in message.get('mc') or []:
                if isinstance(change, dict) and isinstance(change.get('marketDefinition'), dict):
                    final_definition = change['marketDefinition']
    if not isinstance(final_definition, dict):
        raise PilotError('settlement definition missing')
    mapping = helper.runner_map(final_definition, cfg)
    winners = [int(item['id']) for item in final_definition.get('runners') or [] if item.get('status') == 'WINNER']
    if len(winners) != 1:
        raise PilotError('winner label invalid')
    return int(winners[0] == int(mapping['draw_id']))


def validate_metric_inputs(labels: Sequence[int], scores: Sequence[float]) -> None:
    if len(labels) != len(scores) or not labels:
        raise PilotError('metric input length mismatch')
    if any(label not in (0, 1) for label in labels):
        raise PilotError('invalid label')
    if any(not math.isfinite(score) or not 0.000001 <= score <= 0.999999 for score in scores):
        raise PilotError('invalid score')


def average_precision(labels: Sequence[int], scores: Sequence[float]) -> float:
    validate_metric_inputs(labels, scores)
    positives = sum(labels)
    if positives == 0:
        raise PilotError('average precision undefined without positives')
    groups: dict[float, list[int]] = {}
    for label, score in zip(labels, scores):
        groups.setdefault(float(score), []).append(int(label))
    seen = 0
    true_seen = 0
    numerator = 0.0
    for score in sorted(groups, reverse=True):
        group = groups[score]
        group_positive = sum(group)
        seen += len(group)
        true_seen += group_positive
        if group_positive:
            numerator += group_positive * (true_seen / seen)
    return numerator / positives


def roc_auc(labels: Sequence[int], scores: Sequence[float]) -> float:
    validate_metric_inputs(labels, scores)
    positives = [score for label, score in zip(labels, scores) if label == 1]
    negatives = [score for label, score in zip(labels, scores) if label == 0]
    if not positives or not negatives:
        raise PilotError('ROC AUC undefined for one-class labels')
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                wins += 1.0
            elif positive == negative:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def brier(labels: Sequence[int], scores: Sequence[float]) -> float:
    validate_metric_inputs(labels, scores)
    return sum((score - label) ** 2 for label, score in zip(labels, scores)) / len(labels)


def log_loss(labels: Sequence[int], scores: Sequence[float]) -> float:
    validate_metric_inputs(labels, scores)
    return -sum(label * math.log(score) + (1 - label) * math.log(1 - score) for label, score in zip(labels, scores)) / len(labels)


def percentile_r7(values: Sequence[float], probability: float) -> float:
    if not values:
        raise PilotError('empty percentile input')
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def metric_bundle(labels: Sequence[int], baseline_scores: Sequence[float], candidate_scores: Sequence[float]) -> dict[str, Any]:
    baseline = {
        'average_precision': average_precision(labels, baseline_scores),
        'roc_auc': roc_auc(labels, baseline_scores),
        'brier_score': brier(labels, baseline_scores),
        'log_loss': log_loss(labels, baseline_scores),
    }
    candidate = {
        'average_precision': average_precision(labels, candidate_scores),
        'roc_auc': roc_auc(labels, candidate_scores),
        'brier_score': brier(labels, candidate_scores),
        'log_loss': log_loss(labels, candidate_scores),
    }
    deltas = {key: candidate[key] - baseline[key] for key in baseline}
    return {'baseline': baseline, 'candidate': candidate, 'deltas': deltas}


def bootstrap(labels: Sequence[int], baseline_scores: Sequence[float], candidate_scores: Sequence[float]) -> dict[str, Any]:
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(labels)
    delta_rows: dict[str, list[float]] = {
        'average_precision': [],
        'roc_auc': [],
        'brier_score': [],
        'log_loss': [],
    }
    degenerate = 0
    for _ in range(BOOTSTRAP_REPS):
        indices = [rng.randrange(n) for _ in range(n)]
        y = [labels[index] for index in indices]
        baseline = [baseline_scores[index] for index in indices]
        candidate = [candidate_scores[index] for index in indices]
        if sum(y) in (0, len(y)):
            degenerate += 1
            delta_rows['average_precision'].append(0.0)
            delta_rows['roc_auc'].append(0.0)
        else:
            delta_rows['average_precision'].append(average_precision(y, candidate) - average_precision(y, baseline))
            delta_rows['roc_auc'].append(roc_auc(y, candidate) - roc_auc(y, baseline))
        delta_rows['brier_score'].append(brier(y, candidate) - brier(y, baseline))
        delta_rows['log_loss'].append(log_loss(y, candidate) - log_loss(y, baseline))
    intervals = {
        key: {'p05': percentile_r7(values, 0.05), 'p95': percentile_r7(values, 0.95)}
        for key, values in delta_rows.items()
    }
    return {
        'method': 'paired_market_resampling_with_replacement',
        'replicates': BOOTSTRAP_REPS,
        'seed': BOOTSTRAP_SEED,
        'degenerate_one_class_replicates_retained_with_ap_auc_delta_zero': degenerate,
        'delta_intervals': intervals,
    }


def evaluate(labels: Sequence[int], baseline_scores: Sequence[float], candidate_scores: Sequence[float]) -> dict[str, Any]:
    metrics = metric_bundle(labels, baseline_scores, candidate_scores)
    boot = bootstrap(labels, baseline_scores, candidate_scores)
    deltas = metrics['deltas']
    gates = {
        'average_precision_delta_gt_0': deltas['average_precision'] > 0,
        'average_precision_bootstrap_p05_gt_0': boot['delta_intervals']['average_precision']['p05'] > 0,
        'roc_auc_delta_ge_0': deltas['roc_auc'] >= 0,
        'brier_score_delta_le_0': deltas['brier_score'] <= 0,
        'log_loss_delta_le_0': deltas['log_loss'] <= 0,
    }
    passed = all(gates.values())
    return {
        'metrics': metrics,
        'bootstrap': boot,
        'pass_gates': gates,
        'all_pass_gates_met': passed,
        'status': 'PASS_TRAJECTORY_PILOT_R3_INCREMENT' if passed else 'STOP_TRAJECTORY_PILOT_R3_NO_INCREMENT',
    }


def selftest() -> dict[str, Any]:
    labels = [0, 1, 0, 1, 0, 1, 0, 0]
    baseline = [0.10, 0.40, 0.20, 0.35, 0.15, 0.30, 0.25, 0.05]
    candidate = [0.08, 0.45, 0.18, 0.38, 0.14, 0.34, 0.22, 0.04]
    bundle = metric_bundle(labels, baseline, candidate)
    if not all(math.isfinite(value) for section in ('baseline', 'candidate', 'deltas') for value in bundle[section].values()):
        raise PilotError('metric selftest non-finite')
    tie_labels = [1, 0, 1, 0]
    tie_scores = [0.5, 0.5, 0.2, 0.2]
    if not math.isclose(roc_auc(tie_labels, tie_scores), 0.5, rel_tol=0, abs_tol=1e-12):
        raise PilotError('AUC tie selftest failed')
    sample = [float(index) for index in range(10)]
    if not math.isclose(percentile_r7(sample, 0.05), 0.45, rel_tol=0, abs_tol=1e-12):
        raise PilotError('R7 percentile selftest failed')
    return {'status': 'PASS_R3_METRIC_SELFTEST', 'metric_bundle': bundle}


def run_preflight(prereg_path: Path, lock_receipt_path: Path, output: Path) -> None:
    prereg, receipt = verify_preregistration(prereg_path, lock_receipt_path)
    result = {
        'schema_version': 'BETFAIR-DRAW-TRAJECTORY-PILOT-EXECUTION-PREFLIGHT-R3',
        'status': 'PASS_R3_EXECUTION_CODE_PREFLIGHT_NO_LABEL_ACCESS',
        'preregistration_sha256': canonical_sha(prereg),
        'identity_lock_receipt_sha256': canonical_sha(receipt),
        'identity_set_sha256': IDENTITY_SET_SHA,
        'eligible_count': ELIGIBLE_COUNT,
        'selftest': selftest(),
        'authorization_file_present': False,
        'consumed_marker_present': False,
        'winner_labels_read': 0,
        'post_kickoff_messages_parsed': 0,
        'model_fits': 0,
        'thresholds_selected': 0,
        'formal_weight': 0,
        'formal_model_changes': 0,
        'formal_data_changes': 0,
        'formal_config_changes': 0,
        'CURRENT_changes': 0,
    }
    dump(output, result)


def run_labels(prereg_path: Path, lock_receipt_path: Path, checkout: Path, output: Path) -> None:
    prereg, _ = verify_preregistration(prereg_path, lock_receipt_path)
    rows = reconstruct(checkout, prereg)
    module = r3a_module()
    helper = module.helper()
    cfg = module.load(module.HELPER_CFG)
    labels = [read_draw_label(row.source_path, helper, cfg) for row in rows]
    evaluation = evaluate(labels, [row.baseline for row in rows], [row.candidate for row in rows])
    final = {
        'schema_version': 'BETFAIR-DRAW-TRAJECTORY-PILOT-FINAL-R3',
        **evaluation,
        'source_commit': SOURCE_COMMIT,
        'eligible_count': ELIGIBLE_COUNT,
        'identity_set_sha256': IDENTITY_SET_SHA,
        'baseline_id': BASELINE,
        'candidate_id': CANDIDATE,
        'winner_labels_read': len(labels),
        'draw_label_count': sum(labels),
        'raw_or_per_market_data_persisted_or_uploaded': False,
        'model_fits': 0,
        'thresholds_selected': 0,
        'formal_weight': 0,
        'formal_model_changes': 0,
        'formal_data_changes': 0,
        'formal_config_changes': 0,
        'CURRENT_changes': 0,
        'rerun_allowed': False,
        'current_match_use_allowed': False,
        'formal_promotion_allowed': False,
    }
    dump(output, final)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='mode', required=True)
    test_parser = subparsers.add_parser('selftest')
    test_parser.add_argument('--output', type=Path)
    preflight_parser = subparsers.add_parser('preflight')
    preflight_parser.add_argument('--prereg', type=Path, required=True)
    preflight_parser.add_argument('--lock-receipt', type=Path, required=True)
    preflight_parser.add_argument('--output', type=Path, required=True)
    run_parser = subparsers.add_parser('run-labels')
    run_parser.add_argument('--prereg', type=Path, required=True)
    run_parser.add_argument('--lock-receipt', type=Path, required=True)
    run_parser.add_argument('--source-checkout', type=Path, required=True)
    run_parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'selftest':
        value = selftest()
        if args.output:
            dump(args.output, value)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    elif args.mode == 'preflight':
        run_preflight(args.prereg, args.lock_receipt, args.output)
    elif args.mode == 'run-labels':
        run_labels(args.prereg, args.lock_receipt, args.source_checkout, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
