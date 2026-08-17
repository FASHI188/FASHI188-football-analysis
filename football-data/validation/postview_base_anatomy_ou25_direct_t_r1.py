#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
WORK = Path('/tmp/baseanatomy')
FIXTURES = WORK / 'fixtures.parquet'
OUTDIR = ROOT / 'research/anonymous_data_reserve_r1/base_anatomy_20260817_r1'
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
SEED = 20260817
RNG = np.random.default_rng(SEED)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mean_cluster_se(df: pd.DataFrame, value: str, cluster: str) -> float:
    x = df[value].to_numpy(float)
    n = len(x)
    groups = df.groupby(cluster, sort=False)[value]
    g = groups.ngroups
    if g < 2 or n <= 1:
        return float('nan')
    mu = float(np.mean(x))
    sums = groups.apply(lambda s: float((s - mu).sum())).to_numpy(float)
    return float(math.sqrt((g / (g - 1)) * np.sum(sums ** 2) / (n ** 2)))


def cluster_bootstrap_ci(df: pd.DataFrame, value: str, cluster: str, reps: int = 5000) -> tuple[float, float]:
    blocks = [g[value].to_numpy(float) for _, g in df.groupby(cluster, sort=False)]
    k = len(blocks)
    out = np.empty(reps, dtype=float)
    for r in range(reps):
        picks = RNG.integers(0, k, size=k)
        total = 0.0
        count = 0
        for j in picks:
            b = blocks[int(j)]
            total += float(b.sum())
            count += len(b)
        out[r] = total / count
    return tuple(float(v) for v in np.quantile(out, [0.05, 0.95]))


def iid_bootstrap_ci(x: np.ndarray, reps: int = 5000) -> tuple[float, float]:
    x = np.asarray(x, float)
    n = len(x)
    vals = np.empty(reps, dtype=float)
    for r in range(reps):
        vals[r] = float(x[RNG.integers(0, n, size=n)].mean())
    return tuple(float(v) for v in np.quantile(vals, [0.05, 0.95]))


def one_way_icc(df: pd.DataFrame, value: str, cluster: str) -> dict:
    x = df[value].to_numpy(float)
    n = len(x)
    grouped = list(df.groupby(cluster, sort=False))
    k = len(grouped)
    mu = float(x.mean())
    ssb = sum(len(g) * (float(g[value].mean()) - mu) ** 2 for _, g in grouped)
    ssw = sum(float(((g[value] - float(g[value].mean())) ** 2).sum()) for _, g in grouped)
    msb = ssb / max(k - 1, 1)
    msw = ssw / max(n - k, 1)
    sizes = np.asarray([len(g) for _, g in grouped], float)
    n0 = (n - float((sizes ** 2).sum()) / n) / max(k - 1, 1)
    denom = msb + (n0 - 1.0) * msw
    icc = (msb - msw) / denom if denom > 0 else float('nan')
    avg_m = n / k
    design_effect = 1.0 + (avg_m - 1.0) * max(0.0, icc if np.isfinite(icc) else 0.0)
    return {
        'clusters': k,
        'rows': n,
        'mean_cluster_size': avg_m,
        'median_cluster_size': float(np.median(sizes)),
        'max_cluster_size': int(sizes.max()),
        'icc_raw': float(icc),
        'design_effect_nonnegative_icc': float(design_effect),
        'effective_rows_approx': float(n / design_effect),
    }


def make_dummies(series: pd.Series, prefix: str) -> pd.DataFrame:
    return pd.get_dummies(series.astype(str), prefix=prefix, drop_first=True, dtype=float)


def cluster_robust_ols(df: pd.DataFrame, features: list[str], cluster: str, categorical: list[str]) -> dict:
    xparts = [pd.DataFrame({'intercept': np.ones(len(df), dtype=float)}, index=df.index)]
    for f in features:
        xparts.append(pd.DataFrame({f: pd.to_numeric(df[f], errors='raise').astype(float)}, index=df.index))
    for c in categorical:
        xparts.append(make_dummies(df[c], c))
    Xdf = pd.concat(xparts, axis=1)
    X = Xdf.to_numpy(float)
    y = df['ll_delta'].to_numpy(float)
    beta = np.linalg.pinv(X.T @ X) @ X.T @ y
    resid = y - X @ beta
    bread = np.linalg.pinv(X.T @ X)
    meat = np.zeros((X.shape[1], X.shape[1]), dtype=float)
    cluster_values = df[cluster].astype(str).to_numpy()
    uniq = np.unique(cluster_values)
    for key in uniq:
        idx = np.where(cluster_values == key)[0]
        xu = X[idx].T @ resid[idx]
        meat += np.outer(xu, xu)
    g = len(uniq)
    n, p = X.shape
    corr = (g / max(g - 1, 1)) * ((n - 1) / max(n - p, 1))
    cov = corr * bread @ meat @ bread
    se = np.sqrt(np.clip(np.diag(cov), 0, np.inf))
    return {
        'n': n,
        'date_clusters': g,
        'coefficients': {name: float(beta[i]) for i, name in enumerate(Xdf.columns)},
        'cluster_robust_se': {name: float(se[i]) for i, name in enumerate(Xdf.columns)},
    }


# Recover four immutable frozen prediction artifacts. These labels are already viewed.
frames = []
locks = {}
for batch, expected_n in EXPECTED_ROWS.items():
    d = WORK / batch
    preds = list(d.rglob(f'{batch}_frozen_predictions.csv'))
    lock_paths = list(d.rglob('preregistration_and_prediction_lock.json'))
    if len(preds) != 1 or len(lock_paths) != 1:
        raise SystemExit(f'{batch}: expected exactly one frozen prediction CSV and lock')
    pred_path, lock_path = preds[0], lock_paths[0]
    if sha256(pred_path) != EXPECTED_PRED_SHA[batch]:
        raise SystemExit(f'{batch}: prediction SHA mismatch')
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    assert lock['status'] == 'PREDICTIONS_FROZEN_LABELS_UNOPENED'
    assert int(lock['target']['target_labels_accessed']) == 0
    df = pd.read_csv(pred_path)
    if len(df) != expected_n or df['fixture_id'].nunique() != expected_n:
        raise SystemExit(f'{batch}: row identity mismatch')
    df['batch'] = batch
    frames.append(df)
    locks[batch] = {'rows': expected_n, 'prediction_sha256': sha256(pred_path), 'lock_sha256': sha256(lock_path)}

pred = pd.concat(frames, ignore_index=True)
assert len(pred) == 1559 and pred['fixture_id'].nunique() == 1559
if sha256(FIXTURES) != EXPECTED_FIXTURES_SHA:
    raise SystemExit('fixtures parquet SHA mismatch')
fixtures = pd.read_parquet(FIXTURES)
if 'fixture_id' not in fixtures.columns and 'id' in fixtures.columns:
    fixtures = fixtures.rename(columns={'id': 'fixture_id'})
required = ['fixture_id', 'goals_home', 'goals_away', 'is_played']
missing = [c for c in required if c not in fixtures.columns]
if missing:
    raise SystemExit(f'missing fixture columns: {missing}')
extra = [c for c in ['league_name','league_id','country_name','country','season'] if c in fixtures.columns]
labels = fixtures[fixtures['fixture_id'].isin(pred['fixture_id'])][required + extra].copy()
assert len(labels) == 1559 and labels['fixture_id'].nunique() == 1559
assert labels['is_played'].fillna(False).all()
df = pred.merge(labels, on='fixture_id', how='left', validate='one_to_one')

base_cols = [f'baseline_pT{i}' for i in CLASSES]
cand_cols = [f'candidate_pT{i}' for i in CLASSES]
pb = df[base_cols].to_numpy(float); pb /= pb.sum(axis=1, keepdims=True)
pc = df[cand_cols].to_numpy(float); pc /= pc.sum(axis=1, keepdims=True)
raw_total = df['goals_home'].astype(int).to_numpy() + df['goals_away'].astype(int).to_numpy()
y = np.minimum(raw_total, 7)
df['ll_baseline'] = -np.log(np.clip(pb[np.arange(len(df)), y], 1e-15, 1.0))
df['ll_candidate'] = -np.log(np.clip(pc[np.arange(len(df)), y], 1e-15, 1.0))
df['ll_delta'] = df['ll_candidate'] - df['ll_baseline']
df['baseline_over25'] = pb[:, 3:].sum(axis=1)
df['candidate_over25'] = pc[:, 3:].sum(axis=1)
df['over25_shift'] = df['candidate_over25'] - df['baseline_over25']
df['actual_over25'] = (raw_total >= 3).astype(int)

for c in ['kickoff_utc','ou_timestamp_utc','hda_timestamp_utc']:
    df[c] = pd.to_datetime(df[c], utc=True, errors='raise')
df['kickoff_date'] = df['kickoff_utc'].dt.strftime('%Y-%m-%d')
df['ou_quote_age_hours'] = (df['kickoff_utc'] - df['ou_timestamp_utc']).dt.total_seconds() / 3600.0
df['hda_quote_age_hours'] = (df['kickoff_utc'] - df['hda_timestamp_utc']).dt.total_seconds() / 3600.0
df['sync_gap_hours'] = (df['ou_timestamp_utc'] - df['hda_timestamp_utc']).abs().dt.total_seconds() / 3600.0
if (df[['ou_quote_age_hours','hda_quote_age_hours']] < 0).any().any():
    raise SystemExit('post-kickoff quote detected')

if 'league_name' in df.columns:
    df['league_key'] = df['league_name'].fillna('NA').astype(str)
elif 'league_id' in df.columns:
    df['league_key'] = 'league_id=' + df['league_id'].fillna(-1).astype(str)
elif 'country_name' in df.columns:
    df['league_key'] = df['country_name'].fillna('NA').astype(str)
elif 'country' in df.columns:
    df['league_key'] = df['country'].fillna('NA').astype(str)
else:
    df['league_key'] = 'ALL'

# 1) Date-cluster anatomy.
global_mean = float(df['ll_delta'].mean())
iid_se = float(df['ll_delta'].std(ddof=1) / math.sqrt(len(df)))
cluster_se = mean_cluster_se(df, 'll_delta', 'kickoff_date')
iid_ci = iid_bootstrap_ci(df['ll_delta'].to_numpy(float))
cluster_ci = cluster_bootstrap_ci(df, 'll_delta', 'kickoff_date')
icc = one_way_icc(df, 'll_delta', 'kickoff_date')

date_rows = []
for date, g in df.groupby('kickoff_date', sort=True):
    mask = df['kickoff_date'] != date
    date_rows.append({
        'kickoff_date': date,
        'n': int(len(g)),
        'mean_ll_delta': float(g['ll_delta'].mean()),
        'sum_ll_delta': float(g['ll_delta'].sum()),
        'global_weight': float(len(g) / len(df)),
        'leave_one_date_out_mean_ll_delta': float(df.loc[mask, 'll_delta'].mean()),
        'leave_one_date_out_shift_vs_global': float(df.loc[mask, 'll_delta'].mean() - global_mean),
    })
per_date = pd.DataFrame(date_rows)
per_date.to_csv(OUTDIR / 'per_date_anatomy.csv', index=False)

# Retrospective sensitivity/power approximation under observed date-cluster variance.
z_alpha = float(norm.ppf(0.95))  # same-sided threshold implied by an upper 90% CI below zero
power = {}
for target in [0.80, 0.90]:
    z_beta = float(norm.ppf(target))
    mde = (z_alpha + z_beta) * cluster_se
    current_g = int(df['kickoff_date'].nunique())
    if abs(global_mean) > 0 and np.isfinite(cluster_se):
        req_g = int(math.ceil(current_g * ((z_alpha + z_beta) * cluster_se / abs(global_mean)) ** 2))
    else:
        req_g = None
    power[str(target)] = {
        'retrospective_mde_abs_ll_delta_at_current_date_count': float(mde),
        'approx_required_date_clusters_if_true_effect_equals_observed': req_g,
        'approx_required_rows_at_current_mean_rows_per_date': None if req_g is None else int(math.ceil(req_g * len(df) / current_g)),
    }

# 2) Timing/synchronization anatomy.
df['sync_bucket'] = pd.cut(
    df['sync_gap_hours'],
    bins=[-np.inf, 1/60, 0.5, 2.0, 6.0, np.inf],
    labels=['<=1min','1-30min','30min-2h','2-6h','>6h'],
    include_lowest=True,
)
sync_rows = []
for key, g in df.groupby('sync_bucket', observed=True, sort=False):
    sync_rows.append({
        'sync_bucket': str(key), 'n': int(len(g)), 'dates': int(g['kickoff_date'].nunique()),
        'mean_ll_delta': float(g['ll_delta'].mean()), 'median_ll_delta': float(g['ll_delta'].median()),
        'improved_rate': float((g['ll_delta'] < 0).mean()),
        'mean_ou_age_h': float(g['ou_quote_age_hours'].mean()),
        'mean_hda_age_h': float(g['hda_quote_age_hours'].mean()),
    })
sync_table = pd.DataFrame(sync_rows)
sync_table.to_csv(OUTDIR / 'sync_timing_anatomy.csv', index=False)

df['log1p_sync_gap'] = np.log1p(df['sync_gap_hours'])
df['log1p_ou_age'] = np.log1p(df['ou_quote_age_hours'])
df['log1p_hda_age'] = np.log1p(df['hda_quote_age_hours'])
# Fold tiny leagues into OTHER to keep the design stable.
counts = df['league_key'].value_counts()
df['league_fe'] = df['league_key'].where(df['league_key'].map(counts) >= 20, 'OTHER')
timing_ols = cluster_robust_ols(
    df,
    features=['log1p_sync_gap','log1p_ou_age','log1p_hda_age','baseline_over25'],
    cluster='kickoff_date',
    categorical=['batch','league_fe'],
)

# 3) League heterogeneity and leave-one-league-out stability.
league_rows = []
for league, g in df.groupby('league_key', sort=False):
    if len(g) < 20:
        continue
    se_c = mean_cluster_se(g, 'll_delta', 'kickoff_date')
    league_rows.append({
        'league': str(league), 'n': int(len(g)), 'dates': int(g['kickoff_date'].nunique()),
        'mean_ll_delta': float(g['ll_delta'].mean()),
        'cluster_se': float(se_c),
        'normal90_lo': float(g['ll_delta'].mean() - z_alpha * se_c) if np.isfinite(se_c) else np.nan,
        'normal90_hi': float(g['ll_delta'].mean() + z_alpha * se_c) if np.isfinite(se_c) else np.nan,
        'improved_rate': float((g['ll_delta'] < 0).mean()),
        'actual_over25_rate': float(g['actual_over25'].mean()),
        'baseline_over25_mean': float(g['baseline_over25'].mean()),
    })
league_table = pd.DataFrame(league_rows).sort_values('n', ascending=False)
league_table.to_csv(OUTDIR / 'league_anatomy.csv', index=False)

loo_rows = []
for league in league_table['league'].tolist():
    g = df[df['league_key'] != league]
    loo_rows.append({'removed_league': league, 'remaining_n': int(len(g)), 'mean_ll_delta': float(g['ll_delta'].mean())})
loo = pd.DataFrame(loo_rows)
loo.to_csv(OUTDIR / 'leave_one_league_out.csv', index=False)

meta = {'eligible_leagues': int(len(league_table))}
valid = league_table[np.isfinite(league_table['cluster_se']) & (league_table['cluster_se'] > 0)].copy()
if len(valid) >= 2:
    theta = valid['mean_ll_delta'].to_numpy(float)
    var = valid['cluster_se'].to_numpy(float) ** 2
    w = 1.0 / var
    fixed = float(np.sum(w * theta) / np.sum(w))
    Q = float(np.sum(w * (theta - fixed) ** 2))
    dfr = len(theta) - 1
    I2 = max(0.0, (Q - dfr) / Q) if Q > 0 else 0.0
    C = float(np.sum(w) - np.sum(w ** 2) / np.sum(w))
    tau2 = max(0.0, (Q - dfr) / C) if C > 0 else 0.0
    meta.update({'fixed_effect_ll_delta': fixed, 'Q': Q, 'df': dfr, 'I2': I2, 'tau2_DL': tau2})

# 4) Batch stability and baseline calibration anatomy.
batch_table = df.groupby('batch', sort=True).agg(
    n=('ll_delta','size'), dates=('kickoff_date','nunique'), mean_ll_delta=('ll_delta','mean'),
    actual_over25=('actual_over25','mean'), baseline_over25=('baseline_over25','mean'), candidate_over25=('candidate_over25','mean'),
).reset_index()
batch_table.to_csv(OUTDIR / 'batch_anatomy.csv', index=False)

df['baseline_over25_bin'] = pd.cut(df['baseline_over25'], [-np.inf,.4,.5,.6,.7,np.inf], labels=['<40%','40-50%','50-60%','60-70%','>=70%'])
cal_rows = []
for key, g in df.groupby('baseline_over25_bin', observed=True, sort=False):
    cal_rows.append({
        'bin': str(key), 'n': int(len(g)), 'actual_over25': float(g['actual_over25'].mean()),
        'baseline_over25': float(g['baseline_over25'].mean()), 'candidate_over25': float(g['candidate_over25'].mean()),
        'baseline_bias_pred_minus_actual': float(g['baseline_over25'].mean() - g['actual_over25'].mean()),
        'candidate_bias_pred_minus_actual': float(g['candidate_over25'].mean() - g['actual_over25'].mean()),
        'mean_ll_delta': float(g['ll_delta'].mean()),
    })
pd.DataFrame(cal_rows).to_csv(OUTDIR / 'baseline_calibration_anatomy.csv', index=False)

summary = {
    'schema_version': 'POSTVIEW-BASE-ANATOMY-OU25-DIRECT-T-R1',
    'status': 'POSTVIEW_BASE_ANATOMY_COMPLETE_NO_PROMOTION',
    'scope': {
        'rows': 1559, 'batches': ['B01','B02','B03','B04'],
        'all_target_labels_already_viewed_before_this_experiment': True,
        'confirmatory_claim': False, 'formal_weight': 0,
        'parameter_tuning': False, 'threshold_search': False, 'selector_created': False,
        'main_mutation': False, 'CURRENT_mutation': False,
    },
    'input_locks': locks,
    'global': {
        'mean_ll_delta': global_mean,
        'iid_se': iid_se,
        'date_cluster_robust_se': cluster_se,
        'se_inflation_cluster_vs_iid': float(cluster_se / iid_se),
        'iid_bootstrap90': list(iid_ci),
        'date_block_bootstrap90': list(cluster_ci),
        'date_cluster_anatomy': icc,
        'date_mean_ll_delta_std': float(per_date['mean_ll_delta'].std(ddof=1)),
        'date_mean_ll_delta_min': float(per_date['mean_ll_delta'].min()),
        'date_mean_ll_delta_max': float(per_date['mean_ll_delta'].max()),
        'max_abs_leave_one_date_out_shift': float(per_date['leave_one_date_out_shift_vs_global'].abs().max()),
    },
    'retrospective_sensitivity_not_confirmatory_power': power,
    'timing': {
        'bucket_rows': sync_rows,
        'adjusted_cluster_robust_ols': timing_ols,
        'sync_gap_pearson_with_ll_delta': float(df['sync_gap_hours'].corr(df['ll_delta'], method='pearson')),
        'sync_gap_spearman_with_ll_delta': float(df['sync_gap_hours'].corr(df['ll_delta'], method='spearman')),
    },
    'league_heterogeneity': {
        **meta,
        'league_mean_min': float(league_table['mean_ll_delta'].min()) if len(league_table) else None,
        'league_mean_max': float(league_table['mean_ll_delta'].max()) if len(league_table) else None,
        'leagues_with_negative_mean_delta': int((league_table['mean_ll_delta'] < 0).sum()) if len(league_table) else 0,
        'loo_global_mean_min': float(loo['mean_ll_delta'].min()) if len(loo) else None,
        'loo_global_mean_max': float(loo['mean_ll_delta'].max()) if len(loo) else None,
    },
    'governance_interpretation': [
        'All outputs are post-view descriptive diagnostics on already-viewed B01-B04 labels.',
        'Bootstrap, power and heterogeneity quantities must not be re-labelled as preregistered confirmation.',
        'No segment from this output may be converted into a selector or threshold and then re-tested on B01-B04.',
        'Any future confirmation requires a fresh unopened reserve frozen before label access.',
    ],
}
(OUTDIR / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

df[['fixture_id','batch','kickoff_date','league_key','ll_delta','baseline_over25','candidate_over25','actual_over25','sync_gap_hours','ou_quote_age_hours','hda_quote_age_hours']].to_csv(OUTDIR / 'row_anatomy_viewed_only.csv', index=False)
print(json.dumps(summary, ensure_ascii=False, indent=2))
