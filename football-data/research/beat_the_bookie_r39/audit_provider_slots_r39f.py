#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

COL_RE = re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES = ('home', 'draw', 'away')


def parse_dt(d: str, t: str) -> datetime:
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(f'{d} {t}', fmt)
        except ValueError:
            pass
    raise ValueError(f'bad datetime {d} {t}')


def valid(v: str) -> bool:
    s = v.strip().casefold()
    if not s or s in {'nan', 'na', 'null', 'none'}:
        return False
    try:
        x = float(s)
    except ValueError:
        return False
    return math.isfinite(x) and x > 1.0


def parse_mapping(header: list[str]) -> dict[tuple[int, int, str], int]:
    mapping = {}
    for i, name in enumerate(header[3:], 3):
        m = COL_RE.match(name)
        if not m:
            continue
        outcome, book, hour = m.group(1), int(m.group(2)), int(m.group(3))
        mapping[(book, hour, outcome)] = i
    return mapping


def provider_all72(row: list[str], mapping: dict[tuple[int, int, str], int], book: int) -> bool:
    for hour in range(72):
        for outcome in OUTCOMES:
            if not valid(row[mapping[(book, hour, outcome)]]):
                return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--registration', type=Path, required=True)
    ap.add_argument('--source-dir', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, required=True)
    args = ap.parse_args()

    reg = json.loads(args.registration.read_text(encoding='utf-8'))
    start = datetime.fromisoformat(reg['source']['holdout_start'])
    expected_cells = int(reg['audit_requirements']['exact_provider_hour_outcome_cells_per_file'])

    total_rows = 0
    strict_rows = 0
    strict_training = 0
    strict_holdout = 0
    bad_rows = 0
    provider_total = {f'b{i}': 0 for i in range(1, 33)}
    provider_training = {f'b{i}': 0 for i in range(1, 33)}
    provider_holdout = {f'b{i}': 0 for i in range(1, 33)}
    schemas = []
    mapping_sizes = []

    for source in ('odds_series_no_scores.csv.gz', 'odds_series_b_no_scores.csv.gz'):
        with gzip.open(args.source_dir / source, 'rt', encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            header = next(reader)
            if header[:3] != ['match_id', 'match_date', 'match_time']:
                raise RuntimeError(f'bad prefix: {header[:5]}')
            if 'score_home' in header or 'score_away' in header:
                raise RuntimeError('score columns reached Python input')
            mapping = parse_mapping(header)
            mapping_sizes.append(len(mapping))
            if len(mapping) != expected_cells:
                raise RuntimeError(f'expected {expected_cells} provider-hour-outcome cells, found {len(mapping)}')
            slots = sorted({book for book, _, _ in mapping})
            hours = sorted({hour for _, hour, _ in mapping})
            outcomes = sorted({outcome for _, _, outcome in mapping})
            if slots != list(range(1, 33)) or hours != list(range(72)) or outcomes != ['away', 'draw', 'home']:
                raise RuntimeError('provider/hour/outcome schema mismatch')
            schemas.append(header[3:])

            for row in reader:
                if len(row) != len(header) or not row[0].strip():
                    bad_rows += 1
                    continue
                try:
                    dt = parse_dt(row[1], row[2])
                except ValueError:
                    bad_rows += 1
                    continue
                total_rows += 1
                common = []
                for book in range(1, 33):
                    if provider_all72(row, mapping, book):
                        common.append(book)
                if len(common) < 5:
                    continue
                strict_rows += 1
                is_training = dt < start
                if is_training:
                    strict_training += 1
                else:
                    strict_holdout += 1
                for book in common:
                    key = f'b{book}'
                    provider_total[key] += 1
                    if is_training:
                        provider_training[key] += 1
                    else:
                        provider_holdout[key] += 1

    same_schema = len(schemas) == 2 and schemas[0] == schemas[1]
    ge1000 = [k for k, v in provider_training.items() if v >= 1000]
    ge3000 = [k for k, v in provider_training.items() if v >= 3000]
    req = reg['audit_requirements']
    passed = (
        same_schema
        and strict_rows >= int(req['minimum_strict_lane_total_rows'])
        and strict_training >= int(req['minimum_strict_lane_training_rows'])
        and len(ge1000) >= int(req['minimum_provider_slots_with_at_least_1000_training_all72_appearances'])
        and len(ge3000) >= int(req['minimum_provider_slots_with_at_least_3000_training_all72_appearances'])
    )

    out = {
        'schema_version': reg['schema_version'],
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': reg['pass_status'] if passed else reg['fail_status'],
        'source_rows_scanned': total_rows,
        'bad_rows': bad_rows,
        'same_provider_slot_schema_across_files': same_schema,
        'mapping_sizes': mapping_sizes,
        'provider_identity_semantics': 'anonymous stable column slots b1..b32; no claim of brand-name identity',
        'strict_lane_total_rows': strict_rows,
        'strict_lane_training_rows': strict_training,
        'strict_lane_holdout_rows': strict_holdout,
        'provider_all72_total_appearances': provider_total,
        'provider_all72_training_appearances': provider_training,
        'provider_all72_holdout_appearances': provider_holdout,
        'provider_slots_training_ge1000': ge1000,
        'provider_slots_training_ge3000': ge3000,
        'r39e_blind_holdout_binding': reg['r39e_blind_holdout_binding'],
        'no_label_audit': {
            'score_columns_present_in_python_input': False,
            'score_values_accessed': 0,
            'result_values_accessed': 0,
            'prediction_metrics_computed': 0,
            'model_fit': 0,
            'threshold_selection': 0,
            'blind_holdout_labels_accessed': 0
        },
        'hard_limits': reg['hard_limits']
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / 'provider_slot_audit_r39f.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
