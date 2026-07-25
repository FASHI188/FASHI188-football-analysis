#!/usr/bin/env python3
"""V6.24.0 regime state adapter.

Research-only adapter. It does not replace formal V4.6.0 engine.
It prepares a controlled interface so challengers can alter historical borrowing
weights after regime detection without changing score construction logic.

Contract:
- snapshot is read before prediction only
- ledger update is after settlement only
- formal runtime remains unchanged
- formal_weight=0
"""
from dataclasses import asdict
from typing import Any

from v624_regime_ledger import TeamRegimeLedger, snapshot_ledger, update_after_settlement
from v6_team_regime_state_challenger_v6240 import (
    Regime,
    detect_regime,
    regime_weights,
)


MODULE = "V6.24.0_TEAM_REGIME_STATE_ADAPTER"


def build_regime_snapshot(
    team: str,
    ledger: TeamRegimeLedger,
    signals: dict[str, float],
) -> dict[str, Any]:
    """Prediction-time read-only snapshot."""
    regime = detect_regime(signals, ledger)
    weights = regime_weights(regime)
    return {
        "module": MODULE,
        "team": team,
        "regime": regime.value,
        "weights": asdict(weights),
        "ledger_snapshot": snapshot_ledger(ledger),
        "formal_weight": 0,
        "runtime_enabled": False,
    }


def settle_regime_after_match(
    ledger: TeamRegimeLedger,
    settled_signal_strength: float,
) -> TeamRegimeLedger:
    """Update only after the match result is known."""
    return update_after_settlement(ledger, settled_signal_strength)


def audit_contract() -> dict[str, Any]:
    return {
        "module": MODULE,
        "prediction_reads_snapshot_only": True,
        "post_settlement_update_only": True,
        "same_day_leakage_protection": True,
        "formal_weight": 0,
        "runtime_enabled": False,
    }
