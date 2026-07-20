#!/usr/bin/env python3
"""Research-only V4.8 candidate: direct categorical 0..7+ total-goals challenger.

Purpose
-------
Challenge the current formal direct-NB total-goals track without optimizing for
cosmetic score diversity.  The candidate predicts P(T=0),...,P(T=7+) directly
from same-season, strictly prior, competition-local evidence.

Key properties
--------------
* no mu_home + mu_away construction of P(T);
* no cross-competition rows, parameters, calibrators, or weights;
* compact predeclared candidate grid;
* nested chronological candidate selection;
* paired outer-OOS comparison against the CURRENT Champion final chain;
* current point-in-time OOF temperature is applied to both Champion and candidate
  as a conservative drop-in compatibility test;
* mode-3 frequency and weak-peak frequency are diagnostics only, never objectives;
* formal_weight always remains 0.  Any formal use requires a future complete
  CURRENT upgrade plus candidate-specific calibration and promotion governance.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
VALIDATION_DIR = ROOT_DIR / "validation"
for item in (str(ENGINE_DIR), str(VALIDATION_DIR)):
    if item not in sys.path:
        sys.path.insert(0, item)

from football_v460_engine import load_config, predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import ROOT, MatchRow, PlatformError, derive_score_marginals, load_json, read_processed_matches
from total_goals_joint_integration_v466 import _replace_total_marginal

TOTAL_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7+")
EPS = 1e-15
BOOTSTRAP_RESAMPLES = 500
FORMAL_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
CALIBRATOR_ROOT = ROOT / "models" / "formal_core_v460"
OUT_ROOT = ROOT / "models" / "challengers_v480"
STATUS_ROOT = ROOT / "manifests" / "direct_total_distribution_v480"

# Compact, predeclared grid.  Identity is deliberately included as a valid
# selection outcome so the nested selector can refuse to alter the Champion.
CANDIDATES: tuple[dict[str, float | str], ...] = (
    {"id": "D0_identity", "half_life_days": 120.0, "league_prior": 8.0, "team_prior": 8.0, "venue_weight": 0.5, "signal_weight": 0.0},
    {"id": "D1_fast25", "half_life_days": 60.0, "league_prior": 6.0, "team_prior": 6.0, "venue_weight": 0.75, "signal_weight": 0.25},
    {"id": "D2_fast40", "half_life_days": 75.0, "league_prior": 8.0, "team_prior": 6.0, "venue_weight": 0.75, "signal_weight": 0.40},
    {"id": "D3_mid25", "half_life_days": 120.0, "league_prior": 10.0, "team_prior": 8.0, "venue_weight": 0.50, "signal_weight": 0.25},
    {"id": "D4_mid40", "half_life_days": 120.0, "league_prior": 10.0, "team_prior": 8.0, "venue_weight": 0.50, "signal_weight": 0.40},
    {"id": "D5_slow25", "half_life_days": 180.0, "league_prior": 12.0, "team_prior": 10.0, "venue_weight": 0.50, "signal_weight": 0.25},
    {"id": "D6_slow40", "half_life_days": 180.0, "league_prior": 12.0, "team_prior": 10.0, "venue_weight": 0.25, "signal_weight": 0.40},
)


def _bucket(total: int) -> int:
    return min(max(0, int(total)), 7)


def _weight(match_date: datetime, cutoff: datetime, half_life_days: float) -> float:
    age = max(0.0, (cutoff - match_date).total_seconds() / 86400.0)
    return math.exp(-math.log(2.0) * age / max(1e-9, float(half_life_days)))


def _normalize(values: list[float]) -> list[float]:
    clipped = [max(EPS, float(value)) for value in values]
    total = sum(clipped)
    if total <= 0 or not math.isfinite(total):
        raise PlatformError("invalid categorical total-goals normalization")
    return [value / total for value in clipped]


def _histogram(history: list[MatchRow], cutoff: datetime, half_life_days: float, predicate=None) -> tuple[list[float], float]:
    counts = [0.0] * 8
    mass = 0.0
    for match in history:
        if predicate is not None and not predicate(match):
            continue
        w = _weight(match.date, cutoff, half_life_days)
        counts[_bucket(match.home_goals + match.away_goals)] += w
        mass += w
    return counts, mass


def _posterior_hist(counts: list[float], mass: float, prior: list[float], prior_strength: float) -> list[float]:
    denominator = mass + max(0.0, float(prior_strength))
    if denominator <= 0:
        return _normalize(prior)
    return _normalize([
        counts[i] + max(0.0, float(prior_strength)) * prior[i]
        for i in range(8)
    ])


def _log_pool(left: list[float], right: list[float], right_weight: float) -> list[float]:
    w = min(1.0, max(0.0, float(right_weight)))
    if w <= 0:
        return _normalize(left)
    logs = [
        (1.0 - w) * math.log(max(EPS, left[i])) + w * math.log(max(EPS, right[i]))
        for i in range(8)
    ]
    maximum = max(logs)
    return _normalize([math.exp(value - maximum) for value in logs])


def categorical_total_distribution(
    history: list[MatchRow],
    match: MatchRow,
    champion_total: list[float],
    candidate: dict[str, Any],
) -> list[float]:
    signal_weight = float(candidate["signal_weight"])
    if signal_weight <= 0:
        return _normalize(champion_total)

    half_life = float(candidate["half_life_days"])
    league_counts, league_mass = _histogram(history, match.date, half_life)
    league = _posterior_hist(league_counts, league_mass, _normalize(champion_total), float(candidate["league_prior"]))

    home = match.home_team
    away = match.away_team
    home_venue_counts, home_venue_mass = _histogram(history, match.date, half_life, lambda row: row.home_team == home)
    away_venue_counts, away_venue_mass = _histogram(history, match.date, half_life, lambda row: row.away_team == away)
    home_all_counts, home_all_mass = _histogram(history, match.date, half_life, lambda row: row.home_team == home or row.away_team == home)
    away_all_counts, away_all_mass = _histogram(history, match.date, half_life, lambda row: row.home_team == away or row.away_team == away)

    prior_strength = float(candidate["team_prior"])
    home_venue = _posterior_hist(home_venue_counts, home_venue_mass, league, prior_strength)
    away_venue = _posterior_hist(away_venue_counts, away_venue_mass, league, prior_strength)
    home_all = _posterior_hist(home_all_counts, home_all_mass, league, prior_strength)
    away_all = _posterior_hist(away_all_counts, away_all_mass, league, prior_strength)

    venue_weight = min(1.0, max(0.0, float(candidate["venue_weight"])))
    home_profile = _log_pool(home_all, home_venue, venue_weight)
    away_profile = _log_pool(away_all, away_venue, venue_weight)
    pair_profile = _normalize([math.sqrt(home_profile[i] * away_profile[i]) for i in range(8)])
    # Preserve the Champion as the explicit prior and learn only a bounded,
    # competition-local categorical residual around it.
    return _log_pool(_normalize(champion_total), pair_profile, signal_weight)


def _rps(values: list[float], actual_index: int) -> float:
    cp = 0.0
    co = 0.0
    score = 0.0
    for index in range(len(values) - 1):
        cp += values[index]
        co += 1.0 if actual_index == index else 0.0
        score += (cp - co) ** 2
    return score / 7.0


def _multiclass_brier(values: list[float], actual_index: int) -> float:
    return sum((value - (1.0 if i == actual_index else 0.0)) ** 2 for i, value in enumerate(values))


def _score_probability(matrix: list[dict[str, Any]], home: int, away: int) -> float:
    for cell in matrix:
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            return float(cell["probability"])
    return EPS


def _one_x_two(matrix: list[dict[str, Any]]) -> list[float]:
    marg = derive_score_marginals(matrix)["1x2"]
    return [float(marg[key]) for key in ("home", "draw", "away")]


def _topk_hit(matrix: list[dict[str, Any]], home: int, away: int, k: int) -> float:
    ranked = sorted(matrix, key=lambda cell: (-float(cell["probability"]), int(cell["home_goals"]), int(cell["away_goals"])))[:k]
    return 1.0 if any(int(cell["home_goals"]) == home and int(cell["away_goals"]) == away for cell in ranked) else 0.0


def _score_set_hit(matrix: list[dict[str, Any]], target: float, home: int, away: int) -> float:
    ranked = sorted(matrix, key=lambda cell: (-float(cell["probability"]), int(cell["home_goals"]), int(cell["away_goals"])))
    cumulative = 0.0
    hit = False
    for cell in ranked:
        cumulative += float(cell["probability"])
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            hit = True
        if cumulative + 1e-12 >= target:
            return 1.0 if hit else 0.0
    return 1.0 if hit else 0.0


def _tail(values: list[float], threshold: int) -> float:
    return sum(values[threshold:]) if threshold < 7 else values[7]


def _peak(values: list[float]) -> tuple[int, float, float]:
    ranked = sorted(enumerate(values), key=lambda item: (-item[1], item[0]))
    return ranked[0][0], ranked[0][1], ranked[0][1] - ranked[1][1]


def _formal_parameters_by_season(cid: str) -> dict[str, dict[str, Any]]:
    path = FORMAL_REPORT_ROOT / f"{cid}.json"
    if not path.exists():
        raise PlatformError(f"formal report missing: {cid}")
    report = load_json(path)
    result = {}
    for fold in report.get("folds") or []:
        season = fold.get("outer_season")
        params = fold.get("selected_parameters")
        if season is not None and isinstance(params, dict):
            result[str(season)] = dict(params)
    return result


def _season_calibrators(cid: str) -> dict[str, dict[str, Any]]:
    path = CALIBRATOR_ROOT / cid / "oof_matrix_calibrator.json"
    if not path.exists():
        return {}
    artifact = load_json(path)
    mapping = artifact.get("season_calibrators")
    return mapping if isinstance(mapping, dict) else {}


def _team_counts(history: list[MatchRow]) -> tuple[Counter[str], Counter[str]]:
    home = Counter()
    away = Counter()
    for match in history:
        home[match.home_team] += 1
        away[match.away_team] += 1
    return home, away


def evaluate_season(
    cid: str,
    season_matches: list[MatchRow],
    formal_params: dict[str, Any],
    season_calibrator: dict[str, Any] | None,
    candidate: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    by_date: dict[datetime, list[MatchRow]] = defaultdict(list)
    for match in season_matches:
        by_date[match.date].append(match)
    history: list[MatchRow] = []
    output = []
    sequence = 0
    warmup_comp = int(config["validation"]["warmup_competition_matches"])
    warmup_team = int(config["validation"]["warmup_team_matches"])
    temperature = float((season_calibrator or {}).get("temperature", 1.0))
    training_max_raw = (season_calibrator or {}).get("training_max_date")
    training_max = date.fromisoformat(str(training_max_raw)) if training_max_raw else None

    for match_date in sorted(by_date):
        home_counts, away_counts = _team_counts(history)
        for match in sorted(by_date[match_date], key=lambda row: (row.home_team, row.away_team)):
            if len(history) < warmup_comp or home_counts[match.home_team] < warmup_team or away_counts[match.away_team] < warmup_team:
                continue
            try:
                base = predict_from_history(
                    history, cid, str(match.season), match.home_team, match.away_team,
                    match.date, selected_parameters=formal_params, use_team_effects=True,
                )
            except (PlatformError, KeyError, ValueError):
                continue
            base_matrix = base["probabilities"]["score_matrix"]
            base_marg = derive_score_marginals(base_matrix)
            champion_total = [float(base_marg["total_goals"][key]) for key in TOTAL_KEYS]
            candidate_total = categorical_total_distribution(history, match, champion_total, candidate)
            target = {key: candidate_total[i] for i, key in enumerate(TOTAL_KEYS)}
            candidate_matrix = _replace_total_marginal(base_matrix, target)

            current_final = temperature_scale_matrix(base_matrix, temperature) if temperature != 1.0 else base_matrix
            candidate_final = temperature_scale_matrix(candidate_matrix, temperature) if temperature != 1.0 else candidate_matrix
            current_marg = derive_score_marginals(current_final)
            candidate_marg = derive_score_marginals(candidate_final)
            current_total = [float(current_marg["total_goals"][key]) for key in TOTAL_KEYS]
            candidate_total_final = [float(candidate_marg["total_goals"][key]) for key in TOTAL_KEYS]
            h = int(match.home_goals)
            a = int(match.away_goals)
            actual_total = h + a
            actual_total_index = min(actual_total, 7)
            actual_outcome = 0 if h > a else 1 if h == a else 2
            current_one = _one_x_two(current_final)
            candidate_one = _one_x_two(candidate_final)
            current_peak, _, current_gap = _peak(current_total)
            candidate_peak, _, candidate_gap = _peak(candidate_total_final)
            point_safe = training_max is None or training_max < match.date.date()
            output.append({
                "block_id": f"{match.season}:{sequence // 20}",
                "season": str(match.season),
                "date": match.date.date().isoformat(),
                "candidate_id": str(candidate["id"]),
                "point_in_time_calibration_safe": point_safe,
                "total_rps_diff": _rps(candidate_total_final, actual_total_index) - _rps(current_total, actual_total_index),
                "joint_log_diff": -math.log(max(EPS, _score_probability(candidate_final, h, a))) + math.log(max(EPS, _score_probability(current_final, h, a))),
                "one_x_two_brier_diff": _multiclass_brier(candidate_one, actual_outcome) - _multiclass_brier(current_one, actual_outcome),
                "one_x_two_rps_diff": _rps(candidate_one, actual_outcome) - _rps(current_one, actual_outcome),
                "tail4_brier_diff": ( _tail(candidate_total_final, 4) - (1.0 if actual_total >= 4 else 0.0) ) ** 2 - ( _tail(current_total, 4) - (1.0 if actual_total >= 4 else 0.0) ) ** 2,
                "tail5_brier_diff": ( _tail(candidate_total_final, 5) - (1.0 if actual_total >= 5 else 0.0) ) ** 2 - ( _tail(current_total, 5) - (1.0 if actual_total >= 5 else 0.0) ) ** 2,
                "tail7_brier_diff": ( _tail(candidate_total_final, 7) - (1.0 if actual_total >= 7 else 0.0) ) ** 2 - ( _tail(current_total, 7) - (1.0 if actual_total >= 7 else 0.0) ) ** 2,
                "current_top1": _topk_hit(current_final, h, a, 1),
                "current_top3": _topk_hit(current_final, h, a, 3),
                "current_top5": _topk_hit(current_final, h, a, 5),
                "candidate_top1": _topk_hit(candidate_final, h, a, 1),
                "candidate_top3": _topk_hit(candidate_final, h, a, 3),
                "candidate_top5": _topk_hit(candidate_final, h, a, 5),
                "current_cover80": _score_set_hit(current_final, 0.80, h, a),
                "current_cover90": _score_set_hit(current_final, 0.90, h, a),
                "candidate_cover80": _score_set_hit(candidate_final, 0.80, h, a),
                "candidate_cover90": _score_set_hit(candidate_final, 0.90, h, a),
                "current_peak_bucket": current_peak,
                "candidate_peak_bucket": candidate_peak,
                "actual_total_bucket": actual_total_index,
                "current_weak_peak": 1.0 if current_gap < 0.02 else 0.0,
                "candidate_weak_peak": 1.0 if candidate_gap < 0.02 else 0.0,
                "probability_residual": abs(float(candidate_marg["probability_sum"]) - 1.0),
            })
            sequence += 1
        history.extend(by_date[match_date])
        history.sort(key=lambda row: (row.date, row.home_team, row.away_team))
    return output


def _bootstrap_ci(rows: list[dict[str, Any]], field: str, seed: int) -> dict[str, Any]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        blocks[str(row["block_id"])].append(float(row[field]))
    values = list(blocks.values())
    if not values:
        return {"count": 0, "mean_difference": None, "ci95_lower": None, "ci95_upper": None}
    observed = mean(value for block in values for value in block)
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        chosen = [rng.choice(values) for _ in values]
        samples.append(mean(value for block in chosen for value in block))
    samples.sort()
    return {
        "count": sum(len(block) for block in values),
        "blocks": len(values),
        "mean_difference": observed,
        "ci95_lower": samples[max(0, int(0.025 * len(samples)) - 1)],
        "ci95_upper": samples[min(len(samples) - 1, int(0.975 * len(samples)))],
    }


def _selection_objective(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return float("inf")
    # Total RPS is the primary target. Small penalties prevent winning total RPS
    # by badly degrading the coherent joint/1X2 distribution.
    return mean(
        float(row["total_rps_diff"])
        + 0.10 * max(0.0, float(row["joint_log_diff"]))
        + 0.05 * max(0.0, float(row["one_x_two_rps_diff"]))
        for row in rows
    )


def _mode_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if not count:
        return {}
    current_modes = Counter(int(row["current_peak_bucket"]) for row in rows)
    candidate_modes = Counter(int(row["candidate_peak_bucket"]) for row in rows)
    return {
        "current_mode3_rate": current_modes.get(3, 0) / count,
        "candidate_mode3_rate": candidate_modes.get(3, 0) / count,
        "current_unique_peak_buckets": sorted(current_modes),
        "candidate_unique_peak_buckets": sorted(candidate_modes),
        "current_weak_peak_rate": mean(float(row["current_weak_peak"]) for row in rows),
        "candidate_weak_peak_rate": mean(float(row["candidate_weak_peak"]) for row in rows),
        "current_total_top1_accuracy": mean(1.0 if int(row["current_peak_bucket"]) == int(row["actual_total_bucket"]) else 0.0 for row in rows),
        "candidate_total_top1_accuracy": mean(1.0 if int(row["candidate_peak_bucket"]) == int(row["actual_total_bucket"]) else 0.0 for row in rows),
        "note": "diagnostic only; no candidate is selected or promoted for reducing mode-3 frequency or increasing visual diversity",
    }


def validate_competition(cid: str) -> dict[str, Any]:
    matches = read_processed_matches(cid)
    season_map: dict[str, list[MatchRow]] = defaultdict(list)
    for match in matches:
        season_map[str(match.season)].append(match)
    seasons = sorted(season_map, key=lambda season: min(row.date for row in season_map[season]))
    for rows in season_map.values():
        rows.sort(key=lambda row: (row.date, row.home_team, row.away_team))
    formal_params = _formal_parameters_by_season(cid)
    calibrators = _season_calibrators(cid)
    config = load_config()

    cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for season in seasons:
        params = formal_params.get(season)
        if params is None:
            continue
        for candidate in CANDIDATES:
            cache[(season, str(candidate["id"]))] = evaluate_season(
                cid, season_map[season], params, calibrators.get(season), candidate, config
            )

    outer_rows = []
    folds = []
    for outer_index in range(1, len(seasons)):
        outer_season = seasons[outer_index]
        if outer_season not in formal_params:
            continue
        prior_seasons = [season for season in seasons[:outer_index] if season in formal_params]
        if not prior_seasons:
            continue
        scored = []
        for candidate in CANDIDATES:
            cid_ = str(candidate["id"])
            prior_rows = [row for season in prior_seasons for row in cache.get((season, cid_), [])]
            scored.append((_selection_objective(prior_rows), cid_, candidate, len(prior_rows)))
        scored.sort(key=lambda item: (item[0], item[1]))
        _, selected_id, selected, selection_count = scored[0]
        test_rows = cache.get((outer_season, selected_id), [])
        if not test_rows:
            continue
        outer_rows.extend(test_rows)
        folds.append({
            "outer_season": outer_season,
            "prior_seasons": prior_seasons,
            "selected_candidate": selected,
            "selection_predictions": selection_count,
            "outer_predictions": len(test_rows),
            "mean_total_rps_diff": mean(float(row["total_rps_diff"]) for row in test_rows),
            "mean_joint_log_diff": mean(float(row["joint_log_diff"]) for row in test_rows),
        })

    if not outer_rows:
        raise PlatformError(f"no eligible categorical direct-total outer OOS rows: {cid}")

    ci = {
        "total_rps": _bootstrap_ci(outer_rows, "total_rps_diff", 4801),
        "joint_log": _bootstrap_ci(outer_rows, "joint_log_diff", 4802),
        "one_x_two_brier": _bootstrap_ci(outer_rows, "one_x_two_brier_diff", 4803),
        "one_x_two_rps": _bootstrap_ci(outer_rows, "one_x_two_rps_diff", 4804),
    }
    tail = {
        "tail4plus_brier_diff": mean(float(row["tail4_brier_diff"]) for row in outer_rows),
        "tail5plus_brier_diff": mean(float(row["tail5_brier_diff"]) for row in outer_rows),
        "tail7plus_brier_diff": mean(float(row["tail7_brier_diff"]) for row in outer_rows),
    }
    coverage = {
        "current_top1": mean(float(row["current_top1"]) for row in outer_rows),
        "candidate_top1": mean(float(row["candidate_top1"]) for row in outer_rows),
        "current_top3": mean(float(row["current_top3"]) for row in outer_rows),
        "candidate_top3": mean(float(row["candidate_top3"]) for row in outer_rows),
        "current_top5": mean(float(row["current_top5"]) for row in outer_rows),
        "candidate_top5": mean(float(row["candidate_top5"]) for row in outer_rows),
        "current_score80": mean(float(row["current_cover80"]) for row in outer_rows),
        "candidate_score80": mean(float(row["candidate_cover80"]) for row in outer_rows),
        "current_score90": mean(float(row["current_cover90"]) for row in outer_rows),
        "candidate_score90": mean(float(row["candidate_cover90"]) for row in outer_rows),
    }
    checks = {
        "minimum_outer_predictions": len(outer_rows) >= 300,
        "minimum_outer_folds": len(folds) >= 3,
        "total_rps_ci_improves_champion": float(ci["total_rps"]["ci95_upper"]) < 0.0,
        "joint_log_noninferior": float(ci["joint_log"]["ci95_upper"]) <= 0.002,
        "one_x_two_brier_noninferior": float(ci["one_x_two_brier"]["ci95_upper"]) <= 0.002,
        "one_x_two_rps_noninferior": float(ci["one_x_two_rps"]["ci95_upper"]) <= 0.002,
        "tail4_nonworse": tail["tail4plus_brier_diff"] <= 0.0,
        "tail5_nonworse": tail["tail5plus_brier_diff"] <= 0.0,
        "top1_nonworse": coverage["candidate_top1"] >= coverage["current_top1"],
        "top3_nonworse": coverage["candidate_top3"] >= coverage["current_top3"],
        "top5_nonworse": coverage["candidate_top5"] >= coverage["current_top5"],
        "score80_calibrated": 0.76 <= coverage["candidate_score80"] <= 0.84,
        "score90_calibrated": 0.86 <= coverage["candidate_score90"] <= 0.94,
        "point_in_time_calibration_safe": all(bool(row["point_in_time_calibration_safe"]) for row in outer_rows),
        "probability_conservation": max(float(row["probability_residual"]) for row in outer_rows) <= 1e-10,
    }
    status = "RECALIBRATION_REVIEW_CANDIDATE" if all(checks.values()) else "KEEP_RESEARCH_WEIGHT_0"

    latest = seasons[-1]
    earlier_counts = [len(season_map[season]) for season in seasons[:-1] if season_map[season]]
    tuning_seasons = seasons[:-1] if earlier_counts and len(season_map[latest]) < 0.85 * median(earlier_counts) else seasons
    live_scores = []
    for candidate in CANDIDATES:
        cid_ = str(candidate["id"])
        rows = [row for season in tuning_seasons for row in cache.get((season, cid_), [])]
        live_scores.append((_selection_objective(rows), cid_, candidate, len(rows)))
    live_scores.sort(key=lambda item: (item[0], item[1]))
    _, live_id, live_candidate, live_count = live_scores[0]

    payload = {
        "schema_version": "V4.8.0-direct-categorical-total-research-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "competition_id": cid,
        "formal_weight": 0,
        "automatic_promotion": False,
        "status": status,
        "target_live_season": latest,
        "live_candidate": live_candidate,
        "live_selection_predictions": live_count,
        "outer_predictions": len(outer_rows),
        "outer_folds": len(folds),
        "confidence_intervals": ci,
        "tail_brier_differences": tail,
        "score_coverage": coverage,
        "peak_diagnostics": _mode_diagnostics(outer_rows),
        "checks": checks,
        "folds": folds,
        "policy": (
            "Research only. This is not a registered V4.7 formal module. Passing only permits a second-stage "
            "candidate-specific OOF recalibration review and a future complete CURRENT upgrade; it never auto-promotes."
        ),
    }
    target = OUT_ROOT / cid
    target.mkdir(parents=True, exist_ok=True)
    (target / "direct_total_distribution.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATUS_ROOT.mkdir(parents=True, exist_ok=True)
    (STATUS_ROOT / f"{cid}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    args = parser.parse_args()
    try:
        result = validate_competition(args.competition)
    except Exception as exc:
        STATUS_ROOT.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema_version": "V4.8.0-direct-categorical-total-research-r1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "competition_id": args.competition,
            "status": "FAILED",
            "formal_weight": 0,
            "automatic_promotion": False,
            "reason": str(exc),
        }
        (STATUS_ROOT / f"{args.competition}.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({
        "competition_id": result["competition_id"],
        "status": result["status"],
        "live_candidate": result["live_candidate"],
        "total_rps_ci": result["confidence_intervals"]["total_rps"],
        "peak_diagnostics": result["peak_diagnostics"],
        "checks": result["checks"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
