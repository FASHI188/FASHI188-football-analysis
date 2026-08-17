#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / 'research/anonymous_data_reserve_r1/fabulous_ou25_b01_market_direct_t_r1'
PRED = FREEZE_DIR / 'B01_frozen_predictions.csv'
LOCK = FREEZE_DIR / 'preregistration_and_prediction_lock.json'
OUTDIR = ROOT / 'research/anonymous_data_reserve_r1/fabulous_ou25_b01_market_direct_t_settlement_r1'
OUTDIR.mkdir(parents=True, exist_ok=True)
FIXTURES = Path('/tmp/fab/fixtures.parquet')

EXPECTED_B01_MANIFEST_SHA = 'fcba07147d230357925d3ee41027dfae18a27654960641c509ead5626b057baf'
EXPECTED_PRED_SHA = 'b8d65bef860f23999fbaedf5c15f7dd6258d408488daf28668b84543032f7039'
EXPECTED_LOCK_SHA = 'ce519fb417598c05cd32d498eb9fbbd067a11481c8e3265cf94e5a51c6a766a8'
EXPECTED_FIXTURES_SHA = '7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7'
BOOTSTRAP_SEED = 20260817
BOOTSTRAP_REPS = 20000
CLASSES = list(range(8))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_probabilities(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    if p.ndim != 2 or p.shape[1] != 8:
        raise RuntimeError(f'probability shape mismatch: {p.shape}')
    if not np.isfinite(p).all() or np.any(p <= 0):
        raise RuntimeError('probabilities must be finite and strictly positive')
    sums = p.sum(axis=1, keepdims=True)
    if np.max(np.abs(sums - 1.0)) > 1e-9:
        p = p / sums
    return p


def metric_components(y: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    y = np.asarray(y, dtype=int)
    p = normalize_probabilities(p)
    n = len(y)
    truth = np.zeros_like(p)
    truth[np.arange(n), y] = 1.0
    logloss = -np.log(np.clip(p[np.arange(n), y], 1e-15, 1.0))
    brier = ((p - truth) ** 2).sum(axis=1)
    rps = ((np.cumsum(p, axis=1)[:, :-1] - np.cumsum(truth, axis=1)[:, :-1]) ** 2).sum(axis=1) / 7.0
    order = np.argsort(-p, axis=1)
    top1 = (order[:, 0] == y).astype(float)
    top2 = np.asarray([y[i] in order[i, :2] for i in range(n)], dtype=float)
    return {'logloss': logloss, 'brier': brier, 'rps': rps, 'top1': top1, 'top2': top2}


def date_block_bootstrap(delta: np.ndarray, date_keys: np.ndarray) -> dict[str, float | int]:
    delta = np.asarray(delta, dtype=float)
    date_keys = np.asarray(date_keys, dtype=str)
    unique_dates = np.asarray(sorted(set(date_keys.tolist())), dtype=str)
    groups = {d: np.flatnonzero(date_keys == d) for d in unique_dates}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    estimates = np.empty(BOOTSTRAP_REPS, dtype=float)
    for b in range(BOOTSTRAP_REPS):
        sampled_dates = rng.choice(unique_dates, size=len(unique_dates), replace=True)
        idx = np.concatenate([groups[d] for d in sampled_dates])
        estimates[b] = float(delta[idx].mean())
    lo, hi = np.quantile(estimates, [0.05, 0.95], method='linear')
    return {
        'method': 'calendar-date cluster bootstrap; sample unique kickoff dates with replacement; retain every match in each sampled date; statistic=row-weighted mean paired delta',
        'seed': BOOTSTRAP_SEED,
        'reps': BOOTSTRAP_REPS,
        'unique_date_blocks': int(len(unique_dates)),
        'ci_level': 0.90,
        'lower': float(lo),
        'upper': float(hi),
    }


# Hard lock verification BEFORE reading any target labels.
if sha256(PRED) != EXPECTED_PRED_SHA:
    raise SystemExit(f'frozen prediction SHA mismatch: {sha256(PRED)}')
if sha256(LOCK) != EXPECTED_LOCK_SHA:
    raise SystemExit(f'prereg lock SHA mismatch: {sha256(LOCK)}')
if sha256(FIXTURES) != EXPECTED_FIXTURES_SHA:
    raise SystemExit(f'fixture source SHA mismatch: {sha256(FIXTURES)}')
lock = json.loads(LOCK.read_text(encoding='utf-8'))
assert lock['status'] == 'PREDICTIONS_FROZEN_LABELS_UNOPENED'
assert lock['target']['batch_manifest_sha256'] == EXPECTED_B01_MANIFEST_SHA
assert lock['target']['n'] == 400
assert lock['target']['target_labels_accessed'] == 0
assert lock['governance']['B01_labels_accessed'] == 0
assert lock['governance']['B02_B03_B04_labels_accessed'] == 0
assert lock['model']['selected_C'] == 0.1
assert lock['model']['candidate_increment_exact'] == 'logit(de-vigged P(Over2.5))'

pred = pd.read_csv(PRED)
if len(pred) != 400 or pred['fixture_id'].nunique() != 400:
    raise SystemExit('frozen prediction row/fixture count mismatch')
base_cols = [f'baseline_pT{j}' for j in CLASSES]
cand_cols = [f'candidate_pT{j}' for j in CLASSES]
missing = [c for c in ['fixture_id','kickoff_utc',*base_cols,*cand_cols] if c not in pred.columns]
if missing:
    raise SystemExit(f'missing prediction columns: {missing}')
pb = normalize_probabilities(pred[base_cols].to_numpy(float))
pc = normalize_probabilities(pred[cand_cols].to_numpy(float))
fixture_ids = set(pred['fixture_id'].astype(int).tolist())

# LABEL OPENING POINT. Read ONLY B01 identities and the minimum settlement columns.
table = pq.read_table(FIXTURES, columns=['id','date_utc','goals_home','goals_away','is_played'])
fx = table.to_pandas()
fx = fx[fx['id'].isin(fixture_ids)].copy()
if len(fx) != 400 or fx['id'].nunique() != 400:
    raise SystemExit(f'B01 label join mismatch rows={len(fx)} unique={fx["id"].nunique()}')
if not fx['is_played'].fillna(False).all():
    raise SystemExit('one or more B01 fixtures are not played')
if fx[['goals_home','goals_away']].isna().any().any():
    raise SystemExit('missing B01 goal labels')
for col in ['goals_home','goals_away']:
    if ((fx[col] < 0) | (fx[col] > 30)).any():
        raise SystemExit(f'invalid goal values in {col}')

labels = fx[['id','date_utc','goals_home','goals_away']].rename(columns={'id':'fixture_id'})
joined = pred.merge(labels, on='fixture_id', how='left', validate='one_to_one')
if joined[['goals_home','goals_away']].isna().any().any():
    raise SystemExit('joined B01 labels missing')
# Use frozen kickoff identity date for the preregistered calendar-date block.
joined['kickoff_date'] = pd.to_datetime(joined['kickoff_utc'], utc=True).dt.date.astype(str)
y = np.minimum(joined['goals_home'].to_numpy(int) + joined['goals_away'].to_numpy(int), 7)

mb = metric_components(y, pb)
mc = metric_components(y, pc)
delta = {name: mc[name] - mb[name] for name in mb}
point = {
    name: {
        'baseline': float(mb[name].mean()),
        'candidate': float(mc[name].mean()),
        'delta_candidate_minus_baseline': float(delta[name].mean()),
    }
    for name in mb
}
boot = date_block_bootstrap(delta['logloss'], joined['kickoff_date'].to_numpy(str))

# Secondary calibration receipts; no tuning or selection depends on these.
obs = np.bincount(y, minlength=8).astype(float) / len(y)
calibration = {
    'observed_total_bucket_rate': {str(j if j < 7 else '7+'): float(obs[j]) for j in CLASSES},
    'baseline_mean_probability': {str(j if j < 7 else '7+'): float(pb[:,j].mean()) for j in CLASSES},
    'candidate_mean_probability': {str(j if j < 7 else '7+'): float(pc[:,j].mean()) for j in CLASSES},
    'observed_over25_rate': float(np.mean((joined['goals_home'].to_numpy(int) + joined['goals_away'].to_numpy(int)) >= 3)),
}

primary_pass = point['logloss']['delta_candidate_minus_baseline'] < 0 and float(boot['upper']) < 0
support_pass = point['brier']['delta_candidate_minus_baseline'] <= 0 and point['rps']['delta_candidate_minus_baseline'] <= 0
overall_pass = bool(primary_pass and support_pass)
status = 'PASS_B01_OU25_CONFIRMED_DIRECT_T_INCREMENT' if overall_pass else 'FAIL_B01_OU25_NO_CONFIRMED_DIRECT_T_INCREMENT'

result = {
    'schema_version': 'FAB-OU25-B01-MARKET-DIRECT-T-SETTLEMENT-R1',
    'status': status,
    'settlement_scope': {
        'batch_id': 'FAB-OU25-PIT-B01',
        'rows': 400,
        'B01_labels_accessed': 400,
        'B02_B03_B04_labels_accessed': 0,
        'label_columns_read': ['id','date_utc','goals_home','goals_away','is_played'],
    },
    'lock_verification': {
        'batch_manifest_sha256': EXPECTED_B01_MANIFEST_SHA,
        'frozen_prediction_sha256': EXPECTED_PRED_SHA,
        'prereg_lock_sha256': EXPECTED_LOCK_SHA,
        'fixtures_sha256': EXPECTED_FIXTURES_SHA,
        'all_verified_before_label_read': True,
    },
    'point_metrics': point,
    'primary_logloss_date_block_bootstrap90': boot,
    'science_gate': {
        'primary_pass': bool(primary_pass),
        'supporting_brier_rps_pass': bool(support_pass),
        'overall_pass': overall_pass,
        'locked_rule': 'PASS iff LL point delta < 0 AND calendar-date block-bootstrap90 upper < 0 AND Brier point delta <= 0 AND RPS point delta <= 0',
    },
    'secondary_calibration': calibration,
    'governance': {
        'formal_weight': 0,
        'B01_state_after_run': 'VIEWED',
        'B02_B03_B04_state_after_run': 'UNOPENED',
        'post_result_parameter_search_allowed_on_B01': False,
        'selector_used': False,
        'forced_draw_used': False,
        'threshold_tuning_used': False,
        'CURRENT_mutation': False,
        'main_mutation': False,
    },
}
summary = OUTDIR / 'settlement_summary.json'
summary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
result['settlement_summary_sha256'] = sha256(summary)
print(json.dumps(result, ensure_ascii=False, indent=2))
