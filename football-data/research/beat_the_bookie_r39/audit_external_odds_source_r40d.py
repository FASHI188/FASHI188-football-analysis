#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, hashlib, json, math, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def open_text(path: Path):
    try:
        f = path.open('r', encoding='utf-8-sig', newline='')
        f.read(4096); f.seek(0)
        return f, 'utf-8-sig'
    except UnicodeDecodeError:
        try: f.close()
        except Exception: pass
        return path.open('r', encoding='latin-1', newline=''), 'latin-1'


def parse_dt(v: str):
    s = str(v or '').strip()
    if not s:
        return None
    variants = [s, s.replace('Z', '+00:00')]
    for x in variants:
        try:
            return datetime.fromisoformat(x)
        except ValueError:
            pass
    for fmt in (
        '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
        '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
        '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y',
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def numeric(v: str):
    try:
        x = float(str(v).strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--registration', type=Path, required=True)
    ap.add_argument('--odds-csv', type=Path, required=True)
    ap.add_argument('--archive-sha256', required=True)
    ap.add_argument('--archive-members-json', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    reg = json.loads(args.registration.read_text(encoding='utf-8'))
    assert reg['status'] == 'PRE_REGISTERED_ZERO_LABEL_EXTERNAL_ODDS_SOURCE_AUDIT'
    assert reg['hard_limits']['result_labels_allowed'] is False
    assert reg['hard_limits']['model_fit_allowed'] is False
    assert reg['hard_limits']['fifth_fixed100_authorized'] is False

    members = json.loads(args.archive_members_json.read_text(encoding='utf-8'))
    result_members = [x for x in members if 'result' in x.casefold()]
    odds_members = [x for x in members if 'odd' in x.casefold() and x.casefold().endswith('.csv')]

    f, encoding = open_text(args.odds_csv)
    with f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        if not fields:
            raise RuntimeError('empty odds CSV header')
        stats = {k: {'nonempty':0,'numeric':0,'gt1':0,'datetime':0,'unique_sample':set(),'dt_min':None,'dt_max':None} for k in fields}
        rows = 0
        max_profile = 200000
        for row in reader:
            rows += 1
            if rows <= max_profile:
                for k in fields:
                    v = str(row.get(k, '') or '').strip()
                    if not v:
                        continue
                    s = stats[k]; s['nonempty'] += 1
                    if len(s['unique_sample']) < 100000:
                        s['unique_sample'].add(v)
                    x = numeric(v)
                    if x is not None:
                        s['numeric'] += 1
                        if x > 1.0: s['gt1'] += 1
                    d = parse_dt(v)
                    if d is not None:
                        s['datetime'] += 1
                        ds = d.isoformat()
                        s['dt_min'] = ds if s['dt_min'] is None or ds < s['dt_min'] else s['dt_min']
                        s['dt_max'] = ds if s['dt_max'] is None or ds > s['dt_max'] else s['dt_max']

    profile_n = min(rows, max_profile)
    profile = {}
    timestamp_like = []
    odds_like = []
    id_like = []
    for k, s in stats.items():
        non = s['nonempty']
        numeric_rate = s['numeric'] / non if non else 0.0
        gt1_rate = s['gt1'] / s['numeric'] if s['numeric'] else 0.0
        dt_rate = s['datetime'] / non if non else 0.0
        profile[k] = {
            'nonempty_profile_rows': non,
            'numeric_rate_nonempty': numeric_rate,
            'numeric_gt1_rate': gt1_rate,
            'datetime_parse_rate_nonempty': dt_rate,
            'unique_values_sample_capped': len(s['unique_sample']),
            'datetime_min': s['dt_min'], 'datetime_max': s['dt_max'],
        }
        name = k.casefold()
        if dt_rate >= 0.50 or any(t in name for t in ('date','time','timestamp','created','updated','change')):
            timestamp_like.append(k)
        if numeric_rate >= 0.80 and gt1_rate >= 0.50 and any(t in name for t in ('odd','home','draw','away','x','1','2')):
            odds_like.append(k)
        if any(t in name for t in ('match_id','matchid','fixture_id','event_id')) or name in {'id','match'}:
            id_like.append(k)

    unique_match_estimate = 0
    selected_id_col = None
    for k in id_like:
        u = profile[k]['unique_values_sample_capped']
        if u > unique_match_estimate:
            unique_match_estimate = u; selected_id_col = k

    gate = reg['source_screen']
    gates = {
        'odds_rows_minimum': rows >= int(gate['minimum_odds_rows']),
        'unique_match_ids_minimum_in_profile': unique_match_estimate >= int(gate['minimum_unique_match_ids']),
        'timestamp_like_column_present': len(timestamp_like) >= 1,
        'three_odds_like_columns_present': len(odds_like) >= 3,
        'result_file_values_accessed_zero': True,
        'model_fits_zero': True,
        'prediction_metrics_zero': True,
        'identity_locks_zero': True,
    }
    passed = all(gates.values())
    status = 'PASS_R40D_ZERO_LABEL_EXTERNAL_ODDS_SOURCE_SCREEN' if passed else 'STOP_R40D_EXTERNAL_ODDS_SOURCE_NOT_READY'
    out = {
        'schema_version': reg['schema_version'],
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'source': {
            'dataset_slug': reg['source_binding']['dataset_slug'],
            'archive_sha256': args.archive_sha256,
            'archive_members': members,
            'odds_members': odds_members,
            'result_members_present_but_not_extracted': result_members,
            'odds_csv_sha256': sha256(args.odds_csv),
            'odds_csv_bytes': args.odds_csv.stat().st_size,
            'encoding': encoding,
            'rows': rows,
            'profile_rows': profile_n,
            'fields': fields,
        },
        'schema_profile': profile,
        'candidate_timestamp_columns': timestamp_like,
        'candidate_odds_columns': odds_like,
        'candidate_match_id_columns': id_like,
        'selected_match_id_column_for_source_screen': selected_id_col,
        'unique_match_ids_profile_capped': unique_match_estimate,
        'gates': gates,
        'no_label_audit': {
            'result_file_extracted': False,
            'result_values_accessed': 0,
            'model_fits': 0,
            'prediction_metrics': 0,
            'thresholds_selected': 0,
            'identity_locks_created': 0,
            'fifth_fixed100_accessed': 0,
        },
        'next_stage_authorization': 'ZERO_LABEL_KICKOFF_IDENTITY_AUDIT_ONLY_FIFTH100_FORBIDDEN' if passed else 'CLOSE_SOURCE_OR_REPAIR_SOURCE_ACCESS_NO_LABELS',
        'hard_limits': reg['hard_limits'],
    }
    p = args.out_dir / 'external_odds_source_audit_status_r40d.json'
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
