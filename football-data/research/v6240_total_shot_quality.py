#!/usr/bin/env python3
"""V6.24.0 research-only direct total-goals model.

Architecture
------------
1. Team shot quantity is a discrete CMP distribution.
2. Shot conversion/quality uncertainty is represented by a Beta distribution.
3. Conditional on S shots, goals follow the Beta-Binomial predictive distribution.
4. Home and away goal marginals are convolved into a direct P(T=0..7+) track.

The module intentionally contains no fitted football parameters, no historical outcome
lookup, no workflow entrypoint and no automatic fallback. All team/match parameters must
be supplied by a strictly pre-match/PIT estimator. This file is RESEARCH_ONLY and has
formal_weight=0.

The purpose is to replace the old "one expected-goals number -> count distribution"
mechanism with an explicit shot-quantity x shot-quality generative path while preserving
an auditable exact 0..7+ total distribution.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp, lgamma, log
from typing import Iterable, Sequence

EPS = 1e-15
FORMAL_WEIGHT = 0
CLASSIFICATION = "RESEARCH_ONLY_V6_24_0_SHOT_QUANTITY_X_QUALITY"


@dataclass(frozen=True)
class ShotProcessParams:
    """Strictly pre-match parameters for one team.

    cmp_lambda
        Positive CMP intensity controlling shot quantity.
    cmp_nu
        Positive CMP dispersion. nu=1 is Poisson; values below/above one allow
        over/under-dispersion without forcing one league-wide variance structure.
    quality_alpha, quality_beta
        Positive Beta parameters for latent per-shot conversion/quality.

    No defaults are provided because unverified parameters must fail closed.
    """

    cmp_lambda: float
    cmp_nu: float
    quality_alpha: float
    quality_beta: float

    def validate(self) -> None:
        vals = (
            self.cmp_lambda,
            self.cmp_nu,
            self.quality_alpha,
            self.quality_beta,
        )
        if not all(float(x) > 0.0 for x in vals):
            raise ValueError("all shot-process parameters must be strictly positive")


def _normalize(xs: Sequence[float]) -> list[float]:
    s = float(sum(xs))
    if not s > 0.0:
        raise ValueError("probability mass is empty")
    return [max(0.0, float(x)) / s for x in xs]


def cmp_pmf(lam: float, nu: float, max_count: int = 60) -> list[float]:
    """Truncated Conway-Maxwell-Poisson PMF for counts 0..max_count.

    The normalizing constant is computed by log-sum-exp over the explicit truncation.
    The caller owns the truncation choice; no silent football-specific cutoff is used.
    """
    if lam <= 0.0 or nu <= 0.0 or max_count < 0:
        raise ValueError("invalid CMP parameters")
    logw = [k * log(lam) - nu * lgamma(k + 1.0) for k in range(max_count + 1)]
    m = max(logw)
    w = [exp(x - m) for x in logw]
    return _normalize(w)


def beta_binomial_pmf(n: int, alpha: float, beta: float) -> list[float]:
    """Predictive goals 0..n after integrating p~Beta(alpha,beta)."""
    if n < 0 or alpha <= 0.0 or beta <= 0.0:
        raise ValueError("invalid beta-binomial parameters")
    base = lgamma(alpha) + lgamma(beta) - lgamma(alpha + beta)
    out: list[float] = []
    for g in range(n + 1):
        log_choose = lgamma(n + 1) - lgamma(g + 1) - lgamma(n - g + 1)
        log_beta_post = (
            lgamma(g + alpha)
            + lgamma(n - g + beta)
            - lgamma(n + alpha + beta)
        )
        out.append(exp(log_choose + log_beta_post - base))
    return _normalize(out)


def team_goal_distribution(
    params: ShotProcessParams,
    *,
    max_shots: int = 60,
    max_goals: int = 12,
) -> list[float]:
    """Marginal P(G=0..max_goals+) from the shot generative process.

    The last bucket is max_goals+ and includes all generated goal counts >= max_goals.
    """
    params.validate()
    if max_goals < 1:
        raise ValueError("max_goals must be >=1")
    shot_p = cmp_pmf(params.cmp_lambda, params.cmp_nu, max_count=max_shots)
    out = [0.0] * (max_goals + 1)
    for shots, ps in enumerate(shot_p):
        goals_given_shots = beta_binomial_pmf(
            shots, params.quality_alpha, params.quality_beta
        )
        for goals, pg in enumerate(goals_given_shots):
            idx = min(max_goals, goals)
            out[idx] += ps * pg
    return _normalize(out)


def total_goals_0_7plus(
    home: ShotProcessParams,
    away: ShotProcessParams,
    *,
    max_shots: int = 60,
    team_goal_cap: int = 12,
) -> list[float]:
    """Direct match total P(T=0),...,P(T=6),P(T=7+)."""
    hp = team_goal_distribution(home, max_shots=max_shots, max_goals=team_goal_cap)
    ap = team_goal_distribution(away, max_shots=max_shots, max_goals=team_goal_cap)
    total = [0.0] * 8
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            total[min(7, h + a)] += ph * pa
    return _normalize(total)


def audit_distribution(p: Iterable[float], tol: float = 1e-10) -> dict[str, float | bool]:
    vals = [float(x) for x in p]
    residual = abs(sum(vals) - 1.0)
    return {
        "probability_sum": sum(vals),
        "probability_sum_residual": residual,
        "nonnegative": all(x >= -tol for x in vals),
        "finite_range": all(-tol <= x <= 1.0 + tol for x in vals),
        "pass": residual <= tol and all(-tol <= x <= 1.0 + tol for x in vals),
    }
