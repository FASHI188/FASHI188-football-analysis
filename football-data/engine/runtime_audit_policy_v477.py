#!/usr/bin/env python3
"""Runtime audit policies for V4.7.x football decision receipts.

This module does not alter model probabilities. It fixes runtime semantics for:
- EXACT gate status without a hard-coded permanent False;
- market-coordination status based only on an actual optimization audit;
- conclusion priority: result + Asian handicap -> score -> total goals -> price/EV.

No numeric EXACT threshold is invented here. A pass/fail EXACT confidence gate is
only evaluated when explicit frozen criteria are supplied by the active runtime
context or calculation artifact. Otherwise the score matrix may still publish its
Top-1/Top-2, while EXACT confidence status is "未启用".
"""
from __future__ import annotations

import math
from typing import Any


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _set_score_text(conclusions: dict[str, Any], status: str) -> None:
    top_score = conclusions.get("top_score")
    if not top_score:
        conclusions["score_text"] = "精确比分不可用。"
        conclusions["score_label"] = "精确比分不可用"
        return
    if status == "通过":
        conclusions["score_text"] = f"正式Top-1比分 {top_score}；EXACT门控通过。"
        conclusions["score_label"] = "正式Top-1比分"
    elif status == "失败":
        conclusions["score_text"] = f"模型Top-1比分 {top_score}；EXACT置信门控失败，但矩阵Top-1仍保留。"
        conclusions["score_label"] = "模型Top-1比分"
    elif status == "未启用":
        conclusions["score_text"] = f"模型Top-1比分 {top_score}；EXACT置信门控未启用，因缺少冻结阈值。"
        conclusions["score_label"] = "模型Top-1比分"
    else:
        conclusions["score_text"] = "精确比分不可用。"
        conclusions["score_label"] = "精确比分不可用"


def apply_exact_gate_state(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    """Replace the legacy permanently-false EXACT flag with an auditable state.

    Publishing a matrix Top-1 is separate from claiming a high-confidence EXACT
    gate pass. The latter requires explicit frozen criteria; absent such criteria,
    the gate is marked 未启用 rather than silently failing every match.
    """
    states = calculation.setdefault("module_states", {})
    conclusions = calculation.setdefault("conclusions", {})
    matrix_state = states.get("unified_score_matrix")

    if matrix_state != "通过":
        conclusions["exact_gate"] = None
        conclusions["exact_gate_status"] = "不可用"
        conclusions["exact_gate_reason"] = "统一比分矩阵未通过，EXACT门控不可用。"
        _set_score_text(conclusions, "不可用")
        return calculation

    top_score = conclusions.get("top_score")
    second_score = conclusions.get("second_score")
    top_gap = _as_float(conclusions.get("top1_top2_gap"))
    top3 = _as_float(conclusions.get("top3_cumulative"))

    criteria = calculation.get("exact_gate_criteria")
    if not isinstance(criteria, dict):
        criteria = context.get("gates", {}).get("exact_score_gate_criteria")

    if not isinstance(criteria, dict) or not criteria:
        conclusions["exact_gate"] = None
        conclusions["exact_gate_status"] = "未启用"
        conclusions["exact_gate_reason"] = (
            "统一比分矩阵可用并保留Top-1/Top-2，但当前正式规则未提供冻结的数值EXACT阈值；"
            "不得把缺失阈值硬编码成永久False，也不得自行发明阈值。"
        )
        _set_score_text(conclusions, "未启用")
        return calculation

    required = ("min_top1_probability", "min_top1_top2_gap", "min_top3_cumulative")
    if any(key not in criteria for key in required):
        conclusions["exact_gate"] = None
        conclusions["exact_gate_status"] = "不可用"
        conclusions["exact_gate_reason"] = "EXACT门控配置不完整，拒绝部分阈值判断。"
        _set_score_text(conclusions, "不可用")
        return calculation

    matrix = calculation.get("probabilities", {}).get("score_matrix")
    if not isinstance(matrix, list) or not matrix or not top_score:
        conclusions["exact_gate"] = None
        conclusions["exact_gate_status"] = "不可用"
        conclusions["exact_gate_reason"] = "比分矩阵或Top-1缺失。"
        _set_score_text(conclusions, "不可用")
        return calculation

    top_probability = None
    for cell in matrix:
        if not isinstance(cell, dict):
            continue
        score = f"{cell.get('home_goals')}-{cell.get('away_goals')}"
        if score == top_score:
            top_probability = _as_float(cell.get("probability"))
            break

    thresholds = {key: _as_float(criteria.get(key)) for key in required}
    if top_probability is None or top_gap is None or top3 is None or any(v is None for v in thresholds.values()):
        conclusions["exact_gate"] = None
        conclusions["exact_gate_status"] = "不可用"
        conclusions["exact_gate_reason"] = "EXACT门控所需概率或阈值不可解析。"
        _set_score_text(conclusions, "不可用")
        return calculation

    passed = (
        top_probability >= thresholds["min_top1_probability"]
        and top_gap >= thresholds["min_top1_top2_gap"]
        and top3 >= thresholds["min_top3_cumulative"]
    )
    conclusions["exact_gate"] = passed
    conclusions["exact_gate_status"] = "通过" if passed else "失败"
    conclusions["exact_gate_reason"] = {
        "top1": top_score,
        "top2": second_score,
        "top1_probability": top_probability,
        "top1_top2_gap": top_gap,
        "top3_cumulative": top3,
        "criteria": thresholds,
    }
    _set_score_text(conclusions, conclusions["exact_gate_status"])
    return calculation


def apply_market_coordination_state(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    """Set market-coordination runtime state from actual optimization evidence only."""
    states = calculation.setdefault("module_states", {})
    audit = calculation.get("optimization_audit")
    market = context.get("market_assessment") or {}

    if not isinstance(audit, dict):
        states["market_coordination"] = "未启用"
        calculation["market_coordination_reason"] = (
            "本场没有实际优化记录；不得因存在市场赔率就宣称已运行KL、最大熵或IPF。"
        )
        return calculation

    prior = audit.get("prior")
    constraints = audit.get("constraints")
    objective = audit.get("objective")
    converged = audit.get("converged")
    if converged is None:
        converged = audit.get("convergence_status") in {"converged", "通过", "PASS", True}
    residual = _as_float(audit.get("max_constraint_residual"))
    if residual is None:
        residual = _as_float(audit.get("max_residual"))
    probability_sum = _as_float(audit.get("probability_sum"))

    complete_record = (
        prior is not None
        and constraints is not None
        and objective is not None
        and converged is True
        and residual is not None
        and probability_sum is not None
    )

    if not complete_record:
        states["market_coordination"] = "不可用"
        calculation["market_coordination_reason"] = (
            "存在optimization_audit字段但先验、约束、目标函数、收敛状态、残差或概率守恒记录不完整。"
        )
        return calculation

    passed = residual <= 1e-6 and abs(probability_sum - 1.0) <= 1e-8
    states["market_coordination"] = "通过" if passed else "失败"
    calculation["market_coordination_reason"] = (
        "实际优化审计完整且收敛残差/概率守恒通过。"
        if passed
        else "实际优化已运行，但残差或概率守恒未通过。"
    )
    calculation["market_coordination_market_gate"] = bool(market.get("ev_gate"))
    return calculation


def apply_conclusion_priority(calculation: dict[str, Any]) -> dict[str, Any]:
    """Create one canonical display order without changing any probability."""
    probabilities = calculation.get("probabilities") or {}
    conclusions = calculation.setdefault("conclusions", {})
    derived = calculation.get("derived_markets") or {}
    one_x_two = probabilities.get("one_x_two") or {}
    handicap = derived.get("home_handicap") if isinstance(derived, dict) else None
    totals = probabilities.get("total_goals") or {}

    result_block = {
        "one_x_two": one_x_two,
        "result_direction": conclusions.get("result_direction"),
        "confidence_grade": conclusions.get("confidence_grade"),
    }
    if isinstance(handicap, dict):
        result_block["asian_handicap"] = handicap

    score_block = {
        "top_score": conclusions.get("top_score"),
        "second_score": conclusions.get("second_score"),
        "top3_cumulative": conclusions.get("top3_cumulative"),
        "top1_top2_gap": conclusions.get("top1_top2_gap"),
        "exact_gate": conclusions.get("exact_gate"),
        "exact_gate_status": conclusions.get("exact_gate_status"),
    }

    total_block = {
        "distribution": totals,
        "primary": conclusions.get("total_goals_display_primary", conclusions.get("total_goals_primary")),
        "secondary": conclusions.get("total_goals_display_secondary", conclusions.get("total_goals_secondary")),
        "peak_strength": conclusions.get("total_goals_peak_strength"),
        "plateau_label": conclusions.get("total_goals_plateau_label"),
    }

    calculation["conclusion_priority"] = [
        "result_and_asian_handicap",
        "score",
        "total_goals",
        "price_ev_no_bet",
    ]
    calculation["priority_conclusions"] = {
        "result_and_asian_handicap": result_block,
        "score": score_block,
        "total_goals": total_block,
        "price_ev_no_bet": {
            "module_state": calculation.get("module_states", {}).get("price_ev_no_bet"),
            "decision": conclusions.get("price_status"),
            "reason": conclusions.get("price_ev_reason"),
        },
    }
    return calculation


def apply_runtime_audit_policies(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    apply_exact_gate_state(context, calculation)
    apply_market_coordination_state(context, calculation)
    apply_conclusion_priority(calculation)
    return calculation
