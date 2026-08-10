#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np


def load_r39c(path: Path):
    spec = importlib.util.spec_from_file_location('r39c_parent', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot import frozen R39C evaluator')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hfile(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def htext(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def canonical_json_sha(obj) -> str:
    return htext(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False))


def parse_dt(value: str):
    s = str(value or '').strip()
    if not s:
        return None
    for x in (s, s.replace('Z', '+00:00')):
        try:
            return datetime.fromisoformat(x).replace(tzinfo=None)
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


def valid_odd(value: str) -> bool:
    try:
        x = float(str(value).strip())
        return math.isfinite(x) and x > 1.0
    except Exception:
        return False


def mid_key(mid: str):
    try:
        return (0, int(mid))
    except ValueError:
        return (1, mid)


def identity_digest(rows) -> str:
    h = hashlib.sha256()
    for r in rows:
        line = f"{r['match_id']}|{r['kickoff'].isoformat()}|{r['competition']}|{r['home']}|{r['away']}\n"
        h.update(line.encode('utf-8'))
    return h.hexdigest()


def f17(x: float) -> str:
    return format(float(x), '.17g')


def feature_digest(rows) -> str:
    h = hashlib.sha256()
    for r in rows:
        vals = [
            r['match_id'], r['kickoff'].isoformat(),
            *(f17(x) for x in r['q24']),
            *(f17(x) for x in r['q6']),
            *(f17(x) for x in r['q1']),
            *(f17(x) for x in r['features']),
        ]
        h.update(('|'.join(vals) + '\n').encode('utf-8'))
    return h.hexdigest()


def external_reduced_features(r39c, q24, q6, q1):
    d246 = float(q6[1] - q24[1])
    d61 = float(q1[1] - q6[1])
    gap24 = abs(float(q24[0] - q24[2]))
    gap1 = abs(float(q1[0] - q1[2]))
    e24 = r39c.entropy(q24)
    e1 = r39c.entropy(q1)
    return [
        r39c.logit(float(q1[1])),
        d246,
        d61,
        d61 / 5.0 - d246 / 18.0,
        gap24,
        gap1,
        gap1 - gap24,
        e24,
        e1,
        e1 - e24,
    ]


def load_external_features(path: Path, reg: dict, r39c):
    expected = [
        'match_id', 'date_start', 'competition_name', 'date_created',
        'home_team_name', 'away_team_name', 'home_team_odd', 'away_team_odd', 'tie_odd'
    ]
    identities = {}
    inconsistent = set()
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if list(reader.fieldnames or []) != expected:
            raise RuntimeError(f'unexpected external schema {reader.fieldnames}')
        for raw in reader:
            mid = str(raw['match_id']).strip()
            ko = parse_dt(raw['date_start'])
            obs = parse_dt(raw['date_created'])
            if not mid or ko is None or obs is None:
                continue
            ident = (
                raw['date_start'].strip(), raw['competition_name'].strip(),
                raw['home_team_name'].strip(), raw['away_team_name'].strip()
            )
            if mid not in identities:
                identities[mid] = ident
            elif identities[mid] != ident:
                inconsistent.add(mid)

    snapshots = defaultdict(dict)
    conflicting_duplicate_timestamp_odds = 0
    duplicate_identical_timestamp_rows = 0
    dropped_same_or_post = 0
    dropped_invalid_odds = 0
    dropped_inconsistent_rows = 0
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for raw in reader:
            mid = str(raw['match_id']).strip()
            ko = parse_dt(raw['date_start'])
            obs = parse_dt(raw['date_created'])
            if not mid or ko is None or obs is None:
                continue
            if mid in inconsistent:
                dropped_inconsistent_rows += 1
                continue
            if not obs < ko:
                dropped_same_or_post += 1
                continue
            if not all(valid_odd(raw[k]) for k in ('home_team_odd', 'away_team_odd', 'tie_odd')):
                dropped_invalid_odds += 1
                continue
            # Canonical H/D/A ordering. The source columns are home/away/tie.
            odds = (
                float(raw['home_team_odd']),
                float(raw['tie_odd']),
                float(raw['away_team_odd']),
            )
            prior = snapshots[mid].get(obs)
            if prior is None:
                snapshots[mid][obs] = odds
            elif prior == odds:
                duplicate_identical_timestamp_rows += 1
            else:
                conflicting_duplicate_timestamp_odds += 1

    if conflicting_duplicate_timestamp_odds:
        return [], {
            'identity_inconsistent_matches': len(inconsistent),
            'conflicting_duplicate_timestamp_odds': conflicting_duplicate_timestamp_odds,
            'duplicate_identical_timestamp_rows': duplicate_identical_timestamp_rows,
            'dropped_inconsistent_rows': dropped_inconsistent_rows,
            'dropped_same_or_post_rows': dropped_same_or_post,
            'dropped_invalid_odds_rows': dropped_invalid_odds,
        }

    min_obs = int(reg['eligibility_contract']['minimum_distinct_strict_prior_observations'])
    min_cutoff_times = int(reg['eligibility_contract']['minimum_distinct_cutoff_snapshot_times'])
    cutoffs = list(reg['eligibility_contract']['cutoffs_hours_before_kickoff'])
    eligible = []
    for mid, ident in identities.items():
        if mid in inconsistent:
            continue
        ko = parse_dt(ident[0])
        obsmap = snapshots.get(mid, {})
        times = sorted(obsmap)
        if len(times) < min_obs:
            continue
        selected_times = []
        qs = {}
        valid_match = True
        for hours in cutoffs:
            cutoff = ko - timedelta(hours=hours)
            candidates = [x for x in times if x <= cutoff]
            if not candidates:
                valid_match = False
                break
            picked = candidates[-1]
            selected_times.append(picked)
            odds = obsmap[picked]
            qs[hours] = r39c.devig(*odds)
        if not valid_match or len(set(selected_times)) < min_cutoff_times:
            continue
        q24, q6, q1 = qs[24], qs[6], qs[1]
        eligible.append({
            'match_id': mid,
            'kickoff': ko,
            'competition': ident[1],
            'home': ident[2],
            'away': ident[3],
            'q24': [float(x) for x in q24],
            'q6': [float(x) for x in q6],
            'q1': [float(x) for x in q1],
            'features': external_reduced_features(r39c, q24, q6, q1),
        })
    eligible.sort(key=lambda r: (r['kickoff'], mid_key(r['match_id'])))
    return eligible, {
        'identity_inconsistent_matches': len(inconsistent),
        'conflicting_duplicate_timestamp_odds': conflicting_duplicate_timestamp_odds,
        'duplicate_identical_timestamp_rows': duplicate_identical_timestamp_rows,
        'dropped_inconsistent_rows': dropped_inconsistent_rows,
        'dropped_same_or_post_rows': dropped_same_or_post,
        'dropped_invalid_odds_rows': dropped_invalid_odds,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--registration', type=Path, required=True)
    ap.add_argument('--r39c-prereg', type=Path, required=True)
    ap.add_argument('--r39c-code', type=Path, required=True)
    ap.add_argument('--internal-sanitized-dir', type=Path, required=True)
    ap.add_argument('--internal-original-dir', type=Path, required=True)
    ap.add_argument('--external-odds-csv', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    reg = json.loads(args.registration.read_text(encoding='utf-8'))
    parent_pre = json.loads(args.r39c_prereg.read_text(encoding='utf-8'))
    assert reg['status'] == 'PRE_REGISTERED_INTERNAL_TRAIN_EXTERNAL_ZERO_LABEL_FREEZE'
    assert reg['hard_limits']['external_result_file_extraction_allowed'] is False
    assert reg['hard_limits']['external_result_values_allowed'] is False
    assert reg['hard_limits']['internal_holdout_labels_allowed'] is False
    assert reg['hard_limits']['internal_fifth_fixed100_allowed'] is False
    assert hfile(args.external_odds_csv) == reg['external_source']['odds_csv_sha256']

    r39c = load_r39c(args.r39c_code)
    all_internal, _ = r39c.load_feature_rows(args.internal_sanitized_dir, parent_pre)
    holdout_start = datetime.fromisoformat(reg['internal_training_source']['holdout_start'])
    training = sorted(
        [x for x in all_internal.values() if x['dt'] < holdout_start],
        key=lambda x: (x['dt'], x['identity'])
    )
    if len(training) != reg['internal_training_source']['expected_training_rows']:
        raise RuntimeError(f'internal training count changed: {len(training)}')

    training_ids = {x['identity'] for x in training}
    labels, training_label_access, internal_holdout_access = r39c.read_training_labels(
        args.internal_original_dir, training_ids, holdout_start
    )
    if internal_holdout_access != 0 or len(labels) != len(training):
        raise RuntimeError('internal label boundary failed')

    indices = list(reg['reduced_feature_contract']['parent_indices_zero_based'])
    X = np.array([[float(x['features'][j]) for j in indices] for x in training], dtype=float)
    y = np.array([
        1.0 if labels[x['identity']][0] == labels[x['identity']][1] else 0.0
        for x in training
    ], dtype=float)
    Xs, mean, std = r39c.standardize(X)
    beta_cal, diag_cal = r39c.fit_logistic(
        Xs[:, [0]], y,
        l2=float(reg['model_contract']['l2_lambda']),
        max_iter=int(reg['model_contract']['maximum_iterations']),
        tol=float(reg['model_contract']['coefficient_delta_tolerance'])
    )
    beta_traj, diag_traj = r39c.fit_logistic(
        Xs, y,
        l2=float(reg['model_contract']['l2_lambda']),
        max_iter=int(reg['model_contract']['maximum_iterations']),
        tol=float(reg['model_contract']['coefficient_delta_tolerance'])
    )

    external, external_audit = load_external_features(args.external_odds_csv, reg, r39c)
    ext = reg['external_source']
    expected_n = int(ext['expected_eligible_matches'])
    n = len(external)
    fit_n = int(ext['r40g_counts']['fit'])
    policy_n = int(ext['r40g_counts']['policy'])
    blind_n = int(ext['r40g_counts']['blind'])
    ext_fit = external[:fit_n]
    ext_policy = external[fit_n:fit_n + policy_n]
    ext_blind = external[fit_n + policy_n:]

    identity_hashes = {
        'all': identity_digest(external),
        'fit': identity_digest(ext_fit),
        'policy': identity_digest(ext_policy),
        'blind': identity_digest(ext_blind),
    }
    expected_hashes = {
        'all': ext['r40g_identity_sha256'],
        'fit': ext['r40g_fit_sha256'],
        'policy': ext['r40g_policy_sha256'],
        'blind': ext['r40g_blind_sha256'],
    }
    feature_hashes = {
        'all': feature_digest(external),
        'fit': feature_digest(ext_fit),
        'policy': feature_digest(ext_policy),
        'blind': feature_digest(ext_blind),
    }

    model_payload = {
        'schema': 'r40h-cross-source-model-freeze',
        'feature_names': reg['reduced_feature_contract']['feature_names'],
        'parent_feature_indices_zero_based': indices,
        'internal_training_rows': len(training),
        'internal_training_draws': int(y.sum()),
        'internal_training_labels_accessed': training_label_access,
        'internal_holdout_labels_accessed': internal_holdout_access,
        'training_mean': mean.tolist(),
        'training_std': std.tolist(),
        'calibration_beta': beta_cal.tolist(),
        'trajectory_beta': beta_traj.tolist(),
        'calibration_diagnostics': diag_cal,
        'trajectory_diagnostics': diag_traj,
        'l2_lambda': float(reg['model_contract']['l2_lambda']),
        'external_eligible_rows': n,
        'external_identity_hashes': identity_hashes,
        'external_feature_hashes': feature_hashes,
        'external_result_values_accessed_before_freeze': 0,
        'numpy_version': np.__version__,
    }
    model_payload['model_parameter_sha256'] = canonical_json_sha(model_payload)
    (args.out_dir / 'model_freeze_receipt_r40h.json').write_text(
        json.dumps(model_payload, ensure_ascii=False, indent=2), encoding='utf-8'
    )

    gate = reg['freeze_gate']
    gates = {
        'internal_training_rows_exact': len(training) == gate['internal_training_rows_exact'],
        'internal_holdout_labels_accessed_zero': internal_holdout_access == gate['internal_holdout_labels_accessed'],
        'external_eligible_matches_exact': n == gate['external_eligible_matches_exact'],
        'external_split_counts_exact': len(ext_fit) == fit_n and len(ext_policy) == policy_n and len(ext_blind) == blind_n,
        'external_identity_hashes_exact': identity_hashes == expected_hashes,
        'external_conflicting_duplicate_timestamp_odds_zero': external_audit['conflicting_duplicate_timestamp_odds'] == gate['external_conflicting_duplicate_timestamp_odds'],
        'external_result_values_accessed_zero': gate['external_result_values_accessed'] == 0,
        'calibration_model_converged': bool(diag_cal['converged']) is gate['calibration_model_converged'],
        'trajectory_model_converged': bool(diag_traj['converged']) is gate['trajectory_model_converged'],
        'external_feature_snapshot_hash_created': all(len(x) == 64 for x in feature_hashes.values()) is gate['external_feature_snapshot_hash_created'],
    }
    passed = all(gates.values())
    status = 'PASS_R40H_CROSS_SOURCE_MODEL_AND_EXTERNAL_FEATURE_FREEZE' if passed else 'STOP_R40H_CROSS_SOURCE_FREEZE'
    result = {
        'schema_version': reg['schema_version'],
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'internal_training': {
            'rows': len(training),
            'draws': int(y.sum()),
            'labels_accessed': training_label_access,
            'holdout_period_labels_accessed': internal_holdout_access,
        },
        'external': {
            'eligible_matches': n,
            'split_counts': {'fit': len(ext_fit), 'policy': len(ext_policy), 'blind': len(ext_blind)},
            'identity_hashes': identity_hashes,
            'feature_hashes': feature_hashes,
            'audit': external_audit,
            'result_file_extracted': False,
            'result_values_accessed': 0,
        },
        'model_freeze': model_payload,
        'gates': gates,
        'next_stage_authorization': 'ONE_TIME_EXTERNAL_RESULT_EVALUATION_PREREGISTRATION_ALLOWED' if passed else 'STOP_NO_EXTERNAL_RESULTS',
        'hard_limits': reg['hard_limits'],
    }
    (args.out_dir / 'cross_source_transfer_status_r40h.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
