#!/usr/bin/env python3
"""V5.0.2 lineup-only latent-signal chronological OOF diagnostic.

This stage asks a narrow question before any probability mutation is allowed:
Do point-in-time lineup continuity and probable-XI concentration features predict
residual match margin or total goals beyond the frozen calibrated formal matrix?

The target match's actual XI is never used as an input. For each target freeze,
features are derived only from same-season observed lineups whose
source_observed_at_utc is strictly before the target match date. Frozen formal
matrices use the existing nested chronological fold parameters and target-season
OOF calibration.

Candidate profiles are selected using only earlier completed seasons. The last
two completed seasons are held out as outer folds. A baseline-zero profile is
always available, so the procedure never forces a lineup effect.

This is a feature-signal diagnostic only. It does not alter score matrices,
formal probabilities or formal weights. Even a passing signal requires a later
single-matrix projection OOF and independent handicap evaluation.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
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
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import (  # noqa: E402
    PlatformError,
    atomic_write_json,
    load_json,
    read_processed_matches,
    score_matrix_rows,
)

LINK_ROOT = ROOT / "player_xi_links"
IDENTITY_STATUS = ROOT / "manifests" / "lineup_match_identity_v502_status.json"
OUT = ROOT / "manifests" / "lineup_latent_signal_oof_v502_status.json"
REPORT_DIR = ROOT / "manifests" / "lineup_latent_signal_oof_v502"

PILOT_DOMAINS = ("ESP_LaLiga", "GER_Bundesliga")
LOOKBACK = 8
DECAY = 0.78
MIN_TEAM_HISTORY = 3
MIN_MODEL_ROWS = 250
BOOTSTRAP_DRAWS = 1200
BLOCK_SIZE = 20
SEED = 5022026
MARGIN_CLIP = 0.75
TOTAL_CLIP = 0.75
TOTAL_MSE_NONINFERIORITY = 0.02

PROFILES = [
    {
        "id": "baseline_zero",
        "margin_features": [],
        "total_features": [],
        "ridge": 0.0,
    },
    {
        "id": "overlap_margin_ridge5",
        "margin_features": ["recent_overlap_diff"],
        "total_features": [],
        "ridge": 5.0,
    },
    {
        "id": "continuity_margin_ridge5",
        "margin_features": [
            "continuity_diff",
            "confidence_diff",
            "effective_roster_diff",
        ],
        "total_features": [],
        "ridge": 5.0,
    },
    {
        "id": "continuity_two_axis_ridge5",
        "margin_features": [
            "continuity_diff",
            "confidence_diff",
            "effective_roster_diff",
        ],
        "total_features": [
            "instability_sum",
            "effective_roster_sum",
            "confidence_sum",
        ],
        "ridge": 5.0,
    },
    {
        "id": "continuity_two_axis_ridge20",
        "margin_features": [
            "continuity_diff",
            "confidence_diff",
            "effective_roster_diff",
        ],
        "total_features": [
            "instability_sum",
            "effective_roster_sum",
            "confidence_sum",
        ],
        "ridge": 20.0,
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def season_year(season: str) -> int:
    token = str(season).strip()
    if len(token) < 4 or not token[:4].isdigit():
        raise PlatformError(f"cannot parse season: {season!r}")
    return int(token[:4])


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise PlatformError(f"missing JSONL: {path}")
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PlatformError(f"invalid JSONL {path}:{line_number}: {exc}") from exc
        rows.append(row)
    return rows


def matrix_means(matrix: list[dict[str, Any]]) -> tuple[float, float, float]:
    home = 0.0
    away = 0.0
    for h, a, probability in score_matrix_rows(matrix):
        home += h * probability
        away += a * probability
    return home, away, home + away


def parse_observed(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def team_lineup_history(links: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    history: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        season = str(link["season"])
        history[(season, str(link["home_team"]))].append({
            "observed_at": parse_observed(link["home_source_observed_at_utc"]),
            "date": str(link["date"]),
            "starters": tuple(str(item) for item in link["home_starters"]),
        })
        history[(season, str(link["away_team"]))].append({
            "observed_at": parse_observed(link["away_source_observed_at_utc"]),
            "date": str(link["date"]),
            "starters": tuple(str(item) for item in link["away_starters"]),
        })
    for rows in history.values():
        rows.sort(key=lambda item: (item["observed_at"], item["date"]))
    return history


def lineup_features(prior: list[dict[str, Any]]) -> dict[str, float] | None:
    prior = prior[-LOOKBACK:]
    if len(prior) < MIN_TEAM_HISTORY:
        return None
    player_weight: dict[str, float] = defaultdict(float)
    total_weight = 0.0
    for age, row in enumerate(reversed(prior)):
        weight = DECAY ** age
        total_weight += weight
        for player in row["starters"]:
            player_weight[player] += weight
    probabilities = {
        player: weight / max(total_weight, 1e-12)
        for player, weight in player_weight.items()
    }
    top = sorted(probabilities.values(), reverse=True)[:11]
    if len(top) < 11:
        return None
    confidence = mean(top)
    normalized = [value / 11.0 for value in probabilities.values()]
    effective_roster = 1.0 / max(sum(value * value for value in normalized), 1e-12)
    overlap_values = []
    recent = prior[-6:]
    for left, right in zip(recent, recent[1:]):
        overlap_values.append(
            len(set(left["starters"]) & set(right["starters"])) / 11.0
        )
    recent_overlap = mean(overlap_values) if overlap_values else confidence
    continuity = 0.5 * confidence + 0.5 * recent_overlap
    return {
        "confidence": confidence,
        "recent_overlap": recent_overlap,
        "continuity": continuity,
        "effective_roster": effective_roster,
        "history_count": float(len(prior)),
    }


def feature_vector(home: dict[str, float], away: dict[str, float]) -> dict[str, float]:
    return {
        "continuity_diff": home["continuity"] - away["continuity"],
        "confidence_diff": home["confidence"] - away["confidence"],
        "recent_overlap_diff": home["recent_overlap"] - away["recent_overlap"],
        "effective_roster_diff": home["effective_roster"] - away["effective_roster"],
        "instability_sum": (1.0 - home["continuity"]) + (1.0 - away["continuity"]),
        "effective_roster_sum": home["effective_roster"] + away["effective_roster"],
        "confidence_sum": home["confidence"] + away["confidence"],
    }


def build_rows(competition_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    identity = load_json(IDENTITY_STATUS)
    if competition_id not in identity.get("passed_domains", []):
        raise PlatformError(f"identity bridge not passed: {competition_id}")
    links = read_jsonl(LINK_ROOT / competition_id / "fixture_lineup_links.jsonl")
    history = team_lineup_history(links)
    report = load_json(REPORT_ROOT / f"{competition_id}.json")
    all_matches = read_processed_matches(competition_id)
    seasons_with_folds = {
        str(item.get("outer_season"))
        for item in report.get("folds", [])
        if item.get("outer_season")
    }
    rows: list[dict[str, Any]] = []
    skip_reasons: dict[str, int] = defaultdict(int)
    by_season: dict[str, int] = defaultdict(int)

    for match in sorted(all_matches, key=lambda item: (item.date, item.home_team, item.away_team)):
        season = str(match.season)
        if season not in seasons_with_folds:
            continue
        fold = _fold_for_season(report, season)
        parameters = fold.get("selected_parameters")
        if not isinstance(parameters, dict):
            skip_reasons["missing_frozen_parameters"] += 1
            continue
        cutoff = match.date
        home_prior = [
            item for item in history.get((season, match.home_team), [])
            if item["observed_at"] < cutoff
        ]
        away_prior = [
            item for item in history.get((season, match.away_team), [])
            if item["observed_at"] < cutoff
        ]
        home_features = lineup_features(home_prior)
        away_features = lineup_features(away_prior)
        if home_features is None or away_features is None:
            skip_reasons["insufficient_same_season_lineup_history"] += 1
            continue
        try:
            matrix = _predict_from_loaded_matches(
                all_matches,
                match.home_team,
                match.away_team,
                cutoff,
                season,
                parameters,
            )
        except PlatformError:
            skip_reasons["formal_sample_gate"] += 1
            continue
        temperature, calibration_mode = _target_season_temperature(competition_id, season)
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        base_home, base_away, base_total = matrix_means(matrix)
        actual_margin = int(match.home_goals) - int(match.away_goals)
        actual_total = int(match.home_goals) + int(match.away_goals)
        row = {
            "competition_id": competition_id,
            "season": season,
            "date": match.date.date().isoformat(),
            "match_key": f"{competition_id}:{season}:{match.date.date().isoformat()}:{match.home_team}:{match.away_team}",
            "home_team": match.home_team,
            "away_team": match.away_team,
            "base_margin": base_home - base_away,
            "base_total": base_total,
            "actual_margin": float(actual_margin),
            "actual_total": float(actual_total),
            "margin_residual": float(actual_margin) - (base_home - base_away),
            "total_residual": float(actual_total) - base_total,
            "oof_temperature": temperature,
            "oof_calibration_mode": calibration_mode,
            **feature_vector(home_features, away_features),
        }
        rows.append(row)
        by_season[season] += 1
    return rows, {
        "linked_fixture_count": len(links),
        "feature_row_count": len(rows),
        "feature_rows_by_season": dict(sorted(by_season.items(), key=lambda item: season_year(item[0]))),
        "skip_reasons": dict(skip_reasons),
    }


def solve_linear(system: list[list[float]], target: list[float]) -> list[float]:
    n = len(target)
    augmented = [list(system[index]) + [target[index]] for index in range(n)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise PlatformError("singular ridge system")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(n):
            if row == column:
                continue
            factor = augmented[row][column]
            if abs(factor) < 1e-18:
                continue
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(n + 1)
            ]
    return [augmented[index][-1] for index in range(n)]


def fit_ridge(
    rows: list[dict[str, Any]],
    features: list[str],
    target_key: str,
    ridge: float,
) -> dict[str, Any]:
    if not features:
        return {"features": [], "intercept": 0.0, "coefficients": [], "means": [], "scales": []}
    if len(rows) < MIN_MODEL_ROWS:
        raise PlatformError(f"insufficient model rows: {len(rows)} < {MIN_MODEL_ROWS}")
    means = [mean(float(row[key]) for row in rows) for key in features]
    scales = []
    for key, centre in zip(features, means):
        variance = mean((float(row[key]) - centre) ** 2 for row in rows)
        scales.append(max(math.sqrt(variance), 1e-6))
    dimension = len(features) + 1
    xtx = [[0.0 for _ in range(dimension)] for _ in range(dimension)]
    xty = [0.0 for _ in range(dimension)]
    for row in rows:
        vector = [1.0] + [
            (float(row[key]) - centre) / scale
            for key, centre, scale in zip(features, means, scales)
        ]
        target = float(row[target_key])
        for i in range(dimension):
            xty[i] += vector[i] * target
            for j in range(dimension):
                xtx[i][j] += vector[i] * vector[j]
    for index in range(1, dimension):
        xtx[index][index] += float(ridge)
    beta = solve_linear(xtx, xty)
    return {
        "features": list(features),
        "intercept": beta[0],
        "coefficients": beta[1:],
        "means": means,
        "scales": scales,
        "ridge": float(ridge),
        "training_rows": len(rows),
    }


def predict_model(model: dict[str, Any], row: dict[str, Any]) -> float:
    value = float(model.get("intercept", 0.0))
    for key, coefficient, centre, scale in zip(
        model.get("features", []),
        model.get("coefficients", []),
        model.get("means", []),
        model.get("scales", []),
    ):
        value += float(coefficient) * (float(row[key]) - float(centre)) / float(scale)
    return value


def fit_profile(rows: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    if profile["id"] == "baseline_zero":
        return {
            "profile_id": profile["id"],
            "margin": fit_ridge(rows, [], "margin_residual", 0.0),
            "total": fit_ridge(rows, [], "total_residual", 0.0),
        }
    return {
        "profile_id": profile["id"],
        "margin": fit_ridge(
            rows,
            list(profile["margin_features"]),
            "margin_residual",
            float(profile["ridge"]),
        ),
        "total": fit_ridge(
            rows,
            list(profile["total_features"]),
            "total_residual",
            float(profile["ridge"]),
        ),
    }


def score_profile(model: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for row in rows:
        margin_adjustment = max(
            -MARGIN_CLIP,
            min(MARGIN_CLIP, predict_model(model["margin"], row)),
        )
        total_adjustment = max(
            -TOTAL_CLIP,
            min(TOTAL_CLIP, predict_model(model["total"], row)),
        )
        base_margin_error = float(row["margin_residual"])
        base_total_error = float(row["total_residual"])
        candidate_margin_error = base_margin_error - margin_adjustment
        candidate_total_error = base_total_error - total_adjustment
        scored.append({
            "match_key": row["match_key"],
            "season": row["season"],
            "date": row["date"],
            "profile_id": model["profile_id"],
            "margin_adjustment": margin_adjustment,
            "total_adjustment": total_adjustment,
            "baseline_margin_squared_error": base_margin_error ** 2,
            "candidate_margin_squared_error": candidate_margin_error ** 2,
            "baseline_margin_absolute_error": abs(base_margin_error),
            "candidate_margin_absolute_error": abs(candidate_margin_error),
            "baseline_total_squared_error": base_total_error ** 2,
            "candidate_total_squared_error": candidate_total_error ** 2,
            "baseline_total_absolute_error": abs(base_total_error),
            "candidate_total_absolute_error": abs(candidate_total_error),
        })
    return scored


def metric_summary(scored: list[dict[str, Any]]) -> dict[str, Any]:
    if not scored:
        raise PlatformError("cannot summarize empty scored rows")
    keys = (
        "baseline_margin_squared_error",
        "candidate_margin_squared_error",
        "baseline_margin_absolute_error",
        "candidate_margin_absolute_error",
        "baseline_total_squared_error",
        "candidate_total_squared_error",
        "baseline_total_absolute_error",
        "candidate_total_absolute_error",
    )
    out = {key: mean(float(row[key]) for row in scored) for key in keys}
    out["margin_mse_difference"] = (
        out["candidate_margin_squared_error"] - out["baseline_margin_squared_error"]
    )
    out["margin_mae_difference"] = (
        out["candidate_margin_absolute_error"] - out["baseline_margin_absolute_error"]
    )
    out["total_mse_difference"] = (
        out["candidate_total_squared_error"] - out["baseline_total_squared_error"]
    )
    out["total_mae_difference"] = (
        out["candidate_total_absolute_error"] - out["baseline_total_absolute_error"]
    )
    out["row_count"] = len(scored)
    return out


def inner_select(
    prior_seasons: list[str],
    rows_by_season: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile_results = []
    for profile in PROFILES:
        inner_scored: list[dict[str, Any]] = []
        fold_details = []
        for index in range(1, len(prior_seasons)):
            target = prior_seasons[index]
            train_seasons = prior_seasons[:index]
            training = [row for season in train_seasons for row in rows_by_season.get(season, [])]
            target_rows = rows_by_season.get(target, [])
            if profile["id"] != "baseline_zero" and len(training) < MIN_MODEL_ROWS:
                continue
            if not target_rows:
                continue
            model = fit_profile(training, profile)
            scored = score_profile(model, target_rows)
            inner_scored.extend(scored)
            fold_details.append({
                "target_season": target,
                "training_seasons": train_seasons,
                "training_rows": len(training),
                "target_rows": len(target_rows),
            })
        if not inner_scored:
            continue
        summary = metric_summary(inner_scored)
        baseline_margin = summary["baseline_margin_squared_error"]
        baseline_total = summary["baseline_total_squared_error"]
        margin_ratio = summary["candidate_margin_squared_error"] / max(baseline_margin, 1e-12)
        total_ratio = summary["candidate_total_squared_error"] / max(baseline_total, 1e-12)
        eligible = margin_ratio <= 1.01 and total_ratio <= 1.01
        objective = margin_ratio + 0.5 * total_ratio
        profile_results.append({
            "profile_id": profile["id"],
            "eligible": eligible,
            "objective": objective,
            "margin_mse_ratio": margin_ratio,
            "total_mse_ratio": total_ratio,
            "inner_summary": summary,
            "inner_folds": fold_details,
        })
    eligible = [item for item in profile_results if item["eligible"]]
    if not eligible:
        raise PlatformError("no eligible profile including baseline")
    eligible.sort(key=lambda item: (item["objective"], item["profile_id"]))
    return eligible[0], profile_results


def blocks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (season_year(row["season"]), row["date"], row["match_key"]))
    return [ordered[index:index + BLOCK_SIZE] for index in range(0, len(ordered), BLOCK_SIZE)]


def bootstrap_difference(
    rows: list[dict[str, Any]],
    candidate_key: str,
    baseline_key: str,
    seed: int,
) -> dict[str, Any]:
    grouped = blocks(rows)
    point = mean(float(row[candidate_key]) - float(row[baseline_key]) for row in rows)
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled = []
        for _ in range(len(grouped)):
            sampled.extend(rng.choice(grouped))
        draws.append(mean(float(row[candidate_key]) - float(row[baseline_key]) for row in sampled))
    draws.sort()
    return {
        "mean_difference": point,
        "ci95_lower": draws[int(0.025 * (len(draws) - 1))],
        "ci95_upper": draws[int(0.975 * (len(draws) - 1))],
        "blocks": len(grouped),
        "draws": BOOTSTRAP_DRAWS,
    }


def validate_domain(competition_id: str) -> dict[str, Any]:
    feature_rows, data_audit = build_rows(competition_id)
    rows_by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in feature_rows:
        rows_by_season[row["season"]].append(row)
    seasons = sorted(rows_by_season, key=season_year)
    if len(seasons) < 4:
        raise PlatformError(f"need at least four feature seasons, got {seasons}")
    outer_targets = seasons[-2:]
    outer_scored: list[dict[str, Any]] = []
    folds = []
    selected_profiles = []

    for target in outer_targets:
        target_index = seasons.index(target)
        prior_seasons = seasons[:target_index]
        if len(prior_seasons) < 2:
            raise PlatformError(f"insufficient prior seasons for outer target {target}")
        selected, selection_ledger = inner_select(prior_seasons, rows_by_season)
        profile = next(item for item in PROFILES if item["id"] == selected["profile_id"])
        training = [row for season in prior_seasons for row in rows_by_season[season]]
        model = fit_profile(training, profile)
        target_rows = rows_by_season[target]
        scored = score_profile(model, target_rows)
        outer_scored.extend(scored)
        summary = metric_summary(scored)
        folds.append({
            "target_season": target,
            "prior_seasons": prior_seasons,
            "selected_profile": selected["profile_id"],
            "profile_selection": selected,
            "selection_ledger": selection_ledger,
            "training_rows": len(training),
            "outer_rows": len(target_rows),
            "model": model,
            "metrics": summary,
        })
        selected_profiles.append(selected["profile_id"])

    pooled = metric_summary(outer_scored)
    margin_bootstrap = bootstrap_difference(
        outer_scored,
        "candidate_margin_squared_error",
        "baseline_margin_squared_error",
        SEED + sum(ord(ch) for ch in competition_id),
    )
    total_bootstrap = bootstrap_difference(
        outer_scored,
        "candidate_total_squared_error",
        "baseline_total_squared_error",
        SEED + 1000 + sum(ord(ch) for ch in competition_id),
    )
    checks = {
        "two_outer_seasons": len(folds) == 2,
        "minimum_outer_rows_500": len(outer_scored) >= 500,
        "nonbaseline_selected_both_outer_folds": all(
            profile != "baseline_zero" for profile in selected_profiles
        ),
        "margin_mse_ci_improves": margin_bootstrap["ci95_upper"] < 0.0,
        "total_mse_ci_noninferior": total_bootstrap["ci95_upper"] <= TOTAL_MSE_NONINFERIORITY,
        "margin_mse_nonworse_each_outer_season": all(
            fold["metrics"]["margin_mse_difference"] <= 0.0 for fold in folds
        ),
        "total_mse_nonworse_each_outer_season": all(
            fold["metrics"]["total_mse_difference"] <= TOTAL_MSE_NONINFERIORITY for fold in folds
        ),
        "target_actual_xi_used_as_input": False,
        "same_season_lineup_history_only": True,
        "identity_bridge_passed": True,
    }
    signal_pass = all(checks.values())
    return {
        "schema_version": "V5.0.2-lineup-latent-signal-domain-r1",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": (
            "FEATURE_SIGNAL_PASS_MATRIX_PROJECTION_REVIEW"
            if signal_pass
            else "REJECT_KEEP_FORMAL_WEIGHT_0"
        ),
        "profiles": PROFILES,
        "data_audit": data_audit,
        "feature_seasons": seasons,
        "outer_targets": outer_targets,
        "outer_prediction_count": len(outer_scored),
        "selected_profiles": selected_profiles,
        "folds": folds,
        "pooled_metrics": pooled,
        "paired_block_bootstrap": {
            "margin_mse": margin_bootstrap,
            "total_mse": total_bootstrap,
        },
        "checks": checks,
        "handicap_target_status": "UNAVAILABLE_NO_COMPLETE_POINT_IN_TIME_FROZEN_HANDICAP_LINES_IN_CURRENT_REPLAY",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Feature diagnostic only. Passing does not authorize probability mutation or promotion; rejection freezes lineup-only latent signal weight at 0.",
    }


def run(*, write: bool) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for competition_id in PILOT_DOMAINS:
        try:
            report = validate_domain(competition_id)
            reports[competition_id] = report
            if write:
                atomic_write_json(REPORT_DIR / f"{competition_id}.json", report)
        except Exception as exc:
            failures[competition_id] = str(exc)
    passed = [
        cid for cid, report in reports.items()
        if report["status"] == "FEATURE_SIGNAL_PASS_MATRIX_PROJECTION_REVIEW"
    ]
    rejected = [
        cid for cid, report in reports.items()
        if report["status"] == "REJECT_KEEP_FORMAL_WEIGHT_0"
    ]
    manifest = {
        "schema_version": "V5.0.2-lineup-latent-signal-aggregate-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS" if not failures else "FAIL",
        "requested_domains": list(PILOT_DOMAINS),
        "completed_domains": sorted(reports),
        "signal_pass_domains": sorted(passed),
        "rejected_keep_formal_weight_0": sorted(rejected),
        "execution_failures": failures,
        "reports": {
            cid: {
                "status": report["status"],
                "outer_prediction_count": report["outer_prediction_count"],
                "selected_profiles": report["selected_profiles"],
                "pooled_metrics": report["pooled_metrics"],
                "paired_block_bootstrap": report["paired_block_bootstrap"],
                "checks": report["checks"],
            }
            for cid, report in reports.items()
        },
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Lineup-only feature signal OOF. Formal V5 probabilities remain unchanged.",
    }
    if write:
        atomic_write_json(OUT, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    manifest = run(write=not args.check_only)
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
