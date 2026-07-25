#!/usr/bin/env python3
"""V6.24.0 team-regime state challenger primitives.

Research only. Formal weight = 0.

This module replaces the rejected V6.23 cumulative-loss Hedge concept with a
pre-registered regime detector. It does not choose half-lives from target-test
results and it never mutates the formal runtime.

The detector consumes only signals derived from information available before a
prediction cutoff. A ledger supplies hysteresis/cooldown state from previously
settled matches. The same regime-adjusted team state must feed one and only one
joint score matrix; 1X2, total goals and exact score are then derived from that
same matrix.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


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

    def vector(self) -> list[float]:
        return [self.h45, self.h90, self.h180, self.h360]


# Fixed ex ante. These are research constants, not values selected from target results.
STABLE_WEIGHTS = RegimeWeights(0.10, 0.20, 0.30, 0.40)
WATCH_WEIGHTS = RegimeWeights(0.30, 0.30, 0.25, 0.15)
TRANSITION_WEIGHTS = RegimeWeights(0.45, 0.30, 0.20, 0.05)

# One-matrix safeguard: regime changes are shrunk toward the formal baseline before
# score construction instead of applying different adjustments to 1X2/totals/score.
REGIME_BLEND_STRENGTH = {
    Regime.STABLE: 0.20,
    Regime.WATCH: 0.45,
    Regime.TRANSITION: 0.70,
}

# Pre-registered detector thresholds. They are intentionally simple and fixed until
# a future nested-OOS protocol explicitly authorizes a change.
WATCH_EVIDENCE = 0.35
TRANSITION_EVIDENCE = 0.65
ATTACK_STRONG = 0.35
DEFENCE_STRONG = 0.35
VOLATILITY_STRONG = 0.45
STRUCTURAL_EVENT_STRONG = 0.75
MIN_WARNING_STREAK_FOR_TRANSITION = 1


def _clip(value: float, lo: float = 0.0, hi: float = 2.0) -> float:
    return min(hi, max(lo, float(value)))


def confidence_shrink(sample_size: int) -> float:
    """Shrink movement until a team has ten settled same-season matches."""
    return min(1.0, max(0.0, int(sample_size) / 10.0))


def evaluate_regime(signals: Mapping[str, float], prior_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return deterministic regime evidence and a proposed regime.

    Expected signal scale:
      attack_deviation, defence_deviation, volatility: non-negative, usually 0..2
      structural_event_score: 0..1, optional and zero when unavailable
      sample_size: settled same-season team matches available at the cutoff

    A single extreme signal cannot force TRANSITION. Transition requires at least
    two strong signals plus an existing warning streak, so a one-match shock first
    moves the team into WATCH.
    """
    prior = dict(prior_snapshot or {})
    attack_gap = _clip(float(signals.get("attack_deviation", 0.0)))
    defence_gap = _clip(float(signals.get("defence_deviation", 0.0)))
    volatility = _clip(float(signals.get("volatility", 0.0)))
    structural = min(1.0, max(0.0, float(signals.get("structural_event_score", 0.0))))
    sample_size = max(0, int(signals.get("sample_size", 0)))
    confidence = confidence_shrink(sample_size)

    evidence = (
        0.40 * attack_gap
        + 0.40 * defence_gap
        + 0.10 * volatility
        + 0.10 * structural
    ) * confidence

    strong_flags = {
        "attack": attack_gap >= ATTACK_STRONG,
        "defence": defence_gap >= DEFENCE_STRONG,
        "volatility": volatility >= VOLATILITY_STRONG,
        "structural_event": structural >= STRUCTURAL_EVENT_STRONG,
    }
    strong_count = sum(1 for value in strong_flags.values() if value)
    prior_warning = max(0, int(prior.get("warning_streak", 0)))
    prior_regime = str(prior.get("regime", Regime.STABLE.value))
    cooldown = max(0, int(prior.get("cooldown_remaining", 0)))

    # Hysteresis: an already-confirmed transition cannot disappear mid-cooldown.
    if prior_regime == Regime.TRANSITION.value and cooldown > 0:
        proposed = Regime.TRANSITION
        reason = "transition_cooldown"
    elif (
        evidence >= TRANSITION_EVIDENCE
        and strong_count >= 2
        and prior_warning >= MIN_WARNING_STREAK_FOR_TRANSITION
    ):
        proposed = Regime.TRANSITION
        reason = "multi_signal_confirmed_transition"
    elif evidence >= WATCH_EVIDENCE and strong_count >= 1:
        proposed = Regime.WATCH
        reason = "warning_evidence"
    else:
        proposed = Regime.STABLE
        reason = "stable_or_insufficient_evidence"

    return {
        "regime": proposed.value,
        "reason": reason,
        "evidence": float(evidence),
        "confidence": float(confidence),
        "strong_signal_count": int(strong_count),
        "strong_flags": strong_flags,
        "signals": {
            "attack_deviation": attack_gap,
            "defence_deviation": defence_gap,
            "volatility": volatility,
            "structural_event_score": structural,
            "sample_size": sample_size,
        },
    }


def detect_regime(signals: Mapping[str, float], prior_snapshot: Mapping[str, Any] | None = None) -> Regime:
    return Regime(evaluate_regime(signals, prior_snapshot)["regime"])


def regime_weights(regime: Regime) -> RegimeWeights:
    if regime == Regime.TRANSITION:
        return TRANSITION_WEIGHTS
    if regime == Regime.WATCH:
        return WATCH_WEIGHTS
    return STABLE_WEIGHTS


def regime_blend_strength(regime: Regime) -> float:
    return float(REGIME_BLEND_STRENGTH[regime])


def audit_contract() -> dict[str, Any]:
    return {
        "module": "V6.24.0_TEAM_REGIME_STATE_CHALLENGER",
        "formal_weight": 0,
        "runtime_enabled": False,
        "target_result_used_for_parameter_choice": False,
        "same_day_result_leakage_allowed": False,
        "states": [x.value for x in Regime],
        "expert_weights": {
            Regime.STABLE.value: asdict(STABLE_WEIGHTS),
            Regime.WATCH.value: asdict(WATCH_WEIGHTS),
            Regime.TRANSITION.value: asdict(TRANSITION_WEIGHTS),
        },
        "single_match_can_force_transition": False,
        "one_joint_matrix_only": True,
        "status": "RESEARCH_ONLY",
    }
