#!/usr/bin/env python3
"""V6.24.0 regime ledger.

Research only. The ledger stores information available after matches settle.
Prediction code must read a snapshot before kickoff and must never mutate it.
"""

from dataclasses import dataclass, asdict
from typing import Dict, Any


@dataclass
class RegimeLedgerEntry:
    regime: str = "STABLE"
    warning_streak: int = 0
    transition_streak: int = 0
    settled_matches: int = 0
    last_update_date: str = ""


class RegimeLedger:
    def __init__(self):
        self._teams: Dict[str, RegimeLedgerEntry] = {}

    def snapshot(self, team: str) -> Dict[str, Any]:
        entry = self._teams.get(team, RegimeLedgerEntry())
        return asdict(entry)

    def update_after_settlement(
        self,
        team: str,
        detected_regime: str,
        match_date: str,
    ) -> None:
        entry = self._teams.setdefault(team, RegimeLedgerEntry())
        entry.regime = detected_regime
        entry.settled_matches += 1
        entry.last_update_date = match_date

    def export(self) -> Dict[str, Any]:
        return {k: asdict(v) for k, v in self._teams.items()}


AUDIT = {
    "module": "V6.24.0_REGIME_LEDGER",
    "update_policy": "post_settlement_only",
    "prediction_read_only_snapshot": True,
    "same_day_leakage_protection": True,
    "formal_weight": 0,
}
