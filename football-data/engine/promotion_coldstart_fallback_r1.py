#!/usr/bin/env python3
"""Offline promoted-team cold-start fallback for live prematch runtime R1.

Research operational-shadow only. A promoted club's prior Championship GF/GA is
translated to an equivalent Premier League rate with robust median log-ratios
learned from completed historical promotion cohorts. Established-club venue
strength, league baselines, dispersion and score construction all come from the
existing pre-freeze Premier League history and V4.6 primitives. No target-season
result, manual promotion penalty, market input or runtime network call is used.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from football_v460_engine import build_score_matrix, fit_current_season_state, load_config, low_score_factors
from platform_core import ROOT, PlatformError, derive_score_marginals, load_json, normalize_team_token, sha256_file, top_scores

CONFIG = ROOT / "config" / "promotion_coldstart_calibration_r1.json"


def _shrunk_rate(num: float, den: float, prior_rate: float, prior_n: float) -> float:
    return (float(num) + float(prior_rate) * float(prior_n)) / max(1e-12, float(den) + float(prior_n))


def _translation(cal: dict[str, Any]) -> dict[str, Any]:
    attack, defence, rows = [], [], []
    for item in cal.get("historical_cohorts") or []:
        cm, pm = float(item["championship_matches"]), float(item["premier_league_matches"])
        cgf, cga = float(item["championship_gf"]) / cm, float(item["championship_ga"]) / cm
        pgf, pga = float(item["premier_league_gf"]) / pm, float(item["premier_league_ga"]) / pm
        a = math.log(max(1e-12, pgf) / max(1e-12, cgf))
        d = math.log(max(1e-12, pga) / max(1e-12, cga))
        attack.append(a); defence.append(d)
        rows.append({"team": item["team"], "attack_log_ratio": a, "defence_log_ratio": d})
    if len(rows) < 4:
        raise PlatformError("promotion calibration needs at least four completed clubs")
    a_med, d_med = statistics.median(attack), statistics.median(defence)
    return {
        "attack_log_median": a_med,
        "defence_log_median": d_med,
        "attack_multiplier": math.exp(a_med),
        "defence_multiplier": math.exp(d_med),
        "cohort_count": len(rows),
        "cohorts": rows,
    }


def _promoted_prior(team: str, cal: dict[str, Any], tr: dict[str, Any]) -> dict[str, Any] | None:
    targets = cal.get("target_prior_season_championship") or {}
    item = targets.get(team)
    if item is None:
        token = normalize_team_token(team)
        matches = [v for name, v in targets.items() if normalize_team_token(name) == token]
        item = matches[0] if len(matches) == 1 else None
    if item is None:
        return None
    n = float(item["matches"])
    if n <= 0:
        raise PlatformError(f"invalid Championship prior for {team}")
    gf, ga = float(item["gf"]) / n, float(item["ga"]) / n
    return {
        "source_season": item["season"], "matches": int(n),
        "championship_gf_per_match": gf, "championship_ga_per_match": ga,
        "equivalent_pl_gf_per_match": gf * float(tr["attack_multiplier"]),
        "equivalent_pl_ga_per_match": ga * float(tr["defence_multiplier"]),
    }


def _established(state: dict[str, Any], team_name: str, venue: str, params: dict[str, float]) -> dict[str, float]:
    team = (state.get("team") or {}).get(normalize_team_token(team_name))
    if not isinstance(team, dict):
        raise PlatformError(f"established team missing from PL history: {team_name}")
    prior = float(params["team_prior_matches"])
    if venue == "home":
        gf0, ga0 = float(state["league_home_goals"]), float(state["league_away_goals"])
        n, gf, ga = float(team.get("home_matches", 0)), float(team.get("home_gf", 0)), float(team.get("home_ga", 0))
    else:
        gf0, ga0 = float(state["league_away_goals"]), float(state["league_home_goals"])
        n, gf, ga = float(team.get("away_matches", 0)), float(team.get("away_gf", 0)), float(team.get("away_ga", 0))
    return {"gf_rate": _shrunk_rate(gf, n, gf0, prior), "ga_rate": _shrunk_rate(ga, n, ga0, prior), "matches": n}


def predict_promotion_coldstart(history, competition_id: str, home_team: str, away_team: str, freeze, params: dict[str, float]) -> dict[str, Any]:
    if competition_id != "ENG_PremierLeague":
        raise PlatformError("promotion cold-start R1 currently supports ENG_PremierLeague only")
    cal = load_json(CONFIG)
    tr = _translation(cal)
    hp, ap = _promoted_prior(home_team, cal, tr), _promoted_prior(away_team, cal, tr)
    if hp is None and ap is None:
        raise PlatformError("neither club has a frozen promoted-team prior")

    cfg = load_config()
    merged = dict(cfg["default_parameters"]); merged.update({k: float(v) for k, v in params.items()})
    state = fit_current_season_state(list(history), freeze, merged, cfg)
    lh, la = float(state["league_home_goals"]), float(state["league_away_goals"])
    lt = 0.5 * (lh + la)

    if hp:
        h_a, h_d, h_audit = hp["equivalent_pl_gf_per_match"] / lt, hp["equivalent_pl_ga_per_match"] / lt, hp
    else:
        h = _established(state, home_team, "home", merged)
        h_a, h_d, h_audit = h["gf_rate"] / lh, h["ga_rate"] / la, h
    if ap:
        a_a, a_d, a_audit = ap["equivalent_pl_gf_per_match"] / lt, ap["equivalent_pl_ga_per_match"] / lt, ap
    else:
        a = _established(state, away_team, "away", merged)
        a_a, a_d, a_audit = a["gf_rate"] / la, a["ga_rate"] / lh, a

    lo, hi = float(merged["minimum_goal_mean"]), float(merged["maximum_goal_mean"])
    mu_h = min(hi, max(lo, lh * h_a * a_d))
    mu_a = min(hi, max(lo, la * a_a * h_d))
    factors = low_score_factors(state, merged)
    matrix = build_score_matrix(mu_h, mu_a, float(state["nb_dispersion_k"]), float(merged["beta_binomial_concentration"]), int(cfg["max_total_goals_exact"]), factors)
    marg = derive_score_marginals(matrix)
    return {
        "competition_id": competition_id, "season": "PROMOTION_COLDSTART_SHADOW", "history_matches": len(history),
        "team_sample": {"mu_home": mu_h, "mu_away": mu_a, "mu_total": mu_h + mu_a, "ess": 0.0},
        "probabilities": {"one_x_two": marg["1x2"], "total_goals": marg["total_goals"], "btts_yes": marg["btts_yes"], "score_matrix": matrix},
        "top_scores": top_scores(matrix, 10),
        "audit": {
            "classification": "OPERATIONAL_SHADOW_PROMOTION_COLDSTART_R1", "formal_weight": 0,
            "calibration_path": str(CONFIG.relative_to(ROOT)), "calibration_sha256": sha256_file(CONFIG),
            "translation": tr, "home_promoted_prior": hp, "away_promoted_prior": ap,
            "home_rate_audit": h_audit, "away_rate_audit": a_audit,
            "league_home_goals": lh, "league_away_goals": la, "nb_dispersion_k": float(state["nb_dispersion_k"]),
            "target_result_used": False, "manual_promotion_penalty": False, "external_runtime_network_required": False
        }
    }
