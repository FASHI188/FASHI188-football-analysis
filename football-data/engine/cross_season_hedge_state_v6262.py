#!/usr/bin/env python3
"""V6.26.2 cross-season adaptive team-state source (research only).

This module removes the challenger architecture's hard target-season reset without inventing a
manual carry-over coefficient. It keeps four fixed exponential-memory experts (45/90/180/360d)
and combines them with online Hedge weights learned only from already-settled prediction losses.

The state source itself does NOT output football probabilities. It exposes leakage-safe team
context for the independent 1X2 and total-goals heads. This prevents a state-estimation utility
from silently becoming another probability truth.

Same-day contract: callers must predict every match on a calendar date before calling
``update_ledgers`` with that date's results.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

EXPERT_HALF_LIVES = (45.0, 90.0, 180.0, 360.0)
EPS = 1e-12


@dataclass
class HedgeLedger:
    settled_count: int = 0
    cumulative_losses: list[float] = field(default_factory=lambda: [0.0] * len(EXPERT_HALF_LIVES))


def hedge_weights(ledger: HedgeLedger) -> tuple[list[float], float | None]:
    """Return standard Hedge weights; no test-set tuning or hand-picked decay weight."""
    k = len(EXPERT_HALF_LIVES)
    n = int(ledger.settled_count)
    if n <= 0:
        return [1.0 / k] * k, None
    eta = math.sqrt(2.0 * math.log(k) / n)
    logits = [-eta * float(loss) for loss in ledger.cumulative_losses]
    m = max(logits)
    raw = [math.exp(x - m) for x in logits]
    z = sum(raw)
    return [x / z for x in raw], eta


def _age_weight(match_date: datetime, cutoff: datetime, half_life_days: float) -> float:
    age = max(0.0, (cutoff - match_date).total_seconds() / 86400.0)
    return math.exp(-math.log(2.0) * age / max(EPS, float(half_life_days)))


def _team_name(match: Any, side: str) -> str:
    return str(getattr(match, f"{side}_team"))


def _goals(match: Any, side: str) -> int:
    return int(getattr(match, f"{side}_goals"))


def _expert_record(history: Iterable[Any], team: str, cutoff: datetime, half_life_days: float) -> dict[str, float]:
    rec = defaultdict(float)
    raw = 0
    for match in history:
        if match.date >= cutoff:
            continue
        if _team_name(match, "home") == team:
            w = _age_weight(match.date, cutoff, half_life_days)
            rec["effective_matches"] += w
            rec["home_matches"] += w
            rec["home_gf"] += w * _goals(match, "home")
            rec["home_ga"] += w * _goals(match, "away")
            rec["gf"] += w * _goals(match, "home")
            rec["ga"] += w * _goals(match, "away")
            raw += 1
        elif _team_name(match, "away") == team:
            w = _age_weight(match.date, cutoff, half_life_days)
            rec["effective_matches"] += w
            rec["away_matches"] += w
            rec["away_gf"] += w * _goals(match, "away")
            rec["away_ga"] += w * _goals(match, "home")
            rec["gf"] += w * _goals(match, "away")
            rec["ga"] += w * _goals(match, "home")
            raw += 1
    rec["raw_matches"] = float(raw)
    return dict(rec)


def team_state(
    history: Iterable[Any],
    team: str,
    cutoff: datetime,
    ledger: HedgeLedger | None = None,
) -> dict[str, Any]:
    """Build a cross-season team state using only matches strictly before cutoff."""
    rows = list(history)
    experts = [_expert_record(rows, team, cutoff, hl) for hl in EXPERT_HALF_LIVES]
    active_ledger = ledger or HedgeLedger()
    weights, eta = hedge_weights(active_ledger)
    fields = (
        "effective_matches", "home_matches", "away_matches", "home_gf", "home_ga",
        "away_gf", "away_ga", "gf", "ga",
    )
    mixed = {field: sum(weights[i] * float(experts[i].get(field, 0.0)) for i in range(len(experts))) for field in fields}
    mixed["raw_matches"] = max(float(r.get("raw_matches", 0.0)) for r in experts) if experts else 0.0
    return {
        "team": team,
        "cutoff": cutoff.isoformat(),
        "history_scope": "ALL_PRIOR_SEASONS_BEFORE_CUTOFF",
        "expert_half_lives_days": list(EXPERT_HALF_LIVES),
        "expert_weights": weights,
        "hedge_eta": eta,
        "ledger_settled_count": int(active_ledger.settled_count),
        "mixed": mixed,
        "experts": experts,
        "probability_mutation": False,
    }


def normalized_brier_loss(probabilities: dict[str, float], actual: str) -> float:
    classes = ("home", "draw", "away")
    if actual not in classes:
        raise ValueError(f"invalid actual result: {actual}")
    return 0.5 * sum((float(probabilities[k]) - (1.0 if k == actual else 0.0)) ** 2 for k in classes)


def update_ledger(ledger: HedgeLedger, expert_probabilities: list[dict[str, float]], actual: str) -> None:
    """Update only after settlement; callers enforce all-same-day-predict-first ordering."""
    if len(expert_probabilities) != len(EXPERT_HALF_LIVES):
        raise ValueError("expert probability count mismatch")
    losses = [normalized_brier_loss(p, actual) for p in expert_probabilities]
    for i, loss in enumerate(losses):
        ledger.cumulative_losses[i] += float(loss)
    ledger.settled_count += 1


def state_pair(
    history: Iterable[Any],
    home_team: str,
    away_team: str,
    cutoff: datetime,
    ledgers: dict[str, HedgeLedger] | None = None,
) -> dict[str, Any]:
    ledgers = ledgers or {}
    return {
        "home": team_state(history, home_team, cutoff, ledgers.get(home_team)),
        "away": team_state(history, away_team, cutoff, ledgers.get(away_team)),
        "same_day_results_must_be_withheld": True,
        "hard_current_season_reset": False,
        "probability_mutation": False,
    }
