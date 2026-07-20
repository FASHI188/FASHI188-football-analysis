#!/usr/bin/env python3
from __future__ import annotations
import math
from collections import defaultdict
from typing import Any, Iterable

FEATURE_NAMES = ("tail4plus", "tail5plus", "tail7plus")

def _bucket(home: int, away: int) -> int:
    total = home + away
    return total if total <= 6 else 7

def _features(bucket: int) -> dict[str, float]:
    return {
        "tail4plus": 1.0 if bucket >= 4 else 0.0,
        "tail5plus": 1.0 if bucket >= 5 else 0.0,
        "tail7plus": 1.0 if bucket >= 7 else 0.0,
    }

def normalize_parameters(parameters: dict[str, Any] | None) -> dict[str, float]:
    parameters = parameters or {}
    output = {}
    for name in FEATURE_NAMES:
        try:
            value = float(parameters.get(name, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        output[name] = min(1.5, max(-1.5, value))
    return output

def total_vector_from_matrix(matrix: Iterable[dict[str, Any]]) -> list[float]:
    out = [0.0] * 8
    for cell in matrix:
        out[_bucket(int(cell["home_goals"]), int(cell["away_goals"]))] += float(cell["probability"])
    total = sum(out)
    if total <= 0.0 or not math.isfinite(total):
        raise ValueError("invalid matrix total probability")
    return [value / total for value in out]

def tilt_total_vector(probabilities: list[float], parameters: dict[str, Any] | None) -> list[float]:
    if len(probabilities) != 8:
        raise ValueError("total vector must contain 0,1,2,3,4,5,6,7+")
    params = normalize_parameters(parameters)
    weighted = []
    for bucket, base in enumerate(probabilities):
        feats = _features(bucket)
        log_tilt = sum(params[name] * feats[name] for name in FEATURE_NAMES)
        weighted.append(max(0.0, float(base)) * math.exp(log_tilt))
    denominator = sum(weighted)
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise ValueError("total tail tilt normalization failed")
    return [value / denominator for value in weighted]

def apply_total_tail_tilt(matrix: list[dict[str, Any]], parameters: dict[str, Any] | None):
    params = normalize_parameters(parameters)
    before = total_vector_from_matrix(matrix)
    after = tilt_total_vector(before, params)
    factors = [(after[i] / before[i]) if before[i] > 0 else 0.0 for i in range(8)]
    output = []
    for cell in matrix:
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        bucket = _bucket(home, away)
        output.append({"home_goals": home, "away_goals": away, "probability": float(cell["probability"]) * factors[bucket]})
    probability_sum = sum(float(cell["probability"]) for cell in output)
    if probability_sum <= 0.0 or not math.isfinite(probability_sum):
        raise ValueError("tilted matrix has invalid probability sum")
    for cell in output:
        cell["probability"] = float(cell["probability"]) / probability_sum
    after_check = total_vector_from_matrix(output)
    max_vector_residual = max(abs(after[i] - after_check[i]) for i in range(8))
    conditional_residual = 0.0
    grouped_before = defaultdict(dict)
    grouped_after = defaultdict(dict)
    for cell in matrix:
        key = _bucket(int(cell["home_goals"]), int(cell["away_goals"]))
        grouped_before[key][(int(cell["home_goals"]), int(cell["away_goals"]))] = float(cell["probability"])
    for cell in output:
        key = _bucket(int(cell["home_goals"]), int(cell["away_goals"]))
        grouped_after[key][(int(cell["home_goals"]), int(cell["away_goals"]))] = float(cell["probability"])
    for bucket in range(8):
        bsum = sum(grouped_before[bucket].values())
        asum = sum(grouped_after[bucket].values())
        if bsum <= 0 or asum <= 0:
            continue
        for score, value in grouped_before[bucket].items():
            before_cond = value / bsum
            after_cond = grouped_after[bucket].get(score, 0.0) / asum
            conditional_residual = max(conditional_residual, abs(before_cond - after_cond))
    return output, {
        "probability_sum": sum(float(cell["probability"]) for cell in output),
        "before_total_vector": before,
        "after_total_vector": after_check,
        "max_total_vector_residual": max_vector_residual,
        "max_conditional_score_residual": conditional_residual,
        "parameters": params,
        "formal_weight": 0,
        "status": "CHALLENGER_ONLY_VALIDATION_REQUIRED",
    }
