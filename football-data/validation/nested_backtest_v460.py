#!/usr/bin/env python3
"""Nested time-ordered validation for the V4.6.0 formal score core.

Hyperparameters are selected only from earlier seasons. For every prediction,
team strength uses only matches in that same season strictly before the match
date. Same-day results are updated only after every match on that date has been
predicted, preventing within-round leakage.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

import sys

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from football_v460_engine import ENGINE_PATH, load_config, predict_from_history  # noqa: E402
from platform_core import (  # noqa: E402
    ROOT,
    MatchRow,
    PlatformError,
    atomic_write_json,
    load_registry,
    read_processed_matches,
    sha256_file,
    sha256_json,
)

REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
MODEL_ROOT = ROOT / "models" / "formal_core_v460"
MANIFEST_PATH = ROOT / "manifests" / "formal_core_v460_status.json"
POLICY_PATH = ROOT / "validation" / "promotion_policy.json"
EPSILON = 1e-15


def _score_key(home: int, away: int) -> str:
    return f"{home}-{away}"


def _matrix_map(prediction: dict[str, Any]) -> dict[str, float]:
    return {
        _score_key(int(cell["home_goals"]), int(cell["away_goals"])): float(cell["probability"])
        for cell in prediction["probabilities"]["score_matrix"]
    }


def _actual_outcome(home: int, away: int) -> str:
    return "home" if home > away else "draw" if home == away else "away"


def _rps(values: list[float], actual_index: int) -> float:
    cumulative_p = 0.0
    cumulative_o = 0.0
    score = 0.0
    for index in range(len(values) - 1):
        cumulative_p += values[index]
        cumulative_o += 1.0 if actual_index == index else 0.0
        score += (cumulative_p - cumulative_o) ** 2
    return score / max(1, len(values) - 1)


def _minimum_set_contains(matrix: dict[str, float], actual: str, target: float) -> tuple[bool, int]:
    cumulative = 0.0
    size = 0
    for score, probability in sorted(matrix.items(), key=lambda item: (-item[1], item[0])):
        cumulative += probability
        size += 1
        if score == actual:
            actual_rank = size
        if cumulative >= target:
            selected = {key for key, _ in sorted(matrix.items(), key=lambda item: (-item[1], item[0]))[:size]}
            return actual in selected, size
    return True, size


def score_record(match: MatchRow, prediction: dict[str, Any], sequence_index: int) -> dict[str, Any]:
    matrix = _matrix_map(prediction)
    actual_score = _score_key(match.home_goals, match.away_goals)
    p_score = matrix.get(actual_score, EPSILON)
    one = prediction["probabilities"]["one_x_two"]
    actual_outcome = _actual_outcome(match.home_goals, match.away_goals)
    brier = sum((one[key] - (1.0 if key == actual_outcome else 0.0)) ** 2 for key in ("home", "draw", "away"))
    one_rps = _rps([one["home"], one["draw"], one["away"]], ("home", "draw", "away").index(actual_outcome))
    totals = prediction["probabilities"]["total_goals"]
    total_keys = ("0", "1", "2", "3", "4", "5", "6", "7+")
    actual_total = match.home_goals + match.away_goals
    total_index = min(actual_total, 7)
    total_rps = _rps([totals[key] for key in total_keys], total_index)
    ranking = sorted(matrix.items(), key=lambda item: (-item[1], item[0]))
    top_scores = [item[0] for item in ranking[:5]]
    set80, size80 = _minimum_set_contains(matrix, actual_score, 0.80)
    set90, size90 = _minimum_set_contains(matrix, actual_score, 0.90)
    return {
        "match_key": f"{match.season}|{match.date.date().isoformat()}|{match.home_team}|{match.away_team}",
        "season": match.season,
        "date": match.date.date().isoformat(),
        "sequence_index": sequence_index,
        "block_id": f"{match.season}:{sequence_index // 20}",
        "actual_score": actual_score,
        "actual_outcome": actual_outcome,
        "actual_total": actual_total,
        "score_log": -math.log(max(EPSILON, p_score)),
        "one_x_two_brier": brier,
        "one_x_two_rps": one_rps,
        "total_goals_rps": total_rps,
        "top1": actual_score == top_scores[0],
        "top3": actual_score in top_scores[:3],
        "top5": actual_score in top_scores,
        "set80_covered": set80,
        "set90_covered": set90,
        "set80_size": size80,
        "set90_size": size90,
        "p_home": one["home"],
        "p_draw": one["draw"],
        "p_away": one["away"],
        "p_tail4": sum(value for key, value in totals.items() if key == "7+" or (key.isdigit() and int(key) >= 4)),
        "p_tail5": sum(value for key, value in totals.items() if key == "7+" or (key.isdigit() and int(key) >= 5)),
        "p_tail7": totals["7+"],
    }


def _team_counts(history: list[MatchRow]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for match in history:
        counts[match.home_team] += 1
        counts[match.away_team] += 1
    return counts


def evaluate_season(
    competition_id: str,
    season_matches: list[MatchRow],
    params: dict[str, Any],
    *,
    use_team_effects: bool,
) -> list[dict[str, Any]]:
    config = load_config()
    validation = config["validation"]
    warmup_comp = int(validation["warmup_competition_matches"])
    warmup_team = int(validation["warmup_team_matches"])
    by_date: dict[datetime, list[MatchRow]] = defaultdict(list)
    for match in season_matches:
        by_date[match.date].append(match)
    history: list[MatchRow] = []
    records: list[dict[str, Any]] = []
    sequence_index = 0
    for date in sorted(by_date):
        counts = _team_counts(history)
        for match in sorted(by_date[date], key=lambda item: (item.home_team, item.away_team)):
            if len(history) >= warmup_comp and counts[match.home_team] >= warmup_team and counts[match.away_team] >= warmup_team:
                try:
                    prediction = predict_from_history(
                        history, competition_id, match.season, match.home_team, match.away_team,
                        match.date, params, use_team_effects=use_team_effects
                    )
                except PlatformError:
                    continue
                records.append(score_record(match, prediction, sequence_index))
                sequence_index += 1
        history.extend(by_date[date])
        history.sort(key=lambda item: (item.date, item.home_team, item.away_team))
    return records


def _objective(records: list[dict[str, Any]]) -> float:
    if not records:
        return float("inf")
    return mean(record["score_log"] + 0.50 * record["total_goals_rps"] + 0.25 * record["one_x_two_rps"] for record in records)


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"count": 0}
    return {
        "count": len(records),
        "mean_joint_log_score": mean(record["score_log"] for record in records),
        "mean_one_x_two_brier": mean(record["one_x_two_brier"] for record in records),
        "mean_one_x_two_rps": mean(record["one_x_two_rps"] for record in records),
        "mean_total_goals_rps": mean(record["total_goals_rps"] for record in records),
        "top1_accuracy": mean(float(record["top1"]) for record in records),
        "top3_accuracy": mean(float(record["top3"]) for record in records),
        "top5_accuracy": mean(float(record["top5"]) for record in records),
        "score_set_80_coverage": mean(float(record["set80_covered"]) for record in records),
        "score_set_90_coverage": mean(float(record["set90_covered"]) for record in records),
        "mean_score_set_80_size": mean(record["set80_size"] for record in records),
        "mean_score_set_90_size": mean(record["set90_size"] for record in records),
        "tail4_absolute_error": abs(mean(record["p_tail4"] for record in records) - mean(float(record["actual_total"] >= 4) for record in records)),
        "tail5_absolute_error": abs(mean(record["p_tail5"] for record in records) - mean(float(record["actual_total"] >= 5) for record in records)),
        "tail7_absolute_error": abs(mean(record["p_tail7"] for record in records) - mean(float(record["actual_total"] >= 7) for record in records)),
    }


def _multiclass_ece(records: list[dict[str, Any]], bins: int = 10) -> dict[str, float]:
    result = {}
    for outcome, field in (("home", "p_home"), ("draw", "p_draw"), ("away", "p_away")):
        total = len(records)
        error = 0.0
        for index in range(bins):
            low, high = index / bins, (index + 1) / bins
            subset = [r for r in records if low <= r[field] < high or (index == bins - 1 and r[field] == 1.0)]
            if not subset:
                continue
            forecast = mean(r[field] for r in subset)
            observed = mean(float(r["actual_outcome"] == outcome) for r in subset)
            error += len(subset) / total * abs(forecast - observed)
        result[outcome] = error
    result["maximum"] = max(result.values()) if result else 0.0
    return result


def _paired_records(model: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    base_map = {record["match_key"]: record for record in baseline}
    return [(record, base_map[record["match_key"]]) for record in model if record["match_key"] in base_map]


def _block_bootstrap_ci(pairs: list[tuple[dict[str, Any], dict[str, Any]]], field: str, resamples: int, seed: int) -> dict[str, float | int | None]:
    if not pairs:
        return {"count": 0, "mean_difference": None, "ci95_lower": None, "ci95_upper": None}
    blocks: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for pair in pairs:
        blocks[pair[0]["block_id"]].append(pair)
    block_values = list(blocks.values())
    observed = mean(model[field] - baseline[field] for model, baseline in pairs)
    rng = random.Random(seed)
    samples = []
    for _ in range(resamples):
        selected = [rng.choice(block_values) for _ in block_values]
        flattened = [pair for block in selected for pair in block]
        samples.append(mean(model[field] - baseline[field] for model, baseline in flattened))
    samples.sort()
    low_index = max(0, int(0.025 * len(samples)) - 1)
    high_index = min(len(samples) - 1, int(0.975 * len(samples)))
    return {
        "count": len(pairs),
        "blocks": len(block_values),
        "mean_difference": observed,
        "ci95_lower": samples[low_index],
        "ci95_upper": samples[high_index],
    }


def _split_outer_time_blocks(
    model_records: list[dict[str, Any]],
    baseline_records: list[dict[str, Any]],
    blocks: int = 2,
) -> list[dict[str, Any]]:
    """Split one completely unseen outer season into disjoint chronological folds.

    Hyperparameters remain selected only from prior seasons. The split changes
    evaluation granularity, not training, so each record appears in exactly one
    outer time fold and no same-season outcome can affect parameter selection.
    """
    if not model_records:
        return []
    dates = sorted({str(record["date"]) for record in model_records})
    block_count = min(max(1, blocks), len(dates))
    output: list[dict[str, Any]] = []
    for index in range(block_count):
        start = index * len(dates) // block_count
        end = (index + 1) * len(dates) // block_count
        selected_dates = set(dates[start:end])
        model_part = [record for record in model_records if str(record["date"]) in selected_dates]
        baseline_part = [record for record in baseline_records if str(record["date"]) in selected_dates]
        if not model_part:
            continue
        output.append({
            "block_index": index,
            "test_start_date": min(selected_dates),
            "test_end_date": max(selected_dates),
            "model_records": model_part,
            "baseline_records": baseline_part,
        })
    return output


def _season_is_partial(season_matches: dict[str, list[MatchRow]], season: str) -> bool:
    counts = [len(matches) for key, matches in season_matches.items() if key != season]
    if not counts:
        return False
    return len(season_matches[season]) < 0.85 * median(counts)


def validate_competition(competition_id: str, *, write: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_config()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    matches = read_processed_matches(competition_id)
    season_matches: dict[str, list[MatchRow]] = defaultdict(list)
    for match in matches:
        season_matches[match.season].append(match)
    seasons = sorted(season_matches, key=lambda key: min(item.date for item in season_matches[key]))
    candidates = config["candidate_parameters"]
    cache: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    baseline_cache: dict[str, list[dict[str, Any]]] = {}
    for season in seasons:
        ordered = sorted(season_matches[season], key=lambda item: (item.date, item.home_team, item.away_team))
        baseline_cache[season] = evaluate_season(
            competition_id, ordered, config["default_parameters"], use_team_effects=False
        )
        for index, candidate in enumerate(candidates):
            cache[index][season] = evaluate_season(competition_id, ordered, candidate, use_team_effects=True)

    outer_records: list[dict[str, Any]] = []
    outer_baseline: list[dict[str, Any]] = []
    fold_details = []
    for outer_index in range(1, len(seasons)):
        outer_season = seasons[outer_index]
        prior_seasons = seasons[:outer_index]
        candidate_scores = []
        for index, candidate in enumerate(candidates):
            prior_records = [record for season in prior_seasons for record in cache[index][season]]
            candidate_scores.append((_objective(prior_records), index, candidate, len(prior_records)))
        candidate_scores.sort(key=lambda item: (item[0], item[1]))
        _, selected_index, selected_candidate, selection_count = candidate_scores[0]
        model_records = cache[selected_index][outer_season]
        baseline_records = baseline_cache[outer_season]
        if model_records:
            outer_records.extend(model_records)
            outer_baseline.extend(baseline_records)
            fold_details.append({
                "outer_season": outer_season,
                "prior_seasons": prior_seasons,
                "selection_predictions": selection_count,
                "selected_candidate_index": selected_index,
                "selected_parameters": selected_candidate,
                "outer_predictions": len(model_records),
                "model_metrics": _aggregate(model_records),
                "baseline_metrics": _aggregate(baseline_records),
            })

    latest_season = seasons[-1]
    tuning_seasons = seasons[:-1] if _season_is_partial(season_matches, latest_season) else seasons
    aggregate_candidate_scores = []
    for index, candidate in enumerate(candidates):
        records = [record for season in tuning_seasons for record in cache[index][season]]
        aggregate_candidate_scores.append((_objective(records), index, candidate, len(records)))
    aggregate_candidate_scores.sort(key=lambda item: (item[0], item[1]))
    _, live_index, live_params, live_count = aggregate_candidate_scores[0]

    pairs = _paired_records(outer_records, outer_baseline)
    model_metrics = _aggregate([pair[0] for pair in pairs])
    baseline_metrics = _aggregate([pair[1] for pair in pairs])
    bootstrap = {
        "joint_log_score": _block_bootstrap_ci(pairs, "score_log", int(config["validation"]["bootstrap_resamples"]), int(config["validation"]["seed"])),
        "one_x_two_brier": _block_bootstrap_ci(pairs, "one_x_two_brier", int(config["validation"]["bootstrap_resamples"]), int(config["validation"]["seed"]) + 1),
        "one_x_two_rps": _block_bootstrap_ci(pairs, "one_x_two_rps", int(config["validation"]["bootstrap_resamples"]), int(config["validation"]["seed"]) + 2),
        "total_goals_rps": _block_bootstrap_ci(pairs, "total_goals_rps", int(config["validation"]["bootstrap_resamples"]), int(config["validation"]["seed"]) + 3),
    }
    outer_folds = len(fold_details)
    predictions = len(pairs)
    operational = predictions >= int(config["validation"]["minimum_predictions_for_operational_core"])
    a_thresholds = policy["a_grade_thresholds"]
    a_checks = {
        "sample_predictions": predictions >= a_thresholds["minimum_outer_predictions"],
        "outer_time_folds": outer_folds >= a_thresholds["minimum_outer_time_folds"],
        "joint_log_score_ci": bootstrap["joint_log_score"]["ci95_upper"] is not None and bootstrap["joint_log_score"]["ci95_upper"] < 0.0,
        "one_x_two_brier_rps_ci": (
            bootstrap["one_x_two_brier"]["ci95_upper"] is not None
            and bootstrap["one_x_two_brier"]["ci95_upper"] <= 0.002
            and bootstrap["one_x_two_rps"]["ci95_upper"] is not None
            and bootstrap["one_x_two_rps"]["ci95_upper"] <= 0.002
        ),
        "total_goals_rps_ci": bootstrap["total_goals_rps"]["ci95_upper"] is not None and bootstrap["total_goals_rps"]["ci95_upper"] <= 0.0,
        "market_baseline": False,
        "lineup_route": False,
        "independent_replay_receipt": False,
    }
    report = {
        "schema_version": "V4.6.2",
        "competition_id": competition_id,
        "engine_sha256": sha256_file(ENGINE_PATH),
        "config_sha256": sha256_file(ROOT / "config" / "formal_core_v460.json"),
        "data_policy": "same-season matches strictly before each cutoff; same-day results withheld until all same-day predictions finish",
        "historical_odds_used": False,
        "seasons": seasons,
        "latest_season_partial_for_tuning": _season_is_partial(season_matches, latest_season),
        "tuning_seasons": tuning_seasons,
        "selected_candidate_index_for_live": live_index,
        "selected_parameters_for_live": live_params,
        "live_selection_prediction_count": live_count,
        "outer_folds": outer_folds,
        "outer_predictions": predictions,
        "folds": fold_details,
        "model_metrics": model_metrics,
        "strong_non_market_baseline_metrics": baseline_metrics,
        "paired_block_bootstrap": bootstrap,
        "one_x_two_ece": _multiclass_ece([pair[0] for pair in pairs]) if pairs else {},
        "a_grade_checks": a_checks,
        "promotion_status": "NOT_A",
        "a_grade_receipt_issued": False,
        "operational_status": "NON_A_FORMAL_CORE_AVAILABLE" if operational else "INSUFFICIENT_VALIDATION_SAMPLE",
        "limitations": [
            "No timestamped synchronized historical market baseline is used, so A-grade market non-inferiority cannot be tested.",
            "No historical point-in-time lineup route is available.",
            "Top-1 score is a model center, not a high-confidence EXACT claim.",
            "Question-time current market lines may be audited against the matrix but do not alter the center until market projection is independently validated."
        ],
    }
    model = {
        "schema_version": "V4.6.2",
        "competition_id": competition_id,
        "engine_version": config["engine_version"],
        "engine_sha256": report["engine_sha256"],
        "selected_parameters": live_params,
        "point_in_time_parameters": {item["outer_season"]: item["selected_parameters"] for item in fold_details},
        "live_target_season": latest_season,
        "latest_season_partial_for_tuning": _season_is_partial(season_matches, latest_season),
        "selection_seasons": tuning_seasons,
        "selection_predictions": live_count,
        "validation_report_sha256": sha256_json(report),
        "operational_status": report["operational_status"],
        "promotion_status": "NOT_A",
        "formal_center_components": [
            "rolling current-season attack/defence",
            "direct Negative-Binomial total goals",
            "conditional Beta-Binomial score allocation",
            "shrunk capped low-score residual",
            "single unified score matrix"
        ],
        "target_strength_data_policy": "current season before cutoff only",
        "historical_market_policy": "never used as question-time input",
        "exact_policy": "model center score only; independent EXACT gate required"
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
        atomic_write_json(MODEL_ROOT / competition_id / "model.json", model)
    return report, model


def run_all(competition: str | None = None, *, write: bool = True) -> dict[str, Any]:
    registry = load_registry()
    ids = [item["competition_id"] for item in registry["competitions"]]
    if competition:
        if competition not in ids:
            raise PlatformError(f"unknown competition: {competition}")
        ids = [competition]
    reports = {}
    failures = []
    for competition_id in ids:
        try:
            report, _ = validate_competition(competition_id, write=write)
            reports[competition_id] = {
                "operational_status": report["operational_status"],
                "promotion_status": report["promotion_status"],
                "outer_predictions": report["outer_predictions"],
                "outer_folds": report["outer_folds"],
                "engine_sha256": report["engine_sha256"],
            }
        except Exception as exc:  # preserve all domain failures in one manifest
            failures.append({"competition_id": competition_id, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.2",
        "engine_sha256": sha256_file(ENGINE_PATH),
        "competition_count_requested": len(ids),
        "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "formal_core_available_count": sum(item["operational_status"] == "NON_A_FORMAL_CORE_AVAILABLE" for item in reports.values()),
        "a_grade_receipt_count": 0,
        "historical_market_used": False,
        "reports": reports,
        "failures": failures,
    }
    if write and not competition:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"formal core validation failed for {len(failures)} domains: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = run_all(args.competition, write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
