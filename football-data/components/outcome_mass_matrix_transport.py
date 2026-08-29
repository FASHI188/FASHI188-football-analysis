"""Deterministic governance transport from a target 1X2 vector to a score matrix.

This is an architecture adapter, NOT a claim about any legacy source formula.
It preserves the conditional score distribution within home/draw/away outcome
classes and changes only each class's total probability mass.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from pipeline.unified_inference import canonical_matrix, one_x_two

CLASSES = ("home", "draw", "away")


def _class_of(home_goals: int, away_goals: int) -> str:
    return "home" if home_goals > away_goals else "draw" if home_goals == away_goals else "away"


def normalized_1x2(probabilities: Mapping[str, float]) -> dict[str, float]:
    if set(probabilities) != set(CLASSES):
        raise ValueError("1X2 probabilities must contain exactly home/draw/away")
    out = {k: float(probabilities[k]) for k in CLASSES}
    if any((not math.isfinite(v) or v < 0.0) for v in out.values()):
        raise ValueError("invalid 1X2 probability")
    total = sum(out.values())
    if total <= 0.0:
        raise ValueError("1X2 probability mass must be positive")
    return {k: out[k] / total for k in CLASSES}


def assert_1x2_match(actual: Mapping[str, float], expected: Mapping[str, float], *, atol: float = 1e-10) -> None:
    a = normalized_1x2(actual)
    e = normalized_1x2(expected)
    bad = {k: (a[k], e[k]) for k in CLASSES if abs(a[k] - e[k]) > atol}
    if bad:
        raise RuntimeError({"source_1x2_mismatch": bad, "atol": atol})


def lift_1x2_target(
    matrix: Iterable[Mapping[str, Any]],
    target_probabilities: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Lift target 1X2 masses while preserving within-class score shape."""
    cells = canonical_matrix(matrix)
    current = one_x_two(cells)
    target = normalized_1x2(target_probabilities)

    for k in CLASSES:
        if target[k] > 0.0 and current[k] <= 0.0:
            raise RuntimeError(f"cannot allocate positive {k} target mass without existing score support")

    scales = {k: (target[k] / current[k] if current[k] > 0.0 else 0.0) for k in CLASSES}
    out: list[dict[str, Any]] = []
    for cell in cells:
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        k = _class_of(h, a)
        out.append({
            "home_goals": h,
            "away_goals": a,
            "probability": float(cell["probability"]) * scales[k],
        })

    # canonical_matrix validates total mass and stable cell representation.
    return canonical_matrix(out)
