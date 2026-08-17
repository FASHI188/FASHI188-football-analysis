#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / 'research/anonymous_data_reserve_r1/fabulous_ou25_b02_market_direct_t_r1'
PRED = FREEZE_DIR / 'B02_frozen_predictions.csv'
LOCK = FREEZE_DIR / 'preregistration_and_prediction_lock.json'
LABEL_RECEIPT = Path('/tmp/fab/B02_labels_target_only.json')
OUTDIR = ROOT / 'research/anonymous_data_reserve_r1/fabulous_ou25_b02_market_direct_t_settlement_r1'
OUTDIR.mkdir(parents=True, exist_ok=True)

EXPECTED_B02_MANIFEST_SHA = '5f642afc51175ae693d45cb0393a78b3b2d5bd8bc8fb5b1a67567f21a3a906e2'
EXPECTED_PACKET_SHA = 'b5c8019a06fa0b75ba9b205a552beabc1e84e7877e1095d86c2e8125bfc2d6ed'
EXPECTED_PRED_SHA = '9b771c9466842e97fe8984945cc9b2c9f26d6531bab27f7f871305b6e2c8c008'
EXPECTED_LOCK_SHA = '0dd9d39157578b2f3c0d78b66df6bfff5896c6d1a729609bc2ab34cd0d5e45ab'
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


# Hard preregistration/prediction locks. These are checked before consuming target-only label receipt.
if sha256(PRED) != EXPECTED_PRED_SHA:
    raise SystemExit(f'frozen prediction SHA mismatch: {sha256(PRED)}')
if sha256(LOCK) != EXPECTED_LOCK_SHA:
    raise SystemExit(f'prereg lock SHA mismatch: {sha256(LOCK)}')
lock = json.loads(LOCK.read_text(encoding='utf-8'))
assert lock['status'] == 'PREDICTIONS_FROZEN_LABELS_UNOPENED'
assert lock['target']['batch_id'] == 'FAB-OU25-PIT-B02'
assert lock['target']['batch_manifest_sha256'] == EXPECTED_B02_MANIFEST_SHA
assert lock['target']['n'] == 400
assert lock['target']['target_labels_accessed'] == 0
assert lock['historical_training']['target_period_start_exclusive'] == '2023-07-01'
assert lock['historical_training']['post_target_outcome_values_dereferenced'] == 0
assert lock['model']['baseline_features'] == ['logit_pH','logit_pD','logit_pA']
assert lock['model']['candidate_features'] == ['logit_pH','logit_pD','logit_pA','ou25_logit_over']
assert lock['model']['selected_C'] == 0.1
assert lock['model']['candidate_increment_exact'] == 'logit(de-vigged P(Over2.5))'
assert lock['science_gate']['PASS'] == 'point delta < 0 AND calendar-date/bootstrap90 upper bound < 0'

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

# LABEL OPENING POINT: the workflow creates this receipt by server-side filtering for B02 fixture IDs only.
receipt = json.loads(LABEL_RECEIPT.read_text(encoding='utf-8'))
assert receipt['schema_version'] == 'FAB-OU25-B02-TARGET-ONLY-LABEL-RECEIPT-R1'
assert receipt['status'] == 'B02_TARGET_ONLY_LABELS_OPENED'
assert receipt['dataset'] == 'eatpizzanot/soccer-dataset'
assert receipt['config'] == 'fixtures'
assert receipt['split'] == 'train'
assert receipt['filter_endpoint'] == 'https://datasets-server.huggingface.co/filter'
assert receipt['requested_fixture_count'] == 400
assert receipt['returned_fixture_count'] == 400
assert receipt['non_target_rows_returned'] == 0
assert receipt['B03_B04_fixture_ids_requested'] == 0
assert receipt['identity_match_to_pinned_fixture_source'] is True
assert receipt['pinned_fixture_source_sha256'] == '7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7'
labels = pd.DataFrame(receipt['labels'])
if len(labels) != 400 or labels['fixture_id'].nunique() != 400:
    raise SystemExit('B02 target-only label receipt row mismatch')
if set(labels['fixture_id'].astype(int)) != fixture_ids:
    raise SystemExit('B02 target-only label identity set mismatch')
if labels[['goals_home','goals_away']].isna().any().any():
    raise SystemExit('missing B02 goal labels')
if not labels['is_played'].fillna(False).all():
    raise SystemExit('one or more B02 fixtures are not played')
for col in ['goals_home','goals_away']:
    if ((labels[col].astype(int) < 0) | (labels[col].astype(int) > 30)).any():
        raise SystemExit(f'invalid goal values in {col}')

joined = pred.merge(labels[['fixture_id','goals_home','goals_away']], on='fixture_id', how='left', validate='one_to_one')
if joined[['goals_home','goals_away']].isna().any().any():
    raise SystemExit('joined B02 labels missing')
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
status = 'PASS_B02_OU25_CONFIRMED_DIRECT_T_INCREMENT' if overall_pass else 'FAIL_B02_OU25_NO_CONFIRMED_DIRECT_T_INCREMENT'

result = {
    'schema_version': 'FAB-OU25-B02-MARKET-DIRECT-T-SETTLEMENT-R1',
    'status': status,
    'settlement_scope': {
        'batch_id': 'FAB-OU25-PIT-B02',
        'rows': 400,
        'B01_state_before_run': 'VIEWED_PREVIOUSLY',
        'B01_labels_read_in_this_run': 0,
        'B02_labels_opened_in_this_run': 400,
        'B03_B04_fixture_ids_requested': 0,
        'label_columns_materialized_for_non_B02_rows_in_this_run': 0,
        'label_fields_used': ['fixture_id','goals_home','goals_away','is_played'],
    },
    'lock_verification': {
        'batch_manifest_sha256': EXPECTED_B02_MANIFEST_SHA,
        'pre_match_packet_sha256': EXPECTED_PACKET_SHA,
        'frozen_prediction_sha256': EXPECTED_PRED_SHA,
        'prereg_lock_sha256': EXPECTED_LOCK_SHA,
        'all_verified_before_B02_label_query': True,
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
    'label_access_governance': {
        'method': 'Hugging Face Dataset Viewer /filter endpoint; OR predicates restricted to frozen B02 fixture IDs; max 40 requested IDs per query',
        'target_only_receipt_sha256': sha256(LABEL_RECEIPT),
        'B03_B04_fixture_ids_requested': 0,
        'non_target_rows_returned': 0,
    },
    'governance': {
        'formal_weight': 0,
        'B01_state_after_run': 'VIEWED',
        'B02_state_after_run': 'VIEWED',
        'B03_B04_state_after_run': 'UNOPENED_BY_TARGET_ONLY_QUERY',
        'post_result_parameter_search_allowed_on_B02': False,
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
