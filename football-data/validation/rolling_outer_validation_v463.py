#!/usr/bin/env python3
"""True expanding-window rolling outer validation for the V4.6.x football core.

This module fixes the earlier temptation to count one unseen season split into two
blocks as if the two blocks were independent folds without re-selection.  Here
every fold has a strictly earlier information set: candidate hyperparameters are
selected from completed prior seasons plus completed earlier windows of the same
season, then frozen before the next disjoint chronological test window.

The report is A-grade evidence only.  It does not replace the live model artifact
or silently promote any competition.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import sys
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
VALIDATION_DIR = Path(__file__).resolve().parent
if str(VALIDATION_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATION_DIR))

from football_v460_engine import ENGINE_PATH, load_config  # noqa: E402
from nested_backtest_v460 import (  # noqa: E402
    _aggregate,
    _block_bootstrap_ci,
    _multiclass_ece,
    _objective,
    _paired_records,
    evaluate_season,
)
from platform_core import (  # noqa: E402
    ROOT,
    PlatformError,
    atomic_write_json,
    load_json,
    load_registry,
    read_processed_matches,
    sha256_file,
    utc_now,
)

REPORT_ROOT = ROOT / "validation" / "reports" / "rolling_outer_v463"
MANIFEST_PATH = ROOT / "manifests" / "rolling_outer_v463_status.json"
POLICY_PATH = ROOT / "validation" / "promotion_policy.json"
WINDOWS_PER_OUTER_SEASON = 2


def _date_windows(records: list[dict[str, Any]], count: int) -> list[set[str]]:
    dates = sorted({str(record["date"]) for record in records})
    if not dates:
        return []
    count = min(max(1, int(count)), len(dates))
    windows: list[set[str]] = []
    for index in range(count):
        start = index * len(dates) // count
        end = (index + 1) * len(dates) // count
        selected = set(dates[start:end])
        if selected:
            windows.append(selected)
    return windows


def _records_before(records: list[dict[str, Any]], season_order: dict[str, int], season: str, test_start: str) -> list[dict[str, Any]]:
    target_index = season_order[season]
    return [
        record for record in records
        if season_order[str(record["season"])] < target_index
        or (str(record["season"]) == season and str(record["date"]) < test_start)
    ]


def validate_competition(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    config = load_config()
    policy = load_json(POLICY_PATH)
    matches = read_processed_matches(competition_id)
    by_season: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        by_season[match.season].append(match)
    seasons = sorted(by_season, key=lambda key: min(item.date for item in by_season[key]))
    if len(seasons) < 2:
        raise PlatformError(f"rolling outer validation needs at least two seasons: {competition_id}")
    season_order = {season: index for index, season in enumerate(seasons)}

    candidates = config["candidate_parameters"]
    candidate_cache: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    baseline_cache: dict[str, list[dict[str, Any]]] = {}
    for season in seasons:
        ordered = sorted(by_season[season], key=lambda item: (item.date, item.home_team, item.away_team))
        baseline_cache[season] = evaluate_season(
            competition_id, ordered, config["default_parameters"], use_team_effects=False
        )
        for index, candidate in enumerate(candidates):
            candidate_cache[index][season] = evaluate_season(
                competition_id, ordered, candidate, use_team_effects=True
            )

    all_candidate_records = {
        index: [record for season in seasons for record in season_map.get(season, [])]
        for index, season_map in candidate_cache.items()
    }

    fold_details: list[dict[str, Any]] = []
    all_model: list[dict[str, Any]] = []
    all_baseline: list[dict[str, Any]] = []
    seen_match_keys: set[str] = set()

    # Season 0 is warm-up/selection only.  Each later season contributes disjoint
    # chronological future windows.  Before every window we re-select using only
    # records whose outcomes occurred strictly earlier than that window.
    for outer_season in seasons[1:]:
        reference_records = candidate_cache[0][outer_season]
        windows = _date_windows(reference_records, WINDOWS_PER_OUTER_SEASON)
        for window_index, test_dates in enumerate(windows, start=1):
            test_start = min(test_dates)
            test_end = max(test_dates)
            scored_candidates: list[tuple[float, int, dict[str, Any], int, str | None]] = []
            for index, candidate in enumerate(candidates):
                prior = _records_before(all_candidate_records[index], season_order, outer_season, test_start)
                if not prior:
                    continue
                train_end = max((str(record["date"]) for record in prior), default=None)
                scored_candidates.append((_objective(prior), index, candidate, len(prior), train_end))
            if not scored_candidates:
                continue
            scored_candidates.sort(key=lambda item: (item[0], item[1]))
            _, selected_index, selected_params, selection_count, selection_end = scored_candidates[0]
            model_test = [
                record for record in candidate_cache[selected_index][outer_season]
                if str(record["date"]) in test_dates
            ]
            baseline_test = [
                record for record in baseline_cache[outer_season]
                if str(record["date"]) in test_dates
            ]
            pairs = _paired_records(model_test, baseline_test)
            if not pairs:
                continue
            model_test = [pair[0] for pair in pairs]
            baseline_test = [pair[1] for pair in pairs]
            overlap = seen_match_keys.intersection(record["match_key"] for record in model_test)
            if overlap:
                raise PlatformError(f"rolling outer test windows overlap: {sorted(overlap)[:3]}")
            seen_match_keys.update(record["match_key"] for record in model_test)
            all_model.extend(model_test)
            all_baseline.extend(baseline_test)
            fold_details.append({
                "outer_fold_id": f"{outer_season}:RW{window_index}",
                "outer_season": outer_season,
                "selection_information_end": selection_end,
                "test_start_date": test_start,
                "test_end_date": test_end,
                "selection_predictions": selection_count,
                "selected_candidate_index": selected_index,
                "selected_parameters": selected_params,
                "outer_predictions": len(model_test),
                "model_metrics": _aggregate(model_test),
                "baseline_metrics": _aggregate(baseline_test),
            })

    pairs = _paired_records(all_model, all_baseline)
    if not pairs:
        raise PlatformError(f"no rolling outer prediction pairs for {competition_id}")
    validation_cfg = config["validation"]
    bootstrap = {
        "joint_log_score": _block_bootstrap_ci(pairs, "score_log", int(validation_cfg["bootstrap_resamples"]), int(validation_cfg["seed"])),
        "one_x_two_brier": _block_bootstrap_ci(pairs, "one_x_two_brier", int(validation_cfg["bootstrap_resamples"]), int(validation_cfg["seed"]) + 1),
        "one_x_two_rps": _block_bootstrap_ci(pairs, "one_x_two_rps", int(validation_cfg["bootstrap_resamples"]), int(validation_cfg["seed"]) + 2),
        "total_goals_rps": _block_bootstrap_ci(pairs, "total_goals_rps", int(validation_cfg["bootstrap_resamples"]), int(validation_cfg["seed"]) + 3),
    }
    thresholds = policy["a_grade_thresholds"]
    checks = {
        "minimum_outer_predictions": len(pairs) >= int(thresholds["minimum_outer_predictions"]),
        "minimum_outer_time_folds": len(fold_details) >= int(thresholds["minimum_outer_time_folds"]),
        "disjoint_test_windows": len(seen_match_keys) == len(all_model),
        "strictly_prior_selection": all(
            fold["selection_information_end"] is not None
            and str(fold["selection_information_end"]) < str(fold["test_start_date"])
            for fold in fold_details
        ),
        "joint_log_score_ci": bootstrap["joint_log_score"]["ci95_upper"] is not None and float(bootstrap["joint_log_score"]["ci95_upper"]) < 0.0,
        "one_x_two_brier_rps_ci": (
            bootstrap["one_x_two_brier"]["ci95_upper"] is not None
            and float(bootstrap["one_x_two_brier"]["ci95_upper"]) <= float(thresholds["one_x_two_brier_rps_difference_ci_upper_lte"])
            and bootstrap["one_x_two_rps"]["ci95_upper"] is not None
            and float(bootstrap["one_x_two_rps"]["ci95_upper"]) <= float(thresholds["one_x_two_brier_rps_difference_ci_upper_lte"])
        ),
        "total_goals_rps_ci": bootstrap["total_goals_rps"]["ci95_upper"] is not None and float(bootstrap["total_goals_rps"]["ci95_upper"]) <= float(thresholds["total_goals_rps_difference_ci_upper_lte"]),
    }
    report = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "engine_sha256": sha256_file(ENGINE_PATH),
        "design": "expanding_window_nested_outer_validation",
        "windows_per_outer_season": WINDOWS_PER_OUTER_SEASON,
        "selection_policy": "Before each disjoint chronological test window, hyperparameters are re-selected only from completed earlier seasons and completed earlier windows. No target-window outcome enters selection.",
        "outer_folds": len(fold_details),
        "outer_predictions": len(pairs),
        "folds": fold_details,
        "model_metrics": _aggregate([pair[0] for pair in pairs]),
        "strong_non_market_baseline_metrics": _aggregate([pair[1] for pair in pairs]),
        "paired_block_bootstrap": bootstrap,
        "one_x_two_ece": _multiclass_ece([pair[0] for pair in pairs]),
        "checks": checks,
        "promotion_evidence_status": "ROLLING_EVIDENCE_PASS" if all(checks.values()) else "ROLLING_EVIDENCE_NOT_A",
        "note": "This evidence fixes fold-count semantics. It does not satisfy market, lineup, replay, drift, or signed-receipt gates by itself.",
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
    reports: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    for competition_id in ids:
        try:
            report = validate_competition(competition_id, write=write)
            reports[competition_id] = {
                "outer_folds": report["outer_folds"],
                "outer_predictions": report["outer_predictions"],
                "promotion_evidence_status": report["promotion_evidence_status"],
                "checks": report["checks"],
            }
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "competition_count_requested": len(ids),
        "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "reports": reports,
        "failures": failures,
    }
    if write and not competition:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"rolling outer validation failed for {len(failures)} domains: {failures}")
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
