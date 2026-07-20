#!/usr/bin/env python3
"""Leakage-safe entrypoint for the V4.7 draw residual research screen.

This wrapper replaces the base research script's training-season collector with a
strict season-start-year gate. Any fold whose season starts in or after the target
season is excluded, including transition labels such as 2026_special when testing
2025. It also preserves zero-probability total layers unchanged during the research
matrix transform. The underlying challenger remains research-only with formal
weight 0.
"""
from __future__ import annotations

import math
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


def _safe_tilt_diagonal_to_target(matrix, target_draw):
    rows = list(base.score_matrix_rows(matrix))
    even_mass = sum(p for h, a, p in rows if (h + a) % 2 == 0)
    if even_mass <= 0.0:
        return [
            {"home_goals": h, "away_goals": a, "probability": p}
            for h, a, p in rows
        ], 0.0, 0.0
    target = min(even_mass - 1e-9, max(1e-9, float(target_draw)))
    low, high = -20.0, 20.0
    for _ in range(80):
        mid = 0.5 * (low + high)
        value = base._draw_probability_after_lambda(matrix, mid)
        if value < target:
            low = mid
        else:
            high = mid
    lam = 0.5 * (low + high)
    exp_lam = math.exp(lam)
    grouped = {}
    for h, a, p in rows:
        grouped.setdefault(h + a, []).append((h, a, p))
    output = []
    max_total_residual = 0.0
    for _, items in grouped.items():
        total_mass = sum(p for _, _, p in items)
        if total_mass <= 0.0:
            output.extend(
                {"home_goals": h, "away_goals": a, "probability": 0.0}
                for h, a, _ in items
            )
            continue
        weights = [(h, a, p * (exp_lam if h == a else 1.0)) for h, a, p in items]
        denominator = sum(w for _, _, w in weights)
        if denominator <= 0.0 or not math.isfinite(denominator):
            output.extend(
                {"home_goals": h, "away_goals": a, "probability": p}
                for h, a, p in items
            )
            continue
        transformed = [(h, a, total_mass * w / denominator) for h, a, w in weights]
        max_total_residual = max(
            max_total_residual,
            abs(sum(p for _, _, p in transformed) - total_mass),
        )
        output.extend(
            {"home_goals": h, "away_goals": a, "probability": p}
            for h, a, p in transformed
        )
    return output, lam, max_total_residual


base._season_training_rows = _strict_season_training_rows
base._tilt_diagonal_to_target = _safe_tilt_diagonal_to_target


if __name__ == "__main__":
    raise SystemExit(base.main())
