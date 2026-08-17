#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WORK = Path('/tmp/fabdiag')
FIXTURES = WORK / 'fixtures.parquet'
OUTDIR = ROOT / 'research/anonymous_data_reserve_r1/fabulous_ou25_direct_t_postview_diagnostics_r1'
OUTDIR.mkdir(parents=True, exist_ok=True)

EXPECTED_FIXTURES_SHA = '7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7'
EXPECTED_ROWS = {'B01': 400, 'B02': 400, 'B03': 400, 'B04': 359}
EXPECTED_PRED_SHA = {
    'B01': 'b8d65bef860f23999fbaedf5c15f7dd6258d408488daf28668b84543032f7039',
    'B02': '9b771c9466842e97fe8984945cc9b2c9f26d6531bab27f7f871305b6e2c8c008',
    'B03': 'f6dd9831ff0d70f6cd56e84a0d643f56422e67bb963fb1bb51b1d06308c50655',
    'B04': '441fe70c4cf40fd55cfc4cf6ff8baff69910f2171b2cce4c730704fc2611bf00',
}
CLASSES = list(range(8))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metric_components(y: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    p = p / p.sum(axis=1, keepdims=True)
    truth = np.zeros_like(p)
    truth[np.arange(len(y)), y] = 1.0
    logloss = -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0))
    brier = ((p - truth) ** 2).sum(axis=1)
    rps = ((np.cumsum(p, axis=1)[:, :-1] - np.cumsum(truth, axis=1)[:, :-1]) ** 2).sum(axis=1) / 7.0
    order = np.argsort(-p, axis=1)
    top1 = (order[:, 0] == y).astype(float)
    top2 = np.asarray([y[i] in order[i, :2] for i in range(len(y))], dtype=float)
    entropy = -(p * np.log(np.clip(p, 1e-15, 1.0))).sum(axis=1)
    return {'logloss': logloss, 'brier': brier, 'rps': rps, 'top1': top1, 'top2': top2, 'entropy': entropy}


def fixed_bucket(series: pd.Series, edges: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(series, bins=edges, labels=labels, include_lowest=True, right=True, ordered=True)


def segment_table(df: pd.DataFrame, col: str) -> pd.DataFrame:
    rows = []
    for key, g in df.groupby(col, observed=True, sort=False):
        if len(g) == 0:
            continue
        rows.append({
            'segment_variable': col,
            'segment': str(key),
            'n': int(len(g)),
            'logloss_delta_mean': float(g['ll_delta'].mean()),
            'logloss_delta_median': float(g['ll_delta'].median()),
            'row_logloss_improved_rate': float((g['ll_delta'] < 0).mean()),
            'brier_delta_mean': float(g['brier_delta'].mean()),
            'rps_delta_mean': float(g['rps_delta'].mean()),
            'top1_delta_mean': float(g['top1_delta'].mean()),
            'top2_delta_mean': float(g['top2_delta'].mean()),
            'baseline_over25_mean': float(g['baseline_over25'].mean()),
            'candidate_over25_mean': float(g['candidate_over25'].mean()),
            'actual_over25_rate': float(g['actual_over25'].mean()),
            'over25_shift_mean': float(g['over25_shift'].mean()),
            'abs_over25_shift_mean': float(g['abs_over25_shift'].mean()),
            'sync_gap_hours_mean': float(g['sync_gap_hours'].mean()),
            'ou_quote_age_hours_mean': float(g['ou_quote_age_hours'].mean()),
            'hda_quote_age_hours_mean': float(g['hda_quote_age_hours'].mean()),
        })
    return pd.DataFrame(rows)


# ---- Recover the exact four frozen prediction artifacts. All four target batches are already VIEWED.
frames = []
lock_verification = {}
for batch, expected_n in EXPECTED_ROWS.items():
    d = WORK / batch
    preds = list(d.rglob(f'{batch}_frozen_predictions.csv'))
    locks = list(d.rglob('preregistration_and_prediction_lock.json'))
    if len(preds) != 1 or len(locks) != 1:
        raise SystemExit(f'{batch}: expected exactly one prediction CSV and one lock; got {preds=} {locks=}')
    pred_path, lock_path = preds[0], locks[0]
    pred_sha = sha256(pred_path)
    if pred_sha != EXPECTED_PRED_SHA[batch]:
        raise SystemExit(f'{batch}: prediction SHA mismatch {pred_sha}')
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    assert lock['status'] == 'PREDICTIONS_FROZEN_LABELS_UNOPENED'
    assert lock['target']['batch_id'] == f'FAB-OU25-PIT-{batch}'
    assert int(lock['target']['n']) == expected_n
    assert int(lock['target']['target_labels_accessed']) == 0
    assert lock['model']['selected_C'] == 0.1
    assert lock['model']['baseline_features'] == ['logit_pH','logit_pD','logit_pA']
    assert lock['model']['candidate_features'] == ['logit_pH','logit_pD','logit_pA','ou25_logit_over']
    assert lock['model']['candidate_increment_exact'] == 'logit(de-vigged P(Over2.5))'
    df = pd.read_csv(pred_path)
    if len(df) != expected_n or df['fixture_id'].nunique() != expected_n:
        raise SystemExit(f'{batch}: row/identity count mismatch')
    df['batch'] = batch
    frames.append(df)
    lock_verification[batch] = {
        'rows': expected_n,
        'prediction_sha256': pred_sha,
        'lock_sha256': sha256(lock_path),
        'batch_manifest_sha256': lock['target']['batch_manifest_sha256'],
    }

pred = pd.concat(frames, ignore_index=True)
if len(pred) != 1559 or pred['fixture_id'].nunique() != 1559:
    raise SystemExit('combined frozen prediction set is not 1559 unique fixtures')

if sha256(FIXTURES) != EXPECTED_FIXTURES_SHA:
    raise SystemExit(f'fixtures parquet SHA mismatch: {sha256(FIXTURES)}')
fixtures = pd.read_parquet(FIXTURES)
required_fixture_cols = ['fixture_id', 'goals_home', 'goals_away', 'is_played']
missing = [c for c in required_fixture_cols if c not in fixtures.columns]
if missing:
    raise SystemExit(f'fixtures parquet missing required columns: {missing}')
labels = fixtures[fixtures['fixture_id'].isin(pred['fixture_id'])].copy()
if len(labels) != 1559 or labels['fixture_id'].nunique() != 1559:
    raise SystemExit(f'expected 1559 target labels in pinned fixtures parquet, got {len(labels)}')
if labels[['goals_home','goals_away']].isna().any().any() or not labels['is_played'].fillna(False).all():
    raise SystemExit('one or more viewed target fixtures lack valid played labels')

extra_label_cols = [c for c in ['league_name','league_id','country','country_name','season','home_team','away_team','home_name','away_name'] if c in labels.columns]
joined = pred.merge(labels[required_fixture_cols + extra_label_cols], on='fixture_id', how='left', validate='one_to_one')

base_cols = [f'baseline_pT{j}' for j in CLASSES]
cand_cols = [f'candidate_pT{j}' for j in CLASSES]
pb = joined[base_cols].to_numpy(float)
pc = joined[cand_cols].to_numpy(float)
pb = pb / pb.sum(axis=1, keepdims=True)
pc = pc / pc.sum(axis=1, keepdims=True)
raw_total = joined['goals_home'].astype(int).to_numpy() + joined['goals_away'].astype(int).to_numpy()
y = np.minimum(raw_total, 7)
mb = metric_components(y, pb)
mc = metric_components(y, pc)

joined['actual_total_raw'] = raw_total
joined['actual_total_bucket'] = [str(v) if v < 7 else '7+' for v in y]
joined['actual_over25'] = (raw_total >= 3).astype(int)
joined['ll_delta'] = mc['logloss'] - mb['logloss']
joined['brier_delta'] = mc['brier'] - mb['brier']
joined['rps_delta'] = mc['rps'] - mb['rps']
joined['top1_delta'] = mc['top1'] - mb['top1']
joined['top2_delta'] = mc['top2'] - mb['top2']
joined['baseline_entropy'] = mb['entropy']
joined['candidate_entropy'] = mc['entropy']
joined['baseline_over25'] = pb[:, 3:].sum(axis=1)
joined['candidate_over25'] = pc[:, 3:].sum(axis=1)
joined['over25_shift'] = joined['candidate_over25'] - joined['baseline_over25']
joined['abs_over25_shift'] = joined['over25_shift'].abs()
weights = np.arange(8, dtype=float)  # 7+ represented by lower-bound 7; descriptive only.
joined['baseline_trunc_expected_T'] = pb @ weights
joined['candidate_trunc_expected_T'] = pc @ weights
joined['trunc_expected_T_shift'] = joined['candidate_trunc_expected_T'] - joined['baseline_trunc_expected_T']
joined['baseline_top1_bucket'] = np.argmax(pb, axis=1)
joined['candidate_top1_bucket'] = np.argmax(pc, axis=1)
joined['top1_flipped'] = joined['baseline_top1_bucket'] != joined['candidate_top1_bucket']
joined['baseline_top1_correct'] = (joined['baseline_top1_bucket'].to_numpy() == y)
joined['candidate_top1_correct'] = (joined['candidate_top1_bucket'].to_numpy() == y)

for c in ['kickoff_utc','ou_timestamp_utc','hda_timestamp_utc']:
    joined[c] = pd.to_datetime(joined[c], utc=True, errors='raise')
joined['ou_quote_age_hours'] = (joined['kickoff_utc'] - joined['ou_timestamp_utc']).dt.total_seconds() / 3600.0
joined['hda_quote_age_hours'] = (joined['kickoff_utc'] - joined['hda_timestamp_utc']).dt.total_seconds() / 3600.0
joined['sync_gap_hours'] = (joined['ou_timestamp_utc'] - joined['hda_timestamp_utc']).abs().dt.total_seconds() / 3600.0
if (joined[['ou_quote_age_hours','hda_quote_age_hours']] < 0).any().any():
    raise SystemExit('post-kickoff market timestamp detected')

# Fixed descriptive buckets. These are post-view diagnostics, not selectors and not confirmatory tests.
joined['sync_gap_bucket'] = fixed_bucket(
    joined['sync_gap_hours'],
    [-np.inf, 1/60, 0.5, 2.0, 6.0, np.inf],
    ['<=1min','1-30min','30min-2h','2-6h','>6h'],
)
joined['ou_age_bucket'] = fixed_bucket(
    joined['ou_quote_age_hours'],
    [-np.inf, 2.0, 6.0, 12.0, 24.0, np.inf],
    ['<=2h','2-6h','6-12h','12-24h','>24h'],
)
joined['hda_age_bucket'] = fixed_bucket(
    joined['hda_quote_age_hours'],
    [-np.inf, 2.0, 6.0, 12.0, 24.0, np.inf],
    ['<=2h','2-6h','6-12h','12-24h','>24h'],
)
joined['baseline_over25_bucket'] = fixed_bucket(
    joined['baseline_over25'],
    [-np.inf, 0.40, 0.50, 0.60, 0.70, np.inf],
    ['<40%','40-50%','50-60%','60-70%','>=70%'],
)
joined['abs_correction_bucket'] = fixed_bucket(
    joined['abs_over25_shift'],
    [-np.inf, 0.005, 0.01, 0.02, 0.04, np.inf],
    ['<=0.5pp','0.5-1pp','1-2pp','2-4pp','>4pp'],
)
joined['correction_direction'] = np.where(joined['over25_shift'] > 1e-12, 'raise_over25', np.where(joined['over25_shift'] < -1e-12, 'lower_over25', 'no_change'))
joined['entropy_bucket'] = pd.qcut(joined['baseline_entropy'], 5, labels=['Q1_low','Q2','Q3','Q4','Q5_high'], duplicates='drop')
joined['top1_flip_taxonomy'] = np.select(
    [
        (~joined['top1_flipped']) & joined['baseline_top1_correct'],
        (~joined['top1_flipped']) & (~joined['baseline_top1_correct']),
        joined['top1_flipped'] & joined['baseline_top1_correct'] & (~joined['candidate_top1_correct']),
        joined['top1_flipped'] & (~joined['baseline_top1_correct']) & joined['candidate_top1_correct'],
        joined['top1_flipped'] & (~joined['baseline_top1_correct']) & (~joined['candidate_top1_correct']),
    ],
    ['same_top1_correct','same_top1_wrong','flip_lost_correct','flip_gained_correct','flip_both_wrong'],
    default='other',
)

segment_vars = [
    'batch','sync_gap_bucket','ou_age_bucket','hda_age_bucket','baseline_over25_bucket',
    'abs_correction_bucket','correction_direction','entropy_bucket','actual_total_bucket',
    'baseline_top1_bucket','top1_flip_taxonomy',
]
segments = pd.concat([segment_table(joined, c) for c in segment_vars], ignore_index=True)
segments.to_csv(OUTDIR / 'segment_diagnostics.csv', index=False)

# Correlations are descriptive diagnostics only; no p-values or selection claims.
correlations = {}
for x in ['sync_gap_hours','ou_quote_age_hours','hda_quote_age_hours','abs_over25_shift','over25_shift','baseline_over25','baseline_entropy']:
    correlations[x] = {
        'pearson_with_ll_delta': float(joined[x].corr(joined['ll_delta'], method='pearson')),
        'spearman_with_ll_delta': float(joined[x].corr(joined['ll_delta'], method='spearman')),
    }

# Calibration by fixed baseline-over25 bins.
calibration = []
for key, g in joined.groupby('baseline_over25_bucket', observed=True, sort=False):
    calibration.append({
        'bin': str(key),
        'n': int(len(g)),
        'actual_over25_rate': float(g['actual_over25'].mean()),
        'baseline_over25_mean': float(g['baseline_over25'].mean()),
        'candidate_over25_mean': float(g['candidate_over25'].mean()),
        'baseline_bias_pred_minus_actual': float(g['baseline_over25'].mean() - g['actual_over25'].mean()),
        'candidate_bias_pred_minus_actual': float(g['candidate_over25'].mean() - g['actual_over25'].mean()),
        'll_delta_mean': float(g['ll_delta'].mean()),
    })

# Exact global and per-batch descriptive metrics.
def aggregate(g: pd.DataFrame) -> dict:
    return {
        'n': int(len(g)),
        'logloss_delta': float(g['ll_delta'].mean()),
        'brier_delta': float(g['brier_delta'].mean()),
        'rps_delta': float(g['rps_delta'].mean()),
        'top1_delta': float(g['top1_delta'].mean()),
        'top2_delta': float(g['top2_delta'].mean()),
        'row_logloss_improved_rate': float((g['ll_delta'] < 0).mean()),
        'baseline_over25_mean': float(g['baseline_over25'].mean()),
        'candidate_over25_mean': float(g['candidate_over25'].mean()),
        'actual_over25_rate': float(g['actual_over25'].mean()),
        'mean_over25_shift': float(g['over25_shift'].mean()),
        'mean_abs_over25_shift': float(g['abs_over25_shift'].mean()),
        'mean_sync_gap_hours': float(g['sync_gap_hours'].mean()),
        'median_sync_gap_hours': float(g['sync_gap_hours'].median()),
        'mean_ou_quote_age_hours': float(g['ou_quote_age_hours'].mean()),
        'median_ou_quote_age_hours': float(g['ou_quote_age_hours'].median()),
        'mean_hda_quote_age_hours': float(g['hda_quote_age_hours'].mean()),
        'median_hda_quote_age_hours': float(g['hda_quote_age_hours'].median()),
        'top1_flip_rate': float(g['top1_flipped'].mean()),
    }

summary = {
    'schema_version': 'FAB-OU25-DIRECT-T-POSTVIEW-DIAGNOSTICS-R1',
    'status': 'POSTVIEW_EXPLORATORY_DIAGNOSTICS_COMPLETE',
    'scope': {
        'rows': 1559,
        'batches': ['B01','B02','B03','B04'],
        'all_target_labels_already_viewed_before_this_analysis': True,
        'confirmatory_claim': False,
        'formal_weight': 0,
        'selector_created': False,
        'parameter_tuning': False,
        'threshold_search': False,
        'CURRENT_mutation': False,
        'main_mutation': False,
    },
    'data_integrity': {
        'fixtures_sha256': sha256(FIXTURES),
        'prediction_locks': lock_verification,
        'combined_unique_fixture_ids': int(joined['fixture_id'].nunique()),
    },
    'global': aggregate(joined),
    'per_batch': {b: aggregate(g) for b, g in joined.groupby('batch', sort=True)},
    'descriptive_correlations': correlations,
    'over25_calibration_by_fixed_baseline_bin': calibration,
    'top1_flip_taxonomy_counts': {str(k): int(v) for k, v in joined['top1_flip_taxonomy'].value_counts().to_dict().items()},
    'notes': [
        'All 1559 target outcomes were already VIEWED before this diagnostic; this is not a new blind or confirmatory test.',
        'No segment is authorized as a selector. Segment tables diagnose where the already-frozen OU2.5 increment helped or hurt.',
        'Timestamp diagnostics test whether market synchronization/staleness may explain heterogeneity without changing the frozen model.',
        'The 7+ bucket is represented by 7 only for the descriptive truncated expected-T calculation.',
    ],
}
(OUTDIR / 'diagnostic_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

# Keep row diagnostics as an artifact for audit, but do not treat it as reusable confirmatory data.
row_cols = [
    'batch','fixture_id','kickoff_utc','ou_timestamp_utc','hda_timestamp_utc','actual_total_raw','actual_total_bucket',
    'll_delta','brier_delta','rps_delta','top1_delta','top2_delta','baseline_over25','candidate_over25','over25_shift',
    'abs_over25_shift','baseline_entropy','baseline_trunc_expected_T','candidate_trunc_expected_T','trunc_expected_T_shift',
    'ou_quote_age_hours','hda_quote_age_hours','sync_gap_hours','sync_gap_bucket','ou_age_bucket','hda_age_bucket',
    'baseline_over25_bucket','abs_correction_bucket','correction_direction','entropy_bucket','baseline_top1_bucket',
    'candidate_top1_bucket','top1_flipped','top1_flip_taxonomy',
]
joined[row_cols].to_csv(OUTDIR / 'row_diagnostics_viewed_only.csv', index=False)
print(json.dumps(summary, ensure_ascii=False, indent=2))
