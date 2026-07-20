#!/usr/bin/env python3
"""Unified decision-state policy for price, EV and No Bet reporting.

This module separates three concepts that must never be conflated:
1) whether model probabilities / the unified matrix are usable;
2) whether current market prices are available and formally synchronized;
3) whether the final execution decision is Bet or No Bet.

No Bet is a decision outcome, not a module runtime state. A module can be
"部分通过" while the final decision remains No Bet.
"""
from __future__ import annotations

import math
from typing import Any


def _valid_price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 1.0 else None


def _fair_odds_binary_settlement(settlement: dict[str, Any]) -> float | None:
    win = float(settlement.get("win", 0.0) or 0.0)
    loss = float(settlement.get("loss", 0.0) or 0.0)
    if win <= 0.0:
        return None
    return 1.0 + loss / win


def _settlement_ev(settlement: dict[str, Any], odds: float) -> float:
    win = float(settlement.get("win", 0.0) or 0.0)
    loss = float(settlement.get("loss", 0.0) or 0.0)
    return win * (odds - 1.0) - loss


def _inverse_settlement(settlement: dict[str, Any]) -> dict[str, float]:
    return {
        "win": float(settlement.get("loss", 0.0) or 0.0),
        "push": float(settlement.get("push", 0.0) or 0.0),
        "loss": float(settlement.get("win", 0.0) or 0.0),
    }


def build_reference_price_analysis(context: dict[str, Any], calculation: dict[str, Any]) -> list[dict[str, Any]]:
    """Build auditable reference fair prices and EV from an existing matrix.

    These figures are reference analytics only unless a separate formal execution
    / LOMO gate has been explicitly validated. They must not auto-promote a bet.
    """
    snapshot = context.get("original_market_snapshot") or {}
    probabilities = calculation.get("probabilities") or {}
    one_x_two = probabilities.get("one_x_two") or {}
    derived = calculation.get("derived_markets") or {}
    output: list[dict[str, Any]] = []

    market_1x2 = snapshot.get("one_x_two") if isinstance(snapshot, dict) else None
    if isinstance(market_1x2, dict):
        for key, label in (("home", "1X2_HOME"), ("draw", "1X2_DRAW"), ("away", "1X2_AWAY")):
            odds = _valid_price(market_1x2.get(key))
            probability = one_x_two.get(key)
            try:
                probability = float(probability)
            except (TypeError, ValueError):
                probability = None
            if odds is None or probability is None or probability <= 0.0 or probability > 1.0:
                continue
            output.append({
                "market": label,
                "line": None,
                "model_probability": probability,
                "odds": odds,
                "fair_odds": 1.0 / probability,
                "reference_ev": probability * odds - 1.0,
                "formal_execution_eligible": False,
            })

    market_ah = snapshot.get("asian_handicap") if isinstance(snapshot, dict) else None
    derived_ah = derived.get("home_handicap")
    if isinstance(market_ah, dict) and isinstance(derived_ah, dict):
        home_odds = _valid_price(market_ah.get("home"))
        away_odds = _valid_price(market_ah.get("away"))
        line = derived_ah.get("line")
        home_settlement = {
            "win": float(derived_ah.get("win", 0.0) or 0.0),
            "push": float(derived_ah.get("push", 0.0) or 0.0),
            "loss": float(derived_ah.get("loss", 0.0) or 0.0),
        }
        away_settlement = _inverse_settlement(home_settlement)
        if home_odds is not None:
            output.append({
                "market": "AH_HOME",
                "line": line,
                "settlement": home_settlement,
                "odds": home_odds,
                "fair_odds": _fair_odds_binary_settlement(home_settlement),
                "reference_ev": _settlement_ev(home_settlement, home_odds),
                "formal_execution_eligible": False,
            })
        if away_odds is not None:
            output.append({
                "market": "AH_AWAY",
                "line": -float(line) if isinstance(line, (int, float)) else None,
                "settlement": away_settlement,
                "odds": away_odds,
                "fair_odds": _fair_odds_binary_settlement(away_settlement),
                "reference_ev": _settlement_ev(away_settlement, away_odds),
                "formal_execution_eligible": False,
            })

    market_ou = snapshot.get("total_goals") if isinstance(snapshot, dict) else None
    derived_ou = derived.get("over_total")
    if isinstance(market_ou, dict) and isinstance(derived_ou, dict):
        over_odds = _valid_price(market_ou.get("over"))
        under_odds = _valid_price(market_ou.get("under"))
        line = derived_ou.get("line")
        over_settlement = {
            "win": float(derived_ou.get("win", 0.0) or 0.0),
            "push": float(derived_ou.get("push", 0.0) or 0.0),
            "loss": float(derived_ou.get("loss", 0.0) or 0.0),
        }
        under_settlement = _inverse_settlement(over_settlement)
        if over_odds is not None:
            output.append({
                "market": "OU_OVER",
                "line": line,
                "settlement": over_settlement,
                "odds": over_odds,
                "fair_odds": _fair_odds_binary_settlement(over_settlement),
                "reference_ev": _settlement_ev(over_settlement, over_odds),
                "formal_execution_eligible": False,
            })
        if under_odds is not None:
            output.append({
                "market": "OU_UNDER",
                "line": line,
                "settlement": under_settlement,
                "odds": under_odds,
                "fair_odds": _fair_odds_binary_settlement(under_settlement),
                "reference_ev": _settlement_ev(under_settlement, under_odds),
                "formal_execution_eligible": False,
            })

    return output


def apply_price_ev_state(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    """Apply one canonical runtime state machine to price / EV / No Bet.

    Runtime semantics:
    - 不可用: required model matrix or usable prices are absent; EV cannot be computed.
    - 部分通过: model and at least one usable price exist; reference fair price/EV can be
      computed, but the formal synchronized-market and/or execution-validation gate is not complete.
    - 通过: reserved for a future explicitly validated formal execution gate.
    - No Bet: final decision outcome only; never used as a runtime module state.
    """
    states = calculation.setdefault("module_states", {})
    conclusions = calculation.setdefault("conclusions", {})
    market = context.get("market_assessment") or {}
    matrix_state = states.get("unified_score_matrix")
    reference = build_reference_price_analysis(context, calculation) if matrix_state == "通过" else []
    calculation["price_analysis"] = reference

    has_reference_prices = bool(reference)
    tradable = bool(market.get("tradable_prices"))
    formal_market_gate = bool(market.get("ev_gate"))
    formal_execution_gate = bool(
        context.get("gates", {}).get("formal_ev_execution_validated")
        or calculation.get("formal_ev_execution_validated")
    )

    if matrix_state != "通过":
        states["price_ev_no_bet"] = "不可用"
        conclusions["price_status"] = "No Bet" if tradable else "价格不可用"
        conclusions["price_ev_reason"] = "统一比分矩阵未通过，EV不可用。"
        conclusions["reference_ev_available"] = False
        conclusions["formal_ev_available"] = False
        return calculation

    if not has_reference_prices:
        states["price_ev_no_bet"] = "不可用"
        conclusions["price_status"] = "价格不可用"
        conclusions["price_ev_reason"] = "没有可用于结算的实际价格，无法计算参考EV。"
        conclusions["reference_ev_available"] = False
        conclusions["formal_ev_available"] = False
        return calculation

    conclusions["reference_ev_available"] = True
    conclusions["formal_ev_available"] = bool(formal_market_gate and formal_execution_gate)

    if formal_market_gate and formal_execution_gate:
        states["price_ev_no_bet"] = "通过"
        # A separate execution policy may later promote a value candidate. Until then,
        # the decision remains conservative and explicit.
        conclusions.setdefault("price_status", "No Bet")
        conclusions["price_ev_reason"] = "完整同步市场与正式执行验证门均通过；当前决策由价格阈值决定。"
    else:
        states["price_ev_no_bet"] = "部分通过"
        conclusions["price_status"] = "No Bet"
        missing = []
        if not formal_market_gate:
            missing.append("完整同步可成交市场门")
        if not formal_execution_gate:
            missing.append("逐赛事域LOMO/正式执行验证门")
        conclusions["price_ev_reason"] = (
            "模型概率和实际价格可用，已计算参考公平价与参考EV；"
            + "、".join(missing)
            + "未通过，因此正式执行No Bet。"
        )

    return calculation
