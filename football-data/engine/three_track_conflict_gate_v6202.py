#!/usr/bin/env python3
"""V6.20.2 non-mutating three-track conflict audit.

The independent 1X2, exact-score and total-goals tracks retain their own probabilities.
Cross-track agreement is NOT an accuracy selector: V6.20.5 showed that filtering exact
scores by these consistency checks reduced Top-1 accuracy. Therefore this module only
flags contradictions for confidence/display auditing. It never authorizes, suppresses,
reconciles, blends or overwrites any track probability.
"""
from __future__ import annotations
from typing import Any

P60 = 0.60


def _argmax(values):
    return max(range(len(values)), key=lambda i: float(values[i]))


def _score_result(home: int, away: int) -> int:
    return 0 if home > away else 1 if home == away else 2


def audit_three_tracks(
    one_x_two: list[float],
    total_goals: list[float],
    score_ranked: list[dict[str, Any]],
    *,
    score_model_passed: bool = True,
) -> dict[str, Any]:
    if len(one_x_two) != 3 or len(total_goals) != 8:
        raise ValueError('expected 3-way 1X2 and 8-way total-goals distributions')
    if not score_ranked:
        return {
            'one_x_two_status': 'PASS', 'total_status': 'PASS', 'score_status': 'UNAVAILABLE',
            'cross_track_warnings': ['NO_SCORE_RANKING'], 'probability_mutation': False,
            'accuracy_selector': False, 'audit_only': True,
        }
    top = score_ranked[0]
    h, a = int(top['home_goals']), int(top['away_goals'])
    one_pick = _argmax(one_x_two)
    total_order = sorted(range(8), key=lambda i: (-float(total_goals[i]), i))
    score_result = _score_result(h, a)
    score_total = min(7, h + a)
    warnings = []
    if max(one_x_two) >= P60 and score_result != one_pick:
        warnings.append('SCORE_CONFLICTS_WITH_P60_1X2')
    if score_total not in total_order[:2]:
        warnings.append('SCORE_TOTAL_OUTSIDE_INDEPENDENT_TOTAL_TOP2')
    if not score_model_passed:
        warnings.append('SCORE_MODEL_GATE_NOT_PASSED')
    score_status = 'PASS' if score_model_passed else 'MODEL_GATE_FAILED'
    return {
        'one_x_two_status': 'PASS',
        'total_status': 'PASS',
        'score_status': score_status,
        'one_x_two_pick': one_pick,
        'one_x_two_max_probability': max(float(x) for x in one_x_two),
        'total_top2': total_order[:2],
        'score_top1': {'home': h, 'away': a, 'result': score_result, 'total_bucket': score_total},
        'cross_track_warnings': warnings,
        'probability_mutation': False,
        'accuracy_selector': False,
        'audit_only': True,
        'governance': {
            'one_x_two_probability_changed': False,
            'total_probability_changed': False,
            'score_probability_changed': False,
            'cross_track_agreement_may_not_promote_or_suppress_a_score': True,
            'empirical_reason': 'V6.20.5 consistency-filtered score Top1 underperformed raw score Top1',
        },
    }
