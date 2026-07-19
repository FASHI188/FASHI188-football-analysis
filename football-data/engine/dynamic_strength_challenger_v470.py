#!/usr/bin/env python3
"""V4.7.0 staged adaptive commensurate-strength utilities.

The functions here provide an auditable way to borrow prior-season information
without injecting it unconditionally. Competition-specific validation must learn
and approve every coefficient. Formal weight remains 0 until promotion.
"""
from __future__ import annotations

import math
from typing import Any


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, value))


def commensurability_score(
    *,
    roster_continuity: float,
    coach_continuity: float,
    promoted_or_relegated: bool,
    structural_break_score: float,
    coefficients: dict[str, Any] | None = None,
) -> float:
    """Return a prior-borrowing weight in [0,1].

    Inputs must be point-in-time safe and competition-specific. Default
    coefficients are conservative engineering defaults only; they are not formal
    model parameters and must not be used in the center without OOF promotion.
    """
    coefficients = coefficients or {}
    intercept = float(coefficients.get("intercept", -0.5))
    beta_roster = float(coefficients.get("beta_roster_continuity", 2.0))
    beta_coach = float(coefficients.get("beta_coach_continuity", 0.7))
    beta_promotion = float(coefficients.get("beta_promoted_or_relegated", -1.8))
    beta_break = float(coefficients.get("beta_structural_break", -2.2))
    roster = _clip01(float(roster_continuity))
    coach = _clip01(float(coach_continuity))
    break_score = _clip01(float(structural_break_score))
    linear = (
        intercept
        + beta_roster * roster
        + beta_coach * coach
        + beta_promotion * (1.0 if promoted_or_relegated else 0.0)
        + beta_break * break_score
    )
    return 1.0 / (1.0 + math.exp(-linear))


def blend_sufficient_statistic(
    current_value: float,
    current_effective_n: float,
    prior_value: float,
    prior_effective_n: float,
    borrowing_weight: float,
    *,
    max_prior_equivalent_matches: float,
) -> dict[str, float]:
    """Blend one sufficient statistic with capped, adaptive prior borrowing."""
    current_n = max(0.0, float(current_effective_n))
    prior_n = max(0.0, float(prior_effective_n))
    weight = _clip01(float(borrowing_weight))
    borrowed_n = min(prior_n * weight, max(0.0, float(max_prior_equivalent_matches)))
    denominator = current_n + borrowed_n
    if denominator <= 0.0:
        raise ValueError("both current and borrowed effective sample sizes are zero")
    value = (float(current_value) * current_n + float(prior_value) * borrowed_n) / denominator
    return {
        "blended_value": value,
        "current_effective_n": current_n,
        "borrowed_prior_effective_n": borrowed_n,
        "borrowing_weight": weight,
        "formal_weight": 0.0,
    }
