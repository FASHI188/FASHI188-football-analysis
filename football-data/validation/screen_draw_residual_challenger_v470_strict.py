#!/usr/bin/env python3
"""Leakage-safe entrypoint for the V4.7 draw residual research screen.

This wrapper replaces the base research script's training-season collector with a
strict season-start-year gate. Any fold whose season starts in or after the target
season is excluded, including transition labels such as 2026_special when testing
2025. The underlying challenger remains research-only with formal weight 0.
"""
from __future__ import annotations

import re

import screen_draw_residual_challenger_v470 as base


def _season_start_year(season: str) -> int:
    match = re.match(r"^(20\d{2})", str(season).strip())
    if not match:
        raise base.PlatformError(f"cannot resolve season start year: {season!r}")
    return int(match.group(1))


def _strict_season_training_rows(cid, report, all_matches, target_season):
    features = []
    labels = []
    seasons_used = []
    target_year = _season_start_year(target_season)
    folds = report.get("folds") or []
    for fold in folds:
        season = str(fold.get("outer_season") or "")
        if not season:
            continue
        try:
            season_year = _season_start_year(season)
        except base.PlatformError:
            continue
        if season_year >= target_year:
            continue
        params = fold.get("selected_parameters")
        if not isinstance(params, dict):
            continue
        matches = sorted(
            [m for m in all_matches if str(m.season) == season],
            key=lambda m: (m.date, m.home_team, m.away_team),
        )
        if not matches:
            continue
        temperature, _ = base._target_season_temperature(cid, season)
        season_count = 0
        for match in matches:
            try:
                _, history = base.current_season_history(all_matches, match.date, season)
                matrix = base._predict_from_loaded_matches(
                    all_matches,
                    match.home_team,
                    match.away_team,
                    match.date,
                    season,
                    params,
                )
            except base.PlatformError:
                continue
            if abs(temperature - 1.0) > 1e-15:
                matrix = base.temperature_scale_matrix(matrix, temperature)
            one = base.derive_score_marginals(matrix)["1x2"]
            prior = float(params.get("team_prior_matches", 8.0))
            features.append(
                base._venue_draw_features(
                    history,
                    match.home_team,
                    match.away_team,
                    one,
                    matrix,
                    prior,
                )
            )
            labels.append(1 if match.home_goals == match.away_goals else 0)
            season_count += 1
        if season_count:
            seasons_used.append(season)
    if any(_season_start_year(season) >= target_year for season in seasons_used):
        raise base.PlatformError("future or target season leaked into draw residual training")
    return features, labels, seasons_used


base._season_training_rows = _strict_season_training_rows


if __name__ == "__main__":
    raise SystemExit(base.main())
