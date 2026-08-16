#!/usr/bin/env python3
"""Offline promoted-team cold-start fallback for live prematch runtime R1.

The target use case is a promoted club with no usable Premier League history at
matchweek 1. Runtime must not depend on a third-party API. Historical promotion
translation is frozen in config and derived from completed seasons only.

The fallback:
1. estimates an equivalent top-flight GF/GA rate for a promoted club by applying
   robust median log translation learned from historical promotion cohorts;
2. estimates established-club venue attack/defence from the existing PL history
   with the same recency/prior machinery used by the V4.6 engine;
3. combines the relative attack/defence signals around PL home/away baselines;
4. builds one NB + conditional Beta-Binomial score matrix with the existing V4.6
   primitives. No target-season result or manual promotion penalty is used.

Research operational shadow only; formal weight remains zero.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from football_v460_engine import (
    build_score_matrix,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import ROOT, PlatformError, derive_score_marginals, load_json, normalize_team_token, sha256_file, top_scores

CONFIG = ROOT / "config" / "promotion_coldstart_calibration_r1.json"


def _shrunk_rate(numerator: float, denominator: float, prior_rate: float, prior_matches: float) -> float:
    return (float(numerator) + float(prior_rate) * float(prior_matches)) / max(
        1e-12, float(denominator) + float(prior_matches)
    )


def _translation(calibration: dict[str, Any]) -> dict[str, Any]:
    attack_logs = []
    defence_logs = []
    rows = []
    for item in calibration.get("historical_cohorts") or []:
        cm = float(item["championship_matches"])
        pm = float(item["premier_league_matches"])
        cgf = float(item["championship_gf"]) / cm
        cga = float(item["championship_ga"]) / cm
        pgf = float(item["premier_league_gf"]) / pm
        pga = float(item["premier_league_ga"]) / pm
        attack = math.log(max(1e-12, pgf) / max(1e-12, cgf))
        defence = math.log(max(1e-12, pga) / max(1e-12, cga))
        attack_logs.append(attack)
        defence_logs.append(defence)
        rows.append({
            "team": item["team"],
            "attack_log_ratio": attack,
            "defence_log_ratio": defence,
        })
    if len(rows) < 4:
        raise PlatformError("promotion calibration requires at least four completed historical clubs")
    attack_median = statistics.median(attack_logs)
    defence_median = statistics.median(defence_logs)
    return {
        "attack_log_median": attack_median,
        "defence_log_median": defence_median,
        "attack_multiplier": math.exp(attack_median),
        "defence_multiplier": math.exp(defence_median),
        "cohort_count": len(rows),
        "cohorts": rows,
    }


def _target_promoted_equivalent(team: str, calibration: dict[str, Any], translation: dict[str, Any]) -> dict[str, Any] | None:
    targets = calibration.get("target_prior_season_championship") or {}
    item = targets.get(team)
    if item is None:
        token = normalize_team_token(team)
        matches = [(name, value) for name, value in targets.items() if normalize_team_token(name) == token]
        if len(matches) == 1:
            item = matches[0][1]
        else:
            return None
    n = float(item["matches"])
    if n <= 0:
        raise PlatformError(f"invalid prior-season Championship match count for {team}")
    raw_gf = float(item["gf"]) / n
    raw_ga = float(item["ga"]) / n
    return {
        "source_season": item["season"],
        "matches": int(n),
        "championship_gf_per_match": raw_gf,
        "championship_ga_per_match": raw_ga,
        "equivalent_pl_gf_per_match": raw_gf * float(translation["attack_multiplier"]),
        "equivalent_pl_ga_per_match": raw_ga * float(translation["defence_multiplier"]),
    }


def _established_rates(state: dict[str, Any], team_name: str, venue: str, params: dict[str, float]) -> dict[str, float]:
    key = normalize_team_token(team_name)
    team = (state.get("team") or {}).get(key)
    if not isinstance(team, dict):
        raise PlatformError(f"established team not present in PL history: {team_name}")
    prior = float(params["team_prior_matches"])
    if venue == "home":
        baseline_gf = float(state["league_home_goals"])
        baseline_ga = float(state["league_away_goals"])
        matches = float(team.get("home_matches", 0.0))
        gf = float(team.get("home_gf", 0.0))
        ga = float(team.get("home_ga", 0.0))
    elif venue == "away":
        baseline_gf = float(state["league_away_goals"])
        baseline_ga = float(state["league_home_goals"])
        matches = float(team.get("away_matches", 0.0))
        gf = float(team.get("away_gf", 0.0))
        ga = float(team.get("away_ga", 0.0))
    else:
        raise PlatformError(f"invalid venue: {venue}")
    return {
        "gf_rate": _shrunk_rate(gf, matches, baseline_gf, prior),
        "ga_rate": _shrunk_rate(ga, matches, baseline_ga, prior),
        "matches": matches,
    }


def predict_promotion_coldstart(
    history,
    competition_id: str,
    home_team: str,
    away_team: str,
    freeze,
    params: dict[str, float],
) -> dict[str, Any]:
    if competition_id != "ENG_PremierLeague":
        raise PlatformError("promotion cold-start R1 is enabled only for ENG_PremierLeague")
    if not CONFIG.exists():
        raise PlatformError("promotion cold-start calibration config missing")
    calibration = load_json(CONFIG)
    translation = _translation(calibration)
    home_promoted = _target_promoted_equivalent(home_team, calibration, translation)
    away_promoted = _target_promoted_equivalent(away_team, calibration, translation)
    if home_promoted is None and away_promoted is None:
        raise PlatformError("promotion cold-start invoked but neither club has frozen lower-division prior")

    cfg = load_config()
    merged_params = dict(cfg["default_parameters"])
    merged_params.update({key: float(value) for key, value in params.items()})
    state = fit_current_season_state(list(history), freeze, merged_params, cfg)
    league_home = float(state["league_home_goals"])
    league_away = float(state["league_away_goals"])
    league_team = 0.5 * (league_home + league_away)

    if home_promoted is not None:
        home_attack_ratio = float(home_promoted["equivalent_pl_gf_per_match"]) / max(1e-12, league_team)
        home_defence_ratio = float(home_promoted["equivalent_pl_ga_per_match"]) / max(1e-12, league_team)
        home_rate_audit = home_promoted
    else:
        home_rates = _established_rates(state, home_team, "home", merged_params)
        home_attack_ratio = home_rates["gf_rate"] / max(1e-12, league_home)
        home_defence_ratio = home_rates["ga_rate"] / max(1e-12, league_away)
        home_rate_audit = home_rates

    if away_promoted is not None:
        away_attack_ratio = float(away_promoted["equivalent_pl_gf_per_match"]) / max(1e-12, league_team)
        away_defence_ratio = float(away_promoted["equivalent_pl_ga_per_match"]) / max(1e-12, league_team)
        away_rate_audit = away_promoted
    else:
        away_rates = _established_rates(state, away_team, "away", merged_params)
        away_attack_ratio = away_rates["gf_rate"] / max(1e-12, league_away)
        away_defence_ratio = away_rates["ga_rate"] / max(1e-12, league_home)
        away_rate_audit = away_rates

    min_mu = float(merged_params["minimum_goal_mean"])
    max_mu = float(merged_params["maximum_goal_mean"])
    mu_home = league_home * home_attack_ratio * away_defence_ratio
    mu_away = league_away * away_attack_ratio * home_defence_ratio
    mu_home = min(max_mu, max(min_mu, mu_home))
    mu_away = min(max_mu, max(min_mu, mu_away))
    mu_total = mu_home + mu_away

    factors = low_score_factors(state, merged_params)
    matrix = build_score_matrix(
        mu_home,
        mu_away,
        float(state["nb_dispersion_k"]),
        float(merged_params["beta_binomial_concentration"]),
        int(cfg["max_total_goals_exact"]),
        factors,
    )
    marginals = derive_score_marginals(matrix)
    ranking = top_scores(matrix, 10)
    return {
        "competition_id": competition_id,
        "season": "PROMOTION_COLDSTART_SHADOW",
        "history_matches": len(history),
        "team_sample": {
            "mu_home": mu_home,
            "mu_away": mu_away,
            "mu_total": mu_total,
            "home_raw_matches": 0.0 if home_promoted else float(home_rate_audit.get("matches", 0.0)),
            "away_raw_matches": 0.0 if away_promoted else float(away_rate_audit.get("matches", 0.0)),
            "home_effective_matches": 0.0 if home_promoted else float(home_rate_audit.get("matches", 0.0)),
            "away_effective_matches": 0.0 if away_promoted else float(away_rate_audit.get("matches", 0.0)),
            "ess": 0.0,
        },
        "probabilities": {
            "one_x_two": marginals["1x2"],
            "total_goals": marginals["total_goals"],
            "btts_yes": marginals["btts_yes"],
            "score_matrix": matrix,
        },
        "top_scores": ranking,
        "audit": {
            "classification": "OPERATIONAL_SHADOW_PROMOTION_COLDSTART_R1",
            "formal_weight": 0,
            "calibration_path": str(CONFIG.relative_to(ROOT)),
            "calibration_sha256": sha256_file(CONFIG),
            "translation": translation,
            "home_promoted_prior": home_promoted,
            "away_promoted_prior": away_promoted,
            "home_rate_audit": home_rate_audit,
            "away_rate_audit": away_rate_audit,
            "league_home_goals": league_home,
            "league_away_goals": league_away,
            "league_team_goals": league_team,
            "nb_dispersion_k": float(state["nb_dispersion_k"]),
            "target_result_used": false if False else False,
            "manual_promotion_penalty": False,
            "external_runtime_network_required": False
        }
    }
