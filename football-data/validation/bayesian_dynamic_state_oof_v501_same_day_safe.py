#!/usr/bin/env python3
"""Same-day-safe replacement for the invalidated V5 dynamic-state OOF replay.

Every match on a calendar date is predicted from the state available before that date.
No result from that date is applied until all matches for the date have been predicted.
The module reuses the V5.0.0 model and scoring logic, changing only the leakage-prone
update schedule. Formal weight remains zero.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import bayesian_dynamic_state_oof_v500 as base
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError


def simulate_season_same_day_safe(
    competition_id: str,
    season: str,
    all_matches,
    report: dict[str, Any],
) -> dict[str, Any]:
    fold = base._fold_for_season(report, season)
    selected_parameters = fold.get("selected_parameters")
    if not isinstance(selected_parameters, dict):
        raise PlatformError(f"missing frozen parameters for {competition_id} {season}")

    target_matches = sorted(
        [match for match in all_matches if str(match.season) == season],
        key=lambda match: (match.date, match.home_team, match.away_team),
    )
    if not target_matches:
        raise PlatformError(f"no target matches for {competition_id} {season}")

    prior_home, prior_away, prior_count = base._prior_league_rates(all_matches, season)
    temperature, calibration_mode = base._target_season_temperature(competition_id, season)

    states = {profile["id"]: {} for profile in base.PROFILES}
    league = {
        profile["id"]: {
            "home_alpha": prior_home * float(profile["league_prior_matches"]),
            "home_beta": float(profile["league_prior_matches"]),
            "away_alpha": prior_away * float(profile["league_prior_matches"]),
            "away_beta": float(profile["league_prior_matches"]),
        }
        for profile in base.PROFILES
    }
    rows = {profile["id"]: [] for profile in base.PROFILES}
    baseline_rows: list[dict[str, Any]] = []
    skipped = 0
    max_residual = 0.0
    max_total_residual = 0.0

    matches_by_day: dict[str, list[Any]] = defaultdict(list)
    for match in target_matches:
        matches_by_day[match.date.date().isoformat()].append(match)

    for day in sorted(matches_by_day):
        day_matches = sorted(matches_by_day[day], key=lambda match: (match.date, match.home_team, match.away_team))

        # Prediction phase: no same-day outcome has been applied yet.
        for match in day_matches:
            baseline = None
            try:
                baseline = base._predict_from_loaded_matches(
                    all_matches,
                    match.home_team,
                    match.away_team,
                    match.date,
                    season,
                    selected_parameters,
                )
                if abs(temperature - 1.0) > 1e-15:
                    baseline = temperature_scale_matrix(baseline, temperature)
            except PlatformError:
                skipped += 1

            if baseline is None:
                continue

            base_metrics = base._metric_row(baseline, match)
            row_key = (
                f"{competition_id}:{season}:{match.date.date().isoformat()}:"
                f"{match.home_team}:{match.away_team}"
            )
            baseline_rows.append(
                {
                    "match_key": row_key,
                    "season": season,
                    "date": match.date.date().isoformat(),
                    **base_metrics,
                }
            )

            for profile in base.PROFILES:
                profile_id = profile["id"]
                league_state = league[profile_id]
                league_home = league_state["home_alpha"] / league_state["home_beta"]
                league_away = league_state["away_alpha"] / league_state["away_beta"]
                dynamic_home, dynamic_away, state_audit = base._dynamic_rates(
                    states[profile_id],
                    match.home_team,
                    match.away_team,
                    match.date,
                    league_home,
                    league_away,
                    profile,
                )
                candidate, tilt_audit = base._candidate_from_baseline(
                    baseline,
                    dynamic_home,
                    dynamic_away,
                    profile,
                )
                metrics = base._metric_row(candidate, match)
                max_residual = max(
                    max_residual,
                    float(metrics["probability_sum_residual"]),
                    abs(float(tilt_audit["probability_sum_residual"])),
                )
                max_total_residual = max(
                    max_total_residual,
                    abs(float(tilt_audit["max_total_marginal_residual"])),
                )
                rows[profile_id].append(
                    {
                        "match_key": row_key,
                        "season": season,
                        "date": match.date.date().isoformat(),
                        "profile_id": profile_id,
                        **metrics,
                        "audit": {
                            **state_audit,
                            **tilt_audit,
                            "same_day_outcomes_withheld": True,
                        },
                    }
                )

        # Update phase: only after every match for the date has been predicted.
        for match in day_matches:
            for profile in base.PROFILES:
                profile_id = profile["id"]
                league_state = league[profile_id]
                league_home = league_state["home_alpha"] / league_state["home_beta"]
                league_away = league_state["away_alpha"] / league_state["away_beta"]
                base._update_states(
                    states[profile_id],
                    match.home_team,
                    match.away_team,
                    match.date,
                    int(match.home_goals),
                    int(match.away_goals),
                    league_home,
                    league_away,
                    profile,
                )
                league_state["home_alpha"] += int(match.home_goals)
                league_state["home_beta"] += 1.0
                league_state["away_alpha"] += int(match.away_goals)
                league_state["away_beta"] += 1.0

    return {
        "season": season,
        "baseline": baseline_rows,
        "profiles": rows,
        "target_match_count": len(target_matches),
        "baseline_eligible_count": len(baseline_rows),
        "baseline_skipped_count": skipped,
        "prior_league_match_count": prior_count,
        "prior_home_rate": prior_home,
        "prior_away_rate": prior_away,
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "same_day_outcomes_withheld": True,
        "max_probability_sum_residual": max_residual,
        "max_total_marginal_residual": max_total_residual,
    }


def validate_domain_same_day_safe(competition_id: str) -> dict[str, Any]:
    original = base._simulate_season
    base._simulate_season = simulate_season_same_day_safe
    try:
        report = base._validate_domain(competition_id)
    finally:
        base._simulate_season = original

    report["schema_version"] = "V5.0.1-bayesian-dynamic-state-oof-domain-r2"
    report["same_day_outcomes_withheld"] = True
    report["replaces_invalidated_v500_evidence"] = True
    report["invalidation_receipt"] = "manifests/bayesian_dynamic_state_v500_invalidation_status.json"
    report["formal_weight"] = 0
    report["automatic_promotion"] = False
    report["probability_change"] = False
    report["policy"] = (
        "Same-day-safe strict chronological research replay. Formal weight remains zero; "
        "no result may promote without replacement adjudication, fourth-target handicap "
        "evidence and a CURRENT-compliant hash-bound promotion receipt."
    )
    return report
