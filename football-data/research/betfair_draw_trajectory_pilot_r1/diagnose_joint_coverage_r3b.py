#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
R3A = Path(__file__).with_name('diagnose_coverage_r3a.py')

CONFIGS = {
    'R2_STRICT_T90_T15': {
        'start': {'cutoff': 90, 'stale': 900, 'span': 300},
        'end': {'cutoff': 15, 'stale': 300, 'span': 120},
    },
    'T90_30M30M__T15_10M10M': {
        'start': {'cutoff': 90, 'stale': 1800, 'span': 1800},
        'end': {'cutoff': 15, 'stale': 600, 'span': 600},
    },
    'T90_60M30M__T15_15M10M': {
        'start': {'cutoff': 90, 'stale': 3600, 'span': 1800},
        'end': {'cutoff': 15, 'stale': 900, 'span': 600},
    },
    'T60_30M30M__T10_10M10M': {
        'start': {'cutoff': 60, 'stale': 1800, 'span': 1800},
        'end': {'cutoff': 10, 'stale': 600, 'span': 600},
    },
    'T60_60M30M__T15_15M10M': {
        'start': {'cutoff': 60, 'stale': 3600, 'span': 1800},
        'end': {'cutoff': 15, 'stale': 900, 'span': 600},
    },
    'T45_30M30M__T10_10M10M': {
        'start': {'cutoff': 45, 'stale': 1800, 'span': 1800},
        'end': {'cutoff': 10, 'stale': 600, 'span': 600},
    },
    'T30_15M10M__T5_10M10M': {
        'start': {'cutoff': 30, 'stale': 900, 'span': 600},
        'end': {'cutoff': 5, 'stale': 600, 'span': 600},
    },
    'T30_10M10M__T5_10M10M': {
        'start': {'cutoff': 30, 'stale': 600, 'span': 600},
        'end': {'cutoff': 5, 'stale': 600, 'span': 600},
    },
}


def module() -> Any:
    spec = importlib.util.spec_from_file_location('r3a_joint_helper', R3A)
    if spec is None or spec.loader is None:
        raise RuntimeError('R3A module unavailable')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def passes(features: dict[str, Any], gate: dict[str, int]) -> tuple[bool, str]:
    if not features['complete']:
        return False, 'missing_explicit_ltp'
    if features['max_age_seconds'] > gate['stale']:
        return False, 'staleness_exceeded'
    if features['span_seconds'] > gate['span']:
        return False, 'runner_span_exceeded'
    return True, 'pass'


def dump(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def run(checkout: Path, output: Path) -> None:
    m = module()
    helper = m.helper()
    cfg = m.load(m.HELPER_CFG)
    files = m.candidate_files(checkout)
    counts = Counter()
    parse_reasons = Counter()
    first_failures: dict[str, Counter[str]] = {key: Counter() for key in CONFIGS}
    valid = 0

    for path in files:
        try:
            parsed = m.parse_market(path, helper, cfg)
        except Exception as exc:
            parse_reasons[str(exc) or exc.__class__.__name__] += 1
            continue
        valid += 1
        for name, config in CONFIGS.items():
            start_gate = config['start']
            end_gate = config['end']
            start_features = m.snapshot_features(parsed, start_gate['cutoff'])
            start_ok, start_reason = passes(start_features, start_gate)
            if not start_ok:
                first_failures[name][f"T{start_gate['cutoff']}_{start_reason}"] += 1
                continue
            end_features = m.snapshot_features(parsed, end_gate['cutoff'])
            end_ok, end_reason = passes(end_features, end_gate)
            if not end_ok:
                first_failures[name][f"T{end_gate['cutoff']}_{end_reason}"] += 1
                continue
            counts[name] += 1
            first_failures[name]['eligible'] += 1

    report = {
        'schema_version': 'BETFAIR-DRAW-TRAJECTORY-JOINT-COVERAGE-DIAGNOSTIC-R3B',
        'status': 'COMPLETE_NO_LABEL_JOINT_COVERAGE_DIAGNOSTIC',
        'source_commit': m.SOURCE_COMMIT,
        'candidate_files': len(files),
        'valid_identity_markets': valid,
        'parse_or_identity_failure_reasons': dict(sorted(parse_reasons.items())),
        'configurations': CONFIGS,
        'joint_eligible_counts': dict(sorted(counts.items())),
        'first_failure_counts_by_configuration': {
            key: dict(sorted(value.items())) for key, value in sorted(first_failures.items())
        },
        'winner_labels_read': 0,
        'post_kickoff_messages_parsed': 0,
        'raw_names_prices_or_stream_messages_persisted': False,
        'per_market_rows_persisted': False,
        'model_fits': 0,
        'thresholds_selected': 0,
        'formal_weight': 0,
        'formal_model_changes': 0,
        'formal_data_changes': 0,
        'formal_config_changes': 0,
        'CURRENT_changes': 0,
    }
    dump(output, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-checkout', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.source_checkout, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
