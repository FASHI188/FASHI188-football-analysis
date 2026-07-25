#!/usr/bin/env python3
"""V6.24.0 regime-state adapter.

Research-only adapter between the frozen V4.6.0 engine and V6.24 regime logic.
It never changes formal runtime probabilities or CURRENT.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from v624_regime_ledger import RegimeLedger
from v6_team_regime_state_challenger_v6240 import (
    Regime,
    evaluate_regime,
    regime_blend_strength,
    regime_weights,
)


MODULE = "V6.24.0_TEAM_REGIME_STATE_ADAPTER"


def build_regime_snapshot(
    team: str,
    ledger: RegimeLedger,
    signals: dict[str, float],
) -> dict[str, Any]:
    """Build a prediction-time read-only regime snapshot."""
    prior = ledger.snapshot(team)
    evaluation = evaluate_regime(signals, prior)
    regime = Regime(evaluation["regime"])
    weights = regime_weights(regime)
    return {
        "module": MODULE,
        "team": team,
        "regime": regime.value,
        "weights": asdict(weights),
        "weight_vector": weights.vector(),
        "blend_strength": regime_blend_strength(regime),
        "evaluation": evaluation,
        "ledger_snapshot": prior,
        "formal_weight": 0,
        "runtime_enabled": False,
    }


def build_post_settlement_proposal(
    team: str,
    ledger: RegimeLedger,
    signals: dict[str, float],
    match_date: str,
    *,
    settled_increment: int = 1,
) -> dict[str, Any]:
    """Create but do not apply a day-end update proposal."""
    prior = ledger.snapshot(team)
    evaluation = evaluate_regime(signals, prior)
    return {
        "team": team,
        "detected_regime": evaluation["regime"],
        "match_date": str(match_date),
        "evidence": float(evaluation["evidence"]),
        "settled_increment": int(settled_increment),
        "evaluation": evaluation,
    }


def settle_regime_day(ledger: RegimeLedger, proposals: list[dict[str, Any]]) -> None:
    """Apply proposals only after all matches from that date were predicted."""
    ledger.update_day_after_settlement(proposals)


def audit_contract() -> dict[str, Any]:
    return {
        "module": MODULE,
        "prediction_reads_snapshot_only": True,
        "post_settlement_update_only": True,
        "same_day_predict_then_update_barrier": True,
        "formal_weight": 0,
        "runtime_enabled": False,
        "one_joint_matrix_only": True,
    }
