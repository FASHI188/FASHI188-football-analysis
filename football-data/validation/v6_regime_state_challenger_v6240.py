#!/usr/bin/env python3
"""V6.24.0 Team Regime State Challenger (research only).

Purpose:
Replace pure adaptive half-life selection with a regime-change detector.
The hypothesis: football team strength changes are often discontinuous rather than smooth.

No CURRENT mutation. No formal weight. No promotion from this file alone.

Design contract:
1. Keep fixed half-life experts: 45/90/180/360 days.
2. Detect state transition using only information available before prediction.
3. Do not use match result after prediction for the same fixture.
4. Apply conservative transitions: one abnormal match cannot immediately rewrite history.
5. Impact hierarchy: 1X2 > total goals > exact score.
"""

from dataclasses import dataclass
from math import sqrt
from typing import Dict, List

HALF_LIFE_EXPERTS = (45, 90, 180, 360)


@dataclass
class RegimeSignal:
    change_score: float
    confidence: float
    trigger_count: int
    regime: str


@dataclass
class RegimeWeights:
    half_life_weights: Dict[int, float]
    one_x_two_scale: float
    total_goal_scale: float
    score_scale: float



def detect_regime_change(
    recent_attack_delta: float,
    recent_defense_delta: float,
    xg_delta: float | None = None,
    lineup_change_score: float = 0.0,
    manager_change: bool = False,
    sample_size: int = 0,
) -> RegimeSignal:
    """Pre-match regime detector.

    Inputs must be frozen before the target match.
    A single bad result must not create a regime switch.
    """
    triggers = 0
    score = 0.0

    if abs(recent_attack_delta) >= 0.25:
        triggers += 1
        score += 0.25
    if abs(recent_defense_delta) >= 0.25:
        triggers += 1
        score += 0.25
    if xg_delta is not None and abs(xg_delta) >= 0.20:
        triggers += 1
        score += 0.20
    if lineup_change_score >= 0.30:
        triggers += 1
        score += 0.15
    if manager_change:
        triggers += 1
        score += 0.30

    # Require evidence accumulation. Small samples cannot flip regime.
    if sample_size < 5:
        score *= 0.5
    elif sample_size < 10:
        score *= 0.75

    confidence = min(1.0, score)

    if confidence >= 0.65 and triggers >= 2:
        return RegimeSignal(confidence, confidence, triggers, "transition")
    if confidence >= 0.35:
        return RegimeSignal(confidence, confidence, triggers, "watch")
    return RegimeSignal(confidence, confidence, triggers, "stable")



def regime_weights(signal: RegimeSignal) -> RegimeWeights:
    """Convert regime state into conservative history borrowing weights."""
    if signal.regime == "transition":
        weights = {45: 0.45, 90: 0.30, 180: 0.20, 360: 0.05}
        return RegimeWeights(weights, 1.0, 0.70, 0.35)

    if signal.regime == "watch":
        weights = {45: 0.30, 90: 0.30, 180: 0.25, 360: 0.15}
        return RegimeWeights(weights, 0.80, 0.50, 0.20)

    weights = {45: 0.10, 90: 0.20, 180: 0.30, 360: 0.40}
    return RegimeWeights(weights, 0.50, 0.30, 0.10)



def effective_sample_adjustment(sample_size: int) -> float:
    """Small samples limit regime influence."""
    return min(1.0, sqrt(max(sample_size, 0) / 20.0))


if __name__ == "__main__":
    print("V6.24.0 research module only")
