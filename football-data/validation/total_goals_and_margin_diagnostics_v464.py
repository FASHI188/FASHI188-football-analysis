#!/usr/bin/env python3
"""V4.6.4 research diagnostics for total-goal shrinkage and margin/BTTS bias.

This script is deliberately CHALLENGER/DIAGNOSTIC only.  It never changes the
formal V4.6.x engine or model artifacts.  It performs chronological same-season
rolling evaluation and compares the current direct-total formulation with a
less-shrunk adaptive-total-prior challenger.  It also audits BTTS, clean-sheet
and two-plus-goal margin calibration from the unchanged base joint matrix.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from football_v460_engine import (
    build_score_matrix,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
    negative_binomial_pmf,
)
from platform_core import ROOT, PlatformError, derive_score_marginals, load_json, normalize_team_token, read_processed_matches

REGISTRY = ROOT / "config" / "platform_registry.json"
OUT = ROOT / "manifests" / "total_goals_and_margin_diagnostics_v464_status.json"


def total_vector(mean: float, k: float) -> list[float]:
    values = [negative_binomial_pmf(i, mean, k) for i in range(7)]
    tail = max(0.0, 1.0 - sum(values))
    values.append(tail)
    total = sum(values)
    return [v / total for v in values]


def rps(probs: list[float], actual_index: int) -> float:
    cp = 0.0
    co = 0.0
    score = 0.0
    for i in range(len(probs) - 1):
        cp += probs[i]
        co += 1.0 if actual_index == i else 0.0
        score += (cp - co) ** 2
    return score / (len(probs) - 1)


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def adaptive_total_mean(state: dict[str, Any], home_team: str, away_team: str, params: dict[str, float]) -> tuple[float, float]:
    home = state["team"][normalize_team_token(home_team)]
    away = state["team"][normalize_team_token(away_team)]
    league_total = float(state["mean_total_goals"])
    base_prior = float(params["team_prior_matches"])
    ess = min(float(home["home_matches"]), float(away["away_matches"]))
    # Research candidate: keep strong protection at tiny samples, then release
    # shrinkage gradually as venue-specific effective sample grows.
    adaptive_prior = max(1.5, min(base_prior, base_prior * 6.0 / (6.0 + max(0.0, ess))))

    def shrunk(numerator: float, denominator: float) -> float:
        return (numerator + league_total * adaptive_prior) / max(1e-12, denominator + adaptive_prior)

    home_rate = shrunk(float(home["home_gf"] + home["home_ga"]), float(home["home_matches"]))
    away_rate = shrunk(float(away["away_gf"] + away["away_ga"]), float(away["away_matches"]))
    pair = math.sqrt(max(1e-12, home_rate) * max(1e-12, away_rate))
    signal_weight = min(1.0, max(0.0, float(params.get("direct_total_signal_weight", 1.0))))
    mean = math.exp((1.0 - signal_weight) * math.log(max(1e-12, league_total)) + signal_weight * math.log(max(1e-12, pair)))
    return mean, adaptive_prior


def event_probs(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {"btts": 0.0, "home_zero": 0.0, "away_zero": 0.0, "margin2plus": 0.0}
    for cell in matrix:
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        p = float(cell["probability"])
        if h > 0 and a > 0:
            out["btts"] += p
        if h == 0:
            out["home_zero"] += p
        if a == 0:
            out["away_zero"] += p
        if abs(h - a) >= 2:
            out["margin2plus"] += p
    return out


def main() -> int:
    config = load_config()
    params = {key: float(value) for key, value in config["default_parameters"].items()}
    registry = load_json(REGISTRY)
    competition_ids = [row["competition_id"] for row in registry.get("competitions", [])]

    global_rows: list[dict[str, Any]] = []
    competition_reports: dict[str, Any] = {}

    for competition_id in competition_ids:
        try:
            matches = read_processed_matches(competition_id)
        except Exception as exc:
            competition_reports[competition_id] = {"status": "不可用", "reason": str(exc)}
            continue
        seasons: dict[str, list[Any]] = defaultdict(list)
        for match in matches:
            seasons[str(match.season)].append(match)

        comp_rows: list[dict[str, Any]] = []
        for season, season_matches in seasons.items():
            season_matches.sort(key=lambda m: (m.date, m.home_team, m.away_team))
            if len(season_matches) < max(40, int(config["minimum_competition_history_matches"])):
                continue
            for target in season_matches:
                history = [m for m in season_matches if m.date.date() < target.date.date()]
                if len(history) < int(config["minimum_competition_history_matches"]):
                    continue
                try:
                    state = fit_current_season_state(history, target.date, params, config)
                    base_means = expected_goals(state, target.home_team, target.away_team, params, config)
                    challenger_mean, adaptive_prior = adaptive_total_mean(state, target.home_team, target.away_team, params)
                except (PlatformError, KeyError, ValueError):
                    continue

                actual_total = int(target.home_goals + target.away_goals)
                actual_index = actual_total if actual_total <= 6 else 7
                base_probs = total_vector(float(base_means["mu_total"]), float(state["nb_dispersion_k"]))
                challenger_probs = total_vector(challenger_mean, float(state["nb_dispersion_k"]))

                factors = low_score_factors(state, params)
                matrix = build_score_matrix(
                    float(base_means["mu_home"]),
                    float(base_means["mu_away"]),
                    float(state["nb_dispersion_k"]),
                    float(params["beta_binomial_concentration"]),
                    int(config["max_total_goals_exact"]),
                    factors,
                )
                events = event_probs(matrix)
                row = {
                    "competition_id": competition_id,
                    "season": season,
                    "date": target.date.date().isoformat(),
                    "base_rps": rps(base_probs, actual_index),
                    "challenger_rps": rps(challenger_probs, actual_index),
                    "base_mean": float(base_means["mu_total"]),
                    "challenger_mean": challenger_mean,
                    "adaptive_prior": adaptive_prior,
                    "actual_total": actual_total,
                    "base_p7plus": base_probs[7],
                    "challenger_p7plus": challenger_probs[7],
                    "actual_7plus": 1.0 if actual_total >= 7 else 0.0,
                    "btts_p": events["btts"],
                    "btts_y": 1.0 if target.home_goals > 0 and target.away_goals > 0 else 0.0,
                    "home_zero_p": events["home_zero"],
                    "home_zero_y": 1.0 if target.home_goals == 0 else 0.0,
                    "away_zero_p": events["away_zero"],
                    "away_zero_y": 1.0 if target.away_goals == 0 else 0.0,
                    "margin2plus_p": events["margin2plus"],
                    "margin2plus_y": 1.0 if abs(target.home_goals - target.away_goals) >= 2 else 0.0,
                }
                comp_rows.append(row)
                global_rows.append(row)

        if not comp_rows:
            competition_reports[competition_id] = {"status": "不可用", "reason": "no eligible chronological rolling predictions"}
            continue

        n = len(comp_rows)
        base_rps_mean = sum(r["base_rps"] for r in comp_rows) / n
        challenger_rps_mean = sum(r["challenger_rps"] for r in comp_rows) / n
        competition_reports[competition_id] = {
            "status": "通过",
            "n": n,
            "base_total_rps": base_rps_mean,
            "challenger_total_rps": challenger_rps_mean,
            "challenger_minus_base_rps": challenger_rps_mean - base_rps_mean,
            "base_mean_total_average": sum(r["base_mean"] for r in comp_rows) / n,
            "challenger_mean_total_average": sum(r["challenger_mean"] for r in comp_rows) / n,
            "btts_predicted_mean": sum(r["btts_p"] for r in comp_rows) / n,
            "btts_actual_rate": sum(r["btts_y"] for r in comp_rows) / n,
            "home_zero_predicted_mean": sum(r["home_zero_p"] for r in comp_rows) / n,
            "home_zero_actual_rate": sum(r["home_zero_y"] for r in comp_rows) / n,
            "away_zero_predicted_mean": sum(r["away_zero_p"] for r in comp_rows) / n,
            "away_zero_actual_rate": sum(r["away_zero_y"] for r in comp_rows) / n,
            "margin2plus_predicted_mean": sum(r["margin2plus_p"] for r in comp_rows) / n,
            "margin2plus_actual_rate": sum(r["margin2plus_y"] for r in comp_rows) / n,
        }

    n = len(global_rows)
    if n:
        base = sum(r["base_rps"] for r in global_rows) / n
        challenger = sum(r["challenger_rps"] for r in global_rows) / n
        global_summary = {
            "n": n,
            "base_total_rps": base,
            "challenger_total_rps": challenger,
            "challenger_minus_base_rps": challenger - base,
            "btts_predicted_mean": sum(r["btts_p"] for r in global_rows) / n,
            "btts_actual_rate": sum(r["btts_y"] for r in global_rows) / n,
            "home_zero_predicted_mean": sum(r["home_zero_p"] for r in global_rows) / n,
            "home_zero_actual_rate": sum(r["home_zero_y"] for r in global_rows) / n,
            "away_zero_predicted_mean": sum(r["away_zero_p"] for r in global_rows) / n,
            "away_zero_actual_rate": sum(r["away_zero_y"] for r in global_rows) / n,
            "margin2plus_predicted_mean": sum(r["margin2plus_p"] for r in global_rows) / n,
            "margin2plus_actual_rate": sum(r["margin2plus_y"] for r in global_rows) / n,
        }
    else:
        global_summary = {"n": 0}

    swe = competition_reports.get("SWE_Allsvenskan", {})
    promotion = (
        "REVIEW_CANDIDATE"
        if swe.get("status") == "通过" and swe.get("challenger_minus_base_rps", 0.0) < 0.0
        else "KEEP_FORMAL_WEIGHT_0"
    )
    report = {
        "schema_version": "1.0",
        "revision": "V4.6.4",
        "status": "PASS" if n else "FAIL",
        "formal_weight": 0,
        "purpose": "diagnose total-goal over-shrinkage and BTTS/clean-sheet/two-plus-margin calibration; challenger is not auto-promoted",
        "method": "chronological same-season rolling evaluation; target-season team strength only; default frozen parameters; no future match in history",
        "challenger": "ESS-adaptive direct-total prior release; still a direct venue-total model, not mu_home+mu_away substitution",
        "global": global_summary,
        "competitions": competition_reports,
        "swe_allsvenskan_promotion_review": promotion,
        "promotion_policy": "formal_weight remains 0 until nested time-ordered validation and CURRENT-compliant promotion approve it",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "global": global_summary, "SWE_Allsvenskan": swe, "promotion": promotion}, ensure_ascii=False, indent=2))
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
