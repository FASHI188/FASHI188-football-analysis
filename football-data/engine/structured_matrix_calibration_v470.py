#!/usr/bin/env python3
"""V4.7.0 staged low-dimensional structured matrix calibrator.

Unlike a single temperature, this calibrator can correct selected structural
marginals. Parameters are competition-specific and formal weight is 0 until
chronological OOF validation passes all CURRENT guardrails.
"""
from __future__ import annotations

import math
from typing import Any

FEATURES = ("draw", "btts", "home_zero", "away_zero", "margin2plus", "total4plus")


def _feature_vector(h: int, a: int) -> dict[str, float]:
    return {
        "draw": float(h == a),
        "btts": float(h > 0 and a > 0),
        "home_zero": float(h == 0),
        "away_zero": float(a == 0),
        "margin2plus": float(abs(h - a) >= 2),
        "total4plus": float(h + a >= 4),
    }


def apply_structured_calibration(
    matrix: list[dict[str, Any]],
    *,
    temperature: float = 1.0,
    coefficients: dict[str, float] | None = None,
) -> tuple[list[dict[str, float | int]], dict[str, Any]]:
    coefficients = coefficients or {}
    t = float(temperature)
    if not math.isfinite(t) or t <= 0.0:
        raise ValueError("temperature must be positive and finite")
    t = min(3.0, max(0.33, t))
    theta = {name: min(1.0, max(-1.0, float(coefficients.get(name, 0.0)))) for name in FEATURES}
    weighted = []
    for cell in matrix:
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        p = max(1e-300, float(cell["probability"]))
        feats = _feature_vector(h, a)
        log_weight = math.log(p) / t + sum(theta[name] * feats[name] for name in FEATURES)
        weighted.append((h, a, log_weight))
    max_log = max(item[2] for item in weighted)
    exp_weights = [(h, a, math.exp(logw - max_log)) for h, a, logw in weighted]
    z = sum(item[2] for item in exp_weights)
    if z <= 0.0 or not math.isfinite(z):
        raise ValueError("structured calibration normalization failed")
    output = [{"home_goals": h, "away_goals": a, "probability": w / z} for h, a, w in exp_weights]
    audit = {
        "probability_sum": sum(float(c["probability"]) for c in output),
        "temperature": t,
        "coefficients": theta,
        "formal_weight": 0,
        "status": "CHALLENGER_ONLY_VALIDATION_REQUIRED"
    }
    return output, audit
