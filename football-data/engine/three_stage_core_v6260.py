#!/usr/bin/env python3
"""V6.26.0 research-only three-stage football probability core.

Architecture
------------
1) 1X2 is accepted from an independent head.
2) 0-7+ total goals is accepted from an independent head.
3) Exact score is reconciled last from a strictly positive prior score matrix.

Asian handicap is deliberately NOT an independent target in this core. Goal-difference
and handicap settlement may be derived from the final matrix, and AH may later enter an
upstream residual model only after an explicit chronological ablation. It is never a hard
constraint here.

The reconciliation is iterative proportional fitting (I-projection / KL projection over
partition constraints). It preserves both accepted upstream marginals to numerical
tolerance while staying as close as possible to the prior matrix under the IPF geometry.

Research only: formal CURRENT V5.0.1 and formal weights are unchanged.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Callable

DIRECTIONS = ("home", "draw", "away")
TOTAL_BUCKETS = ("0", "1", "2", "3", "4", "5", "6", "7+")
TOL = 1e-10
MAX_ITER = 2000
EPS = 1e-15


class ThreeStageCoreError(RuntimeError):
    pass


def _result_index(home: int, away: int) -> int:
    return 0 if home > away else 1 if home == away else 2


def _total_index(home: int, away: int) -> int:
    return min(7, home + away)


def _copy_matrix(matrix: list[dict[str, Any]]) -> list[dict[str, float | int]]:
    out: list[dict[str, float | int]] = []
    for cell in matrix:
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        p = float(cell["probability"])
        if h < 0 or a < 0 or not math.isfinite(p) or p < 0.0:
            raise ThreeStageCoreError("invalid prior score cell")
        out.append({"home_goals": h, "away_goals": a, "probability": p})
    if not out:
        raise ThreeStageCoreError("empty prior score matrix")
    z = sum(float(c["probability"]) for c in out)
    if not math.isfinite(z) or z <= 0.0:
        raise ThreeStageCoreError("prior score matrix has zero/non-finite mass")
    for c in out:
        c["probability"] = float(c["probability"]) / z
    return out


def _normalize_target(values: list[float], expected: int, name: str) -> list[float]:
    if len(values) != expected:
        raise ThreeStageCoreError(f"{name} dimension {len(values)} != {expected}")
    cleaned = [float(x) for x in values]
    if any((not math.isfinite(x)) or x < 0.0 for x in cleaned):
        raise ThreeStageCoreError(f"{name} contains invalid probability")
    z = sum(cleaned)
    if z <= 0.0:
        raise ThreeStageCoreError(f"{name} has zero probability mass")
    return [x / z for x in cleaned]


def one_x_two_vector(matrix: list[dict[str, Any]]) -> list[float]:
    out = [0.0, 0.0, 0.0]
    for c in matrix:
        h = int(c["home_goals"])
        a = int(c["away_goals"])
        out[_result_index(h, a)] += float(c["probability"])
    return out


def total_goals_vector(matrix: list[dict[str, Any]]) -> list[float]:
    out = [0.0] * 8
    for c in matrix:
        h = int(c["home_goals"])
        a = int(c["away_goals"])
        out[_total_index(h, a)] += float(c["probability"])
    return out


def goal_difference_distribution(matrix: list[dict[str, Any]]) -> dict[int, float]:
    out: dict[int, float] = defaultdict(float)
    for c in matrix:
        h = int(c["home_goals"])
        a = int(c["away_goals"])
        out[h - a] += float(c["probability"])
    return dict(sorted(out.items()))


def _scale_partition(
    matrix: list[dict[str, float | int]],
    group_fn: Callable[[int, int], int],
    targets: list[float],
) -> None:
    current = [0.0] * len(targets)
    for c in matrix:
        h = int(c["home_goals"])
        a = int(c["away_goals"])
        current[group_fn(h, a)] += float(c["probability"])

    factors: list[float] = []
    for idx, (have, want) in enumerate(zip(current, targets)):
        if have <= EPS:
            if want > TOL:
                raise ThreeStageCoreError(f"prior has zero support for required partition {idx}")
            factors.append(1.0)
        else:
            factors.append(want / have)

    for c in matrix:
        h = int(c["home_goals"])
        a = int(c["away_goals"])
        c["probability"] = float(c["probability"]) * factors[group_fn(h, a)]


def _kl_from_prior(
    matrix: list[dict[str, float | int]], prior: list[dict[str, float | int]]
) -> float:
    if len(matrix) != len(prior):
        raise ThreeStageCoreError("matrix/prior length mismatch")
    value = 0.0
    for q_cell, p_cell in zip(matrix, prior):
        q = float(q_cell["probability"])
        p = float(p_cell["probability"])
        if q <= 0.0:
            continue
        if p <= 0.0:
            return math.inf
        value += q * math.log(q / p)
    return value


def reconcile(
    prior_matrix: list[dict[str, Any]],
    target_1x2: list[float],
    target_total_0_7plus: list[float],
    *,
    tolerance: float = TOL,
    max_iter: int = MAX_ITER,
) -> tuple[list[dict[str, float | int]], dict[str, Any]]:
    """Reconcile a score prior to independent 1X2 and total-goals heads.

    No Asian-handicap target is accepted by design. The returned matrix is the only joint
    probability object from which exact score and any optional handicap settlement should be
    derived.
    """
    target_one = _normalize_target(target_1x2, 3, "1X2")
    target_total = _normalize_target(target_total_0_7plus, 8, "total_goals")
    prior = _copy_matrix(prior_matrix)
    matrix = _copy_matrix(prior_matrix)

    residual = math.inf
    for iteration in range(1, int(max_iter) + 1):
        _scale_partition(matrix, _result_index, target_one)
        _scale_partition(matrix, _total_index, target_total)

        one = one_x_two_vector(matrix)
        total = total_goals_vector(matrix)
        mass = sum(float(c["probability"]) for c in matrix)
        residual = max(
            abs(mass - 1.0),
            max(abs(a - b) for a, b in zip(one, target_one)),
            max(abs(a - b) for a, b in zip(total, target_total)),
        )
        if residual <= tolerance:
            z = sum(float(c["probability"]) for c in matrix)
            for c in matrix:
                c["probability"] = float(c["probability"]) / z
            final_one = one_x_two_vector(matrix)
            final_total = total_goals_vector(matrix)
            audit = {
                "status": "PASS",
                "converged": True,
                "iterations": iteration,
                "max_residual": max(
                    max(abs(a - b) for a, b in zip(final_one, target_one)),
                    max(abs(a - b) for a, b in zip(final_total, target_total)),
                    abs(sum(float(c["probability"]) for c in matrix) - 1.0),
                ),
                "one_x_two_residuals": [a - b for a, b in zip(final_one, target_one)],
                "total_goals_residuals": [a - b for a, b in zip(final_total, target_total)],
                "probability_sum": sum(float(c["probability"]) for c in matrix),
                "kl_q_from_prior": _kl_from_prior(matrix, prior),
                "asian_handicap_used_as_hard_constraint": False,
            }
            return matrix, audit

    return matrix, {
        "status": "FAIL",
        "converged": False,
        "iterations": int(max_iter),
        "max_residual": residual,
        "reason": "MAX_ITER",
        "asian_handicap_used_as_hard_constraint": False,
    }


def top_scores(matrix: list[dict[str, Any]], k: int = 3) -> list[dict[str, Any]]:
    ranked = sorted(matrix, key=lambda c: float(c["probability"]), reverse=True)[: max(1, int(k))]
    return [
        {
            "score": f"{int(c['home_goals'])}-{int(c['away_goals'])}",
            "probability": float(c["probability"]),
        }
        for c in ranked
    ]


def build_three_stage_output(
    prior_matrix: list[dict[str, Any]],
    accepted_1x2: dict[str, float],
    accepted_total: dict[str, float],
) -> dict[str, Any]:
    one = [float(accepted_1x2[k]) for k in DIRECTIONS]
    total = [float(accepted_total[k]) for k in TOTAL_BUCKETS]
    matrix, audit = reconcile(prior_matrix, one, total)
    if not audit.get("converged"):
        raise ThreeStageCoreError(f"three-stage reconciliation failed: {audit}")
    final_one = one_x_two_vector(matrix)
    final_total = total_goals_vector(matrix)
    return {
        "architecture": "V6.26.0_THREE_STAGE_1X2_TOTAL_SCORE",
        "formal_weight": 0,
        "stage_1_one_x_two": {k: final_one[i] for i, k in enumerate(DIRECTIONS)},
        "stage_2_total_goals": {k: final_total[i] for i, k in enumerate(TOTAL_BUCKETS)},
        "stage_3_score_matrix": matrix,
        "top_scores": top_scores(matrix, 3),
        "derived_goal_difference": goal_difference_distribution(matrix),
        "asian_handicap_role": "DERIVED_OR_AUXILIARY_ONLY_NOT_PRIMARY_TARGET",
        "reconciliation_audit": audit,
    }
