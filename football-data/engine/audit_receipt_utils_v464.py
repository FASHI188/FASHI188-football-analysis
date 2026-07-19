#!/usr/bin/env python3
"""Audit receipt helpers for V4.6.4 hardening.

This module does not alter formal model probabilities.  It only derives robust
reporting fields from an already-produced unified score matrix / marginal map.
"""
from __future__ import annotations

from typing import Any

from platform_core import PlatformError, derive_score_marginals


def total_goals_0_7plus(
    total_goals: dict[str, Any] | None = None,
    score_matrix: list[dict[str, Any]] | None = None,
    *,
    tolerance: float = 1e-8,
) -> dict[str, float]:
    """Return an audited 0,1,...,6,7+ total-goals vector.

    Prefer the canonical ``7+`` bucket when it already exists.  If a caller
    supplies legacy exact numeric tail keys, aggregate every integer key >=7.
    When a score matrix is available, derive the canonical vector from that
    matrix and use it as the authoritative fallback.  This prevents the old
    one-off receipt bug where a canonical ``7+`` key was ignored and reported
    as zero.
    """
    mapping = total_goals if isinstance(total_goals, dict) else {}
    result = {str(i): float(mapping.get(str(i), 0.0) or 0.0) for i in range(7)}

    if "7+" in mapping:
        result["7+"] = float(mapping.get("7+", 0.0) or 0.0)
    else:
        result["7+"] = sum(
            float(value or 0.0)
            for key, value in mapping.items()
            if str(key).isdigit() and int(str(key)) >= 7
        )

    total = sum(result.values())
    if abs(total - 1.0) <= tolerance:
        return result

    if score_matrix:
        derived = derive_score_marginals(score_matrix)["total_goals"]
        rebuilt = {key: float(derived[key]) for key in ("0", "1", "2", "3", "4", "5", "6", "7+")}
        rebuilt_total = sum(rebuilt.values())
        if abs(rebuilt_total - 1.0) > tolerance:
            raise PlatformError(f"derived total-goals vector sums to {rebuilt_total:.12f}, not 1")
        return rebuilt

    raise PlatformError(f"total-goals 0-7+ vector sums to {total:.12f}, not 1 and no score matrix was supplied")


def total_peak_diagnostics(total_goals: dict[str, Any]) -> dict[str, Any]:
    """Describe whether the total-goals Top-1 is a strong or weak discrete peak."""
    canonical = {key: float(total_goals.get(key, 0.0) or 0.0) for key in ("0", "1", "2", "3", "4", "5", "6", "7+")}
    ranking = sorted(canonical.items(), key=lambda item: (-item[1], item[0]))
    primary, secondary = ranking[0], ranking[1]
    gap = primary[1] - secondary[1]
    if gap < 0.01:
        strength = "极弱Top-1"
    elif gap < 0.02:
        strength = "弱Top-1"
    elif gap < 0.04:
        strength = "中等Top-1"
    else:
        strength = "强Top-1"
    return {
        "primary": primary[0],
        "primary_probability": primary[1],
        "secondary": secondary[0],
        "secondary_probability": secondary[1],
        "gap": gap,
        "strength": strength,
        "interpretation": (
            "Top-1仅为离散众数，不应表述为锁定单一总进球数。"
            if gap < 0.02
            else "Top-1与第二选择存在可见分离，但仍应同时报告完整0-7+分布。"
        ),
    }
