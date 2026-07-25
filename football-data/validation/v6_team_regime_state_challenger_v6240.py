#!/usr/bin/env python3
"""V6.24.0 Team Regime State Challenger.

Research only. Formal weight = 0.

Goal:
Replace fixed historical borrowing adaptation with a regime-change layer.
The module does not learn a best half-life from test results.

Design contract:
- Prediction reads only information available before kickoff.
- Match results update state ledger only after settlement.
- Single-match shocks cannot immediately force a regime switch.
- State changes require multi-signal confirmation.
- Output is a state multiplier consumed by research challengers only.

States:
STABLE:
  Long-memory team identity dominates.
WATCH:
  Evidence of recent deviation; cautious short-memory increase.
TRANSITION:
  Confirmed structural change; short-memory emphasis.

Signals:
- rolling attack deviation
- rolling defence deviation
- scoring/conceding volatility
- squad/coach events when available
- sample-size confidence shrinkage

No runtime probability changes are enabled by this file.
"""

from dataclasses import dataclass
from enum import Enum
from math import exp
from typing import Dict, Any


class Regime(str, Enum):
    STABLE = "STABLE"
    WATCH = "WATCH"
    TRANSITION = "TRANSITION"


@dataclass(frozen=True)
class RegimeWeights:
    h45: float
    h90: float
    h180: float
    h360: float


STABLE_WEIGHTS = RegimeWeights(0.10, 0.20, 0.30, 0.40)
WATCH_WEIGHTS = RegimeWeights(0.30, 0.30, 0.25, 0.15)
TRANSITION_WEIGHTS = RegimeWeights(0.45, 0.30, 0.20, 0.05)


@dataclass
class TeamRegimeLedger:
    regime: Regime = Regime.STABLE
    consecutive_warning: int = 0
    confirmed_transition: int = 0


def _confidence_shrink(sample_size: int) -> float:
    """Small samples cannot trigger large state movement."""
    return min(1.0, max(0.0, sample_size / 10.0))


def detect_regime(signals: Dict[str, float], ledger: TeamRegimeLedger) -> Regime:
    """Pure pre-result-free regime detector placeholder.

    The final thresholds remain research parameters and are not calibrated on target results.
    """
    attack_gap = abs(float(signals.get("attack_deviation", 0.0)))
    defence_gap = abs(float(signals.get("defence_deviation", 0.0)))
    volatility = float(signals.get("volatility", 0.0))
    confidence = _confidence_shrink(int(signals.get("sample_size", 0)))

    evidence = (0.45 * attack_gap + 0.45 * defence_gap + 0.10 * volatility) * confidence

    if evidence > 0.75:
        if ledger.consecutive_warning >= 2:
            return Regime.TRANSITION
        return Regime.WATCH
    if evidence > 0.40:
        return Regime.WATCH
    return Regime.STABLE


def regime_weights(regime: Regime) -> RegimeWeights:
    if regime == Regime.TRANSITION:
        return TRANSITION_WEIGHTS
    if regime == Regime.WATCH:
        return WATCH_WEIGHTS
    return STABLE_WEIGHTS


def audit_contract() -> Dict[str, Any]:
    return {
        "module": "V6.24.0_TEAM_REGIME_STATE_CHALLENGER",
        "formal_weight": 0,
        "runtime_enabled": False,
        "target_result_used": False,
        "same_day_result_leakage": False,
        "states": [x.value for x in Regime],
        "status": "RESEARCH_ONLY",
    }
