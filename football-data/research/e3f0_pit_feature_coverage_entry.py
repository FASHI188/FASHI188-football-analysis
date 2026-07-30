#!/usr/bin/env python3
"""E3f-0 audited entrypoint.

This wrapper removes the ambiguous short token `xa` before schema scanning and
replaces schedule/task derivation with a season-safe implementation. Rest and
congestion may carry across seasons, but standings are keyed by (season, team)
and therefore reset correctly at every new season.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta
from typing import Any

import e3f0_pit_feature_coverage as audit


audit.FAMILIES["xg_chance_quality"] = tuple(
    token for token in audit.FAMILIES["xg_chance_quality"] if token != "xa"
)


def season_safe_schedule_features(
    competition_id: str,
    sample_keys: set[str],
    raw_lookup: dict[str, dict[str, str]],
) -> dict[str, dict[str, Any]]:
    matches = audit.read_processed_matches(competition_id)
    by_date: dict[datetime, list[Any]] = defaultdict(list)
    for match in matches:
        by_date[match.date].append(match)

    last_date: dict[str, datetime] = {}
    recent_dates: dict[str, deque[datetime]] = defaultdict(deque)
    table: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"played": 0, "points": 0, "gd": 0}
    )
    shot_history: Counter[str] = Counter()
    state_history: Counter[str] = Counter()
    derived: dict[str, dict[str, Any]] = {}

    for date in sorted(by_date):
        day = sorted(by_date[date], key=lambda match: (match.home_team, match.away_team))
        for match in day:
            key = (
                f"{match.season}|{match.date.date().isoformat()}|"
                f"{match.home_team}|{match.away_team}"
            )
            if key not in sample_keys:
                continue
            for team in (match.home_team, match.away_team):
                cutoff = date - timedelta(days=14)
                while recent_dates[team] and recent_dates[team][0] < cutoff:
                    recent_dates[team].popleft()
            home_last = last_date.get(match.home_team)
            away_last = last_date.get(match.away_team)
            home_table = table[(match.season, match.home_team)]
            away_table = table[(match.season, match.away_team)]
            derived[key] = {
                "rest_days_home": None if home_last is None else (date - home_last).days,
                "rest_days_away": None if away_last is None else (date - away_last).days,
                "matches_14d_home": len(recent_dates[match.home_team]),
                "matches_14d_away": len(recent_dates[match.away_team]),
                "standings_available": True,
                "home_matches_played_before": home_table["played"],
                "away_matches_played_before": away_table["played"],
                "points_gap_before": home_table["points"] - away_table["points"],
                "goal_difference_gap_before": home_table["gd"] - away_table["gd"],
                "shot_proxy_history_available": (
                    shot_history[match.home_team] >= audit.MIN_HISTORY
                    and shot_history[match.away_team] >= audit.MIN_HISTORY
                ),
                "game_state_history_available": (
                    state_history[match.home_team] >= audit.MIN_HISTORY
                    and state_history[match.away_team] >= audit.MIN_HISTORY
                ),
            }

        # Freeze all matches on the date before applying any same-day results.
        for match in day:
            key = (
                f"{match.season}|{match.date.date().isoformat()}|"
                f"{match.home_team}|{match.away_team}"
            )
            raw = raw_lookup.get(key, {})
            home_goals = int(match.home_goals)
            away_goals = int(match.away_goals)
            home_points = 3 if home_goals > away_goals else 1 if home_goals == away_goals else 0
            away_points = 3 if away_goals > home_goals else 1 if home_goals == away_goals else 0
            home_table = table[(match.season, match.home_team)]
            away_table = table[(match.season, match.away_team)]
            home_table["played"] += 1
            away_table["played"] += 1
            home_table["points"] += home_points
            away_table["points"] += away_points
            home_table["gd"] += home_goals - away_goals
            away_table["gd"] += away_goals - home_goals
            last_date[match.home_team] = date
            last_date[match.away_team] = date
            recent_dates[match.home_team].append(date)
            recent_dates[match.away_team].append(date)
            if audit.complete_fields(raw, ("HS", "AS", "HST", "AST", "HC", "AC")):
                shot_history[match.home_team] += 1
                shot_history[match.away_team] += 1
            if audit.complete_fields(raw, ("HTHG", "HTAG", "FTHG", "FTAG")):
                state_history[match.home_team] += 1
                state_history[match.away_team] += 1

    return derived


audit.same_day_schedule_features = season_safe_schedule_features


if __name__ == "__main__":
    raise SystemExit(audit.main())
