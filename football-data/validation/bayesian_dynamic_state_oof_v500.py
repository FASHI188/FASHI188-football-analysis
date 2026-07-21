#!/usr/bin/env python3
"""V5 competition-local Bayesian dynamic-state rolling OOF challenger.

The challenger maintains diagonal-Gaussian attack and defensive-weakness states
for each team and updates them sequentially with an assumed-density Poisson filter.
Every prediction uses only matches strictly before the target match.  Candidate
profiles are chosen for each outer target season using only earlier completed
seasons from the same competition.  The candidate starts from the final calibrated
formal matrix and applies two auditable minimum-KL exponential tilts:

1. a total-goals tilt to a blended dynamic total mean;
2. a within-total home-goal tilt to a blended dynamic home share.

The second tilt preserves every P(T=t), so the result remains one coherent score
matrix.  This is research only: formal weight remains zero unless a later CURRENT-
compliant competition/season promotion receipt is issued.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    FORMAL_STATUS,
    REPORT_ROOT,
    _actual_result,
    _fold_for_season,
    _one_x_two_brier,
    _one_x_two_rps,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, derive_score_marginals, load_json, read_processed_matches, score_matrix_rows, top_scores

OUT = ROOT / "manifests" / "bayesian_dynamic_state_oof_v500_status.json"
REPORT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v500"
EPS = 1e-15
SEED = 5002026
BOOTSTRAP_DRAWS = 1200
BLOCK_SIZE = 20
MIN_PRIOR_SELECTION_ROWS = 300

PROFILES = [
    {"id": "conservative", "process_noise_per_day": 0.00025, "half_life_days": 365.0, "prior_variance": 0.16, "total_weight": 0.20, "share_weight": 0.30, "league_prior_matches": 60.0},
    {"id": "slow_share", "process_noise_per_day": 0.00050, "half_life_days": 240.0, "prior_variance": 0.22, "total_weight": 0.18, "share_weight": 0.50, "league_prior_matches": 50.0},
    {"id": "medium_balanced", "process_noise_per_day": 0.00120, "half_life_days": 160.0, "prior_variance": 0.30, "total_weight": 0.35, "share_weight": 0.55, "league_prior_matches": 40.0},
    {"id": "fast_balanced", "process_noise_per_day": 0.00250, "half_life_days": 90.0, "prior_variance": 0.40, "total_weight": 0.45, "share_weight": 0.65, "league_prior_matches": 30.0},
    {"id": "result_focus", "process_noise_per_day": 0.00160, "half_life_days": 120.0, "prior_variance": 0.34, "total_weight": 0.12, "share_weight": 0.75, "league_prior_matches": 40.0},
    {"id": "total_focus", "process_noise_per_day": 0.00100, "half_life_days": 180.0, "prior_variance": 0.28, "total_weight": 0.65, "share_weight": 0.42, "league_prior_matches": 40.0},
]


@dataclass
class TeamState:
    attack_mean: float
    attack_var: float
    defence_mean: float
    defence_var: float
    last_date: datetime | None


def _season_year(season: str) -> int:
    token = str(season).strip()
    if len(token) < 4 or not token[:4].isdigit():
        raise PlatformError(f"cannot parse season year: {season!r}")
    return int(token[:4])


def _completed_outer_seasons(competition_id: str, report: dict[str, Any]) -> list[str]:
    cap = _season_year(_requested_last_complete_season(competition_id))
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "").strip()
        if season and _season_year(season) <= cap and season not in seasons:
            seasons.append(season)
    return sorted(seasons, key=_season_year)


def _clip(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _logit(p: float) -> float:
    p = _clip(float(p), 1e-6, 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def _logistic(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _matrix_probability_sum(matrix: list[dict[str, Any]]) -> float:
    return sum(float(cell["probability"]) for cell in matrix)


def _matrix_means(matrix: list[dict[str, Any]]) -> tuple[float, float, float]:
    home = away = 0.0
    for h, a, p in score_matrix_rows(matrix):
        home += h * p
        away += a * p
    total = home + away
    return home, away, total


def _renormalize(matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = _matrix_probability_sum(matrix)
    if total <= 0.0 or not math.isfinite(total):
        raise PlatformError("candidate matrix has non-finite or zero mass")
    return [{"home_goals": int(c["home_goals"]), "away_goals": int(c["away_goals"]), "probability": float(c["probability"]) / total} for c in matrix]


def _total_tilt(matrix: list[dict[str, Any]], target_mean: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [(int(c["home_goals"]), int(c["away_goals"]), float(c["probability"])) for c in matrix]
    support = [h + a for h, a, _ in rows]
    target = _clip(float(target_mean), min(support) + 1e-8, max(support) - 1e-8)

    def expectation(theta: float) -> tuple[float, float, list[float]]:
        logs = [math.log(max(EPS, p)) + theta * t for (_, _, p), t in zip(rows, support)]
        anchor = max(logs)
        weights = [math.exp(value - anchor) for value in logs]
        z = sum(weights)
        probs = [w / z for w in weights]
        return sum(p * t for p, t in zip(probs, support)), z, probs

    lo, hi = -20.0, 20.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        value, _, _ = expectation(mid)
        if value < target:
            lo = mid
        else:
            hi = mid
    theta = (lo + hi) / 2.0
    achieved, _, probs = expectation(theta)
    out = [{"home_goals": h, "away_goals": a, "probability": p} for (h, a, _), p in zip(rows, probs)]
    return _renormalize(out), {"theta_total": theta, "target_total_mean": target, "achieved_total_mean": achieved, "total_mean_residual": achieved - target}


def _home_share_tilt_preserve_totals(matrix: list[dict[str, Any]], target_home_mean: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    groups: dict[int, list[tuple[int, int, float]]] = defaultdict(list)
    total_mass: dict[int, float] = defaultdict(float)
    for h, a, p in score_matrix_rows(matrix):
        groups[h + a].append((h, a, p))
        total_mass[h + a] += p
    total_mean = sum(t * mass for t, mass in total_mass.items())
    target = _clip(float(target_home_mean), 1e-8, max(1e-8, total_mean - 1e-8))

    def transformed(eta: float) -> tuple[float, list[dict[str, Any]]]:
        out: list[dict[str, Any]] = []
        achieved = 0.0
        for total, items in groups.items():
            logs = [math.log(max(EPS, p)) + eta * h for h, _, p in items]
            anchor = max(logs)
            weights = [math.exp(value - anchor) for value in logs]
            z = sum(weights)
            mass = total_mass[total]
            for (h, a, _), weight in zip(items, weights):
                probability = mass * weight / z
                achieved += h * probability
                out.append({"home_goals": h, "away_goals": a, "probability": probability})
        return achieved, out

    lo, hi = -20.0, 20.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        value, _ = transformed(mid)
        if value < target:
            lo = mid
        else:
            hi = mid
    eta = (lo + hi) / 2.0
    achieved, out = transformed(eta)
    before_total = _total_distribution(matrix)
    normalized = _renormalize(out)
    after_total = _total_distribution(normalized)
    residual = max(abs(after_total.get(k, 0.0) - before_total.get(k, 0.0)) for k in set(before_total) | set(after_total))
    return normalized, {"eta_home_share": eta, "target_home_mean": target, "achieved_home_mean": achieved, "home_mean_residual": achieved - target, "max_total_marginal_residual": residual}


def _total_distribution(matrix: list[dict[str, Any]]) -> dict[int, float]:
    out: dict[int, float] = defaultdict(float)
    for h, a, p in score_matrix_rows(matrix):
        out[h + a] += p
    return dict(out)


def _evolve(team: TeamState, date: datetime, profile: dict[str, Any]) -> None:
    if team.last_date is None:
        team.last_date = date
        return
    days = max(0.0, (date - team.last_date).total_seconds() / 86400.0)
    decay = math.exp(-math.log(2.0) * days / max(1.0, float(profile["half_life_days"])))
    prior_var = float(profile["prior_variance"])
    process = float(profile["process_noise_per_day"]) * min(days, 90.0)
    team.attack_mean *= decay
    team.defence_mean *= decay
    team.attack_var = _clip(prior_var + (team.attack_var - prior_var) * decay * decay + process, 0.02, 2.5)
    team.defence_var = _clip(prior_var + (team.defence_var - prior_var) * decay * decay + process, 0.02, 2.5)
    team.last_date = date


def _new_state(profile: dict[str, Any]) -> TeamState:
    variance = float(profile["prior_variance"])
    return TeamState(0.0, variance, 0.0, variance, None)


def _poisson_pair_update(first_mean: float, first_var: float, second_mean: float, second_var: float, base_rate: float, observed: int) -> tuple[float, float, float, float, float]:
    linear = _clip(math.log(max(0.05, base_rate)) + first_mean + second_mean, -4.0, 3.0)
    lam = math.exp(linear)
    information = max(1e-8, lam)
    denom = 1.0 + information * (first_var + second_var)
    residual = float(observed) - lam
    first_mean += first_var * residual / denom
    second_mean += second_var * residual / denom
    first_var = max(0.015, first_var - information * first_var * first_var / denom)
    second_var = max(0.015, second_var - information * second_var * second_var / denom)
    return _clip(first_mean, -2.5, 2.5), first_var, _clip(second_mean, -2.5, 2.5), second_var, lam


def _update_states(states: dict[str, TeamState], home: str, away: str, date: datetime, home_goals: int, away_goals: int, league_home_rate: float, league_away_rate: float, profile: dict[str, Any]) -> dict[str, float]:
    hs = states.setdefault(home, _new_state(profile))
    aws = states.setdefault(away, _new_state(profile))
    _evolve(hs, date, profile)
    _evolve(aws, date, profile)
    hs.attack_mean, hs.attack_var, aws.defence_mean, aws.defence_var, pred_h = _poisson_pair_update(
        hs.attack_mean, hs.attack_var, aws.defence_mean, aws.defence_var, league_home_rate, home_goals
    )
    aws.attack_mean, aws.attack_var, hs.defence_mean, hs.defence_var, pred_a = _poisson_pair_update(
        aws.attack_mean, aws.attack_var, hs.defence_mean, hs.defence_var, league_away_rate, away_goals
    )
    if states:
        average_attack = mean(item.attack_mean for item in states.values())
        for item in states.values():
            item.attack_mean -= average_attack
            item.defence_mean += average_attack
    return {"filter_pred_home": pred_h, "filter_pred_away": pred_a}


def _dynamic_rates(states: dict[str, TeamState], home: str, away: str, date: datetime, league_home_rate: float, league_away_rate: float, profile: dict[str, Any]) -> tuple[float, float, dict[str, Any]]:
    hs = states.setdefault(home, _new_state(profile))
    aws = states.setdefault(away, _new_state(profile))
    _evolve(hs, date, profile)
    _evolve(aws, date, profile)
    home_rate = math.exp(_clip(math.log(max(0.05, league_home_rate)) + hs.attack_mean + aws.defence_mean, -3.0, 2.0))
    away_rate = math.exp(_clip(math.log(max(0.05, league_away_rate)) + aws.attack_mean + hs.defence_mean, -3.0, 2.0))
    return _clip(home_rate, 0.08, 5.0), _clip(away_rate, 0.08, 5.0), {
        "home_attack_mean": hs.attack_mean,
        "home_attack_var": hs.attack_var,
        "home_defence_mean": hs.defence_mean,
        "home_defence_var": hs.defence_var,
        "away_attack_mean": aws.attack_mean,
        "away_attack_var": aws.attack_var,
        "away_defence_mean": aws.defence_mean,
        "away_defence_var": aws.defence_var,
    }


def _candidate_from_baseline(baseline: list[dict[str, Any]], dynamic_home: float, dynamic_away: float, profile: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_home, base_away, base_total = _matrix_means(baseline)
    dynamic_total = dynamic_home + dynamic_away
    total_weight = float(profile["total_weight"])
    share_weight = float(profile["share_weight"])
    target_total = math.exp((1.0 - total_weight) * math.log(max(EPS, base_total)) + total_weight * math.log(max(EPS, dynamic_total)))
    base_share = base_home / max(EPS, base_total)
    dynamic_share = dynamic_home / max(EPS, dynamic_total)
    target_share = _logistic((1.0 - share_weight) * _logit(base_share) + share_weight * _logit(dynamic_share))
    total_tilted, total_audit = _total_tilt(baseline, target_total)
    final, share_audit = _home_share_tilt_preserve_totals(total_tilted, target_total * target_share)
    audit = {
        "baseline_total_mean": base_total,
        "dynamic_total_mean": dynamic_total,
        "target_total_mean": target_total,
        "baseline_home_share": base_share,
        "dynamic_home_share": dynamic_share,
        "target_home_share": target_share,
        **total_audit,
        **share_audit,
        "probability_sum_residual": abs(_matrix_probability_sum(final) - 1.0),
    }
    return final, audit


def _total8(matrix: list[dict[str, Any]]) -> list[float]:
    out = [0.0] * 8
    for h, a, p in score_matrix_rows(matrix):
        out[min(7, h + a)] += p
    return out


def _total_rps(probabilities: list[float], actual_total: int) -> float:
    actual = min(7, int(actual_total))
    running = 0.0
    score = 0.0
    for index, probability in enumerate(probabilities[:-1]):
        running += probability
        observed = 1.0 if actual <= index else 0.0
        score += (running - observed) ** 2
    return score / 7.0


def _joint_log(matrix: list[dict[str, Any]], hg: int, ag: int) -> float:
    probability = sum(p for h, a, p in score_matrix_rows(matrix) if h == hg and a == ag)
    return -math.log(max(EPS, probability))


def _metric_row(matrix: list[dict[str, Any]], match) -> dict[str, Any]:
    marginals = derive_score_marginals(matrix)
    one = {key: float(marginals["1x2"][key]) for key in ("home", "draw", "away")}
    actual = _actual_result(int(match.home_goals), int(match.away_goals))
    pick = max(("home", "draw", "away"), key=lambda key: one[key])
    score_rank = top_scores(matrix, 3)
    observed_score = f"{int(match.home_goals)}-{int(match.away_goals)}"
    totals = _total8(matrix)
    total_rank = sorted(range(8), key=lambda index: (-totals[index], index))
    observed_total = min(7, int(match.home_goals) + int(match.away_goals))
    return {
        "one_x_two_accuracy": 1.0 if pick == actual else 0.0,
        "one_x_two_brier": _one_x_two_brier(one, actual),
        "one_x_two_rps": _one_x_two_rps(one, actual),
        "joint_log": _joint_log(matrix, int(match.home_goals), int(match.away_goals)),
        "score_top1": 1.0 if score_rank and score_rank[0]["score"] == observed_score else 0.0,
        "score_top3": 1.0 if any(item["score"] == observed_score for item in score_rank) else 0.0,
        "total_top1": 1.0 if total_rank[0] == observed_total else 0.0,
        "total_top2": 1.0 if observed_total in total_rank[:2] else 0.0,
        "total_rps": _total_rps(totals, observed_total),
        "probability_sum_residual": abs(float(marginals["probability_sum"]) - 1.0),
    }


def _prior_league_rates(all_matches, target_season: str) -> tuple[float, float, int]:
    year = _season_year(target_season)
    prior = [m for m in all_matches if _season_year(str(m.season)) < year]
    if not prior:
        raise PlatformError(f"no strictly prior season matches for {target_season}")
    return mean(float(m.home_goals) for m in prior), mean(float(m.away_goals) for m in prior), len(prior)


def _simulate_season(competition_id: str, season: str, all_matches, report: dict[str, Any]) -> dict[str, Any]:
    fold = _fold_for_season(report, season)
    selected_parameters = fold.get("selected_parameters")
    if not isinstance(selected_parameters, dict):
        raise PlatformError(f"missing frozen parameters for {competition_id} {season}")
    target_matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    if not target_matches:
        raise PlatformError(f"no target matches for {competition_id} {season}")
    prior_home, prior_away, prior_count = _prior_league_rates(all_matches, season)
    temperature, calibration_mode = _target_season_temperature(competition_id, season)

    states = {profile["id"]: {} for profile in PROFILES}
    league = {
        profile["id"]: {
            "home_alpha": prior_home * float(profile["league_prior_matches"]),
            "home_beta": float(profile["league_prior_matches"]),
            "away_alpha": prior_away * float(profile["league_prior_matches"]),
            "away_beta": float(profile["league_prior_matches"]),
        }
        for profile in PROFILES
    }
    rows = {profile["id"]: [] for profile in PROFILES}
    baseline_rows = []
    skipped = 0
    max_residual = 0.0
    max_total_residual = 0.0

    for match in target_matches:
        baseline = None
        try:
            baseline = _predict_from_loaded_matches(
                all_matches, match.home_team, match.away_team, match.date, season, selected_parameters
            )
            if abs(temperature - 1.0) > 1e-15:
                baseline = temperature_scale_matrix(baseline, temperature)
        except PlatformError:
            skipped += 1

        if baseline is not None:
            base_metrics = _metric_row(baseline, match)
            row_key = f"{competition_id}:{season}:{match.date.date().isoformat()}:{match.home_team}:{match.away_team}"
            base_row = {"match_key": row_key, "season": season, "date": match.date.date().isoformat(), **base_metrics}
            baseline_rows.append(base_row)
            for profile in PROFILES:
                pid = profile["id"]
                lg = league[pid]
                league_home = lg["home_alpha"] / lg["home_beta"]
                league_away = lg["away_alpha"] / lg["away_beta"]
                dyn_home, dyn_away, state_audit = _dynamic_rates(states[pid], match.home_team, match.away_team, match.date, league_home, league_away, profile)
                candidate, tilt_audit = _candidate_from_baseline(baseline, dyn_home, dyn_away, profile)
                metrics = _metric_row(candidate, match)
                max_residual = max(max_residual, float(metrics["probability_sum_residual"]), abs(float(tilt_audit["probability_sum_residual"])))
                max_total_residual = max(max_total_residual, abs(float(tilt_audit["max_total_marginal_residual"])))
                rows[pid].append({"match_key": row_key, "season": season, "date": match.date.date().isoformat(), "profile_id": pid, **metrics, "audit": {**state_audit, **tilt_audit}})

        for profile in PROFILES:
            pid = profile["id"]
            lg = league[pid]
            league_home = lg["home_alpha"] / lg["home_beta"]
            league_away = lg["away_alpha"] / lg["away_beta"]
            _update_states(states[pid], match.home_team, match.away_team, match.date, int(match.home_goals), int(match.away_goals), league_home, league_away, profile)
            lg["home_alpha"] += int(match.home_goals)
            lg["home_beta"] += 1.0
            lg["away_alpha"] += int(match.away_goals)
            lg["away_beta"] += 1.0

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
        "max_probability_sum_residual": max_residual,
        "max_total_marginal_residual": max_total_residual,
    }


def _selection_objective(rows: list[dict[str, Any]]) -> float:
    return (
        mean(float(row["one_x_two_rps"]) for row in rows)
        + 0.25 * mean(float(row["one_x_two_brier"]) for row in rows)
        + 3.0 * mean(float(row["total_rps"]) for row in rows)
        + 0.02 * mean(float(row["joint_log"]) for row in rows)
    )


def _blocks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (row["season"], row["date"], row["match_key"]))
    return [ordered[index:index + BLOCK_SIZE] for index in range(0, len(ordered), BLOCK_SIZE)]


def _bootstrap(rows: list[dict[str, Any]], candidate_key: str, baseline_key: str, seed: int) -> dict[str, Any]:
    blocks = _blocks(rows)
    point = mean(float(row[candidate_key]) - float(row[baseline_key]) for row in rows)
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled = []
        for _ in range(len(blocks)):
            sampled.extend(rng.choice(blocks))
        samples.append(mean(float(row[candidate_key]) - float(row[baseline_key]) for row in sampled))
    samples.sort()
    lo = samples[int(0.025 * (len(samples) - 1))]
    hi = samples[int(0.975 * (len(samples) - 1))]
    return {"mean_difference": point, "ci95_lower": lo, "ci95_upper": hi, "blocks": len(blocks), "draws": BOOTSTRAP_DRAWS}


def _paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = ["one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log", "score_top1", "score_top3", "total_top1", "total_top2", "total_rps"]
    summary = {}
    for metric in metrics:
        base = mean(float(row[f"baseline_{metric}"]) for row in rows)
        cand = mean(float(row[f"candidate_{metric}"]) for row in rows)
        summary[metric] = {"baseline": base, "candidate": cand, "candidate_minus_baseline": cand - base}
    return summary


def _validate_domain(competition_id: str) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{competition_id}.json")
    seasons = _completed_outer_seasons(competition_id, report)
    if len(seasons) < 3:
        raise PlatformError(f"need at least three completed outer seasons for {competition_id}")
    all_matches = read_processed_matches(competition_id)
    simulations = {season: _simulate_season(competition_id, season, all_matches, report) for season in seasons}
    outer_rows: list[dict[str, Any]] = []
    folds = []

    for target_index in range(2, len(seasons)):
        target = seasons[target_index]
        prior_seasons = seasons[:target_index]
        scored_profiles = []
        for profile in PROFILES:
            pid = profile["id"]
            prior_rows = [row for season in prior_seasons for row in simulations[season]["profiles"][pid]]
            if len(prior_rows) < MIN_PRIOR_SELECTION_ROWS:
                continue
            scored_profiles.append({"profile_id": pid, "objective": _selection_objective(prior_rows), "selection_rows": len(prior_rows)})
        if not scored_profiles:
            folds.append({"target_season": target, "status": "NO_PRIOR_PROFILE_SELECTION", "prior_seasons": prior_seasons})
            continue
        scored_profiles.sort(key=lambda item: (item["objective"], item["profile_id"]))
        selected = scored_profiles[0]
        pid = selected["profile_id"]
        base_map = {row["match_key"]: row for row in simulations[target]["baseline"]}
        candidate_map = {row["match_key"]: row for row in simulations[target]["profiles"][pid]}
        keys = sorted(set(base_map) & set(candidate_map))
        season_rows = []
        for key in keys:
            base = base_map[key]
            cand = candidate_map[key]
            row = {"match_key": key, "season": target, "date": base["date"], "selected_profile": pid}
            for metric in ("one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log", "score_top1", "score_top3", "total_top1", "total_top2", "total_rps"):
                row[f"baseline_{metric}"] = base[metric]
                row[f"candidate_{metric}"] = cand[metric]
            season_rows.append(row)
            outer_rows.append(row)
        folds.append({
            "target_season": target,
            "status": "EVALUATED_FORWARD_FROZEN_PROFILE",
            "prior_seasons": prior_seasons,
            "selected_profile": pid,
            "profile_selection_objective": selected["objective"],
            "profile_selection_rows": selected["selection_rows"],
            "outer_predictions": len(season_rows),
            "metrics": _paired_summary(season_rows) if season_rows else None,
        })

    if not outer_rows:
        raise PlatformError(f"no forward OOF rows for {competition_id}")
    pooled = _paired_summary(outer_rows)
    ci = {
        "one_x_two_brier": _bootstrap(outer_rows, "candidate_one_x_two_brier", "baseline_one_x_two_brier", SEED + 1),
        "one_x_two_rps": _bootstrap(outer_rows, "candidate_one_x_two_rps", "baseline_one_x_two_rps", SEED + 2),
        "joint_log": _bootstrap(outer_rows, "candidate_joint_log", "baseline_joint_log", SEED + 3),
        "total_rps": _bootstrap(outer_rows, "candidate_total_rps", "baseline_total_rps", SEED + 4),
    }
    evaluated_folds = [fold for fold in folds if fold.get("status") == "EVALUATED_FORWARD_FROZEN_PROFILE"]
    season_acc_diffs = [float(fold["metrics"]["one_x_two_accuracy"]["candidate_minus_baseline"]) for fold in evaluated_folds if fold.get("metrics")]
    max_prob_residual = max(float(simulations[season]["max_probability_sum_residual"]) for season in seasons)
    max_total_residual = max(float(simulations[season]["max_total_marginal_residual"]) for season in seasons)
    checks = {
        "at_least_two_forward_outer_seasons": len(evaluated_folds) >= 2,
        "minimum_outer_predictions_500": len(outer_rows) >= 500,
        "one_x_two_brier_ci_improves": ci["one_x_two_brier"]["ci95_upper"] < 0.0,
        "one_x_two_rps_ci_improves": ci["one_x_two_rps"]["ci95_upper"] < 0.0,
        "total_rps_ci_noninferior": ci["total_rps"]["ci95_upper"] <= 0.0005,
        "joint_log_ci_noninferior": ci["joint_log"]["ci95_upper"] <= 0.002,
        "one_x_two_accuracy_nonworse": pooled["one_x_two_accuracy"]["candidate"] + 1e-12 >= pooled["one_x_two_accuracy"]["baseline"],
        "score_top1_nonworse": pooled["score_top1"]["candidate"] + 1e-12 >= pooled["score_top1"]["baseline"],
        "score_top3_nonworse": pooled["score_top3"]["candidate"] + 1e-12 >= pooled["score_top3"]["baseline"],
        "total_top1_nonworse": pooled["total_top1"]["candidate"] + 1e-12 >= pooled["total_top1"]["baseline"],
        "probability_conservation": max_prob_residual <= 1e-10,
        "total_projection_conservation": max_total_residual <= 1e-10,
        "handicap_fourth_target_available": false
    }
    probability_checks = {key: value for key, value in checks.items() if key != "handicap_fourth_target_available"}
    review_candidate = all(probability_checks.values())
    status = "RESEARCH_REVIEW_CANDIDATE_AH_PENDING" if review_candidate else "KEEP_FORMAL_WEIGHT_0"
    return {
        "schema_version": "V5.0.0-bayesian-dynamic-state-oof-domain-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": competition_id,
        "status": status,
        "formal_weight": 0,
        "automatic_promotion": false,
        "probability_change": false,
        "completed_outer_seasons": seasons,
        "candidate_profiles": PROFILES,
        "profile_selection_objective": "mean_1x2_rps + 0.25*mean_1x2_brier + 3*mean_total_rps + 0.02*mean_joint_log",
        "outer_prediction_count": len(outer_rows),
        "evaluated_outer_season_count": len(evaluated_folds),
        "folds": folds,
        "pooled_metrics": pooled,
        "paired_block_bootstrap": ci,
        "season_one_x_two_accuracy_difference_min": min(season_acc_diffs) if season_acc_diffs else None,
        "season_one_x_two_accuracy_difference_std": pstdev(season_acc_diffs) if len(season_acc_diffs) > 1 else 0.0 if season_acc_diffs else None,
        "max_probability_sum_residual": max_prob_residual,
        "max_total_marginal_residual": max_total_residual,
        "checks": checks,
        "handicap_target_status": "UNAVAILABLE_NO_COMPLETE_POINT_IN_TIME_FROZEN_HANDICAP_LINES_IN_CURRENT_17_DOMAIN_REPLAY",
        "policy": "Research only. Even a probability-side review candidate remains formal_weight=0 until the handicap fourth target is independently evaluated where frozen lines exist and a future CURRENT-compliant competition/season promotion receipt is issued."
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports = {}
    failures = {}
    candidates = []
    for competition_id in competitions:
        try:
            report = _validate_domain(competition_id)
            reports[competition_id] = report
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(REPORT_DIR / f"{competition_id}.json", report)
            if report["status"] == "RESEARCH_REVIEW_CANDIDATE_AH_PENDING":
                candidates.append(competition_id)
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"
    payload = {
        "schema_version": "V5.0.0-bayesian-dynamic-state-oof-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "research_review_candidates_ah_pending": candidates,
        "reports": {competition_id: {
            "status": report["status"],
            "outer_prediction_count": report["outer_prediction_count"],
            "evaluated_outer_season_count": report["evaluated_outer_season_count"],
            "pooled_metrics": report["pooled_metrics"],
            "paired_block_bootstrap": report["paired_block_bootstrap"],
            "checks": report["checks"],
            "handicap_target_status": report["handicap_target_status"]
        } for competition_id, report in reports.items()},
        "failures": failures,
        "formal_weight_change": false,
        "probability_change": false,
        "automatic_promotion": false,
        "policy": "17-domain strict chronological research screen. Profiles are frozen from earlier completed seasons. No result alters V5 formal probabilities without competition-specific promotion and fourth-target handicap evidence."
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({"status": payload["status"], "completed": len(reports), "candidates": candidates, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
