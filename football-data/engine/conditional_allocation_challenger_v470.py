#!/usr/bin/env python3
"""V4.7.0 staged conditional-allocation challenger.

This module never changes the direct total-goals marginal. It applies a learned,
competition-specific exponential tilt within each fixed total-goal bucket to
correct structural score-allocation bias (BTTS, clean sheets and 2+ goal margins).
Formal weight is 0 until chronological OOF validation promotes a competition-
specific parameter artifact under CURRENT rules.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable


FEATURE_NAMES = (
    "btts",
    "home_zero",
    "away_zero",
    "margin2plus",
)


def _features(home_goals: int, away_goals: int) -> dict[str, float]:
    return {
        "btts": 1.0 if home_goals > 0 and away_goals > 0 else 0.0,
        "home_zero": 1.0 if home_goals == 0 else 0.0,
        "away_zero": 1.0 if away_goals == 0 else 0.0,
        "margin2plus": 1.0 if abs(home_goals - away_goals) >= 2 else 0.0,
    }


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def normalize_parameters(parameters: dict[str, Any] | None) -> dict[str, float]:
    parameters = parameters or {}
    result = {name: _finite(parameters.get(name, 0.0)) for name in FEATURE_NAMES}
    # Hard cap is a safety guardrail, not a learned value. It prevents a malformed
    # artifact from collapsing a total-specific conditional distribution.
    return {name: min(1.5, max(-1.5, value)) for name, value in result.items()}


def _total_marginal(matrix: Iterable[dict[str, Any]]) -> dict[int, float]:
    output: dict[int, float] = defaultdict(float)
    for cell in matrix:
        total = int(cell["home_goals"]) + int(cell["away_goals"])
        output[total] += float(cell["probability"])
    return dict(output)


def apply_conditional_exponential_tilt(
    matrix: list[dict[str, Any]],
    parameters: dict[str, Any] | None,
) -> tuple[list[dict[str, float | int]], dict[str, Any]]:
    """Tilt P(H,A|T) while preserving P(T) exactly up to floating-point error."""
    params = normalize_parameters(parameters)
    before_total = _total_marginal(matrix)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for cell in matrix:
        grouped[int(cell["home_goals"]) + int(cell["away_goals"])].append(cell)

    output: list[dict[str, float | int]] = []
    for total, cells in sorted(grouped.items()):
        p_total = sum(float(cell["probability"]) for cell in cells)
        if p_total <= 0.0 or not math.isfinite(p_total):
            raise ValueError(f"invalid total probability for total={total}")
        weighted: list[tuple[int, int, float]] = []
        for cell in cells:
            home = int(cell["home_goals"])
            away = int(cell["away_goals"])
            base = float(cell["probability"]) / p_total
            feats = _features(home, away)
            log_tilt = sum(params[name] * feats[name] for name in FEATURE_NAMES)
            weighted.append((home, away, base * math.exp(log_tilt)))
        normalizer = sum(weight for _, _, weight in weighted)
        if normalizer <= 0.0 or not math.isfinite(normalizer):
            raise ValueError(f"conditional tilt failed for total={total}")
        for home, away, weight in weighted:
            output.append({
                "home_goals": home,
                "away_goals": away,
                "probability": p_total * weight / normalizer,
            })

    probability_sum = sum(float(cell["probability"]) for cell in output)
    if probability_sum <= 0.0 or not math.isfinite(probability_sum):
        raise ValueError("tilted matrix has invalid probability sum")
    output = [
        {
            "home_goals": int(cell["home_goals"]),
            "away_goals": int(cell["away_goals"]),
            "probability": float(cell["probability"]) / probability_sum,
        }
        for cell in output
    ]

    after_total = _total_marginal(output)
    totals = sorted(set(before_total) | set(after_total))
    max_total_residual = max(
        (abs(before_total.get(total, 0.0) - after_total.get(total, 0.0)) for total in totals),
        default=0.0,
    )
    audit = {
        "probability_sum": sum(float(cell["probability"]) for cell in output),
        "max_total_marginal_residual": max_total_residual,
        "parameters": params,
        "feature_names": list(FEATURE_NAMES),
        "formal_weight": 0,
        "status": "CHALLENGER_ONLY_VALIDATION_REQUIRED",
    }
    return output, audit


def structural_marginals(matrix: Iterable[dict[str, Any]]) -> dict[str, float]:
    metrics = {
        "btts": 0.0,
        "home_zero": 0.0,
        "away_zero": 0.0,
        "margin2plus": 0.0,
        "probability_sum": 0.0,
    }
    for cell in matrix:
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        probability = float(cell["probability"])
        feats = _features(home, away)
        metrics["probability_sum"] += probability
        for name in FEATURE_NAMES:
            metrics[name] += probability * feats[name]
    return metrics
