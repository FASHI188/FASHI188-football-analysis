#!/usr/bin/env python3
"""V5.0.4 ITA-only player-XI unified score-matrix projection OOF.

The V5.0.3 corrected governance receipt permits only ITA_SerieA to enter this
stage. The frozen player profile is ``player_margin_p10_s100`` in both untouched
outer seasons. For each eligible target match:

1. reconstruct the calibrated formal score matrix using the frozen formal fold;
2. obtain the player-XI expected-margin adjustment from the timestamp-safe
   same-season player residual state;
3. preserve the full total-goals marginal exactly;
4. apply the minimum-KL exponential tilt within each total-goal slice so the
   candidate expected home-goal mean moves by half the margin adjustment;
5. derive every evaluated probability target from that one candidate matrix.

No target-match actual XI is used. No total-goal probability is changed. The
historical replay still lacks complete point-in-time frozen handicap lines, so a
probability-side pass remains AH-evidence blocked and formal_weight=0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
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

from backtest_last_complete_season_all_domains_v470 import (  # noqa: E402
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from bayesian_dynamic_state_oof_v500 import (  # noqa: E402
    _home_share_tilt_preserve_totals,
    _matrix_means,
    _metric_row,
    _total_distribution,
)
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import (  # noqa: E402
    PlatformError,
    atomic_write_json,
    derive_score_marginals,
    load_json,
    read_processed_matches,
    score_matrix_rows,
    sha256_file,
)
from player_xi_residual_signal_oof_v502 import (  # noqa: E402
    PROFILES,
    base_records,
    simulate_profile,
)

COMPETITION_ID = "ITA_SerieA"
FROZEN_PROFILE_ID = "player_margin_p10_s100"
GATE_RECEIPT = ROOT / "manifests" / "player_xi_gate_adjudication_v503_status.json"
REPLICATION_RECEIPT = ROOT / "manifests" / "player_xi_residual_replication_v503_status.json"
PLAYER_SCRIPT = ROOT / "validation" / "player_xi_residual_signal_oof_v502.py"
PROJECTION_HELPER = ROOT / "validation" / "bayesian_dynamic_state_oof_v500.py"
OUT = ROOT / "manifests" / "player_xi_matrix_projection_ita_v504_status.json"
DETAIL = ROOT / "manifests" / "player_xi_matrix_projection_ita_v504" / "ITA_SerieA.json"

BOOTSTRAP_DRAWS = 2000
BLOCK_SIZE = 20
SEED = 5042026
EPS = 1e-12

# Frozen before execution. Lower is better for proper scores.
ONE_X_TWO_CI_LIMIT = 0.0
JOINT_LOG_NONINFERIORITY = 0.002
MARGIN_RPS_CI_LIMIT = 0.0
PROBABILITY_TOLERANCE = 1e-10
TOTAL_MARGINAL_TOLERANCE = 1e-10
HOME_MEAN_TOLERANCE = 1e-8
TOTAL_METRIC_TOLERANCE = 1e-12

METRICS = (
    "one_x_two_accuracy",
    "one_x_two_brier",
    "one_x_two_rps",
    "joint_log",
    "score_top1",
    "score_top3",
    "total_top1",
    "total_top2",
    "total_rps",
    "margin_rps",
    "margin_mean_squared_error",
    "margin_mean_absolute_error",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def profile_hash(profile: dict[str, Any]) -> str:
    payload = json.dumps(profile, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def match_key(match) -> str:
    return (
        f"{COMPETITION_ID}:{match.season}:{match.date.date().isoformat()}:"
        f"{match.home_team}:{match.away_team}"
    )


def probability_sum(matrix: list[dict[str, Any]]) -> float:
    return sum(float(cell["probability"]) for cell in matrix)


def margin_distribution(matrix: list[dict[str, Any]]) -> dict[int, float]:
    output: dict[int, float] = defaultdict(float)
    for home, away, probability in score_matrix_rows(matrix):
        output[home - away] += probability
    return dict(output)


def ordered_rps(distribution: dict[int, float], actual: int) -> float:
    if not distribution:
        raise PlatformError("empty ordered distribution")
    low = min(distribution)
    high = max(distribution)
    actual_clipped = min(high, max(low, int(actual)))
    thresholds = list(range(low, high))
    if not thresholds:
        return 0.0
    running = 0.0
    score = 0.0
    for threshold in thresholds:
        running += float(distribution.get(threshold, 0.0))
        observed = 1.0 if actual_clipped <= threshold else 0.0
        score += (running - observed) ** 2
    return score / len(thresholds)


def extended_metrics(matrix: list[dict[str, Any]], match) -> dict[str, float]:
    base = {key: float(value) for key, value in _metric_row(matrix, match).items()}
    home_mean, away_mean, _ = _matrix_means(matrix)
    expected_margin = home_mean - away_mean
    actual_margin = int(match.home_goals) - int(match.away_goals)
    error = float(actual_margin) - expected_margin
    base.update({
        "margin_rps": ordered_rps(margin_distribution(matrix), actual_margin),
        "margin_mean_squared_error": error * error,
        "margin_mean_absolute_error": abs(error),
        "expected_margin": expected_margin,
        "expected_home_goals": home_mean,
        "expected_away_goals": away_mean,
    })
    return base


def total_marginal_residual(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
) -> float:
    before = _total_distribution(baseline)
    after = _total_distribution(candidate)
    return max(
        abs(float(after.get(total, 0.0)) - float(before.get(total, 0.0)))
        for total in set(before) | set(after)
    )


def paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise PlatformError("cannot summarize empty paired rows")
    summary: dict[str, Any] = {}
    for metric in METRICS:
        baseline = mean(float(row[f"baseline_{metric}"]) for row in rows)
        candidate = mean(float(row[f"candidate_{metric}"]) for row in rows)
        summary[metric] = {
            "baseline": baseline,
            "candidate": candidate,
            "candidate_minus_baseline": candidate - baseline,
        }
    return summary


def blocks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (row["season"], row["date"], row["match_key"]))
    return [ordered[index:index + BLOCK_SIZE] for index in range(0, len(ordered), BLOCK_SIZE)]


def bootstrap(
    rows: list[dict[str, Any]],
    candidate_key: str,
    baseline_key: str,
    seed: int,
) -> dict[str, Any]:
    grouped = blocks(rows)
    if not grouped:
        raise PlatformError("no bootstrap blocks")
    point = mean(float(row[candidate_key]) - float(row[baseline_key]) for row in rows)
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled = []
        for _ in range(len(grouped)):
            sampled.extend(rng.choice(grouped))
        samples.append(
            mean(float(row[candidate_key]) - float(row[baseline_key]) for row in sampled)
        )
    samples.sort()
    return {
        "mean_difference": point,
        "ci95_lower": samples[int(0.025 * (len(samples) - 1))],
        "ci95_upper": samples[int(0.975 * (len(samples) - 1))],
        "blocks": len(grouped),
        "draws": BOOTSTRAP_DRAWS,
    }


def frozen_profile_and_seasons() -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    gate = load_json(GATE_RECEIPT)
    domain = (
        gate.get("player_signal_independent_replication", {})
        .get("domains", {})
        .get(COMPETITION_ID)
    )
    if not isinstance(domain, dict):
        raise PlatformError("corrected ITA player-signal adjudication missing")
    if domain.get("corrected_status") != "PLAYER_SIGNAL_PASS_MATRIX_PROJECTION_REVIEW":
        raise PlatformError(f"ITA not eligible for matrix review: {domain.get('corrected_status')}")
    selected = [str(item) for item in domain.get("selected_profiles") or []]
    if selected != [FROZEN_PROFILE_ID, FROZEN_PROFILE_ID]:
        raise PlatformError(f"unexpected frozen profiles: {selected}")
    profile = next((item for item in PROFILES if item["id"] == FROZEN_PROFILE_ID), None)
    if profile is None:
        raise PlatformError("frozen profile not found in implementation")
    replication = load_json(REPLICATION_RECEIPT)
    report = (replication.get("reports") or {}).get(COMPETITION_ID) or {}
    outer_count = int(report.get("outer_prediction_count") or 0)
    if outer_count < 500:
        raise PlatformError(f"replication sample gate failed: {outer_count}")
    detail = load_json(
        ROOT / "manifests" / "player_xi_residual_replication_v503" / f"{COMPETITION_ID}.json"
    )
    seasons = [str(item) for item in detail.get("outer_targets") or []]
    if len(seasons) != 2:
        raise PlatformError(f"expected two frozen outer seasons, got {seasons}")
    return profile, seasons, {
        "gate_receipt_sha256": sha256_file(GATE_RECEIPT),
        "replication_receipt_sha256": sha256_file(REPLICATION_RECEIPT),
        "player_script_sha256": sha256_file(PLAYER_SCRIPT),
        "projection_helper_sha256": sha256_file(PROJECTION_HELPER),
        "profile_sha256": profile_hash(profile),
        "replication_outer_prediction_count": outer_count,
        "selected_profiles": selected,
    }


def reconstruct_baseline(match, all_matches, formal_report: dict[str, Any]) -> list[dict[str, Any]]:
    season = str(match.season)
    fold = _fold_for_season(formal_report, season)
    parameters = fold.get("selected_parameters")
    if not isinstance(parameters, dict):
        raise PlatformError(f"missing frozen formal parameters for {season}")
    matrix = _predict_from_loaded_matches(
        all_matches,
        match.home_team,
        match.away_team,
        match.date,
        season,
        parameters,
    )
    temperature, _ = _target_season_temperature(COMPETITION_ID, season)
    if abs(temperature - 1.0) > 1e-15:
        matrix = temperature_scale_matrix(matrix, temperature)
    return matrix


def project_matrix(
    baseline: list[dict[str, Any]],
    margin_adjustment: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_home, base_away, base_total = _matrix_means(baseline)
    target_margin = (base_home - base_away) + float(margin_adjustment)
    target_home = (base_total + target_margin) / 2.0
    candidate, tilt = _home_share_tilt_preserve_totals(baseline, target_home)
    candidate_home, candidate_away, candidate_total = _matrix_means(candidate)
    achieved_margin = candidate_home - candidate_away
    return candidate, {
        "objective": "minimum_KL_exponential_tilt_within_each_total_goal_slice",
        "constraint": "preserve_full_total_goal_marginal_and_match_target_home_mean",
        "baseline_home_mean": base_home,
        "baseline_away_mean": base_away,
        "baseline_total_mean": base_total,
        "baseline_margin_mean": base_home - base_away,
        "player_margin_adjustment": float(margin_adjustment),
        "target_margin_mean": target_margin,
        "target_home_mean": target_home,
        "achieved_home_mean": candidate_home,
        "achieved_away_mean": candidate_away,
        "achieved_total_mean": candidate_total,
        "achieved_margin_mean": achieved_margin,
        "target_margin_residual": achieved_margin - target_margin,
        "probability_sum_residual": abs(probability_sum(candidate) - 1.0),
        "max_total_marginal_residual": total_marginal_residual(baseline, candidate),
        **tilt,
    }


def run(*, write: bool) -> dict[str, Any]:
    profile, outer_seasons, binding = frozen_profile_and_seasons()
    records, data_audit = base_records(COMPETITION_ID)
    by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_season[str(record["season"])].append(record)
    adjustment_map: dict[str, dict[str, Any]] = {}
    simulation_counts: dict[str, int] = {}
    for season in outer_seasons:
        simulated = simulate_profile(by_season.get(season, []), profile)
        simulation_counts[season] = len(simulated)
        for row in simulated:
            key = str(row["match_key"])
            if key in adjustment_map:
                raise PlatformError(f"duplicate player adjustment: {key}")
            adjustment_map[key] = row

    formal_report = load_json(REPORT_ROOT / f"{COMPETITION_ID}.json")
    all_matches = read_processed_matches(COMPETITION_ID)
    matches = {
        match_key(match): match
        for match in all_matches
        if str(match.season) in set(outer_seasons)
    }

    paired_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    unmatched_adjustments: list[str] = []
    for key in sorted(adjustment_map):
        adjustment = adjustment_map[key]
        match = matches.get(key)
        if match is None:
            unmatched_adjustments.append(key)
            continue
        baseline = reconstruct_baseline(match, all_matches, formal_report)
        candidate, audit = project_matrix(baseline, float(adjustment["margin_adjustment"]))
        base_metrics = extended_metrics(baseline, match)
        candidate_metrics = extended_metrics(candidate, match)
        row = {
            "match_key": key,
            "season": str(match.season),
            "date": match.date.date().isoformat(),
            "selected_profile": FROZEN_PROFILE_ID,
        }
        for metric in METRICS:
            row[f"baseline_{metric}"] = base_metrics[metric]
            row[f"candidate_{metric}"] = candidate_metrics[metric]
        paired_rows.append(row)
        audit_rows.append({
            "match_key": key,
            "season": str(match.season),
            "date": match.date.date().isoformat(),
            "home_team": match.home_team,
            "away_team": match.away_team,
            "actual_score": [int(match.home_goals), int(match.away_goals)],
            "player_state": {
                "margin_adjustment": float(adjustment["margin_adjustment"]),
                "total_adjustment": float(adjustment["total_adjustment"]),
                "home_margin_rating": float(adjustment["home_margin_rating"]),
                "away_margin_rating": float(adjustment["away_margin_rating"]),
            },
            "projection": audit,
            "baseline_1x2": derive_score_marginals(baseline)["1x2"],
            "candidate_1x2": derive_score_marginals(candidate)["1x2"],
        })

    if unmatched_adjustments:
        raise PlatformError(f"unmatched adjustments: {len(unmatched_adjustments)}")
    if len(paired_rows) < 500:
        raise PlatformError(f"matrix projection sample below 500: {len(paired_rows)}")

    folds = []
    for season in outer_seasons:
        season_rows = [row for row in paired_rows if row["season"] == season]
        if not season_rows:
            raise PlatformError(f"no projected rows for outer season {season}")
        folds.append({
            "target_season": season,
            "selected_profile": FROZEN_PROFILE_ID,
            "outer_predictions": len(season_rows),
            "metrics": paired_summary(season_rows),
        })
    pooled = paired_summary(paired_rows)
    ci = {
        "one_x_two_brier": bootstrap(
            paired_rows,
            "candidate_one_x_two_brier",
            "baseline_one_x_two_brier",
            SEED + 1,
        ),
        "one_x_two_rps": bootstrap(
            paired_rows,
            "candidate_one_x_two_rps",
            "baseline_one_x_two_rps",
            SEED + 2,
        ),
        "joint_log": bootstrap(
            paired_rows,
            "candidate_joint_log",
            "baseline_joint_log",
            SEED + 3,
        ),
        "score_top1": bootstrap(
            paired_rows,
            "candidate_score_top1",
            "baseline_score_top1",
            SEED + 4,
        ),
        "score_top3": bootstrap(
            paired_rows,
            "candidate_score_top3",
            "baseline_score_top3",
            SEED + 5,
        ),
        "total_rps": bootstrap(
            paired_rows,
            "candidate_total_rps",
            "baseline_total_rps",
            SEED + 6,
        ),
        "margin_rps": bootstrap(
            paired_rows,
            "candidate_margin_rps",
            "baseline_margin_rps",
            SEED + 7,
        ),
        "margin_mean_squared_error": bootstrap(
            paired_rows,
            "candidate_margin_mean_squared_error",
            "baseline_margin_mean_squared_error",
            SEED + 8,
        ),
    }

    max_probability_residual = max(
        float(row["projection"]["probability_sum_residual"]) for row in audit_rows
    )
    max_total_residual = max(
        float(row["projection"]["max_total_marginal_residual"]) for row in audit_rows
    )
    max_home_residual = max(
        abs(float(row["projection"]["home_mean_residual"])) for row in audit_rows
    )
    max_margin_residual = max(
        abs(float(row["projection"]["target_margin_residual"])) for row in audit_rows
    )
    max_total_metric_difference = max(
        abs(float(pooled[metric]["candidate_minus_baseline"]))
        for metric in ("total_top1", "total_top2", "total_rps")
    )
    season_accuracy_differences = [
        float(fold["metrics"]["one_x_two_accuracy"]["candidate_minus_baseline"])
        for fold in folds
    ]

    checks = {
        "two_outer_seasons": len(folds) == 2,
        "minimum_outer_predictions_500": len(paired_rows) >= 500,
        "frozen_profile_identical_both_outer_seasons": all(
            fold["selected_profile"] == FROZEN_PROFILE_ID for fold in folds
        ),
        "one_x_two_brier_ci_improves": ci["one_x_two_brier"]["ci95_upper"] < ONE_X_TWO_CI_LIMIT,
        "one_x_two_rps_ci_improves": ci["one_x_two_rps"]["ci95_upper"] < ONE_X_TWO_CI_LIMIT,
        "joint_log_ci_noninferior": ci["joint_log"]["ci95_upper"] <= JOINT_LOG_NONINFERIORITY,
        "margin_rps_ci_improves": ci["margin_rps"]["ci95_upper"] < MARGIN_RPS_CI_LIMIT,
        "margin_mse_ci_improves": ci["margin_mean_squared_error"]["ci95_upper"] < 0.0,
        "one_x_two_accuracy_nonworse_pooled": pooled["one_x_two_accuracy"]["candidate"] + EPS >= pooled["one_x_two_accuracy"]["baseline"],
        "one_x_two_accuracy_nonworse_each_outer_season": all(value >= -EPS for value in season_accuracy_differences),
        "score_top1_nonworse_pooled": pooled["score_top1"]["candidate"] + EPS >= pooled["score_top1"]["baseline"],
        "score_top3_nonworse_pooled": pooled["score_top3"]["candidate"] + EPS >= pooled["score_top3"]["baseline"],
        "score_top3_nonworse_each_outer_season": all(
            float(fold["metrics"]["score_top3"]["candidate_minus_baseline"]) >= -EPS
            for fold in folds
        ),
        "total_metrics_exactly_preserved": max_total_metric_difference <= TOTAL_METRIC_TOLERANCE,
        "probability_conservation": max_probability_residual <= PROBABILITY_TOLERANCE,
        "full_total_marginal_conservation": max_total_residual <= TOTAL_MARGINAL_TOLERANCE,
        "home_mean_constraint_converged": max_home_residual <= HOME_MEAN_TOLERANCE,
        "margin_constraint_converged": max_margin_residual <= 2.0 * HOME_MEAN_TOLERANCE,
        "target_actual_xi_excluded": True,
        "updates_delayed_until_source_observed_at": True,
        "single_joint_matrix_used_for_all_metrics": True,
    }
    probability_side_pass = all(checks.values())
    handicap_target_available = False
    status = (
        "MATRIX_PROJECTION_PROBABILITY_PASS_AH_EVIDENCE_BLOCKED"
        if probability_side_pass
        else "MATRIX_PROJECTION_REJECT_KEEP_FORMAL_WEIGHT_0"
    )

    report = {
        "schema_version": "V5.0.4-player-xi-matrix-projection-ita-r1",
        "generated_at_utc": utc_now(),
        "competition_id": COMPETITION_ID,
        "status": status,
        "frozen_binding": binding,
        "frozen_profile": profile,
        "outer_seasons": outer_seasons,
        "simulation_counts": simulation_counts,
        "data_audit": data_audit,
        "outer_prediction_count": len(paired_rows),
        "folds": folds,
        "pooled_metrics": pooled,
        "paired_block_bootstrap": ci,
        "projection_audit": {
            "objective": "minimum_KL_exponential_tilt_within_each_total_goal_slice",
            "prior": "calibrated_formal_unified_score_matrix",
            "constraints": [
                "preserve every total-goal marginal probability",
                "shift expected home-goal mean to implement frozen player margin adjustment",
                "probabilities remain nonnegative and sum to one",
            ],
            "optimizer": "bounded_bisection_on_exponential_tilt_parameter",
            "iterations_per_match": 100,
            "convergence_status": "PASS" if max_home_residual <= HOME_MEAN_TOLERANCE else "FAIL",
            "max_probability_sum_residual": max_probability_residual,
            "max_total_marginal_residual": max_total_residual,
            "max_home_mean_residual": max_home_residual,
            "max_margin_mean_residual": max_margin_residual,
            "max_total_metric_difference": max_total_metric_difference,
            "audited_match_count": len(audit_rows),
        },
        "checks": checks,
        "probability_side_pass": probability_side_pass,
        "handicap_fourth_target_available": handicap_target_available,
        "handicap_target_status": "UNAVAILABLE_NO_COMPLETE_POINT_IN_TIME_FROZEN_HANDICAP_LINES_IN_CURRENT_REPLAY",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "ITA-only matrix research. Even a probability-side pass remains formal_weight=0 until complete PIT handicap evidence and a future CURRENT-compliant hash-bound promotion receipt are available.",
    }
    if write:
        atomic_write_json(DETAIL, {
            **report,
            "per_match_projection_audit": audit_rows,
        })
        atomic_write_json(OUT, {
            **report,
            "per_match_projection_audit": "stored_in_detail_receipt",
        })
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    report = run(write=not args.check_only)
    if args.print_summary:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
