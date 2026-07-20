#!/usr/bin/env python3
"""Read-only final-matrix diagnostics for total-goals Top-1/Top-2 peak strength.

This module never changes probabilities. It only derives the CURRENT-required
Top-1, Top-2, probability gap, and weak-peak-platform label from the final
0..7+ total-goals marginal after all validated runtime transforms.
"""
from __future__ import annotations

import copy
from typing import Any

TOTAL_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7+")
WEAK_PLATFORM_GAP = 0.02


def _rank_total_goals(total_goals: dict[str, Any]) -> list[tuple[str, float]]:
    index = {key: i for i, key in enumerate(TOTAL_KEYS)}
    values = []
    for key in TOTAL_KEYS:
        if key not in total_goals:
            raise ValueError(f"final total-goals marginal missing bucket {key}")
        values.append((key, float(total_goals[key])))
    return sorted(values, key=lambda item: (-item[1], index[item[0]]))


def apply_total_goals_peak_diagnostics(calculation: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(calculation)
    probabilities = output.get("probabilities") or {}
    total_goals = probabilities.get("total_goals")
    if not isinstance(total_goals, dict):
        output["total_goals_peak_audit"] = {
            "status": "不可用",
            "reason": "final total-goals marginal missing",
            "probability_mutation": False,
        }
        return output

    try:
        ranking = _rank_total_goals(total_goals)
    except (TypeError, ValueError) as exc:
        output["total_goals_peak_audit"] = {
            "status": "不可用",
            "reason": str(exc),
            "probability_mutation": False,
        }
        return output

    probability_sum = sum(value for _, value in ranking)
    if abs(probability_sum - 1.0) > 1e-8:
        output["total_goals_peak_audit"] = {
            "status": "失败",
            "reason": f"final total-goals marginal does not sum to 1: {probability_sum}",
            "probability_mutation": False,
        }
        return output

    top1_key, top1_p = ranking[0]
    top2_key, top2_p = ranking[1]
    gap = top1_p - top2_p
    weak_platform = gap < WEAK_PLATFORM_GAP
    peak_label = "峰值平台/弱Top-1" if weak_platform else "Top-1差距≥2pp"

    conclusions = output.setdefault("conclusions", {})
    conclusions.update({
        "total_goals_primary": top1_key,
        "total_goals_secondary": top2_key,
        "total_goals_top1_probability": top1_p,
        "total_goals_top2_probability": top2_p,
        "total_goals_top1_top2_gap": gap,
        "total_goals_peak_status": "WEAK_PEAK_PLATFORM" if weak_platform else "DISTINCT_TOP1",
        "total_goals_peak_label": peak_label,
        "total_goals_text": (
            f"最终总进球Top-1：{top1_key}球 {top1_p:.1%}；"
            f"Top-2：{top2_key}球 {top2_p:.1%}；差距{gap:.1%}；{peak_label}。"
        ),
    })
    output["total_goals_peak_audit"] = {
        "status": "通过",
        "method": "read_only_final_total_marginal_top2_gap",
        "probability_mutation": False,
        "top1": {"bucket": top1_key, "probability": top1_p},
        "top2": {"bucket": top2_key, "probability": top2_p},
        "gap": gap,
        "weak_platform_threshold": WEAK_PLATFORM_GAP,
        "peak_status": conclusions["total_goals_peak_status"],
        "probability_sum": probability_sum,
    }
    return output
