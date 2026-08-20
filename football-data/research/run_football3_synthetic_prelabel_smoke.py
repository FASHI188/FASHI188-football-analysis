from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from football3_core import (
    SealedPool,
    assert_disjoint_identity_sets,
    assert_exact_one_to_one_join,
    assert_feature_pit,
    assert_master_prediction_cutoff,
    assert_same_prediction_cutoff,
    assert_sealed_boundaries,
    assert_temporal_oos,
    devig_two_way,
    evaluate_frozen_experiment,
    ordered_identity_sha256,
    paired_bootstrap_delta_logloss,
    score_bundle,
    validate_nested_ou_tails,
)


def make_prob(q3: np.ndarray, shape_shift: np.ndarray | None = None) -> np.ndarray:
    q3 = np.asarray(q3, float)
    n = len(q3)
    p = np.zeros((n, 8), float)
    p[:, 0] = 0.10
    p[:, 1] = 0.20
    p[:, 2] = 1.0 - q3 - 0.30
    tail = np.array([0.38, 0.27, 0.17, 0.10, 0.08], float)
    p[:, 3:] = q3[:, None] * tail[None, :]
    if shape_shift is not None:
        s = np.asarray(shape_shift, float)
        amt = np.minimum(np.maximum(s, -0.005), 0.005)
        p[:, 2] -= amt
        p[:, 3] += amt
    if np.any(p < 0):
        raise RuntimeError('synthetic probability construction invalid')
    p /= p.sum(axis=1, keepdims=True)
    return p


def synthetic_contract(identity_hashes: list[str]) -> dict:
    return {
        'data_plan': {
            'identity_count': len(identity_hashes),
            'ordered_identity_sha256': ordered_identity_sha256(identity_hashes),
        },
        'sample_plan': {
            'development_minimum_n': 160,
            'confirmation': False,
        },
        'metrics': {'calibration': {'bins': 10}},
        'bootstrap': {'resamples': 1000, 'seed': 72099, 'ci': 0.90},
        'oos_design': {'minimum_test_rows_per_fold': 20},
        'success_gates': {
            'primary': {'delta_max': 0.0, 'bootstrap_ci_high_max': 0.0},
            'secondary_noninferiority': {
                'Brier_delta_max': 0.0,
                'RPS_delta_max': 0.0,
                'Top1ECE_delta_max': 0.0,
                'ClasswiseECE_delta_max': 0.0,
            },
            'temporal_consistency': {'minimum_fold_win_fraction': 0.50},
            'domain_consistency': {
                'minimum_domains': 4,
                'minimum_rows_per_domain': 20,
                'minimum_win_fraction': 0.50,
                'max_domain_logloss_regression': 0.01,
            },
        },
    }


def main() -> int:
    n = 160
    ids = [f'synthetic-{i:04d}' for i in range(n)]
    identity_hashes=[hashlib.sha256(x.encode('utf-8')).hexdigest() for x in ids]
    frame = pd.DataFrame({
        'id': ids,
        'cutoff': pd.date_range('2025-01-01', periods=n, freq='D', tz='UTC') + pd.Timedelta(hours=12),
        'odds_ts': pd.date_range('2025-01-01', periods=n, freq='D', tz='UTC') + pd.Timedelta(hours=11, minutes=59),
        'O25': np.linspace(1.65, 2.20, n),
        'U25': np.linspace(2.25, 1.70, n),
    })
    assert_same_prediction_cutoff('T-15m', 'T-15m')
    assert_master_prediction_cutoff('T-15m', 'T-15m')
    assert_feature_pit(frame, cutoff_col='cutoff', feature_timestamp_cols=['odds_ts'])
    assert_temporal_oos(frame.cutoff.iloc[:100], frame.cutoff.iloc[100:])
    assert_disjoint_identity_sets({'development': ids[:100], 'evaluation': ids[100:]})
    assert_sealed_boundaries(
        {'C070-F Confirmation1597': 0, 'N17 reserve266': 0, 'N18C confirmation150': 0},
        [SealedPool('C070-F Confirmation1597'), SealedPool('N17 reserve266'), SealedPool('N18C confirmation150')],
    )

    q3 = devig_two_way(frame.O25.to_numpy(), frame.U25.to_numpy())
    for i in (0, n // 2, n - 1):
        validate_nested_ou_tails([2.5], [float(q3[i])])

    left = frame[['id', 'cutoff']].copy()
    synthetic_labels = pd.DataFrame({'id': ids, 'target': np.arange(n, dtype=int) % 8})
    joined = assert_exact_one_to_one_join(left, synthetic_labels, keys=['id'], expected_rows=n)
    y = joined['target'].to_numpy(dtype=int)

    baseline = make_prob(q3)
    candidate = make_prob(q3, shape_shift=np.sin(np.arange(n)) * 0.002)
    b = score_bundle(baseline, y)
    c = score_bundle(candidate, y)
    boot = paired_bootstrap_delta_logloss(baseline, candidate, y, n_resamples=1000, seed=72099)
    folds = np.repeat(['fold1','fold2','fold3','fold4'], 40)
    domains = np.repeat(['league1','league2','league3','league4'], 40)
    canonical = evaluate_frozen_experiment(
        baseline,
        candidate,
        y,
        identity_sha256=identity_hashes,
        fold_ids=folds,
        domain_ids=domains,
        contract=synthetic_contract(identity_hashes),
    )

    out = {
        'status': 'FOOTBALL3_SYNTHETIC_PRELABEL_SMOKE_PASS',
        'real_target_labels_opened': 0,
        'synthetic_rows': n,
        'same_cutoff_guard': True,
        'master_cutoff_guard': True,
        'pit_guard': True,
        'temporal_oos_guard': True,
        'identity_join_guard': True,
        'sealed_boundary_guard': True,
        'ou_direction_guard': True,
        'canonical_evaluator_guard': True,
        'canonical_identity_binding_guard': canonical['scored_identity_sha256'] == ordered_identity_sha256(identity_hashes),
        'canonical_sample_minimum_guard': canonical['n'] >= canonical['frozen_minimum_n'],
        'metric_bundle_keys': sorted(b.keys()),
        'baseline': b,
        'candidate': c,
        'bootstrap': boot,
        'canonical_evaluation': canonical,
    }
    Path('football-data/research/football3_synthetic_prelabel_smoke_summary.json').write_text(
        json.dumps(out, indent=2) + '\n', encoding='utf-8'
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
