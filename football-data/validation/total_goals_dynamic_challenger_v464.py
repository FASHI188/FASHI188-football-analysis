#!/usr/bin/env python3
"""Research-only dynamic direct-total challenger with true rolling OOS selection.

This module predicts P(T) directly from same-season prior matches.  It never forms
P(T) by adding home and away scoring means.  Candidate model families are selected
only from strictly earlier rolling-origin evidence and are compared with a strong
league-only non-market baseline using total-goals RPS and paired block bootstrap.

Passing this challenger does NOT automatically change the formal center or issue an
A receipt.  Integration requires a separate CURRENT-compliant promotion step and a
full final-chain replay after the unified matrix is rebuilt around the selected P(T).
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import sys
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from football_v460_engine import load_config  # noqa: E402
from platform_core import (  # noqa: E402
    ROOT,
    MatchRow,
    PlatformError,
    atomic_write_json,
    load_json,
    load_registry,
    read_processed_matches,
    sha256_file,
    utc_now,
)

REPORT_ROOT = ROOT / "validation" / "reports" / "total_goals_dynamic_v464"
MANIFEST_PATH = ROOT / "manifests" / "total_goals_dynamic_v464_status.json"
POLICY_PATH = ROOT / "validation" / "promotion_policy.json"
SCRIPT_PATH = Path(__file__).resolve()
TOTAL_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7+")
WINDOWS_PER_OUTER_SEASON = 2
BOOTSTRAP_RESAMPLES = 2000
SEED = 464

# Predeclared literature-motivated candidates.  The grid is intentionally compact
# to reduce data-mining risk.  All parameters are frozen before outer evaluation.
CANDIDATES: list[dict[str, float | str]] = [
    {"id": "C0_current_nb", "half_life_days": 90.0, "team_prior_matches": 6.0, "venue_weight": 1.0, "signal_weight": 1.0, "poisson_blend": 0.0},
    {"id": "C1_shrunk_venue_nb", "half_life_days": 90.0, "team_prior_matches": 6.0, "venue_weight": 0.5, "signal_weight": 0.65, "poisson_blend": 0.0},
    {"id": "C2_shrunk_venue_mix", "half_life_days": 90.0, "team_prior_matches": 6.0, "venue_weight": 0.5, "signal_weight": 0.65, "poisson_blend": 0.5},
    {"id": "C3_mid_dynamic_mix", "half_life_days": 120.0, "team_prior_matches": 6.0, "venue_weight": 0.5, "signal_weight": 0.65, "poisson_blend": 0.5},
    {"id": "C4_slow_dynamic_nb", "half_life_days": 180.0, "team_prior_matches": 8.0, "venue_weight": 0.5, "signal_weight": 0.65, "poisson_blend": 0.0},
    {"id": "C5_allvenue_mix", "half_life_days": 180.0, "team_prior_matches": 8.0, "venue_weight": 0.0, "signal_weight": 0.50, "poisson_blend": 0.5},
    {"id": "C6_fast_dynamic_mix", "half_life_days": 60.0, "team_prior_matches": 4.0, "venue_weight": 0.5, "signal_weight": 0.65, "poisson_blend": 0.5},
    {"id": "C7_pooled_poisson", "half_life_days": 120.0, "team_prior_matches": 8.0, "venue_weight": 0.25, "signal_weight": 0.50, "poisson_blend": 1.0},
]


def _weight(match_date: Any, cutoff: Any, half_life_days: float) -> float:
    age_days = max(0.0, (cutoff - match_date).total_seconds() / 86400.0)
    return math.exp(-math.log(2.0) * age_days / max(1e-9, half_life_days))


def _shrunk_rate(numerator: float, denominator: float, prior_rate: float, prior_matches: float) -> float:
    return (numerator + prior_rate * prior_matches) / max(1e-12, denominator + prior_matches)


def _poisson_pmf(total: int, mu: float) -> float:
    if total < 0:
        return 0.0
    mu = max(1e-12, float(mu))
    return math.exp(-mu + total * math.log(mu) - math.lgamma(total + 1))


def _nb_pmf(total: int, mu: float, k: float) -> float:
    if total < 0:
        return 0.0
    mu = max(1e-12, float(mu))
    k = max(1e-9, float(k))
    return math.exp(
        math.lgamma(total + k) - math.lgamma(k) - math.lgamma(total + 1)
        + k * math.log(k / (k + mu)) + total * math.log(mu / (k + mu))
    )


def _distribution(mu: float, k: float, poisson_blend: float) -> dict[str, float]:
    blend = min(1.0, max(0.0, float(poisson_blend)))
    values: dict[str, float] = {}
    exact_sum = 0.0
    for total in range(7):
        p = (1.0 - blend) * _nb_pmf(total, mu, k) + blend * _poisson_pmf(total, mu)
        values[str(total)] = p
        exact_sum += p
    values["7+"] = max(0.0, 1.0 - exact_sum)
    norm = sum(values.values())
    if norm <= 0 or not math.isfinite(norm):
        raise PlatformError("invalid direct-total probability normalization")
    return {key: value / norm for key, value in values.items()}


def _rps(values: list[float], actual_index: int) -> float:
    cp = 0.0
    co = 0.0
    score = 0.0
    for index in range(len(values) - 1):
        cp += values[index]
        co += 1.0 if actual_index == index else 0.0
        score += (cp - co) ** 2
    return score / max(1, len(values) - 1)


def _history_state(history: list[MatchRow], cutoff: Any, half_life_days: float, config: dict[str, Any]) -> dict[str, Any]:
    weighted_matches = 0.0
    weighted_total = 0.0
    weighted_totals: list[tuple[float, int]] = []
    teams: dict[str, dict[str, float]] = defaultdict(lambda: {
        "home_raw": 0.0, "away_raw": 0.0,
        "home_w": 0.0, "away_w": 0.0,
        "home_total": 0.0, "away_total": 0.0,
    })
    for match in history:
        w = _weight(match.date, cutoff, half_life_days)
        total = int(match.home_goals + match.away_goals)
        weighted_matches += w
        weighted_total += w * total
        weighted_totals.append((w, total))
        home = teams[match.home_team]
        away = teams[match.away_team]
        home["home_raw"] += 1.0
        away["away_raw"] += 1.0
        home["home_w"] += w
        away["away_w"] += w
        home["home_total"] += w * total
        away["away_total"] += w * total
    if weighted_matches <= 0:
        raise PlatformError("zero effective history")
    league_total = weighted_total / weighted_matches
    variance = sum(w * (total - league_total) ** 2 for w, total in weighted_totals) / max(1e-12, weighted_matches)
    defaults = config["default_parameters"]
    if variance > league_total + 1e-6:
        empirical_k = league_total * league_total / (variance - league_total)
    else:
        empirical_k = 100.0
    prior_n = float(defaults.get("dispersion_prior_matches", 30.0))
    default_k = float(defaults.get("nb_default_k", 10.0))
    k = (empirical_k * weighted_matches + default_k * prior_n) / (weighted_matches + prior_n)
    k = min(100.0, max(1.25, k))
    return {"league_total": league_total, "k": k, "teams": teams, "effective_matches": weighted_matches}


def _candidate_mu(state: dict[str, Any], home_team: str, away_team: str, candidate: dict[str, Any]) -> float:
    teams = state["teams"]
    if home_team not in teams or away_team not in teams:
        raise PlatformError("team missing from current-season direct-total history")
    home = teams[home_team]
    away = teams[away_team]
    league_total = float(state["league_total"])
    prior = float(candidate["team_prior_matches"])

    home_venue = _shrunk_rate(home["home_total"], home["home_w"], league_total, prior)
    away_venue = _shrunk_rate(away["away_total"], away["away_w"], league_total, prior)
    home_all_num = home["home_total"] + home["away_total"]
    home_all_den = home["home_w"] + home["away_w"]
    away_all_num = away["home_total"] + away["away_total"]
    away_all_den = away["home_w"] + away["away_w"]
    home_all = _shrunk_rate(home_all_num, home_all_den, league_total, prior)
    away_all = _shrunk_rate(away_all_num, away_all_den, league_total, prior)

    venue_pair = math.sqrt(max(1e-12, home_venue) * max(1e-12, away_venue))
    all_pair = math.sqrt(max(1e-12, home_all) * max(1e-12, away_all))
    venue_weight = min(1.0, max(0.0, float(candidate["venue_weight"])))
    pair_signal = math.exp(
        venue_weight * math.log(max(1e-12, venue_pair))
        + (1.0 - venue_weight) * math.log(max(1e-12, all_pair))
    )
    signal_weight = min(1.0, max(0.0, float(candidate["signal_weight"])))
    mu = math.exp(
        signal_weight * math.log(max(1e-12, pair_signal))
        + (1.0 - signal_weight) * math.log(max(1e-12, league_total))
    )
    return min(9.0, max(0.30, mu))


def _score(match: MatchRow, distribution: dict[str, float], sequence_index: int) -> dict[str, Any]:
    actual_total = int(match.home_goals + match.away_goals)
    actual_index = min(actual_total, 7)
    return {
        "match_key": f"{match.season}|{match.date.date().isoformat()}|{match.home_team}|{match.away_team}",
        "season": match.season,
        "date": match.date.date().isoformat(),
        "sequence_index": sequence_index,
        "block_id": f"{match.season}:{sequence_index // 20}",
        "actual_total": actual_total,
        "total_goals_rps": _rps([distribution[key] for key in TOTAL_KEYS], actual_index),
        "probabilities": distribution,
    }


def _team_relevant_counts(history: list[MatchRow]) -> tuple[Counter[str], Counter[str]]:
    home = Counter()
    away = Counter()
    for match in history:
        home[match.home_team] += 1
        away[match.away_team] += 1
    return home, away


def _evaluate_season(season_matches: list[MatchRow], candidate: dict[str, Any] | None, config: dict[str, Any]) -> list[dict[str, Any]]:
    by_date: dict[Any, list[MatchRow]] = defaultdict(list)
    for match in season_matches:
        by_date[match.date].append(match)
    validation = config["validation"]
    warmup_comp = int(validation["warmup_competition_matches"])
    warmup_team = int(validation["warmup_team_matches"])
    defaults = config["default_parameters"]
    history: list[MatchRow] = []
    records: list[dict[str, Any]] = []
    sequence_index = 0
    for date in sorted(by_date):
        home_counts, away_counts = _team_relevant_counts(history)
        for match in sorted(by_date[date], key=lambda item: (item.home_team, item.away_team)):
            if len(history) < warmup_comp or home_counts[match.home_team] < warmup_team or away_counts[match.away_team] < warmup_team:
                continue
            if candidate is None:
                half_life = float(defaults["half_life_days"])
                state = _history_state(history, match.date, half_life, config)
                mu = float(state["league_total"])
                distribution = _distribution(mu, float(state["k"]), 0.0)
            else:
                state = _history_state(history, match.date, float(candidate["half_life_days"]), config)
                mu = _candidate_mu(state, match.home_team, match.away_team, candidate)
                distribution = _distribution(mu, float(state["k"]), float(candidate["poisson_blend"]))
            records.append(_score(match, distribution, sequence_index))
            sequence_index += 1
        # Same-day results become available only after every match that day is forecast.
        history.extend(by_date[date])
        history.sort(key=lambda item: (item.date, item.home_team, item.away_team))
    return records


def _date_windows(records: list[dict[str, Any]], count: int) -> list[set[str]]:
    dates = sorted({str(record["date"]) for record in records})
    if not dates:
        return []
    count = min(max(1, int(count)), len(dates))
    output = []
    for index in range(count):
        start = index * len(dates) // count
        end = (index + 1) * len(dates) // count
        selected = set(dates[start:end])
        if selected:
            output.append(selected)
    return output


def _prior_records(records: list[dict[str, Any]], season_order: dict[str, int], season: str, test_start: str) -> list[dict[str, Any]]:
    target = season_order[season]
    return [
        record for record in records
        if season_order[str(record["season"])] < target
        or (str(record["season"]) == season and str(record["date"]) < test_start)
    ]


def _pair(model: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    base = {record["match_key"]: record for record in baseline}
    return [(record, base[record["match_key"]]) for record in model if record["match_key"] in base]


def _bootstrap_ci(pairs: list[tuple[dict[str, Any], dict[str, Any]]], resamples: int, seed: int) -> dict[str, Any]:
    if not pairs:
        return {"count": 0, "blocks": 0, "mean_difference": None, "ci95_lower": None, "ci95_upper": None}
    blocks: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for pair in pairs:
        blocks[pair[0]["block_id"]].append(pair)
    block_values = list(blocks.values())
    observed = mean(model["total_goals_rps"] - baseline["total_goals_rps"] for model, baseline in pairs)
    rng = random.Random(seed)
    samples = []
    for _ in range(resamples):
        sampled = [rng.choice(block_values) for _ in block_values]
        flattened = [pair for block in sampled for pair in block]
        samples.append(mean(model["total_goals_rps"] - baseline["total_goals_rps"] for model, baseline in flattened))
    samples.sort()
    lo = samples[max(0, int(0.025 * len(samples)) - 1)]
    hi = samples[min(len(samples) - 1, int(0.975 * len(samples)))]
    return {"count": len(pairs), "blocks": len(block_values), "mean_difference": observed, "ci95_lower": lo, "ci95_upper": hi}


def validate_competition(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    config = load_config()
    policy = load_json(POLICY_PATH)
    matches = read_processed_matches(competition_id)
    by_season: dict[str, list[MatchRow]] = defaultdict(list)
    for match in matches:
        by_season[match.season].append(match)
    seasons = sorted(by_season, key=lambda season: min(item.date for item in by_season[season]))
    if len(seasons) < 2:
        raise PlatformError(f"need at least two seasons: {competition_id}")
    season_order = {season: index for index, season in enumerate(seasons)}

    baseline_cache = {
        season: _evaluate_season(sorted(by_season[season], key=lambda x: (x.date, x.home_team, x.away_team)), None, config)
        for season in seasons
    }
    candidate_cache: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for index, candidate in enumerate(CANDIDATES):
        for season in seasons:
            candidate_cache[index][season] = _evaluate_season(
                sorted(by_season[season], key=lambda x: (x.date, x.home_team, x.away_team)), candidate, config
            )
    all_candidate = {
        index: [record for season in seasons for record in season_map[season]]
        for index, season_map in candidate_cache.items()
    }

    folds = []
    all_model: list[dict[str, Any]] = []
    all_baseline: list[dict[str, Any]] = []
    seen: set[str] = set()
    for outer_season in seasons[1:]:
        for window_index, test_dates in enumerate(_date_windows(baseline_cache[outer_season], WINDOWS_PER_OUTER_SEASON), start=1):
            test_start = min(test_dates)
            scored = []
            for index, candidate in enumerate(CANDIDATES):
                prior = _prior_records(all_candidate[index], season_order, outer_season, test_start)
                if not prior:
                    continue
                scored.append((mean(record["total_goals_rps"] for record in prior), index, len(prior), max(str(r["date"]) for r in prior)))
            if not scored:
                continue
            scored.sort(key=lambda item: (item[0], item[1]))
            prior_rps, selected_index, selection_count, selection_end = scored[0]
            model_test = [r for r in candidate_cache[selected_index][outer_season] if str(r["date"]) in test_dates]
            baseline_test = [r for r in baseline_cache[outer_season] if str(r["date"]) in test_dates]
            pairs = _pair(model_test, baseline_test)
            if not pairs:
                continue
            model_test = [p[0] for p in pairs]
            baseline_test = [p[1] for p in pairs]
            overlap = seen.intersection(r["match_key"] for r in model_test)
            if overlap:
                raise PlatformError(f"overlapping total-goals challenger test windows: {sorted(overlap)[:3]}")
            seen.update(r["match_key"] for r in model_test)
            all_model.extend(model_test)
            all_baseline.extend(baseline_test)
            folds.append({
                "outer_fold_id": f"{outer_season}:RW{window_index}",
                "outer_season": outer_season,
                "selection_information_end": selection_end,
                "test_start_date": test_start,
                "test_end_date": max(test_dates),
                "selection_predictions": selection_count,
                "selected_candidate_index": selected_index,
                "selected_candidate": CANDIDATES[selected_index],
                "selection_mean_total_goals_rps": prior_rps,
                "outer_predictions": len(model_test),
                "model_mean_total_goals_rps": mean(r["total_goals_rps"] for r in model_test),
                "baseline_mean_total_goals_rps": mean(r["total_goals_rps"] for r in baseline_test),
            })

    pairs = _pair(all_model, all_baseline)
    if not pairs:
        raise PlatformError(f"no paired challenger predictions: {competition_id}")
    ci = _bootstrap_ci(pairs, BOOTSTRAP_RESAMPLES, SEED)
    thresholds = policy["a_grade_thresholds"]
    checks = {
        "minimum_outer_predictions": len(pairs) >= int(thresholds["minimum_outer_predictions"]),
        "minimum_outer_time_folds": len(folds) >= int(thresholds["minimum_outer_time_folds"]),
        "disjoint_test_windows": len(seen) == len(all_model),
        "strictly_prior_selection": all(str(f["selection_information_end"]) < str(f["test_start_date"]) for f in folds),
        "total_goals_rps_ci": ci["ci95_upper"] is not None and float(ci["ci95_upper"]) <= float(thresholds["total_goals_rps_difference_ci_upper_lte"]),
    }
    selected_counts = Counter(f["selected_candidate"]["id"] for f in folds)
    report = {
        "schema_version": "V4.6.4-challenger",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "TOTAL_GOALS_CHALLENGER_PASS" if all(checks.values()) else "TOTAL_GOALS_CHALLENGER_NOT_PROMOTED",
        "formal_weight": 0,
        "design": "same-season-direct-total_true_expanding_window_nested_selection",
        "candidate_count": len(CANDIDATES),
        "candidates": CANDIDATES,
        "outer_folds": len(folds),
        "outer_predictions": len(pairs),
        "folds": folds,
        "selected_candidate_counts": dict(selected_counts),
        "model_mean_total_goals_rps": mean(p[0]["total_goals_rps"] for p in pairs),
        "baseline_mean_total_goals_rps": mean(p[1]["total_goals_rps"] for p in pairs),
        "paired_block_bootstrap": ci,
        "checks": checks,
        "implementation_sha256": sha256_file(SCRIPT_PATH),
        "promotion_note": "Pass is research evidence only. Formal integration requires unified-matrix preservation of P(T), final-chain replay, and CURRENT-compliant promotion; no automatic weight change.",
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_all(competition: str | None = None, *, write: bool = True) -> dict[str, Any]:
    ids = [item["competition_id"] for item in load_registry()["competitions"]]
    if competition:
        if competition not in ids:
            raise PlatformError(f"unknown competition: {competition}")
        ids = [competition]
    reports = {}
    failures = []
    for competition_id in ids:
        try:
            report = validate_competition(competition_id, write=write)
            reports[competition_id] = {
                "status": report["status"],
                "outer_folds": report["outer_folds"],
                "outer_predictions": report["outer_predictions"],
                "mean_difference": report["paired_block_bootstrap"]["mean_difference"],
                "ci95_upper": report["paired_block_bootstrap"]["ci95_upper"],
                "checks": report["checks"],
            }
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.4-challenger",
        "generated_at_utc": utc_now(),
        "competition_count_requested": len(ids),
        "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "passed_count": sum(item["status"] == "TOTAL_GOALS_CHALLENGER_PASS" for item in reports.values()),
        "reports": reports,
        "failures": failures,
    }
    if write and not competition:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"total-goals challenger failed for {len(failures)} domains: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        result = run_all(args.competition, write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
