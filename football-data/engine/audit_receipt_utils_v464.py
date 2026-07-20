#!/usr/bin/env python3
"""Audit receipt helpers for V4.6.4 hardening.

This module does not alter formal model probabilities. It only derives robust
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

    Prefer the canonical ``7+`` bucket when it already exists. If a caller
    supplies legacy exact numeric tail keys, aggregate every integer key >=7.
    When a score matrix is available, derive the canonical vector from that
    matrix and use it as the authoritative fallback. This prevents the old
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


def _total_bucket_index(bucket: str) -> int:
    """Map canonical total-goal bucket labels to an ordered integer index."""
    return 7 if bucket == "7+" else int(bucket)


def _plateau_label(primary: str, secondary: str) -> str:
    first, second = sorted((primary, secondary), key=_total_bucket_index)
    if second == "7+":
        return f"{first}—7+球平台"
    return f"{first}—{second}球平台"


def total_peak_diagnostics(total_goals: dict[str, Any]) -> dict[str, Any]:
    """Audit total-goals peak strength without hiding the actual modal result.

    The model Top-1 is always retained as the mathematical mode of P(T). When the
    top two buckets are separated by less than two percentage points, reporting
    additionally labels the result as a plateau/weak peak so the mode is not
    mistaken for a high-confidence single-goal prediction. This changes reporting
    only, never P(T).
    """
    canonical = {
        key: float(total_goals.get(key, 0.0) or 0.0)
        for key in ("0", "1", "2", "3", "4", "5", "6", "7+")
    }
    ranking = sorted(canonical.items(), key=lambda item: (-item[1], _total_bucket_index(item[0])))
    primary, secondary = ranking[0], ranking[1]
    gap = primary[1] - secondary[1]
    adjacent = abs(_total_bucket_index(primary[0]) - _total_bucket_index(secondary[0])) == 1

    if gap < 0.01:
        strength = "极弱Top-1"
    elif gap < 0.02:
        strength = "弱Top-1"
    elif gap < 0.04:
        strength = "中等Top-1"
    else:
        strength = "强Top-1"

    strong_single_peak = gap >= 0.02
    reporting_mode = "single_peak" if strong_single_peak else "plateau"
    plateau = _plateau_label(primary[0], secondary[0]) if adjacent and not strong_single_peak else None

    return {
        "primary": primary[0],
        "primary_probability": primary[1],
        "secondary": secondary[0],
        "secondary_probability": secondary[1],
        "gap": gap,
        "gap_percentage_points": gap * 100.0,
        "strength": strength,
        "adjacent_top_two": adjacent,
        "top_two_probability": primary[1] + secondary[1],
        "single_point_eligible": True,
        "single_point_status": "保留Top-1" if strong_single_peak else "弱峰保留Top-1",
        "reporting_mode": reporting_mode,
        "plateau_label": plateau,
        "interpretation": (
            f"{plateau}；仍保留{primary[0]}球作为数学Top-1，但不得表述为高置信单点。"
            if plateau
            else (
                f"Top-1为{primary[0]}球，但与Top-2差距不足2个百分点；保留Top-1并标记弱峰。"
                if not strong_single_peak
                else "Top-1与第二选择存在可见分离，但仍应同时报告完整0—7+分布。"
            )
        ),
    }
