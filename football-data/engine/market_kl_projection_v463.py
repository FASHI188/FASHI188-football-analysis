#!/usr/bin/env python3
"""Auditable KL market projection and leave-one-market-out (LOMO) helpers.

The module is deliberately not auto-enabled in the formal center.  It provides a
real executable optimization layer with explicit prior, constraints, objective,
convergence and residuals.  Promotion still requires time-ordered historical
validation on timestamped synchronized market snapshots.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from platform_core import PlatformError, derive_score_marginals, score_matrix_rows, settle_home_handicap, settle_over_total

TOLERANCE = 1e-9
MAX_ITERATIONS = 80


def _fair_two_way(price_a: float, price_b: float) -> tuple[float, float]:
    a, b = float(price_a), float(price_b)
    if not (math.isfinite(a) and math.isfinite(b) and a > 1.0 and b > 1.0):
        raise PlatformError("two-way decimal prices must be finite and > 1")
    ia, ib = 1.0 / a, 1.0 / b
    total = ia + ib
    return ia / total, ib / total


def _fair_three_way(home: float, draw: float, away: float) -> dict[str, float]:
    values = [float(home), float(draw), float(away)]
    if not all(math.isfinite(value) and value > 1.0 for value in values):
        raise PlatformError("1X2 decimal prices must be finite and > 1")
    inverses = [1.0 / value for value in values]
    total = sum(inverses)
    return {key: value / total for key, value in zip(("home", "draw", "away"), inverses)}


def _solve_linear(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    n = len(rhs)
    augmented = [list(matrix[i]) + [rhs[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            raise PlatformError("KL dual Hessian is singular")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        scale = augmented[col][col]
        augmented[col] = [value / scale for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            if abs(factor) <= 1e-18:
                continue
            augmented[row] = [left - factor * right for left, right in zip(augmented[row], augmented[col])]
    return [augmented[row][-1] for row in range(n)]


def _constraint_rows(snapshot: dict[str, Any], include: set[str]) -> tuple[list[str], list[list[float]], list[float], list[tuple[int, int, float]]]:
    # This helper is completed in project_market because score rows are needed.
    raise RuntimeError("internal helper must not be called directly")


def project_market(
    prior_matrix: list[dict[str, Any]],
    market_snapshot: dict[str, Any],
    *,
    include: Iterable[str] = ("1x2", "ah", "ou"),
    tolerance: float = TOLERANCE,
    max_iterations: int = MAX_ITERATIONS,
) -> dict[str, Any]:
    rows = list(score_matrix_rows(prior_matrix))
    if not rows:
        raise PlatformError("KL projection requires a non-empty prior matrix")
    prior_sum = sum(probability for _, _, probability in rows)
    if abs(prior_sum - 1.0) > 1e-8:
        raise PlatformError(f"KL prior probability sum is {prior_sum}, not 1")
    include_set = {str(item).lower() for item in include}

    names: list[str] = []
    features: list[list[float]] = []
    targets: list[float] = []

    def add_constraint(name: str, values: list[float], target: float) -> None:
        names.append(name)
        features.append(values)
        targets.append(float(target))

    if "1x2" in include_set:
        one = market_snapshot.get("one_x_two")
        if isinstance(one, dict):
            fair = _fair_three_way(one["home"], one["draw"], one["away"])
            add_constraint("1x2_home", [1.0 if home > away else 0.0 for home, away, _ in rows], fair["home"])
            add_constraint("1x2_draw", [1.0 if home == away else 0.0 for home, away, _ in rows], fair["draw"])

    if "ah" in include_set:
        ah = market_snapshot.get("asian_handicap")
        if isinstance(ah, dict) and isinstance(ah.get("line"), (int, float)):
            fair_home, _ = _fair_two_way(ah["home"], ah["away"])
            line = float(ah["line"])
            values = []
            for home, away, _ in rows:
                settlement = settle_home_handicap(home, away, line)
                # Fair two-way odds identify the conditional share of winning
                # stake among non-push stake.  Quarter-line half outcomes are
                # already represented by fractional win/push/loss weights.
                values.append(settlement["win"] - fair_home * (settlement["win"] + settlement["loss"]))
            add_constraint("asian_handicap_conditional_fair_share", values, 0.0)

    if "ou" in include_set:
        ou = market_snapshot.get("total_goals")
        if isinstance(ou, dict) and isinstance(ou.get("line"), (int, float)):
            fair_over, _ = _fair_two_way(ou["over"], ou["under"])
            line = float(ou["line"])
            values = []
            for home, away, _ in rows:
                settlement = settle_over_total(home, away, line)
                values.append(settlement["win"] - fair_over * (settlement["win"] + settlement["loss"]))
            add_constraint("over_under_conditional_fair_share", values, 0.0)

    if not features:
        raise PlatformError("no usable market constraints supplied for KL projection")

    probabilities = [probability for _, _, probability in rows]
    lambdas = [0.0] * len(features)
    converged = False
    iterations = 0
    residuals: list[float] = []

    for iteration in range(1, int(max_iterations) + 1):
        iterations = iteration
        logits = [
            math.log(max(1e-300, probabilities[index]))
            + sum(lambdas[j] * features[j][index] for j in range(len(features)))
            for index in range(len(rows))
        ]
        maximum = max(logits)
        weights = [math.exp(value - maximum) for value in logits]
        denominator = sum(weights)
        q = [weight / denominator for weight in weights]
        expectations = [sum(q[i] * feature[i] for i in range(len(rows))) for feature in features]
        residuals = [expectations[j] - targets[j] for j in range(len(features))]
        if max(abs(value) for value in residuals) <= tolerance:
            converged = True
            break
        hessian: list[list[float]] = []
        for a, fa in enumerate(features):
            row_values = []
            for b, fb in enumerate(features):
                covariance = sum(q[i] * fa[i] * fb[i] for i in range(len(rows))) - expectations[a] * expectations[b]
                row_values.append(covariance + (1e-10 if a == b else 0.0))
            hessian.append(row_values)
        try:
            step = _solve_linear(hessian, residuals)
        except PlatformError:
            break
        # Damp Newton steps to remain stable near redundant constraints.
        scale = max(1.0, max(abs(value) for value in step) / 5.0)
        lambdas = [value - delta / scale for value, delta in zip(lambdas, step)]

    logits = [
        math.log(max(1e-300, probabilities[index]))
        + sum(lambdas[j] * features[j][index] for j in range(len(features)))
        for index in range(len(rows))
    ]
    maximum = max(logits)
    weights = [math.exp(value - maximum) for value in logits]
    denominator = sum(weights)
    q = [weight / denominator for weight in weights]
    final_expectations = [sum(q[i] * feature[i] for i in range(len(rows))) for feature in features]
    final_residuals = [final_expectations[j] - targets[j] for j in range(len(features))]
    max_residual = max(abs(value) for value in final_residuals)
    converged = converged or max_residual <= tolerance

    projected = [
        {"home_goals": home, "away_goals": away, "probability": q[index]}
        for index, (home, away, _) in enumerate(rows)
    ]
    kl = sum(qi * math.log(max(1e-300, qi) / max(1e-300, pi)) for qi, pi in zip(q, probabilities))
    marginals = derive_score_marginals(projected)
    return {
        "matrix": projected,
        "audit": {
            "method": "minimum_KL_I_projection_exponential_family",
            "objective": "minimize KL(q||p) subject to linear de-vigged market constraints",
            "prior_probability_sum": prior_sum,
            "final_probability_sum": marginals["probability_sum"],
            "constraint_names": names,
            "constraint_targets": targets,
            "constraint_expectations": final_expectations,
            "constraint_residuals": {name: residual for name, residual in zip(names, final_residuals)},
            "max_abs_constraint_residual": max_residual,
            "dual_parameters": lambdas,
            "iterations": iterations,
            "max_iterations": int(max_iterations),
            "tolerance": tolerance,
            "converged": converged,
            "kl_q_to_prior": kl,
            "included_markets": sorted(include_set),
        },
        "marginals": marginals,
    }


def lomo_projections(prior_matrix: list[dict[str, Any]], market_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build leave-one-market-out projections for circularity-safe value research.

    1X2 value uses AH+OU only; AH value uses 1X2+OU only; OU value uses 1X2+AH
    only.  These outputs remain research/validation artifacts until historical
    time-ordered validation promotes them.
    """
    plans = {
        "1x2": ("ah", "ou"),
        "ah": ("1x2", "ou"),
        "ou": ("1x2", "ah"),
    }
    output: dict[str, Any] = {}
    for target, include in plans.items():
        try:
            output[target] = project_market(prior_matrix, market_snapshot, include=include)
        except (KeyError, TypeError, ValueError, PlatformError) as exc:
            output[target] = {"status": "不可用", "reason": str(exc), "included_markets": list(include)}
    return output
