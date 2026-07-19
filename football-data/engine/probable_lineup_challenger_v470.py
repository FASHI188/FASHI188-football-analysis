#!/usr/bin/env python3
"""V4.7.0 staged same-season probable-lineup probability builder.

Predicted XIs are never written back as observed XIs. The module only converts
verified same-target-season starts plus point-in-time availability into starter
probabilities. Numeric lineup-to-score effects remain formal weight 0 until
competition-specific OOF validation.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any


def starter_probabilities(
    observed_lineups: list[dict[str, Any]],
    cutoff: datetime,
    current_players: list[str],
    availability: dict[str, str] | None = None,
    *,
    half_life_days: float = 45.0,
    prior_starts: float = 1.0,
    prior_matches: float = 2.0,
) -> dict[str, Any]:
    availability = availability or {}
    starts = defaultdict(float)
    matches = 0.0
    used = 0
    for row in observed_lineups:
        observed_at = row.get("kickoff")
        if not isinstance(observed_at, datetime) or observed_at >= cutoff:
            continue
        age = max(0.0, (cutoff - observed_at).total_seconds() / 86400.0)
        w = math.exp(-math.log(2.0) * age / max(1e-9, half_life_days))
        xi = row.get("starting_xi") or []
        if len(xi) != 11:
            continue
        matches += w
        used += 1
        for player in xi:
            starts[str(player)] += w
    probabilities = {}
    for player in current_players:
        status = str(availability.get(player, "available")).lower()
        if status in {"out", "suspended", "unavailable"}:
            probability = 0.0
        else:
            probability = (starts[player] + prior_starts) / max(1e-12, matches + prior_matches)
            if status in {"doubtful", "questionable"}:
                probability *= 0.6
        probabilities[player] = min(1.0, max(0.0, probability))
    return {
        "starter_probabilities": probabilities,
        "verified_same_season_lineups_used": used,
        "effective_lineup_matches": matches,
        "predicted_xi_written_as_observed": False,
        "formal_probability_effect_weight": 0,
        "status": "PROBABLE_LINEUP_ONLY_EFFECT_VALIDATION_REQUIRED"
    }
