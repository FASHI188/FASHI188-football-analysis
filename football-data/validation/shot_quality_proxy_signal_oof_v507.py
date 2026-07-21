#!/usr/bin/env python3
"""V5.0.7 lagged shot quantity/quality-proxy chronological OOF signal test.

Full-time shots and shots-on-target are used only after their match date and
only for later fixtures. Same-day matches are evaluated before any same-day
state update. The target match's own shots are never inputs.

The challenger predicts residual expected goal margin and residual expected
total goals after the calibrated formal score matrix. A zero-effect profile is
always eligible. Discovery domains, features, profiles and gates are loaded
from the frozen V5.0.7 registry. This stage does not mutate a score matrix or
formal probability.
"""

from __future__ import annotations

import argparse
import csv
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
    canonical_team_name,
    load_aliases,
    load_json,
    parse_match_date,
    read_processed_matches,
    score_matrix_rows,
    sha256_file,
)

CONFIG = ROOT / "config" / "shot_quality_proxy_challenger_v507.json"
READINESS = ROOT / "manifests" / "shot_event_data_readiness_v506_status.json"
OUT = ROOT / "manifests" / "shot_quality_proxy_signal_oof_v507_status.json"
REPORT_DIR = ROOT / "manifests" / "shot_quality_proxy_signal_oof_v507"

MARGIN_CLIP = 0.75
TOTAL_CLIP = 0.75
SEED = 5072026


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def season_year(season: str) -> int:
    token = str(season).strip()
    if len(token) < 4 or not token[:4].isdigit():
        raise PlatformError(f"cannot parse season: {season!r}")
    return int(token[:4])


def numeric(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def matrix_means(matrix: list[dict[str, Any]]) -> tuple[float, float, float]:
    home = 0.0
    away = 0.0
    for home_goals, away_goals, probability in score_matrix_rows(matrix):
        home += home_goals * probability
        away += away_goals * probability
    return home, away, home + away


def match_key(season: str, date: str, home: str, away: str) -> tuple[str, str, str, str]:
    return season, date, home, away


def read_shot_rows(competition_id: str) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], dict[str, Any]]:
    aliases = load_aliases()
    directory = ROOT / "processed" / competition_id
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    invalid = 0
    missing = 0
    duplicate = 0
    file_hashes = {}
    by_season: dict[str, int] = defaultdict(int)
    for path in sorted(directory.glob("*.csv")):
        file_hashes[path.relative_to(ROOT).as_posix()] = sha256_file(path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                row = {str(key).strip(): "" if value is None else str(value).strip() for key, value in raw.items() if key}
                if not row.get("HomeTeam") or not row.get("AwayTeam"):
                    continue
                season = row.get("season") or row.get("Season") or ""
                try:
                    date = parse_match_date(row.get("Date", ""), season).date().isoformat()
                except Exception:
                    invalid += 1
                    continue
                hs = numeric(row.get("HS"))
                away_shots = numeric(row.get("AS"))
                hst = numeric(row.get("HST"))
                ast = numeric(row.get("AST"))
                if any(value is None for value in (hs, away_shots, hst, ast)):
                    missing += 1
                    continue
                assert hs is not None and away_shots is not None and hst is not None and ast is not None
                if min(hs, away_shots, hst, ast) < 0 or hst > hs + 1e-9 or ast > away_shots + 1e-9:
                    invalid += 1
                    continue
                home = canonical_team_name(competition_id, row["HomeTeam"], aliases)
                away = canonical_team_name(competition_id, row["AwayTeam"], aliases)
                key = match_key(season, date, home, away)
                if key in rows:
                    duplicate += 1
                    continue
                rows[key] = {
                    "season": season,
                    "date": date,
                    "home_team": home,
                    "away_team": away,
                    "home_shots": hs,
                    "away_shots": away_shots,
                    "home_sot": hst,
                    "away_sot": ast,
                }
                by_season[season] += 1
    return rows, {
        "valid_shot_rows": len(rows),
        "valid_rows_by_season": dict(sorted(by_season.items(), key=lambda item: season_year(item[0]))),
        "missing_quartet_rows": missing,
        "invalid_quartet_rows": invalid,
        "duplicate_rows": duplicate,
        "file_hashes": file_hashes,
    }


def rolling_state(history: list[dict[str, float]], lookback: int, decay: float) -> dict[str, float] | None:
    history = history[-lookback:]
    if not history:
        return None
    totals = defaultdict(float)
    total_weight = 0.0
    for age, item in enumerate(reversed(history)):
        weight = decay ** age
        total_weight += weight
        for key, value in item.items():
            totals[key] += weight * float(value)
    output = {key: value / max(total_weight, 1e-12) for key, value in totals.items()}
    output["attack_accuracy"] = output["sot_for"] / max(output["shots_for"], 1e-6)
    output["defence_accuracy_allowed"] = output["sot_against"] / max(output["shots_against"], 1e-6)
    output["history_count"] = float(len(history))
    return output


def fixture_features(home: dict[str, float], away: dict[str, float]) -> dict[str, float]:
    expected_home_shots = 0.5 * (home["shots_for"] + away["shots_against"])
    expected_away_shots = 0.5 * (away["shots_for"] + home["shots_against"])
    expected_home_sot = 0.5 * (home["sot_for"] + away["sot_against"])
    expected_away_sot = 0.5 * (away["sot_for"] + home["sot_against"])
    shot_total = expected_home_shots + expected_away_shots
    sot_total = expected_home_sot + expected_away_sot
    return {
        "expected_shot_margin": expected_home_shots - expected_away_shots,
        "expected_sot_margin": expected_home_sot - expected_away_sot,
        "expected_shot_total": shot_total,
        "expected_sot_total": sot_total,
        "attacking_accuracy_difference": home["attack_accuracy"] - away["attack_accuracy"],
        "attacking_accuracy_sum": home["attack_accuracy"] + away["attack_accuracy"],
        "shot_share_margin": (expected_home_shots - expected_away_shots) / max(shot_total, 1e-6),
        "sot_share_margin": (expected_home_sot - expected_away_sot) / max(sot_total, 1e-6),
    }


def build_feature_rows(competition_id: str, config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    shot_rows, shot_audit = read_shot_rows(competition_id)
    formal_report = load_json(REPORT_ROOT / f"{competition_id}.json")
    all_matches = read_processed_matches(competition_id)
    feature_config = config["feature_engine"]
    lookback = int(feature_config["lookback_matches"])
    decay = float(feature_config["decay"])
    minimum_history = int(feature_config["minimum_team_history"])
    by_season: dict[str, list[Any]] = defaultdict(list)
    for match in all_matches:
        by_season[str(match.season)].append(match)
    output = []
    skipped = defaultdict(int)
    rows_by_season = defaultdict(int)

    for season in sorted(by_season, key=season_year):
        team_history: dict[str, list[dict[str, float]]] = defaultdict(list)
        matches_by_date: dict[str, list[Any]] = defaultdict(list)
        for match in by_season[season]:
            matches_by_date[match.date.date().isoformat()].append(match)
        for date in sorted(matches_by_date):
            date_matches = sorted(matches_by_date[date], key=lambda item: (item.home_team, item.away_team))
            for match in date_matches:
                home_state = rolling_state(team_history[match.home_team], lookback, decay)
                away_state = rolling_state(team_history[match.away_team], lookback, decay)
                if (
                    home_state is None
                    or away_state is None
                    or home_state["history_count"] < minimum_history
                    or away_state["history_count"] < minimum_history
                ):
                    skipped["insufficient_same_season_history"] += 1
                    continue
                try:
                    fold = _fold_for_season(formal_report, season)
                except Exception:
                    skipped["formal_fold_unavailable"] += 1
                    continue
                parameters = fold.get("selected_parameters")
                if not isinstance(parameters, dict):
                    skipped["formal_parameters_unavailable"] += 1
                    continue
                try:
                    matrix = _predict_from_loaded_matches(
                        all_matches,
                        match.home_team,
                        match.away_team,
                        match.date,
                        season,
                        parameters,
                    )
                except PlatformError:
                    skipped["formal_sample_gate"] += 1
                    continue
                temperature, calibration_mode = _target_season_temperature(competition_id, season)
                if abs(temperature - 1.0) > 1e-15:
                    matrix = temperature_scale_matrix(matrix, temperature)
                home_mean, away_mean, total_mean = matrix_means(matrix)
                actual_margin = int(match.home_goals) - int(match.away_goals)
                actual_total = int(match.home_goals) + int(match.away_goals)
                output.append({
                    "competition_id": competition_id,
                    "season": season,
                    "date": date,
                    "match_key": f"{competition_id}:{season}:{date}:{match.home_team}:{match.away_team}",
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "base_margin": home_mean - away_mean,
                    "base_total": total_mean,
                    "margin_residual": float(actual_margin) - (home_mean - away_mean),
                    "total_residual": float(actual_total) - total_mean,
                    "oof_temperature": temperature,
                    "oof_calibration_mode": calibration_mode,
                    **fixture_features(home_state, away_state),
                })
                rows_by_season[season] += 1

            # Same-day stats become eligible only for a later calendar date.
            for match in date_matches:
                key = match_key(season, date, match.home_team, match.away_team)
                shot = shot_rows.get(key)
                if shot is None:
                    skipped["shot_row_missing_or_invalid"] += 1
                    continue
                team_history[match.home_team].append({
                    "shots_for": shot["home_shots"],
                    "shots_against": shot["away_shots"],
                    "sot_for": shot["home_sot"],
                    "sot_against": shot["away_sot"],
                })
                team_history[match.away_team].append({
                    "shots_for": shot["away_shots"],
                    "shots_against": shot["home_shots"],
                    "sot_for": shot["away_sot"],
                    "sot_against": shot["home_sot"],
                })

    return output, {
        "shot_data": shot_audit,
        "feature_row_count": len(output),
        "feature_rows_by_season": dict(sorted(rows_by_season.items(), key=lambda item: season_year(item[0]))),
        "skipped": dict(skipped),
        "same_day_updates_prohibited": True,
        "target_match_own_shots_used": False,
    }


def solve_linear(system: list[list[float]], target: list[float]) -> list[float]:
    size = len(target)
    augmented = [list(system[index]) + [target[index]] for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise PlatformError("singular ridge system")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if abs(factor) < 1e-18:
                continue
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(size + 1)
            ]
    return [augmented[index][-1] for index in range(size)]


def fit_ridge(rows: list[dict[str, Any]], features: list[str], target: str, ridge: float) -> dict[str, Any]:
    if not features:
        return {"features": [], "intercept": 0.0, "coefficients": [], "means": [], "scales": [], "ridge": 0.0}
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
        value = float(row[target])
        for left in range(dimension):
            xty[left] += vector[left] * value
            for right in range(dimension):
                xtx[left][right] += vector[left] * vector[right]
    for index in range(1, dimension):
        xtx[index][index] += ridge
    coefficients = solve_linear(xtx, xty)
    return {
        "features": features,
        "intercept": coefficients[0],
        "coefficients": coefficients[1:],
        "means": means,
        "scales": scales,
        "ridge": ridge,
        "training_rows": len(rows),
    }


def predict(model: dict[str, Any], row: dict[str, Any]) -> float:
    value = float(model["intercept"])
    for key, coefficient, centre, scale in zip(
        model["features"], model["coefficients"], model["means"], model["scales"]
    ):
        value += float(coefficient) * (float(row[key]) - float(centre)) / float(scale)
    return value


def fit_profile(rows: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": profile["id"],
        "margin": fit_ridge(rows, list(profile["margin_features"]), "margin_residual", float(profile["ridge"])),
        "total": fit_ridge(rows, list(profile["total_features"]), "total_residual", float(profile["ridge"])),
    }


def score(model: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        margin_adjustment = max(-MARGIN_CLIP, min(MARGIN_CLIP, predict(model["margin"], row)))
        total_adjustment = max(-TOTAL_CLIP, min(TOTAL_CLIP, predict(model["total"], row)))
        margin_error = float(row["margin_residual"])
        total_error = float(row["total_residual"])
        output.append({
            "match_key": row["match_key"],
            "season": row["season"],
            "date": row["date"],
            "profile_id": model["profile_id"],
            "margin_adjustment": margin_adjustment,
            "total_adjustment": total_adjustment,
            "baseline_margin_squared_error": margin_error ** 2,
            "candidate_margin_squared_error": (margin_error - margin_adjustment) ** 2,
            "baseline_margin_absolute_error": abs(margin_error),
            "candidate_margin_absolute_error": abs(margin_error - margin_adjustment),
            "baseline_total_squared_error": total_error ** 2,
            "candidate_total_squared_error": (total_error - total_adjustment) ** 2,
            "baseline_total_absolute_error": abs(total_error),
            "candidate_total_absolute_error": abs(total_error - total_adjustment),
        })
    return output


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise PlatformError("empty score rows")
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
    result = {key: mean(float(row[key]) for row in rows) for key in keys}
    result["margin_mse_difference"] = result["candidate_margin_squared_error"] - result["baseline_margin_squared_error"]
    result["margin_mae_difference"] = result["candidate_margin_absolute_error"] - result["baseline_margin_absolute_error"]
    result["total_mse_difference"] = result["candidate_total_squared_error"] - result["baseline_total_squared_error"]
    result["total_mae_difference"] = result["candidate_total_absolute_error"] - result["baseline_total_absolute_error"]
    result["row_count"] = len(rows)
    return result


def select_profile(prior_seasons: list[str], rows_by_season: dict[str, list[dict[str, Any]]], profiles: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ledger = []
    for profile in profiles:
        inner_rows = []
        for index in range(1, len(prior_seasons)):
            target = prior_seasons[index]
            training = [row for season in prior_seasons[:index] for row in rows_by_season[season]]
            target_rows = rows_by_season[target]
            if not training or not target_rows:
                continue
            model = fit_profile(training, profile)
            inner_rows.extend(score(model, target_rows))
        if not inner_rows:
            continue
        metrics = summary(inner_rows)
        margin_ratio = metrics["candidate_margin_squared_error"] / max(metrics["baseline_margin_squared_error"], 1e-12)
        total_ratio = metrics["candidate_total_squared_error"] / max(metrics["baseline_total_squared_error"], 1e-12)
        eligible = margin_ratio <= 1.01 and total_ratio <= 1.01
        ledger.append({
            "profile_id": profile["id"],
            "eligible": eligible,
            "objective": margin_ratio + total_ratio,
            "margin_mse_ratio": margin_ratio,
            "total_mse_ratio": total_ratio,
            "inner_rows": len(inner_rows),
            "inner_metrics": metrics,
        })
    eligible = [item for item in ledger if item["eligible"]]
    if not eligible:
        raise PlatformError("no eligible profile including baseline")
    eligible.sort(key=lambda item: (item["objective"], item["profile_id"]))
    return eligible[0], ledger


def blocks(rows: list[dict[str, Any]], block_size: int) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (season_year(row["season"]), row["date"], row["match_key"]))
    return [ordered[index:index + block_size] for index in range(0, len(ordered), block_size)]


def bootstrap(rows: list[dict[str, Any]], candidate: str, baseline: str, draws: int, block_size: int, seed: int) -> dict[str, Any]:
    grouped = blocks(rows, block_size)
    point = mean(float(row[candidate]) - float(row[baseline]) for row in rows)
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        sampled = []
        for _ in range(len(grouped)):
            sampled.extend(rng.choice(grouped))
        samples.append(mean(float(row[candidate]) - float(row[baseline]) for row in sampled))
    samples.sort()
    return {
        "mean_difference": point,
        "ci95_lower": samples[int(0.025 * (len(samples) - 1))],
        "ci95_upper": samples[int(0.975 * (len(samples) - 1))],
        "blocks": len(grouped),
        "draws": draws,
    }


def validate_domain(competition_id: str, config: dict[str, Any]) -> dict[str, Any]:
    rows, data_audit = build_feature_rows(competition_id, config)
    rows_by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_season[row["season"]].append(row)
    seasons = sorted(rows_by_season, key=season_year)
    if len(seasons) < 4:
        raise PlatformError(f"need at least four feature seasons, got {seasons}")
    outer = seasons[-2:]
    profiles = list(config["profiles_frozen_before_results"])
    folds = []
    outer_rows = []
    selected_profiles = []
    for target in outer:
        index = seasons.index(target)
        prior = seasons[:index]
        selected, ledger = select_profile(prior, rows_by_season, profiles)
        profile = next(item for item in profiles if item["id"] == selected["profile_id"])
        training = [row for season in prior for row in rows_by_season[season]]
        model = fit_profile(training, profile)
        scored = score(model, rows_by_season[target])
        outer_rows.extend(scored)
        selected_profiles.append(profile["id"])
        folds.append({
            "target_season": target,
            "prior_seasons": prior,
            "selected_profile": profile["id"],
            "selection": selected,
            "selection_ledger": ledger,
            "training_rows": len(training),
            "outer_rows": len(scored),
            "model": model,
            "metrics": summary(scored),
        })
    pooled = summary(outer_rows)
    validation = config["chronological_validation"]
    draws = int(validation["paired_block_bootstrap_draws"])
    block_size = int(validation["block_size"])
    margin_ci = bootstrap(
        outer_rows,
        "candidate_margin_squared_error",
        "baseline_margin_squared_error",
        draws,
        block_size,
        SEED + sum(ord(char) for char in competition_id),
    )
    total_ci = bootstrap(
        outer_rows,
        "candidate_total_squared_error",
        "baseline_total_squared_error",
        draws,
        block_size,
        SEED + 1000 + sum(ord(char) for char in competition_id),
    )
    other_limit = float(config["signal_gate_frozen_before_results"]["other_axis_ci95_upper_at_most"])
    fold_limit = float(config["signal_gate_frozen_before_results"]["each_outer_season_axis_noninferiority_limit"])
    margin_improves = margin_ci["ci95_upper"] < 0.0
    total_improves = total_ci["ci95_upper"] < 0.0
    checks = {
        "two_outer_seasons": len(folds) == 2,
        "minimum_outer_predictions": len(outer_rows) >= int(validation["minimum_outer_predictions"]),
        "nonbaseline_selected_both_outer_folds": all(profile != "baseline_zero" for profile in selected_profiles),
        "at_least_one_axis_ci_improves": margin_improves or total_improves,
        "other_axis_ci_noninferior": (
            total_ci["ci95_upper"] <= other_limit if margin_improves else margin_ci["ci95_upper"] <= other_limit
        ),
        "margin_noninferior_each_outer_season": all(fold["metrics"]["margin_mse_difference"] <= fold_limit for fold in folds),
        "total_noninferior_each_outer_season": all(fold["metrics"]["total_mse_difference"] <= fold_limit for fold in folds),
        "target_match_stats_excluded": True,
        "same_day_updates_prohibited": True,
        "same_season_state_reset": True,
    }
    signal_pass = all(checks.values())
    return {
        "schema_version": "V5.0.7-shot-quality-proxy-signal-domain-r1",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "SHOT_PROXY_SIGNAL_PASS_MATRIX_REVIEW" if signal_pass else "REJECT_KEEP_FORMAL_WEIGHT_0",
        "data_audit": data_audit,
        "feature_seasons": seasons,
        "outer_targets": outer,
        "outer_prediction_count": len(outer_rows),
        "selected_profiles": selected_profiles,
        "folds": folds,
        "pooled_metrics": pooled,
        "paired_block_bootstrap": {
            "margin_mse": margin_ci,
            "total_mse": total_ci,
        },
        "checks": checks,
        "semantic_scope": "shot quantity and shots-on-target quality proxy; not xG/xT/OBV/VAEP",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
    }


def run(*, write: bool) -> dict[str, Any]:
    config = load_json(CONFIG)
    readiness = load_json(READINESS)
    domains = [str(item) for item in config["discovery_domains_frozen_before_signal_results"]]
    ready = set(readiness.get("shot_quantity_quality_proxy_ready_domains") or [])
    if not set(domains).issubset(ready):
        raise PlatformError(f"discovery domains not readiness-approved: {sorted(set(domains) - ready)}")
    reports = {}
    failures = {}
    for competition_id in domains:
        try:
            report = validate_domain(competition_id, config)
            reports[competition_id] = report
            if write:
                atomic_write_json(REPORT_DIR / f"{competition_id}.json", report)
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"
    passed = sorted(
        competition_id for competition_id, report in reports.items()
        if report["status"] == "SHOT_PROXY_SIGNAL_PASS_MATRIX_REVIEW"
    )
    rejected = sorted(set(reports) - set(passed))
    payload = {
        "schema_version": "V5.0.7-shot-quality-proxy-signal-aggregate-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS" if not failures and len(reports) == len(domains) else "PARTIAL",
        "config_path": CONFIG.relative_to(ROOT.parent).as_posix(),
        "config_sha256": sha256_file(CONFIG),
        "readiness_path": READINESS.relative_to(ROOT.parent).as_posix(),
        "readiness_sha256": sha256_file(READINESS),
        "requested_domains": domains,
        "completed_domains": sorted(reports),
        "signal_pass_domains": passed,
        "rejected_keep_formal_weight_0": rejected,
        "execution_failures": failures,
        "reports": {
            competition_id: {
                "status": report["status"],
                "outer_prediction_count": report["outer_prediction_count"],
                "selected_profiles": report["selected_profiles"],
                "pooled_metrics": report["pooled_metrics"],
                "paired_block_bootstrap": report["paired_block_bootstrap"],
                "checks": report["checks"],
            }
            for competition_id, report in reports.items()
        },
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Discovery signal OOF only. Passing domains may enter independent replication and later unified-matrix research; formal probabilities remain unchanged."
    }
    if write:
        atomic_write_json(OUT, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    payload = run(write=not args.check_only)
    if args.print_summary:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
