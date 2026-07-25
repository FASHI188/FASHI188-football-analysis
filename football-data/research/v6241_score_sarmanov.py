#!/usr/bin/env python3
"""V6.24.1 research-only conditional exact-score model.

This module consumes already-formed home/away goal marginals plus a direct total-goals
0..7+ distribution. It adds flexible home/away dependence with a bounded Sarmanov term,
then projects each total-goals diagonal to the supplied direct total track.

Design goals
------------
- Do not derive the total-goals track from the score model.
- Do not hand-pick 1-1/2-1/1-0 corrections.
- Enforce non-negativity by computing the admissible Sarmanov rho interval.
- Enforce exact consistency with the independent P(T=0..7+) track via diagonal scaling.
- Require rho to be supplied by a PIT/OOS estimator; no football-specific default.
- Research only, formal_weight=0, no workflow or prediction side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Sequence

FORMAL_WEIGHT = 0
CLASSIFICATION = "RESEARCH_ONLY_V6_24_1_SARMANOV_CONDITIONAL_SCORE"


@dataclass(frozen=True)
class JointAudit:
    probability_sum: float
    probability_sum_residual: float
    min_cell: float
    total_marginal_max_residual: float
    passed: bool


def _normalize(xs: Sequence[float]) -> list[float]:
    s = float(sum(xs))
    if not s > 0.0:
        raise ValueError("probability mass is empty")
    return [max(0.0, float(x)) / s for x in xs]


def _kernel(p: Sequence[float]) -> list[float]:
    """Bounded standardized score-count kernel with mean zero under p.

    We start from z=(k-E[K])/sd(K), then scale by max|z| so phi is in [-1,1].
    Degenerate marginals return an all-zero kernel, which correctly removes dependence.
    """
    q = _normalize(p)
    mean = sum(i * x for i, x in enumerate(q))
    var = sum(((i - mean) ** 2) * x for i, x in enumerate(q))
    if var <= 0.0:
        return [0.0 for _ in q]
    sd = sqrt(var)
    z = [(i - mean) / sd for i in range(len(q))]
    m = max(abs(x) for x in z)
    if m <= 0.0:
        return [0.0 for _ in q]
    phi = [x / m for x in z]
    weighted_mean = sum(x * w for x, w in zip(phi, q))
    # Numerical recentering preserves E[phi]=0 before the final bound rescale.
    phi = [x - weighted_mean for x in phi]
    m2 = max(abs(x) for x in phi)
    return [x / m2 for x in phi] if m2 > 0.0 else [0.0 for _ in phi]


def admissible_rho_interval(
    home_p: Sequence[float], away_p: Sequence[float]
) -> tuple[float, float]:
    """Exact rho interval ensuring 1+rho*phi_h*phi_a >= 0 for every cell."""
    ph = _kernel(home_p)
    pa = _kernel(away_p)
    lo = float("-inf")
    hi = float("inf")
    for x in ph:
        for y in pa:
            z = x * y
            if z > 0.0:
                lo = max(lo, -1.0 / z)
            elif z < 0.0:
                hi = min(hi, -1.0 / z)
    if lo == float("-inf"):
        lo = -1.0e12
    if hi == float("inf"):
        hi = 1.0e12
    if lo > hi:
        raise ValueError("empty admissible rho interval")
    return lo, hi


def sarmanov_prior(
    home_p: Sequence[float], away_p: Sequence[float], *, rho: float
) -> list[list[float]]:
    """Construct a non-negative Sarmanov joint prior from two goal marginals."""
    hp = _normalize(home_p)
    ap = _normalize(away_p)
    lo, hi = admissible_rho_interval(hp, ap)
    if not (lo <= float(rho) <= hi):
        raise ValueError(f"rho={rho} outside admissible interval [{lo},{hi}]")
    kh = _kernel(hp)
    ka = _kernel(ap)
    joint = [
        [hp[h] * ap[a] * (1.0 + float(rho) * kh[h] * ka[a]) for a in range(len(ap))]
        for h in range(len(hp))
    ]
    s = sum(sum(row) for row in joint)
    if s <= 0.0:
        raise ValueError("invalid joint mass")
    return [[max(0.0, x) / s for x in row] for row in joint]


def _target_total_bucket(h: int, a: int) -> int:
    return min(7, h + a)


def project_to_total_track(
    joint: Sequence[Sequence[float]], target_total_0_7plus: Sequence[float]
) -> list[list[float]]:
    """Scale score diagonals so the joint exactly matches P(T=0..7+).

    This projection changes only mass between total-goal diagonals; relative score shares
    within each diagonal remain those of the Sarmanov prior. If a target diagonal has
    positive mass but the prior gives it zero support, the model fails closed.
    """
    target = _normalize(target_total_0_7plus)
    if len(target) != 8:
        raise ValueError("target total distribution must have 8 buckets: 0..6,7+")
    rows = [list(map(float, row)) for row in joint]
    if not rows or not rows[0]:
        raise ValueError("joint matrix is empty")
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise ValueError("joint matrix is ragged")

    current = [0.0] * 8
    for h, row in enumerate(rows):
        for a, p in enumerate(row):
            current[_target_total_bucket(h, a)] += p

    scale = [0.0] * 8
    for t in range(8):
        if target[t] > 0.0 and current[t] <= 0.0:
            raise ValueError(f"no prior support for positive target total bucket {t}")
        scale[t] = target[t] / current[t] if current[t] > 0.0 else 0.0

    out = []
    for h, row in enumerate(rows):
        out.append([
            max(0.0, p) * scale[_target_total_bucket(h, a)]
            for a, p in enumerate(row)
        ])
    s = sum(sum(r) for r in out)
    if s <= 0.0:
        raise ValueError("projected joint mass is empty")
    return [[x / s for x in row] for row in out]


def total_marginal(joint: Sequence[Sequence[float]]) -> list[float]:
    out = [0.0] * 8
    for h, row in enumerate(joint):
        for a, p in enumerate(row):
            out[_target_total_bucket(h, a)] += float(p)
    return out


def audit_joint(
    joint: Sequence[Sequence[float]],
    target_total_0_7plus: Sequence[float],
    *,
    tol: float = 1e-10,
) -> JointAudit:
    target = _normalize(target_total_0_7plus)
    s = sum(sum(float(x) for x in row) for row in joint)
    min_cell = min(float(x) for row in joint for x in row)
    tm = total_marginal(joint)
    max_total_resid = max(abs(a - b) for a, b in zip(tm, target))
    residual = abs(s - 1.0)
    passed = residual <= tol and min_cell >= -tol and max_total_resid <= tol
    return JointAudit(
        probability_sum=s,
        probability_sum_residual=residual,
        min_cell=min_cell,
        total_marginal_max_residual=max_total_resid,
        passed=passed,
    )


def build_conditional_score_matrix(
    home_goal_p: Sequence[float],
    away_goal_p: Sequence[float],
    total_0_7plus: Sequence[float],
    *,
    rho: float,
) -> tuple[list[list[float]], JointAudit]:
    """One-call research constructor; rho must come from a pre-match estimator."""
    prior = sarmanov_prior(home_goal_p, away_goal_p, rho=rho)
    joint = project_to_total_track(prior, total_0_7plus)
    audit = audit_joint(joint, total_0_7plus)
    if not audit.passed:
        raise ValueError(f"joint audit failed: {audit}")
    return joint, audit
