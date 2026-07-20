#!/usr/bin/env python3
"""Standardized question-time audit wrapper for competitions outside formal registry.

This script NEVER creates probabilities from market odds. It accepts an externally
computed direct-total track + conditional allocation + unified score matrix, then
validates the matrix and standardizes runtime states, Asian-handicap/OU settlement,
price/EV reference analytics and conclusion priority.

Use case: a competition not yet registered in the 17 formal domains, where current
web-verified data is used for a one-off audit calculation. Formal model authority and
OOF/LOMO validation are not implied.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from decision_state_policy_v477 import apply_price_ev_state
from platform_core import (
    PlatformError,
    atomic_write_json,
    derive_score_marginals,
    settle_home_handicap,
    settle_over_total,
    top_scores,
)
from runtime_audit_policy_v477 import apply_runtime_audit_policies


def _valid_price(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 1.0


def _assess_market(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {
            "status": "不可用",
            "tradable_prices": False,
            "ev_gate": False,
            "complete_1x2": False,
            "complete_asian_handicap": False,
            "complete_total_goals": False,
            "synchronized": False,
        }
    one = snapshot.get("one_x_two") or {}
    ah = snapshot.get("asian_handicap") or {}
    ou = snapshot.get("total_goals") or {}
    complete_1x2 = all(_valid_price(one.get(k)) for k in ("home", "draw", "away"))
    complete_ah = (
        isinstance(ah.get("line"), (int, float))
        and _valid_price(ah.get("home"))
        and _valid_price(ah.get("away"))
    )
    complete_ou = (
        isinstance(ou.get("line"), (int, float))
        and _valid_price(ou.get("over"))
        and _valid_price(ou.get("under"))
    )
    sources = snapshot.get("sources")
    synchronized = bool(snapshot.get("synchronized"))
    tradable = bool(snapshot.get("tradable_prices", bool(sources)))
    ev_gate = complete_1x2 and complete_ah and complete_ou and synchronized and tradable
    return {
        "status": "通过" if ev_gate else "降级",
        "tradable_prices": tradable,
        "ev_gate": ev_gate,
        "complete_1x2": complete_1x2,
        "complete_asian_handicap": complete_ah,
        "complete_total_goals": complete_ou,
        "synchronized": synchronized,
    }


def _track_pass(track: Any) -> bool:
    if not isinstance(track, dict) or track.get("status") != "通过":
        return False
    required = ("algorithm", "input_evidence", "result", "audit")
    return all(track.get(key) is not None for key in required)


def _derive_line_market(matrix: list[dict[str, Any]], line: float, settlement_fn) -> dict[str, float]:
    result = {"win": 0.0, "push": 0.0, "loss": 0.0}
    for cell in matrix:
        settlement = settlement_fn(int(cell["home_goals"]), int(cell["away_goals"]), line)
        probability = float(cell["probability"])
        for key in result:
            result[key] += probability * settlement[key]
    return result


def build_external_audit(payload: dict[str, Any]) -> dict[str, Any]:
    identity = payload.get("match_identity")
    if not isinstance(identity, dict):
        raise PlatformError("external audit requires match_identity")

    calculation_audit = payload.get("calculation_audit") or {}
    direct_track = calculation_audit.get("direct_total_track")
    conditional_track = calculation_audit.get("conditional_allocation_track")
    if not _track_pass(direct_track):
        raise PlatformError("external audit requires a truly executed direct-total track with algorithm/input/result/audit")
    if not _track_pass(conditional_track):
        raise PlatformError("external audit requires a truly executed conditional-allocation track with algorithm/input/result/audit")

    matrix = payload.get("score_matrix")
    if not isinstance(matrix, list) or not matrix:
        raise PlatformError("external audit requires a non-empty unified score_matrix")
    marginals = derive_score_marginals(matrix)
    if abs(float(marginals["probability_sum"]) - 1.0) > 1e-8:
        raise PlatformError("external unified score matrix does not conserve probability")

    ranking = top_scores(matrix, 10)
    market_snapshot = payload.get("market_snapshot") or {}
    market_assessment = _assess_market(market_snapshot)
    context = {
        "match_identity": identity,
        "market_assessment": market_assessment,
        "original_market_snapshot": market_snapshot,
        "gates": {
            "formal_ev_execution_validated": bool(payload.get("formal_ev_execution_validated", False)),
            "exact_score_gate_criteria": payload.get("exact_score_gate_criteria"),
        },
    }

    derived: dict[str, Any] = {}
    ah = market_snapshot.get("asian_handicap") if isinstance(market_snapshot, dict) else None
    if isinstance(ah, dict) and isinstance(ah.get("line"), (int, float)):
        line = float(ah["line"])
        derived["home_handicap"] = {"line": line, **_derive_line_market(matrix, line, settle_home_handicap)}
    ou = market_snapshot.get("total_goals") if isinstance(market_snapshot, dict) else None
    if isinstance(ou, dict) and isinstance(ou.get("line"), (int, float)):
        line = float(ou["line"])
        derived["over_total"] = {"line": line, **_derive_line_market(matrix, line, settle_over_total)}

    total_rank = sorted(marginals["total_goals"].items(), key=lambda item: (-item[1], item[0]))
    model_validation = payload.get("model_validation") or {}
    oof_validated = bool(model_validation.get("competition_specific_oof_validated"))

    calculation = {
        "schema_version": "V4.7.7-external-question-time-audit",
        "rule_version": "V4.7.0",
        "formal_status": "EXTERNAL_AUDIT_NOT_FORMAL_DOMAIN_CORE",
        "module_states": {
            "direct_total_goals": "通过",
            "conditional_goal_difference": "通过",
            "unified_score_matrix": "通过",
            "market_coordination": "未启用",
            "price_ev_no_bet": "未启用",
            "external_model_validation": "通过" if oof_validated else "部分通过",
        },
        "probabilities": {
            "one_x_two": marginals["1x2"],
            "total_goals": marginals["total_goals"],
            "btts_yes": marginals["btts_yes"],
            "score_matrix": matrix,
        },
        "derived_markets": derived,
        "optimization_audit": calculation_audit.get("optimization_audit"),
        "calculation_audit": calculation_audit,
        "model_validation": model_validation,
        "conclusions": {
            "result_direction": max(marginals["1x2"], key=marginals["1x2"].get),
            "confidence_grade": payload.get("confidence_grade", "D" if not oof_validated else "C"),
            "top_score": ranking[0]["score"],
            "second_score": ranking[1]["score"] if len(ranking) > 1 else None,
            "top3_cumulative": sum(item["probability"] for item in ranking[:3]),
            "top1_top2_gap": ranking[0]["probability"] - ranking[1]["probability"] if len(ranking) > 1 else None,
            "total_goals_primary": total_rank[0][0],
            "total_goals_secondary": total_rank[1][0] if len(total_rank) > 1 else None,
            "price_status": "No Bet",
        },
        "external_audit_policy": (
            "one-off web-verified external audit; not a registered formal-domain model; "
            "no automatic promotion or formal weight"
        ),
    }

    calculation = apply_price_ev_state(context, calculation)
    calculation = apply_runtime_audit_policies(context, calculation)
    return calculation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = build_external_audit(payload)
        atomic_write_json(Path(args.output), result)
    except (PlatformError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
