#!/usr/bin/env python3
"""Lightweight ClubElo cross-league cold-start fallback for live runtime R1.

This is an operational-shadow fallback, not a formal model promotion. It exists
for clubs that have no usable history in the target top-flight dataset at the
question-time freeze (most importantly newly promoted clubs before matchweek 1).

Live-path design:
* one dated ClubElo ranking snapshot only -- no per-club history crawl or live retraining;
* strict provider identity mapping;
* cross-league team-strength split from the dated Elo difference;
* home advantage and draw base rate are estimated directly from already-completed
  top-flight history before the freeze, not hand-entered;
* total-goal and score-cell priors are empirical completed top-flight history;
* V6.26 IPF reconciles the score matrix to both accepted 1X2 and total marginals.

No target result, future result, future rating, market price, lineup or manually
chosen promotion penalty is used.
"""
from __future__ import annotations

import csv
import io
import math
import time
import urllib.request
from collections import Counter
from datetime import date, timedelta
from typing import Any

import three_stage_core_v6260 as three_stage
from platform_core import PlatformError, normalize_team_token, top_scores

TOTAL_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7+")
MAX_SCORE_TOTAL = 10
RATING_SCALE = 400.0

PROVIDER_ALIASES = {
    "Nott'm Forest": "Forest",
    "Nottingham Forest": "Forest",
    "Manchester City": "Man City",
    "Manchester United": "Man United",
    "AFC Bournemouth": "Bournemouth",
    "Brighton & Hove Albion": "Brighton",
    "Newcastle United": "Newcastle",
    "Tottenham Hotspur": "Tottenham",
    "West Ham United": "West Ham",
    "Wolverhampton Wanderers": "Wolves",
    "Leeds United": "Leeds",
    "Ipswich Town": "Ipswich",
    "Coventry City": "Coventry",
    "Hull City": "Hull",
}

_SNAPSHOT_CACHE: dict[str, list[dict[str, str]]] = {}


def _fetch_snapshot(day: date) -> list[dict[str, str]]:
    key = day.isoformat()
    if key in _SNAPSHOT_CACHE:
        return _SNAPSHOT_CACHE[key]
    errors = []
    # Provider is historically documented on HTTP; HTTPS is tried as a fallback.
    for base in ("http://api.clubelo.com", "https://api.clubelo.com"):
        url = f"{base}/{key}"
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "football-analysis-live-shadow/1.1"})
                with urllib.request.urlopen(req, timeout=12) as response:
                    text = response.read().decode("utf-8-sig", errors="replace")
                rows = list(csv.DictReader(io.StringIO(text)))
                if not rows:
                    raise RuntimeError("empty ClubElo daily ranking CSV")
                _SNAPSHOT_CACHE[key] = rows
                return rows
            except Exception as exc:
                errors.append(f"{url} attempt={attempt + 1}: {type(exc).__name__}: {exc}")
                time.sleep(0.5 * (attempt + 1))
    raise PlatformError("ClubElo dated snapshot unavailable: " + " | ".join(errors))


def _resolve_row(team: str, rows: list[dict[str, str]], country: str) -> dict[str, str]:
    requested = PROVIDER_ALIASES.get(team, team)
    token = normalize_team_token(requested)
    candidates = [
        row for row in rows
        if str(row.get("Country") or "").strip() == country
        and normalize_team_token(str(row.get("Club") or "")) == token
    ]
    if len(candidates) != 1:
        raise PlatformError(
            f"ClubElo identity fail closed for {team!r} -> {requested!r}; matches={len(candidates)}"
        )
    try:
        float(candidates[0]["Elo"])
    except Exception as exc:
        raise PlatformError(f"ClubElo Elo value invalid for {team!r}") from exc
    return candidates[0]


def _historical_base_rates(history) -> dict[str, Any]:
    if len(history) < 300:
        raise PlatformError(f"top-flight cold-start base history below minimum: {len(history)}")
    outcome = Counter()
    totals = Counter()
    score = Counter()
    for m in history:
        if m.home_goals > m.away_goals:
            outcome["home"] += 1
        elif m.home_goals == m.away_goals:
            outcome["draw"] += 1
        else:
            outcome["away"] += 1
        total = int(m.home_goals + m.away_goals)
        totals[str(total) if total <= 6 else "7+"] += 1
        if total <= MAX_SCORE_TOTAL:
            score[(int(m.home_goals), int(m.away_goals))] += 1

    n = float(len(history))
    draw_rate = outcome["draw"] / n
    decisive = outcome["home"] + outcome["away"]
    if decisive <= 0:
        raise PlatformError("top-flight history has no decisive matches")
    home_decisive_share = outcome["home"] / decisive
    clipped = min(1.0 - 1e-6, max(1e-6, home_decisive_share))
    home_advantage_elo = RATING_SCALE * math.log10(clipped / (1.0 - clipped))

    # Jeffreys/Laplace-style smoothing ensures every total bucket and score
    # partition has support for IPF without inventing a team-specific effect.
    total_alpha = 0.5
    total_den = n + total_alpha * len(TOTAL_KEYS)
    total_distribution = {
        key: (float(totals[key]) + total_alpha) / total_den for key in TOTAL_KEYS
    }

    score_alpha = 0.20
    score_cells = []
    for total in range(MAX_SCORE_TOTAL + 1):
        for home in range(total + 1):
            away = total - home
            score_cells.append({
                "home_goals": home,
                "away_goals": away,
                "probability": float(score[(home, away)]) + score_alpha,
            })
    z = sum(float(c["probability"]) for c in score_cells)
    for cell in score_cells:
        cell["probability"] = float(cell["probability"]) / z

    return {
        "outcome_counts": dict(outcome),
        "draw_rate": draw_rate,
        "home_decisive_share": home_decisive_share,
        "derived_home_advantage_elo": home_advantage_elo,
        "total_distribution": total_distribution,
        "score_prior": score_cells,
    }


def _one_x_two_from_elo(home_elo: float, away_elo: float, base: dict[str, Any]) -> dict[str, float]:
    adjusted_difference = home_elo - away_elo + float(base["derived_home_advantage_elo"])
    home_share_if_decisive = 1.0 / (1.0 + 10.0 ** (-adjusted_difference / RATING_SCALE))
    draw = min(0.45, max(0.10, float(base["draw_rate"])))
    decisive_mass = 1.0 - draw
    return {
        "home": decisive_mass * home_share_if_decisive,
        "draw": draw,
        "away": decisive_mass * (1.0 - home_share_if_decisive),
    }


def predict_clubelo_coldstart(
    history,
    competition_id: str,
    home_team: str,
    away_team: str,
    freeze,
    *,
    country: str = "ENG",
) -> dict[str, Any]:
    if competition_id != "ENG_PremierLeague":
        raise PlatformError("ClubElo cold-start R1 is currently enabled only for ENG_PremierLeague")
    if not history:
        raise PlatformError("ClubElo cold-start requires completed top-flight base history")

    rating_day = freeze.date() - timedelta(days=1)
    snapshot = _fetch_snapshot(rating_day)
    home_row = _resolve_row(home_team, snapshot, country)
    away_row = _resolve_row(away_team, snapshot, country)
    home_elo = float(home_row["Elo"])
    away_elo = float(away_row["Elo"])

    base = _historical_base_rates(history)
    one = _one_x_two_from_elo(home_elo, away_elo, base)
    total = base["total_distribution"]
    matrix, reconciliation = three_stage.reconcile(
        base["score_prior"],
        [one["home"], one["draw"], one["away"]],
        [float(total[k]) for k in TOTAL_KEYS],
    )
    if not reconciliation.get("converged"):
        raise PlatformError(f"ClubElo cold-start IPF failed: {reconciliation}")

    final_one = three_stage.one_x_two_vector(matrix)
    final_total = three_stage.total_goals_vector(matrix)
    mean_total = sum(i * final_total[i] for i in range(7)) + 7.5 * final_total[7]
    return {
        "competition_id": competition_id,
        "season": "CROSS_LEAGUE_CLUBELO_COLDSTART",
        "history_matches": len(history),
        "team_sample": {
            "home_raw_matches": 0.0,
            "away_raw_matches": 0.0,
            "home_effective_matches": 0.0,
            "away_effective_matches": 0.0,
            "ess": 0.0,
            "mu_total": mean_total,
        },
        "probabilities": {
            "one_x_two": {"home": final_one[0], "draw": final_one[1], "away": final_one[2]},
            "total_goals": {k: final_total[i] for i, k in enumerate(TOTAL_KEYS)},
            "score_matrix": matrix,
        },
        "top_scores": top_scores(matrix, 10),
        "audit": {
            "classification": "OPERATIONAL_SHADOW_CLUBELO_CROSS_LEAGUE_COLDSTART_R1_1",
            "formal_weight": 0,
            "rating_day": rating_day.isoformat(),
            "provider": "ClubElo dated daily ranking CSV",
            "provider_rows": len(snapshot),
            "target": {
                "home_team": home_team,
                "home_provider": str(home_row.get("Club")),
                "home_elo": home_elo,
                "home_provider_level": home_row.get("Level"),
                "away_team": away_team,
                "away_provider": str(away_row.get("Club")),
                "away_elo": away_elo,
                "away_provider_level": away_row.get("Level"),
                "elo_difference": home_elo - away_elo,
            },
            "top_flight_training": {
                "history_matches": len(history),
                "outcome_counts": base["outcome_counts"],
                "draw_rate": base["draw_rate"],
                "home_decisive_share": base["home_decisive_share"],
                "derived_home_advantage_elo": base["derived_home_advantage_elo"],
            },
            "one_x_two_method": "ClubElo_difference_standard_400_scale_plus_empirical_PL_home_advantage_and_draw_rate",
            "total_method": "smoothed_empirical_completed_PL_total_distribution",
            "score_prior_method": "smoothed_empirical_completed_PL_score_cells_then_V626_IPF",
            "reconciliation": reconciliation,
            "manual_promotion_penalty": False,
            "market_used": False,
            "target_result_used": False,
        },
    }
