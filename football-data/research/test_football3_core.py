from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from football3_core import (
    Football3ContractError,
    SealedPool,
    assert_disjoint_identity_sets,
    assert_exact_one_to_one_join,
    assert_feature_pit,
    assert_same_prediction_cutoff,
    assert_sealed_boundaries,
    assert_temporal_oos,
    devig_two_way,
    multiclass_brier,
    multiclass_logloss,
    normalized_rps,
    ordered_identity_sha256,
    ou_tail_k,
    paired_bootstrap_delta_logloss,
    score_bundle,
    validate_nested_ou_tails,
    validate_probability_matrix,
    validate_target,
)


def test_ou_mapping_and_devig_direction():
    assert [ou_tail_k(x) for x in (0.5, 1.5, 2.5, 3.5, 4.5)] == [1, 2, 3, 4, 5]
    p = devig_two_way([2.0, 1.5], [2.0, 3.0])
    assert np.allclose(p, [0.5, 2 / 3])
    validate_nested_ou_tails([0.5, 1.5, 2.5], [0.85, 0.60, 0.40])
    with pytest.raises(Football3ContractError):
        validate_nested_ou_tails([0.5, 1.5, 2.5], [0.50, 0.60, 0.40])


def test_target_and_probability_fail_closed():
    y = validate_target([0, 1, 7])
    assert y.tolist() == [0, 1, 7]
    p = validate_probability_matrix([[0.7, 0.3, 0, 0, 0, 0, 0, 0]])
    assert p.shape == (1, 8)
    with pytest.raises(Football3ContractError):
        validate_target([8])
    with pytest.raises(Football3ContractError):
        validate_probability_matrix([[0.6, 0.5, 0, 0, 0, 0, 0, 0]])


def test_metrics_have_fixed_class_mapping():
    p = np.array([
        [0.70, 0.20, 0.10, 0, 0, 0, 0, 0],
        [0.10, 0.20, 0.70, 0, 0, 0, 0, 0],
    ])
    y = np.array([0, 2])
    ll = multiclass_logloss(p, y)
    assert np.isclose(ll, -np.log(0.7))
    assert 0 <= multiclass_brier(p, y) <= 2
    assert 0 <= normalized_rps(p, y) <= 1
    s = score_bundle(p, y)
    assert s['Top1'] == 1.0
    assert s['Top3'] == 1.0
    assert s['probability_residual_max'] <= 1e-15


def test_paired_bootstrap_is_match_paired_and_seeded():
    y = np.array([0, 1, 2, 3] * 30)
    b = np.full((len(y), 8), 0.02)
    c = np.full((len(y), 8), 0.02)
    for i, yi in enumerate(y):
        b[i, yi] = 0.86
        c[i, yi] = 0.88
        # Remove mass from a fixed wrong class so rows still sum to one.
        wrong = (yi + 1) % 8
        b[i, wrong] += 1.0 - b[i].sum()
        c[i, wrong] += 1.0 - c[i].sum()
    r1 = paired_bootstrap_delta_logloss(b, c, y, n_resamples=500, seed=123)
    r2 = paired_bootstrap_delta_logloss(b, c, y, n_resamples=500, seed=123)
    assert r1 == r2
    assert r1['paired'] is True
    assert r1['delta'] < 0


def test_identity_disjoint_and_exact_join():
    assert_disjoint_identity_sets({'a': {'x', 'y'}, 'b': {'z'}})
    with pytest.raises(Football3ContractError):
        assert_disjoint_identity_sets({'a': {'x'}, 'b': {'x'}})
    assert ordered_identity_sha256(['a', 'b']) == ordered_identity_sha256(['a', 'b'])
    left = pd.DataFrame({'id': ['1', '2'], 'x': [3, 4]})
    right = pd.DataFrame({'id': ['1', '2'], 'T': [1, 2]})
    joined = assert_exact_one_to_one_join(left, right, keys=['id'], expected_rows=2)
    assert len(joined) == 2
    with pytest.raises(Football3ContractError):
        assert_exact_one_to_one_join(left, right.iloc[:1], keys=['id'], expected_rows=2)


def test_temporal_pit_and_same_cutoff():
    assert_temporal_oos(['2024-01-01', '2024-02-01'], ['2024-03-01'])
    with pytest.raises(Football3ContractError):
        assert_temporal_oos(['2024-03-01'], ['2024-03-01'])
    f = pd.DataFrame({
        'cutoff': ['2024-01-02T12:00:00Z'],
        'odds_ts': ['2024-01-02T11:59:00Z'],
    })
    assert_feature_pit(f, cutoff_col='cutoff', feature_timestamp_cols=['odds_ts'])
    f.loc[0, 'odds_ts'] = '2024-01-02T12:01:00Z'
    with pytest.raises(Football3ContractError):
        assert_feature_pit(f, cutoff_col='cutoff', feature_timestamp_cols=['odds_ts'])
    assert_same_prediction_cutoff('T-15m', ' t-15m ')
    with pytest.raises(Football3ContractError):
        assert_same_prediction_cutoff('opening', 'T-15m')


def test_sealed_pool_guard():
    pools = [SealedPool('C070-F1597'), SealedPool('N17-reserve266')]
    assert_sealed_boundaries({'C070-F1597': 0, 'N17-reserve266': 0}, pools)
    with pytest.raises(Football3ContractError):
        assert_sealed_boundaries({'C070-F1597': 1}, pools)
