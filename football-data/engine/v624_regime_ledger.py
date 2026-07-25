#!/usr/bin/env python3
"""V6.24.0 regime ledger.

Research only. Prediction code may read snapshots but may not mutate this ledger.
All mutations occur after the relevant match/day has settled.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable


VALID_REGIMES = {"STABLE", "WATCH", "TRANSITION"}


@dataclass
class RegimeLedgerEntry:
    regime: str = "STABLE"
    warning_streak: int = 0
    transition_streak: int = 0
    cooldown_remaining: int = 0
    settled_matches: int = 0
    last_update_date: str = ""
    last_evidence: float = 0.0


class RegimeLedger:
    """Mutable post-settlement store with read-only prediction snapshots."""

    def __init__(self) -> None:
        self._teams: Dict[str, RegimeLedgerEntry] = {}

    def snapshot(self, team: str) -> Dict[str, Any]:
        entry = self._teams.get(team, RegimeLedgerEntry())
        return dict(asdict(entry))

    def update_after_settlement(
        self,
        team: str,
        detected_regime: str,
        match_date: str,
        *,
        evidence: float = 0.0,
        settled_increment: int = 1,
    ) -> Dict[str, Any]:
        """Apply one post-settlement proposal and return the new snapshot.

        TRANSITION has a two-settlement cooldown. WATCH raises the warning streak.
        STABLE decays the warning streak by one instead of clearing it instantly.
        This adds hysteresis and prevents one anomalous match from flipping state.
        """
        proposal = str(detected_regime).upper()
        if proposal not in VALID_REGIMES:
            raise ValueError(f"invalid regime proposal: {detected_regime!r}")
        entry = self._teams.setdefault(team, RegimeLedgerEntry())
        prior_regime = entry.regime

        if entry.cooldown_remaining > 0:
            entry.cooldown_remaining -= 1

        if proposal == "TRANSITION":
            entry.warning_streak = max(2, entry.warning_streak + 1)
            entry.transition_streak += 1
            entry.cooldown_remaining = max(entry.cooldown_remaining, 2)
            entry.regime = "TRANSITION"
        elif proposal == "WATCH":
            entry.warning_streak += 1
            entry.transition_streak = 0
            if prior_regime == "TRANSITION" and entry.cooldown_remaining > 0:
                entry.regime = "TRANSITION"
            else:
                entry.regime = "WATCH"
        else:
            entry.warning_streak = max(0, entry.warning_streak - 1)
            entry.transition_streak = 0
            if prior_regime == "TRANSITION" and entry.cooldown_remaining > 0:
                entry.regime = "TRANSITION"
            elif entry.warning_streak > 0:
                entry.regime = "WATCH"
            else:
                entry.regime = "STABLE"

        entry.settled_matches += max(0, int(settled_increment))
        entry.last_update_date = str(match_date)
        entry.last_evidence = float(evidence)
        return dict(asdict(entry))

    def update_day_after_settlement(self, updates: Iterable[Dict[str, Any]]) -> None:
        """Batch day-level updates after every prediction from that date is complete."""
        for update in updates:
            self.update_after_settlement(
                str(update["team"]),
                str(update["detected_regime"]),
                str(update["match_date"]),
                evidence=float(update.get("evidence", 0.0)),
                settled_increment=int(update.get("settled_increment", 1)),
            )

    def export(self) -> Dict[str, Any]:
        return {k: dict(asdict(v)) for k, v in sorted(self._teams.items())}


AUDIT = {
    "module": "V6.24.0_REGIME_LEDGER",
    "update_policy": "post_settlement_only",
    "prediction_read_only_snapshot": True,
    "same_day_batch_update_supported": True,
    "transition_cooldown_settlements": 2,
    "formal_weight": 0,
    "runtime_enabled": False,
}
