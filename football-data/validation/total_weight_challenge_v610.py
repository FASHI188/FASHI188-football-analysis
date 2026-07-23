#!/usr/bin/env python3
"""V6.10 research-only direct-total weight challenger.

Purpose
-------
Diagnose whether the broad V4.6.x total-goals RPS weakness is primarily caused by the
coarse/entangled direct_total_signal_weight search space.  The formal V5.0.1 runtime and
formal_core_v460.json are never modified.

Design
------
* Same current-season NB + conditional Beta-Binomial joint-matrix engine.
* Same expanding-window, disjoint chronological outer windows as V4.6.3.
* Current comparator is selected exactly from the frozen formal_core_v460 candidate set using
  the existing joint objective on strictly-prior records.
* Challenger expands only direct_total_signal_weight on the two existing structural parameter
  families.  No new distribution family, no hand-written goal buckets, no market data.
* Before every future window, challenger selection uses only strictly-prior records.
* Selection is total-RPS focused but is fail-closed by prior-data guardrails against material
  joint-log or 1X2 degradation.  The current comparator is always present in the challenge grid,
  so the guardrail cannot force an unrelated model.
* Promotion authority is always false.  A challenge win is diagnostic evidence only.

This is intentionally stage 1.  A dedicated total-CDF calibrator should only be tested if a finer
weight grid does not solve the problem robustly.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
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
    _objective,
    _paired_records,
    evaluate_season,
)
from platform_core import (  # noqa: E402
    ROOT,
    PlatformError,
    atomic_write_json,
    read_processed_matches,
    sha256_file,
    sha256_json,
    utc_now,
)
from rolling_outer_validation_v463 import _date_windows, _records_before  # noqa: E402

REPORT_ROOT = ROOT / "validation" / "reports" / "total_weight_challenge_v610"
MANIFEST_PATH = ROOT / "manifests" / "total_weight_challenge_v610_status.json"
SMOKE_DOMAINS = ("ENG_PremierLeague", "ESP_LaLiga", "JPN_J1")
WEIGHT_GRID = (0.0, 0.25, 0.40, 0.55, 0.65, 0.70, 0.85, 1.0)
WINDOWS_PER_OUTER_SEASON = 2

# Prior-information selection guardrails.  These are not promotion thresholds; they prevent a
# total-only selector from knowingly buying total RPS with material degradation elsewhere.
PRIOR_JOINT_LOG_MEAN_TOLERANCE = 0.010
PRIOR_ONE_X_TWO_RPS_MEAN_TOLERANCE = 0.003

# Future-window diagnostic win guardrails.  Total RPS must win robustly; other dimensions must not
# materially regress in mean.  Formal promotion remains false regardless of these diagnostics.
FUTURE_JOINT_LOG_MEAN_TOLERANCE = 0.005
FUTURE_ONE_X_TWO_BRIER_MEAN_TOLERANCE = 0.003
FUTURE_ONE_X_TWO_RPS_MEAN_TOLERANCE = 0.002


def _structural_families(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Deduplicate current candidates after removing only direct_total_signal_weight."""
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for candidate in config["candidate_parameters"]:
        base = {key: value for key, value in candidate.items() if key != "direct_total_signal_weight"}
        token = json.dumps(base, sort_keys=True, separators=(",", ":"))
        if token not in seen:
            seen.add(token)
            output.append(base)
    if not output:
        raise PlatformError("no structural parameter families available")
    return output


def _challenge_candidates(config: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in _structural_families(config):
        for weight in WEIGHT_GRID:
            candidate = dict(base)
            candidate["direct_total_signal_weight"] = float(weight)
            token = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
            if token not in seen:
                seen.add(token)
                candidates.append(candidate)
    return candidates


def _mean_field(records: list[dict[str, Any]], field: str) -> float:
    return mean(float(record[field]) for record in records) if records else float("inf")


def _selection_metrics(records: list[dict[str, Any]]) -> dict[str, float | int]:
    return {
        "count": len(records),
        "mean_joint_log_score": _mean_field(records, "score_log"),
        "mean_one_x_two_rps": _mean_field(records, "one_x_two_rps"),
        "mean_total_goals_rps": _mean_field(records, "total_goals_rps"),
        "legacy_joint_objective": _objective(records),
    }


def _select_current(
    candidates: list[dict[str, Any]],
    all_records: dict[int, list[dict[str, Any]]],
    season_order: dict[str, int],
    outer_season: str,
    test_start: str,
) -> tuple[int, dict[str, Any], list[dict[str, Any]], dict[str, float | int]]:
    scored = []
    for index, candidate in enumerate(candidates):
        prior = _records_before(all_records[index], season_order, outer_season, test_start)
        if prior:
            scored.append((_objective(prior), index, candidate, prior))
    if not scored:
        raise PlatformError("no strictly-prior records for current comparator selection")
    scored.sort(key=lambda item: (item[0], item[1]))
    _, index, candidate, prior = scored[0]
    return index, candidate, prior, _selection_metrics(prior)


def _select_challenger(
    candidates: list[dict[str, Any]],
    all_records: dict[int, list[dict[str, Any]]],
    season_order: dict[str, int],
    outer_season: str,
    test_start: str,
    current_prior_metrics: dict[str, float | int],
) -> tuple[int, dict[str, Any], list[dict[str, Any]], dict[str, float | int], int]:
    eligible = []
    rejected_guardrail = 0
    current_joint = float(current_prior_metrics["mean_joint_log_score"])
    current_one_rps = float(current_prior_metrics["mean_one_x_two_rps"])
    for index, candidate in enumerate(candidates):
        prior = _records_before(all_records[index], season_order, outer_season, test_start)
        if not prior:
            continue
        metrics = _selection_metrics(prior)
        if (
            float(metrics["mean_joint_log_score"]) > current_joint + PRIOR_JOINT_LOG_MEAN_TOLERANCE
            or float(metrics["mean_one_x_two_rps"]) > current_one_rps + PRIOR_ONE_X_TWO_RPS_MEAN_TOLERANCE
        ):
            rejected_guardrail += 1
            continue
        eligible.append(
            (
                float(metrics["mean_total_goals_rps"]),
                float(metrics["legacy_joint_objective"]),
                index,
                candidate,
                prior,
                metrics,
            )
        )
    if not eligible:
        raise PlatformError("all total-weight challengers rejected by strictly-prior guardrails")
    eligible.sort(key=lambda item: (item[0], item[1], item[2]))
    _, _, index, candidate, prior, metrics = eligible[0]
    return index, candidate, prior, metrics, rejected_guardrail


def validate_competition(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    config = load_config()
    current_candidates = list(config["candidate_parameters"])
    challenge_candidates = _challenge_candidates(config)
    matches = read_processed_matches(competition_id)
    by_season: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        by_season[match.season].append(match)
    seasons = sorted(by_season, key=lambda key: min(item.date for item in by_season[key]))
    if len(seasons) < 2:
        raise PlatformError(f"total-weight challenge needs at least two seasons: {competition_id}")
    season_order = {season: index for index, season in enumerate(seasons)}

    current_cache: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    challenge_cache: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for season in seasons:
        ordered = sorted(by_season[season], key=lambda item: (item.date, item.home_team, item.away_team))
        for index, candidate in enumerate(current_candidates):
            current_cache[index][season] = evaluate_season(
                competition_id, ordered, candidate, use_team_effects=True
            )
        for index, candidate in enumerate(challenge_candidates):
            challenge_cache[index][season] = evaluate_season(
                competition_id, ordered, candidate, use_team_effects=True
            )

    current_all = {
        index: [record for season in seasons for record in season_map.get(season, [])]
        for index, season_map in current_cache.items()
    }
    challenge_all = {
        index: [record for season in seasons for record in season_map.get(season, [])]
        for index, season_map in challenge_cache.items()
    }

    folds: list[dict[str, Any]] = []
    all_challenge: list[dict[str, Any]] = []
    all_current: list[dict[str, Any]] = []
    seen_match_keys: set[str] = set()
    selected_weight_counts: defaultdict[str, int] = defaultdict(int)

    for outer_season in seasons[1:]:
        reference_records = current_cache[0][outer_season]
        for window_index, test_dates in enumerate(
            _date_windows(reference_records, WINDOWS_PER_OUTER_SEASON), start=1
        ):
            test_start = min(test_dates)
            test_end = max(test_dates)
            (
                current_index,
                current_params,
                current_prior,
                current_prior_metrics,
            ) = _select_current(
                current_candidates, current_all, season_order, outer_season, test_start
            )
            (
                challenge_index,
                challenge_params,
                challenge_prior,
                challenge_prior_metrics,
                guardrail_rejections,
            ) = _select_challenger(
                challenge_candidates,
                challenge_all,
                season_order,
                outer_season,
                test_start,
                current_prior_metrics,
            )
            current_test = [
                record
                for record in current_cache[current_index][outer_season]
                if str(record["date"]) in test_dates
            ]
            challenge_test = [
                record
                for record in challenge_cache[challenge_index][outer_season]
                if str(record["date"]) in test_dates
            ]
            pairs = _paired_records(challenge_test, current_test)
            if not pairs:
                continue
            challenge_test = [pair[0] for pair in pairs]
            current_test = [pair[1] for pair in pairs]
            overlap = seen_match_keys.intersection(record["match_key"] for record in challenge_test)
            if overlap:
                raise PlatformError(f"challenge outer windows overlap: {sorted(overlap)[:3]}")
            seen_match_keys.update(record["match_key"] for record in challenge_test)
            all_challenge.extend(challenge_test)
            all_current.extend(current_test)
            weight = float(challenge_params["direct_total_signal_weight"])
            selected_weight_counts[f"{weight:.2f}"] += 1
            folds.append(
                {
                    "outer_fold_id": f"{outer_season}:RW{window_index}",
                    "outer_season": outer_season,
                    "test_start_date": test_start,
                    "test_end_date": test_end,
                    "selection_information_end": max(
                        [str(record["date"]) for record in challenge_prior] or [""]
                    ),
                    "current_candidate_index": current_index,
                    "current_parameters": current_params,
                    "challenge_candidate_index": challenge_index,
                    "challenge_parameters": challenge_params,
                    "challenge_selected_weight": weight,
                    "challenge_guardrail_rejections": guardrail_rejections,
                    "current_prior_metrics": current_prior_metrics,
                    "challenge_prior_metrics": challenge_prior_metrics,
                    "outer_predictions": len(pairs),
                    "current_metrics": _aggregate(current_test),
                    "challenge_metrics": _aggregate(challenge_test),
                }
            )

    pairs = _paired_records(all_challenge, all_current)
    if not pairs:
        raise PlatformError(f"no paired challenge/current predictions: {competition_id}")
    validation_cfg = config["validation"]
    seed = int(validation_cfg["seed"]) + 6100
    bootstrap = {
        "total_goals_rps": _block_bootstrap_ci(
            pairs, "total_goals_rps", int(validation_cfg["bootstrap_resamples"]), seed
        ),
        "joint_log_score": _block_bootstrap_ci(
            pairs, "score_log", int(validation_cfg["bootstrap_resamples"]), seed + 1
        ),
        "one_x_two_brier": _block_bootstrap_ci(
            pairs, "one_x_two_brier", int(validation_cfg["bootstrap_resamples"]), seed + 2
        ),
        "one_x_two_rps": _block_bootstrap_ci(
            pairs, "one_x_two_rps", int(validation_cfg["bootstrap_resamples"]), seed + 3
        ),
    }
    challenge_metrics = _aggregate([pair[0] for pair in pairs])
    current_metrics = _aggregate([pair[1] for pair in pairs])
    mean_differences = {
        "total_goals_rps": float(challenge_metrics["mean_total_goals_rps"])
        - float(current_metrics["mean_total_goals_rps"]),
        "joint_log_score": float(challenge_metrics["mean_joint_log_score"])
        - float(current_metrics["mean_joint_log_score"]),
        "one_x_two_brier": float(challenge_metrics["mean_one_x_two_brier"])
        - float(current_metrics["mean_one_x_two_brier"]),
        "one_x_two_rps": float(challenge_metrics["mean_one_x_two_rps"])
        - float(current_metrics["mean_one_x_two_rps"]),
    }
    checks = {
        "minimum_predictions": len(pairs) >= 200,
        "minimum_time_folds": len(folds) >= 8,
        "strictly_prior_selection": all(
            fold["selection_information_end"]
            and str(fold["selection_information_end"]) < str(fold["test_start_date"])
            for fold in folds
        ),
        "disjoint_test_windows": len(seen_match_keys) == len(all_challenge),
        "total_rps_robustly_better": bootstrap["total_goals_rps"]["ci95_upper"] is not None
        and float(bootstrap["total_goals_rps"]["ci95_upper"]) < 0.0,
        "joint_log_mean_not_materially_worse": mean_differences["joint_log_score"]
        <= FUTURE_JOINT_LOG_MEAN_TOLERANCE,
        "one_x_two_brier_mean_not_materially_worse": mean_differences["one_x_two_brier"]
        <= FUTURE_ONE_X_TWO_BRIER_MEAN_TOLERANCE,
        "one_x_two_rps_mean_not_materially_worse": mean_differences["one_x_two_rps"]
        <= FUTURE_ONE_X_TWO_RPS_MEAN_TOLERANCE,
    }
    challenge_win = all(checks.values())
    report = {
        "schema_version": "V6.10-total-weight-challenge-r1",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "formal_current_version": "V5.0.1",
        "research_only": True,
        "formal_weight": 0,
        "promotion_authority": False,
        "engine_sha256": sha256_file(ENGINE_PATH),
        "current_candidate_config_sha256": sha256_json(current_candidates),
        "challenge_grid_sha256": sha256_json(challenge_candidates),
        "design": "expanding_window_nested_outer_current_comparator_vs_total_weight_grid",
        "weight_grid": list(WEIGHT_GRID),
        "structural_family_count": len(_structural_families(config)),
        "challenge_candidate_count": len(challenge_candidates),
        "outer_folds": len(folds),
        "outer_predictions": len(pairs),
        "selected_weight_counts": dict(sorted(selected_weight_counts.items())),
        "folds": folds,
        "current_metrics": current_metrics,
        "challenge_metrics": challenge_metrics,
        "challenge_minus_current_mean": mean_differences,
        "paired_block_bootstrap": bootstrap,
        "checks": checks,
        "diagnostic_status": "CHALLENGE_WIN" if challenge_win else "CHALLENGE_NOT_PROVEN",
        "governance": {
            "no_current_rule_change": True,
            "no_formal_weight_change": True,
            "no_runtime_probability_change": True,
            "no_market_data_used": True,
            "no_hand_authored_total_buckets": True,
            "same_joint_matrix_engine": True,
            "future_window_outcomes_never_enter_selection": True,
            "challenge_win_does_not_promote": True,
        },
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_smoke(*, write: bool = True) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    for competition_id in SMOKE_DOMAINS:
        try:
            report = validate_competition(competition_id, write=write)
            reports[competition_id] = {
                "outer_folds": report["outer_folds"],
                "outer_predictions": report["outer_predictions"],
                "selected_weight_counts": report["selected_weight_counts"],
                "challenge_minus_current_mean": report["challenge_minus_current_mean"],
                "total_rps_bootstrap": report["paired_block_bootstrap"]["total_goals_rps"],
                "checks": report["checks"],
                "diagnostic_status": report["diagnostic_status"],
            }
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": f"{type(exc).__name__}: {exc}"})
    manifest = {
        "schema_version": "V6.10-total-weight-challenge-status-r1",
        "generated_at_utc": utc_now(),
        "formal_current_version": "V5.0.1",
        "research_only": True,
        "formal_weight": 0,
        "promotion_authority": False,
        "competition_count_requested": len(SMOKE_DOMAINS),
        "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "reports": reports,
        "failures": failures,
    }
    if write:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"V6.10 smoke failed: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", choices=SMOKE_DOMAINS)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        result = (
            validate_competition(args.competition, write=not args.check_only)
            if args.competition
            else run_smoke(write=not args.check_only)
        )
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
