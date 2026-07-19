#!/usr/bin/env python3
"""V4.7.0 staged xG residual feature builder.

Produces current-season, point-in-time-safe residual signals. It does not alter
formal probabilities by itself. Provider-specific coverage and competition-
specific chronological OOF validation are required before any non-zero weight.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any


def build_xg_residual_features(
    rows: list[dict[str, Any]],
    cutoff: datetime,
    *,
    half_life_days: float = 90.0,
) -> dict[str, Any]:
    weighted_n = 0.0
    goals_for = goals_against = xg_for = xg_against = 0.0
    used = 0
    for row in rows:
        when = row.get("kickoff")
        if not isinstance(when, datetime) or when >= cutoff:
            continue
        vals = [row.get("goals_for"), row.get("goals_against"), row.get("xg_for"), row.get("xg_against")]
        try:
            gf, ga, xf, xa = map(float, vals)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (gf, ga, xf, xa)):
            continue
        age = max(0.0, (cutoff - when).total_seconds() / 86400.0)
        w = math.exp(-math.log(2.0) * age / max(1e-9, half_life_days))
        weighted_n += w
        used += 1
        goals_for += w * gf
        goals_against += w * ga
        xg_for += w * xf
        xg_against += w * xa
    if weighted_n <= 0.0:
        raise ValueError("no point-in-time xG rows available")
    return {
        "matches_used": used,
        "effective_matches": weighted_n,
        "finishing_residual": (goals_for - xg_for) / weighted_n,
        "goalkeeping_or_opponent_finishing_residual": (xg_against - goals_against) / weighted_n,
        "mean_xg_for": xg_for / weighted_n,
        "mean_xg_against": xg_against / weighted_n,
        "formal_weight": 0,
        "status": "FEATURE_ONLY_COVERAGE_AND_VALIDATION_REQUIRED"
    }
