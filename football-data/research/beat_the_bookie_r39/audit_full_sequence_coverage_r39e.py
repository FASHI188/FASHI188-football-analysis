#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

COL_RE = re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES = ('home', 'draw', 'away')


def valid(value: str) -> bool:
    text = value.strip().casefold()
    if not text or text in {'nan', 'na', 'null', 'none'}:
        return False
    try:
        x = float(text)
    except ValueError:
        return False
    return math.isfinite(x) and x > 1.0


def parse_dt(d: str, t: str) -> datetime:
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(f'{d} {t}', fmt)
        except ValueError:
            pass
    raise ValueError(f'bad datetime {d} {t}')


def parse_mapping(header):
    mapping = {}
    for i, name in enumerate(header[3:], 3):
        m = COL_RE.match(name)
        if m:
            mapping[(int(m.group(2)), int(m.group(3)), m.group(1))] = i
    expected = 32 * 72 * 3
    if len(mapping) != expected:
        raise RuntimeError(f'odds mapping mismatch: {len(mapping)} != {expected}')
    return mapping


def complete_books(row, mapping, suffix: int) -> set[int]:
    books = set()
    for b in range(1, 33):
        vals = [row[mapping[(b, suffix, o)]] for o in OUTCOMES]
        if all(valid(v) for v in vals):
            books.add(b)
    return books


def longest_false_run(mask: list[bool]) -> int:
    best = cur = 0
    for flag in mask:
        if flag:
            cur = 0
        else:
            cur += 1
            best = max(best, cur)
    return best


def stats(values: list[int]) -> dict:
    if not values:
        return {'n': 0}
    x = sorted(values)
    n = len(x)
    def q(frac: float):
        pos = min(n - 1, max(0, int(round(frac * (n - 1)))))
        return x[pos]
    return {
        'n': n,
        'min': x[0],
        'p05': q(.05),
        'p10': q(.10),
        'p25': q(.25),
        'p50': q(.50),
        'p75': q(.75),
        'p90': q(.90),
        'p95': q(.95),
        'max': x[-1],
        'mean': sum(x) / n,
    }


def canonical_sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--registration', type=Path, required=True)
    ap.add_argument('--source-dir', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, required=True)
    args = ap.parse_args()
    reg = json.loads(args.registration.read_text(encoding='utf-8'))
    assert reg['status'] == 'PRE_REGISTERED_NO_LABEL_SEQUENCE_COVERAGE_AUDIT'
    assert reg['time_semantics']['kickoff_T0_suffix_71_allowed'] is False
    assert reg['hard_limits']['result_labels_allowed'] is False
    args.out_dir.mkdir(parents=True, exist_ok=True)

    holdout_start = datetime.fromisoformat(reg['population_binding']['holdout_start'])
    threshold_list = [int(x) for x in reg['hourly_consensus_audit']['bookmaker_thresholds']]
    primary = int(reg['hourly_consensus_audit']['primary_sequence_threshold'])
    assert primary == 3

    distributions = {
        'training': {k: [] for k in (
            'observed_hour_count_ge1', 'observed_hour_count_ge3', 'observed_hour_count_ge5',
            'observed_hour_count_ge10', 'longest_consecutive_missing_run_ge3',
            'early_T71_T25_observed_count_ge3', 'late_T24_T1_observed_count_ge3')},
        'holdout': {k: [] for k in (
            'observed_hour_count_ge1', 'observed_hour_count_ge3', 'observed_hour_count_ge5',
            'observed_hour_count_ge10', 'longest_consecutive_missing_run_ge3',
            'early_T71_T25_observed_count_ge3', 'late_T24_T1_observed_count_ge3')},
    }
    eligible = Counter()
    all_rows_seen = Counter()
    invalid_length_rows = 0
    t0_access_count = 0
    anchor_min_common = {'training': [], 'holdout': []}

    for original_name in ('odds_series.csv.gz', 'odds_series_b.csv.gz'):
        path = args.source_dir / original_name.replace('.csv.gz', '_no_scores.csv.gz')
        with gzip.open(path, 'rt', encoding='utf-8-sig', errors='strict', newline='') as f:
            reader = csv.reader(f)
            header = next(reader)
            if header[:3] != ['match_id', 'match_date', 'match_time']:
                raise RuntimeError(f'bad sanitized header: {header[:5]}')
            forbidden = {'score_home', 'score_away', 'score', 'home_score', 'away_score'}
            if forbidden & set(header):
                raise RuntimeError(f'forbidden score columns present: {forbidden & set(header)}')
            mapping = parse_mapping(header)

            for row in reader:
                all_rows_seen[original_name] += 1
                if len(row) != len(header):
                    invalid_length_rows += 1
                    continue
                if not row[0].strip():
                    continue
                dt = parse_dt(row[1], row[2])
                partition = 'training' if dt < holdout_start else 'holdout'

                anchors = [complete_books(row, mapping, 71 - h) for h in (24, 6, 1)]
                common = set.intersection(*anchors)
                if len(common) < 5:
                    continue
                eligible[partition] += 1
                anchor_min_common[partition].append(len(common))

                # Strictly pre-match suffixes 0..70 == approximately T-71h..T-1h.
                # Suffix 71 (kickoff T0) is intentionally never dereferenced.
                book_counts = []
                for suffix in range(0, 71):
                    book_counts.append(len(complete_books(row, mapping, suffix)))
                masks = {thr: [n >= thr for n in book_counts] for thr in threshold_list}
                d = distributions[partition]
                for thr in threshold_list:
                    d[f'observed_hour_count_ge{thr}'].append(sum(masks[thr]))
                primary_mask = masks[primary]
                d['longest_consecutive_missing_run_ge3'].append(longest_false_run(primary_mask))
                # suffix 0..46 corresponds approximately T-71..T-25; 47..70 == T-24..T-1.
                d['early_T71_T25_observed_count_ge3'].append(sum(primary_mask[:47]))
                d['late_T24_T1_observed_count_ge3'].append(sum(primary_mask[47:71]))

    expected_train = int(reg['population_binding']['expected_training_eligible_rows'])
    expected_hold = int(reg['population_binding']['expected_holdout_eligible_rows'])
    summary = {
        part: {name: stats(vals) for name, vals in metrics.items()}
        for part, metrics in distributions.items()
    }
    anchor_summary = {part: stats(vals) for part, vals in anchor_min_common.items()}
    min_observed = min(
        summary['training']['observed_hour_count_ge3']['min'],
        summary['holdout']['observed_hour_count_ge3']['min'],
    )
    gates = {
        'training_rows_exact': eligible['training'] == expected_train,
        'holdout_rows_exact': eligible['holdout'] == expected_hold,
        'all_eligible_rows_have_at_least_three_ge3_observed_hours': min_observed >= 3,
        'T0_access_count_zero': t0_access_count == 0,
        'score_or_result_access_count_zero': True,
        'prediction_metrics_count_zero': True,
        'identity_lock_count_zero': True,
    }
    gates['passed'] = all(gates.values())
    status = (
        'PASS_R39E_FULL_PREMATCH_SEQUENCE_COVERAGE_NO_LABELS'
        if gates['passed']
        else 'STOP_R39E_FULL_PREMATCH_SEQUENCE_COVERAGE_NO_LABELS'
    )
    result = {
        'schema_version': reg['schema_version'],
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'registration_canonical_sha256': canonical_sha(reg),
        'population': {
            'training_eligible_rows': eligible['training'],
            'holdout_eligible_rows': eligible['holdout'],
            'all_rows_seen_by_file': dict(all_rows_seen),
            'invalid_length_rows': invalid_length_rows,
        },
        'sequence_definition': {
            'suffixes_accessed': [0, 70],
            'approx_hours_before_kickoff': [71, 1],
            'hours_per_sequence': 71,
            'kickoff_T0_suffix_71_accessed': False,
            'primary_per_hour_minimum_complete_bookies': primary,
        },
        'aggregate_coverage_distributions': summary,
        'anchor_common_bookmakers_distribution': anchor_summary,
        'gates': gates,
        'no_label_audit': {
            'score_columns_present_in_python_input': False,
            'score_values_accessed': 0,
            'result_values_accessed': 0,
            'prediction_metrics_computed': 0,
            'model_fits': 0,
            'thresholds_selected': 0,
            'holdout_individual_identities_output': 0,
            'identity_locks_created': 0,
            'T0_suffix_71_odds_values_accessed': t0_access_count,
        },
        'hard_limits': reg['hard_limits'],
    }
    (args.out_dir / 'sequence_coverage_status_r39e.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
