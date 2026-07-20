#!/usr/bin/env python3
"""Auditable V4.7 market-coordination runtime.

This module performs a genuine KL projection of the final unified score-matrix
prior onto synchronized question-time market constraints.  It deliberately
separates *algorithm execution* from *formal probability activation*:

- a complete synchronized 1X2/AH/OU snapshot may run the coordination candidate;
- without a competition/season LOMO promotion receipt, the candidate has weight 0
  and the formal matrix is unchanged;
- formal EV remains separately gated by the LOMO execution gate;
- every run records prior, constraints, objective, iterations, convergence,
  residuals and probability conservation.

The solver uses the exponential-family dual of min KL(q || p) under linear
expectation constraints.  It is dependency-free and fail-closed.
"""
from __future__ import annotations

import copy
import math
from typing import Any

from football_v460_engine import conditional_goal_difference_by_total, minimum_score_set
from platform_core import (
    PlatformError,
    derive_score_marginals,
    settle_home_handicap,
    settle_over_total,
    sha256_json,
    top_scores,
)

TOLERANCE = 1e-8
MAX_ITERATIONS = 80


def _valid_odds(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PlatformError(f"invalid decimal odds: {value!r}") from exc
    if not math.isfinite(number) or number <= 1.0:
        raise PlatformError(f"decimal odds must be >1: {number}")
    return number


def _two_way_no_vig(a: float, b: float) -> tuple[float, float]:
    ia, ib = 1.0 / a, 1.0 / b
    total = ia + ib
    return ia / total, ib / total


def _three_way_no_vig(home: float, draw: float, away: float) -> dict[str, float]:
    raw = {"home": 1.0 / home, "draw": 1.0 / draw, "away": 1.0 / away}
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}


def _solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Small dense Gaussian elimination with pivoting."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise PlatformError("market coordination dual Hessian is singular")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
        divisor = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= divisor
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            if factor == 0.0:
                continue
            for j in range(col, n + 1):
                a[row][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]


def _distribution(prior: list[float], features: list[list[float]], lambdas: list[float]) -> list[float]:
    logits = []
    for p, row in zip(prior, features):
        if p <= 0.0:
            logits.append(float("-inf"))
            continue
        logits.append(math.log(p) + sum(lam * value for lam, value in zip(lambdas, row)))
    finite = [value for value in logits if math.isfinite(value)]
    if not finite:
        raise PlatformError("market coordination prior has no positive support")
    maximum = max(finite)
    weights = [0.0 if not math.isfinite(value) else math.exp(value - maximum) for value in logits]
    total = sum(weights)
    if total <= 0.0 or not math.isfinite(total):
        raise PlatformError("market coordination normalization failed")
    return [weight / total for weight in weights]


def _moments(q: list[float], features: list[list[float]]) -> tuple[list[float], list[list[float]]]:
    k = len(features[0])
    means = [sum(prob * row[j] for prob, row in zip(q, features)) for j in range(k)]
    covariance = [[0.0 for _ in range(k)] for _ in range(k)]
    for prob, row in zip(q, features):
        centered = [row[j] - means[j] for j in range(k)]
        for i in range(k):
            for j in range(k):
                covariance[i][j] += prob * centered[i] * centered[j]
    for i in range(k):
        covariance[i][i] += 1e-10
    return means, covariance


def _max_abs(values: list[float]) -> float:
    return max((abs(value) for value in values), default=0.0)


def _kl_project(
    prior: list[float],
    features: list[list[float]],
    targets: list[float],
) -> tuple[list[float], dict[str, Any]]:
    if not features or not features[0]:
        raise PlatformError("market coordination requires at least one constraint")
    lambdas = [0.0] * len(targets)
    q = prior[:]
    converged = False
    iterations = 0
    residuals: list[float] = []

    for iteration in range(1, MAX_ITERATIONS + 1):
        iterations = iteration
        q = _distribution(prior, features, lambdas)
        means, hessian = _moments(q, features)
        residuals = [mean - target for mean, target in zip(means, targets)]
        current = _max_abs(residuals)
        if current <= TOLERANCE:
            converged = True
            break
        step = _solve_linear(hessian, residuals)
        accepted = False
        alpha = 1.0
        for _ in range(20):
            candidate_lambdas = [lam - alpha * delta for lam, delta in zip(lambdas, step)]
            candidate_q = _distribution(prior, features, candidate_lambdas)
            candidate_means, _ = _moments(candidate_q, features)
            candidate_residuals = [mean - target for mean, target in zip(candidate_means, targets)]
            if _max_abs(candidate_residuals) < current:
                lambdas = candidate_lambdas
                q = candidate_q
                residuals = candidate_residuals
                accepted = True
                break
            alpha *= 0.5
        if not accepted:
            break

    q = _distribution(prior, features, lambdas)
    means, _ = _moments(q, features)
    residuals = [mean - target for mean, target in zip(means, targets)]
    converged = converged or _max_abs(residuals) <= TOLERANCE
    objective = 0.0
    for qi, pi in zip(q, prior):
        if qi > 0.0 and pi > 0.0:
            objective += qi * math.log(qi / pi)
    return q, {
        "converged": converged,
        "iterations": iterations,
        "lambdas": lambdas,
        "achieved_moments": means,
        "residuals": residuals,
        "max_constraint_residual": _max_abs(residuals),
        "kl_q_to_prior": objective,
    }


def _market_constraints(snapshot: dict[str, Any], matrix: list[dict[str, Any]]) -> tuple[list[list[float]], list[float], list[dict[str, Any]]]:
    one = snapshot.get("one_x_two") or {}
    ah = snapshot.get("asian_handicap") or {}
    ou = snapshot.get("total_goals") or {}
    if not isinstance(ah.get("line"), (int, float)) or not isinstance(ou.get("line"), (int, float)):
        raise PlatformError("complete market coordination requires explicit AH and OU lines")

    one_fair = _three_way_no_vig(
        _valid_odds(one.get("home")), _valid_odds(one.get("draw")), _valid_odds(one.get("away"))
    )
    ah_home, _ = _two_way_no_vig(_valid_odds(ah.get("home")), _valid_odds(ah.get("away")))
    ou_over, _ = _two_way_no_vig(_valid_odds(ou.get("over")), _valid_odds(ou.get("under")))
    ah_line = float(ah["line"])
    ou_line = float(ou["line"])

    features: list[list[float]] = []
    for cell in matrix:
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        ah_settlement = settle_home_handicap(home, away, ah_line)
        ou_settlement = settle_over_total(home, away, ou_line)
        features.append([
            1.0 if home > away else 0.0,
            1.0 if home == away else 0.0,
            (1.0 - ah_home) * ah_settlement["win"] - ah_home * ah_settlement["loss"],
            (1.0 - ou_over) * ou_settlement["win"] - ou_over * ou_settlement["loss"],
        ])
    targets = [one_fair["home"], one_fair["draw"], 0.0, 0.0]
    constraints = [
        {"name": "1X2_HOME", "target": one_fair["home"], "type": "marginal_probability"},
        {"name": "1X2_DRAW", "target": one_fair["draw"], "type": "marginal_probability"},
        {
            "name": "AH_HOME_FAIR_SETTLEMENT",
            "line": ah_line,
            "target": 0.0,
            "de_vig_home_cover_share": ah_home,
            "type": "linear_fair_settlement_constraint",
        },
        {
            "name": "OU_OVER_FAIR_SETTLEMENT",
            "line": ou_line,
            "target": 0.0,
            "de_vig_over_share": ou_over,
            "type": "linear_fair_settlement_constraint",
        },
    ]
    return features, targets, constraints


def _settlement_market(matrix: list[dict[str, Any]], line: float, fn) -> dict[str, float]:
    result = {"win": 0.0, "push": 0.0, "loss": 0.0}
    for cell in matrix:
        settlement = fn(int(cell["home_goals"]), int(cell["away_goals"]), line)
        probability = float(cell["probability"])
        for key in result:
            result[key] += probability * float(settlement[key])
    return result


def _candidate_summary(matrix: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, Any]:
    marginals = derive_score_marginals(matrix)
    ranked = top_scores(matrix, 5)
    ah = snapshot.get("asian_handicap") or {}
    ou = snapshot.get("total_goals") or {}
    summary = {
        "one_x_two": marginals["1x2"],
        "total_goals": marginals["total_goals"],
        "btts_yes": marginals["btts_yes"],
        "top_scores": ranked,
    }
    if isinstance(ah.get("line"), (int, float)):
        summary["home_handicap"] = {
            "line": float(ah["line"]),
            **_settlement_market(matrix, float(ah["line"]), settle_home_handicap),
        }
    if isinstance(ou.get("line"), (int, float)):
        summary["over_total"] = {
            "line": float(ou["line"]),
            **_settlement_market(matrix, float(ou["line"]), settle_over_total),
        }
    return summary


def _apply_formal_matrix(calculation: dict[str, Any], matrix: list[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    marginals = derive_score_marginals(matrix)
    ranked = top_scores(matrix, 10)
    calculation.setdefault("probabilities", {})["score_matrix"] = matrix
    calculation["probabilities"]["one_x_two"] = marginals["1x2"]
    calculation["probabilities"]["total_goals"] = marginals["total_goals"]
    calculation["probabilities"]["btts_yes"] = marginals["btts_yes"]
    calculation["conditional_goal_difference_audit"] = conditional_goal_difference_by_total(matrix)
    calculation["score_set_audit"] = {
        "80": minimum_score_set(matrix, 0.80),
        "90": minimum_score_set(matrix, 0.90),
    }
    derived = calculation.setdefault("derived_markets", {})
    ah = snapshot.get("asian_handicap") or {}
    ou = snapshot.get("total_goals") or {}
    if isinstance(ah.get("line"), (int, float)):
        derived["home_handicap"] = {
            "line": float(ah["line"]),
            **_settlement_market(matrix, float(ah["line"]), settle_home_handicap),
        }
    if isinstance(ou.get("line"), (int, float)):
        derived["over_total"] = {
            "line": float(ou["line"]),
            **_settlement_market(matrix, float(ou["line"]), settle_over_total),
        }
    conclusions = calculation.setdefault("conclusions", {})
    total_rank = sorted(marginals["total_goals"].items(), key=lambda item: (-item[1], item[0]))
    conclusions["result_direction"] = max(marginals["1x2"], key=marginals["1x2"].get)
    conclusions["result_text"] = (
        f"90分钟市场协调后概率：主胜{marginals['1x2']['home']:.1%}、"
        f"平局{marginals['1x2']['draw']:.1%}、客胜{marginals['1x2']['away']:.1%}。"
    )
    conclusions["total_goals_primary"] = total_rank[0][0]
    conclusions["total_goals_secondary"] = total_rank[1][0]
    conclusions["top_score"] = ranked[0]["score"] if ranked else None
    conclusions["second_score"] = ranked[1]["score"] if len(ranked) > 1 else None
    conclusions["top3_cumulative"] = sum(item["probability"] for item in ranked[:3])
    conclusions["top1_top2_gap"] = (
        ranked[0]["probability"] - ranked[1]["probability"] if len(ranked) > 1 else None
    )


def apply_market_coordination_runtime(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(calculation)
    snapshot = context.get("original_market_snapshot") or {}
    market = context.get("market_assessment") or {}
    gates = context.get("gates") or {}
    matrix = output.get("probabilities", {}).get("score_matrix")

    candidate_gate = bool(gates.get("market_coordination_candidate_may_run"))
    if not candidate_gate or not isinstance(matrix, list) or not matrix:
        output["market_coordination_runtime_audit"] = {
            "status": "不可用",
            "reason": "完整同步1X2/AH/OU市场快照不可用或统一比分矩阵缺失",
            "formal_applied": False,
            "probability_mutation": False,
        }
        output.setdefault("module_states", {})["market_coordination"] = "不可用"
        return output

    prior = [float(cell["probability"]) for cell in matrix]
    total = sum(prior)
    if abs(total - 1.0) > 1e-8:
        raise PlatformError("market coordination prior matrix is not probability-conserving")

    try:
        features, targets, constraints = _market_constraints(snapshot, matrix)
        q, solver = _kl_project(prior, features, targets)
    except PlatformError as exc:
        output["market_coordination_runtime_audit"] = {
            "status": "失败",
            "reason": str(exc),
            "formal_applied": False,
            "probability_mutation": False,
        }
        output.setdefault("module_states", {})["market_coordination"] = "失败"
        return output

    coordinated_matrix = [
        {
            **cell,
            "probability": probability,
        }
        for cell, probability in zip(matrix, q)
    ]
    probability_sum = sum(q)
    formal_applied = bool(gates.get("formal_market_coordination_may_apply")) and solver["converged"]
    audit = {
        "prior": {
            "score_matrix_sha256": sha256_json(matrix),
            "probability_sum": total,
            "one_x_two": derive_score_marginals(matrix)["1x2"],
        },
        "constraints": constraints,
        "objective": "minimize_KL(q||prior)_subject_to_synchronized_market_constraints",
        "solver": "damped_newton_exponential_family_dual",
        "converged": solver["converged"],
        "iterations": solver["iterations"],
        "dual_parameters": solver["lambdas"],
        "achieved_moments": solver["achieved_moments"],
        "constraint_residuals": solver["residuals"],
        "max_constraint_residual": solver["max_constraint_residual"],
        "kl_q_to_prior": solver["kl_q_to_prior"],
        "probability_sum": probability_sum,
        "market_snapshot_complete": bool(market.get("snapshot_complete_gate")),
        "formal_applied": formal_applied,
        "candidate_only": not formal_applied,
        "formal_weight": 1.0 if formal_applied else 0.0,
        "lomo_status": market.get("lomo_validation_status"),
    }
    output["optimization_audit"] = audit
    output["market_coordination_candidate"] = _candidate_summary(coordinated_matrix, snapshot)
    output["market_coordination_runtime_audit"] = {
        "status": "通过" if solver["converged"] else "失败",
        "formal_applied": formal_applied,
        "probability_mutation": formal_applied,
        "candidate_probability_sum": probability_sum,
        "candidate_matrix_sha256": sha256_json(coordinated_matrix),
        "policy": (
            "同步市场下真实KL投影已运行；缺LOMO正式回执时仅保留候选审计，正式矩阵不变。"
            if not formal_applied
            else "同步市场KL投影及赛事域LOMO正式门均通过，协调矩阵进入正式中心。"
        ),
    }
    if not solver["converged"] or solver["max_constraint_residual"] > TOLERANCE or abs(probability_sum - 1.0) > 1e-8:
        output.setdefault("module_states", {})["market_coordination"] = "失败"
        return output

    if formal_applied:
        _apply_formal_matrix(output, coordinated_matrix, snapshot)
        output.setdefault("module_states", {})["market_coordination"] = "通过"
    else:
        output.setdefault("module_states", {})["market_coordination"] = "部分通过"
    return output
