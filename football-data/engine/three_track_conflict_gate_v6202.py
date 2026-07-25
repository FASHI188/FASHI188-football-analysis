#!/usr/bin/env python3
"""V6.20.2 non-mutating three-track conflict gate.

The independent 1X2, exact-score and total-goals tracks retain their own probabilities.
This module never reconciles, blends or overwrites them. It only audits contradictions
and may downgrade the weaker exact-score conclusion. The P60 threshold is frozen from
the previously audited independent-1X2 research tier; no threshold is tuned here.
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
            'score_exact_allowed': False, 'reasons': ['NO_SCORE_RANKING'], 'probability_mutation': False,
        }
    top = score_ranked[0]
    h, a = int(top['home_goals']), int(top['away_goals'])
    one_pick = _argmax(one_x_two)
    total_order = sorted(range(8), key=lambda i: (-float(total_goals[i]), i))
    score_result = _score_result(h, a)
    score_total = min(7, h + a)
    reasons = []
    if max(one_x_two) >= P60 and score_result != one_pick:
        reasons.append('SCORE_CONFLICTS_WITH_P60_1X2')
    if score_total not in total_order[:2]:
        reasons.append('SCORE_TOTAL_OUTSIDE_INDEPENDENT_TOTAL_TOP2')
    if not score_model_passed:
        reasons.append('SCORE_MODEL_GATE_NOT_PASSED')
    score_status = 'PASS' if not reasons else 'DEGRADED'
    return {
        'one_x_two_status': 'PASS',
        'total_status': 'PASS',
        'score_status': score_status,
        'score_exact_allowed': score_status == 'PASS',
        'one_x_two_pick': one_pick,
        'one_x_two_max_probability': max(float(x) for x in one_x_two),
        'total_top2': total_order[:2],
        'score_top1': {'home': h, 'away': a, 'result': score_result, 'total_bucket': score_total},
        'reasons': reasons,
        'probability_mutation': False,
        'governance': {
            'one_x_two_probability_changed': False,
            'total_probability_changed': False,
            'score_probability_changed': False,
            'weak_track_may_be_downgraded_but_never_rewrites_stronger_tracks': True,
        },
    }
