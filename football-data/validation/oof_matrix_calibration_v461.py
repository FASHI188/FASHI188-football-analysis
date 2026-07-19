#!/usr/bin/env python3
"""Leakage-safe rolling OOF calibration builder for the V4.6.x score matrix."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from football_v460_engine import ENGINE_PATH, load_config, predict_from_history  # noqa: E402
from oof_matrix_calibration import CALIBRATION_MODULE_PATH, temperature_scale_matrix  # noqa: E402
from platform_core import (  # noqa: E402
    ROOT, MatchRow, PlatformError, atomic_write_json, derive_score_marginals,
    load_json, load_registry, read_processed_matches, sha256_file, sha256_json, top_scores,
)

EPS = 1e-15
REPORT_ROOT = ROOT / "validation" / "reports" / "oof_matrix_calibration_v461"
MODEL_ROOT = ROOT / "models" / "formal_core_v460"
SOURCE_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
MANIFEST_PATH = ROOT / "manifests" / "oof_matrix_calibration_v461_status.json"


def _score(home: int, away: int) -> str:
    return f"{home}-{away}"


def _team_counts(history: list[MatchRow]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for match in history:
        counts[match.home_team] += 1
        counts[match.away_team] += 1
    return counts


def _record(match: MatchRow, prediction: dict[str, Any]) -> dict[str, Any] | None:
    matrix = prediction["probabilities"]["score_matrix"]
    keys = [_score(int(c["home_goals"]), int(c["away_goals"])) for c in matrix]
    actual = _score(match.home_goals, match.away_goals)
    try:
        actual_index = keys.index(actual)
    except ValueError:
        return None
    logs = [math.log(max(EPS, float(c["probability"]))) for c in matrix]
    return {
        "season": match.season,
        "date": match.date.date().isoformat(),
        "actual_score": actual,
        "actual_outcome": "home" if match.home_goals > match.away_goals else "draw" if match.home_goals == match.away_goals else "away",
        "actual_total": match.home_goals + match.away_goals,
        "actual_index": actual_index,
        "logs": logs,
        "matrix": matrix,
    }


def evaluate_outer_season(competition_id: str, matches: list[MatchRow], params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    cfg = load_config()["validation"]
    warmup_comp, warmup_team = int(cfg["warmup_competition_matches"]), int(cfg["warmup_team_matches"])
    by_date: dict[datetime, list[MatchRow]] = defaultdict(list)
    for match in matches:
        by_date[match.date].append(match)
    history: list[MatchRow] = []
    records: list[dict[str, Any]] = []
    unsupported = 0
    for date in sorted(by_date):
        counts = _team_counts(history)
        for match in sorted(by_date[date], key=lambda x: (x.home_team, x.away_team)):
            if len(history) < warmup_comp or counts[match.home_team] < warmup_team or counts[match.away_team] < warmup_team:
                continue
            try:
                prediction = predict_from_history(history, competition_id, match.season, match.home_team, match.away_team, match.date, params, use_team_effects=True)
            except PlatformError:
                continue
            item = _record(match, prediction)
            if item is None:
                unsupported += 1
            else:
                records.append(item)
        history.extend(by_date[date])
        history.sort(key=lambda x: (x.date, x.home_team, x.away_team))
    return records, unsupported


def _nll_grad_hess(record: dict[str, Any], beta: float) -> tuple[float, float, float]:
    logs = record["logs"]
    scaled = [beta * value for value in logs]
    maximum = max(scaled)
    weights = [math.exp(value - maximum) for value in scaled]
    z = sum(weights)
    actual_log = logs[int(record["actual_index"])]
    expected = sum(w * value for w, value in zip(weights, logs)) / z
    expected_sq = sum(w * value * value for w, value in zip(weights, logs)) / z
    return -(beta * actual_log - maximum - math.log(max(EPS, z))), -actual_log + expected, max(0.0, expected_sq - expected * expected)


def _joint_log(records: list[dict[str, Any]], temperature: float) -> float:
    beta = 1.0 / max(1e-9, float(temperature))
    return mean(_nll_grad_hess(record, beta)[0] for record in records) if records else float("inf")


def fit_temperature(records: list[dict[str, Any]]) -> float:
    if not records:
        return 1.0
    beta = 1.0
    for _ in range(10):
        values = [_nll_grad_hess(record, beta) for record in records]
        gradient = mean(item[1] for item in values)
        hessian = mean(item[2] for item in values)
        if abs(gradient) < 1e-8 or hessian <= 1e-10:
            break
        candidate = min(2.0, max(0.4, beta - gradient / hessian))
        if abs(candidate - beta) < 1e-7:
            beta = candidate
            break
        beta = candidate
    temperature = 1.0 / beta
    candidates = (max(0.5, temperature * 0.95), temperature, min(2.5, temperature * 1.05), 1.0)
    return min(candidates, key=lambda value: _joint_log(records, value))


def _rps(values: list[float], actual_index: int) -> float:
    cp = co = score = 0.0
    for index in range(len(values) - 1):
        cp += values[index]
        co += 1.0 if actual_index == index else 0.0
        score += (cp - co) ** 2
    return score / max(1, len(values) - 1)


def _covered(matrix: list[dict[str, Any]], actual: str, target: float) -> tuple[bool, int]:
    cumulative = 0.0
    selected = set()
    for item in top_scores(matrix, len(matrix)):
        selected.add(item["score"])
        cumulative += float(item["probability"])
        if cumulative >= target:
            return actual in selected, len(selected)
    return True, len(selected)


def evaluate_records(records: list[dict[str, Any]], temperature: float) -> dict[str, Any]:
    if not records:
        return {"count": 0}
    logs: list[float] = []
    briers: list[float] = []
    one_rps: list[float] = []
    total_rps: list[float] = []
    cover80: list[tuple[bool, int]] = []
    cover90: list[tuple[bool, int]] = []
    residuals: list[float] = []
    for record in records:
        matrix = temperature_scale_matrix(record["matrix"], temperature)
        margins = derive_score_marginals(matrix)
        p = next((float(c["probability"]) for c in matrix if _score(int(c["home_goals"]), int(c["away_goals"])) == record["actual_score"]), EPS)
        logs.append(-math.log(max(EPS, p)))
        one, outcome = margins["1x2"], record["actual_outcome"]
        briers.append(sum((one[key] - (1.0 if key == outcome else 0.0)) ** 2 for key in ("home", "draw", "away")))
        one_rps.append(_rps([one["home"], one["draw"], one["away"]], ("home", "draw", "away").index(outcome)))
        keys = ("0", "1", "2", "3", "4", "5", "6", "7+")
        total_rps.append(_rps([margins["total_goals"][key] for key in keys], min(int(record["actual_total"]), 7)))
        cover80.append(_covered(matrix, record["actual_score"], 0.80))
        cover90.append(_covered(matrix, record["actual_score"], 0.90))
        residuals.append(abs(margins["probability_sum"] - 1.0))
    return {
        "count": len(records),
        "mean_joint_log_score": mean(logs),
        "mean_one_x_two_brier": mean(briers),
        "mean_one_x_two_rps": mean(one_rps),
        "mean_total_goals_rps": mean(total_rps),
        "score_set_80_coverage": mean(float(x[0]) for x in cover80),
        "score_set_90_coverage": mean(float(x[0]) for x in cover90),
        "mean_score_set_80_size": mean(x[1] for x in cover80),
        "mean_score_set_90_size": mean(x[1] for x in cover90),
        "max_probability_sum_residual": max(residuals),
    }


def _weighted(parts: list[dict[str, Any]], key: str) -> float | None:
    values = [(int(item.get("count", 0)), item.get(key)) for item in parts if item.get("count", 0) and item.get(key) is not None]
    total = sum(count for count, _ in values)
    return None if not total else sum(count * float(value) for count, value in values) / total


def _rolling_summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    raw_parts = [item["raw_metrics"] for item in steps]
    candidate_parts = [item["calibrated_metrics"] for item in steps]
    raw_joint = _weighted(raw_parts, "mean_joint_log_score")
    candidate_joint = _weighted(candidate_parts, "mean_joint_log_score")
    raw_brier = _weighted(raw_parts, "mean_one_x_two_brier")
    candidate_brier = _weighted(candidate_parts, "mean_one_x_two_brier")
    raw_one_rps = _weighted(raw_parts, "mean_one_x_two_rps")
    candidate_one_rps = _weighted(candidate_parts, "mean_one_x_two_rps")
    raw_total_rps = _weighted(raw_parts, "mean_total_goals_rps")
    candidate_total_rps = _weighted(candidate_parts, "mean_total_goals_rps")
    return {
        "count": sum(int(item.get("count", 0)) for item in raw_parts),
        "raw_mean_joint_log_score": raw_joint,
        "candidate_mean_joint_log_score": candidate_joint,
        "candidate_minus_raw_joint_log_score": None if raw_joint is None or candidate_joint is None else candidate_joint - raw_joint,
        "raw_mean_one_x_two_brier": raw_brier,
        "candidate_mean_one_x_two_brier": candidate_brier,
        "candidate_minus_raw_one_x_two_brier": None if raw_brier is None or candidate_brier is None else candidate_brier - raw_brier,
        "raw_mean_one_x_two_rps": raw_one_rps,
        "candidate_mean_one_x_two_rps": candidate_one_rps,
        "candidate_minus_raw_one_x_two_rps": None if raw_one_rps is None or candidate_one_rps is None else candidate_one_rps - raw_one_rps,
        "raw_mean_total_goals_rps": raw_total_rps,
        "candidate_mean_total_goals_rps": candidate_total_rps,
        "candidate_minus_raw_total_goals_rps": None if raw_total_rps is None or candidate_total_rps is None else candidate_total_rps - raw_total_rps,
        "raw_score_set_80_coverage": _weighted(raw_parts, "score_set_80_coverage"),
        "candidate_score_set_80_coverage": _weighted(candidate_parts, "score_set_80_coverage"),
        "raw_score_set_90_coverage": _weighted(raw_parts, "score_set_90_coverage"),
        "candidate_score_set_90_coverage": _weighted(candidate_parts, "score_set_90_coverage"),
        "max_probability_sum_residual": max([float(item["max_probability_sum_residual"]) for item in candidate_parts] or [0.0]),
    }


def _passes_guardrails(summary: dict[str, Any]) -> bool:
    if int(summary.get("count", 0)) < 100:
        return False
    limits = {
        "candidate_minus_raw_joint_log_score": 0.0,
        "candidate_minus_raw_one_x_two_brier": 0.002,
        "candidate_minus_raw_one_x_two_rps": 0.002,
        "candidate_minus_raw_total_goals_rps": 0.002,
    }
    for key, limit in limits.items():
        value = summary.get(key)
        if value is None or float(value) > limit:
            return False
    return float(summary.get("max_probability_sum_residual", 1.0)) <= 1e-10


def validate_competition(competition_id: str, *, write: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    source_path = SOURCE_REPORT_ROOT / f"{competition_id}.json"
    if not source_path.exists():
        raise PlatformError(f"source nested-backtest report missing: {source_path}")
    source = load_json(source_path)
    engine_sha = sha256_file(ENGINE_PATH)
    if source.get("engine_sha256") != engine_sha:
        raise PlatformError("source nested-backtest report engine hash mismatch")

    by_season: dict[str, list[MatchRow]] = defaultdict(list)
    for match in read_processed_matches(competition_id):
        by_season[match.season].append(match)

    # Nested validation may split one unseen season into multiple disjoint
    # evaluation folds for A-grade fold counting. OOF calibration remains
    # season-routed: evaluate each outer season exactly once so a target season
    # can never train its own calibrator through an earlier sub-fold.
    folds, unsupported = [], 0
    seen_outer_seasons: set[str] = set()
    for fold in source.get("folds", []):
        season = fold["outer_season"]
        if season in seen_outer_seasons:
            continue
        seen_outer_seasons.add(season)
        records, missing = evaluate_outer_season(
            competition_id,
            sorted(by_season[season], key=lambda x: (x.date, x.home_team, x.away_team)),
            fold["selected_parameters"],
        )
        folds.append({"season": season, "records": records})
        unsupported += missing
    all_records = [record for fold in folds for record in fold["records"]]
    if len(all_records) < 100:
        raise PlatformError(f"insufficient OOF matrices for calibration: {len(all_records)}")

    # Each rolling step is genuinely out-of-fold: the calibrator for fold j sees
    # only folds < j. These steps become evidence for later seasons, never for
    # the season they evaluate.
    rolling_steps: list[dict[str, Any]] = []
    for index in range(1, len(folds)):
        train = [record for fold in folds[:index] for record in fold["records"]]
        test = folds[index]["records"]
        if len(train) < 100 or not test:
            continue
        temperature = fit_temperature(train)
        rolling_steps.append({
            "index": index,
            "validation_season": folds[index]["season"],
            "training_predictions": len(train),
            "validation_predictions": len(test),
            "temperature": temperature,
            "raw_metrics": evaluate_records(test, 1.0),
            "calibrated_metrics": evaluate_records(test, temperature),
        })

    season_calibrators: dict[str, Any] = {}
    for target_index in range(1, len(folds)):
        target_season = folds[target_index]["season"]
        training_records = [record for fold in folds[:target_index] for record in fold["records"]]
        if len(training_records) < 100:
            continue
        # Crucially exclude the target season's own outcomes from the decision to
        # activate calibration. Only earlier rolling validation evidence is used.
        evidence_steps = [step for step in rolling_steps if int(step["index"]) < target_index]
        summary = _rolling_summary(evidence_steps)
        learned_temperature = fit_temperature(training_records)
        guardrail_passed = _passes_guardrails(summary)
        live_temperature = learned_temperature if guardrail_passed else 1.0
        mode = "temperature" if guardrail_passed else "identity_guardrail"
        dates = [record["date"] for record in training_records if record.get("date")]
        season_calibrators[target_season] = {
            "target_season": target_season,
            "mode": mode,
            "temperature": live_temperature,
            "learned_temperature": learned_temperature,
            "guardrail_passed": guardrail_passed,
            "training_predictions": len(training_records),
            "training_seasons": [fold["season"] for fold in folds[:target_index]],
            "training_max_date": max(dates) if dates else None,
            "rolling_validation_predictions": int(summary.get("count", 0)),
            "rolling_validation": summary,
        }

    if not folds or folds[-1]["season"] not in season_calibrators:
        raise PlatformError("no replay-safe calibrator available for latest target season")
    latest_season = folds[-1]["season"]
    selected = season_calibrators[latest_season]
    operational = (
        unsupported == 0
        and int(selected["training_predictions"]) >= 100
        and int(selected["rolling_validation_predictions"]) >= 100
        and float(selected["rolling_validation"].get("max_probability_sum_residual", 1.0)) <= 1e-10
    )
    status = "OOF_MATRIX_CALIBRATOR_AVAILABLE" if operational else "INSUFFICIENT_OOF_CALIBRATION_VALIDATION"
    code_sha = sha256_file(CALIBRATION_MODULE_PATH)
    report = {
        "schema_version": "V4.6.2",
        "competition_id": competition_id,
        "engine_sha256": engine_sha,
        "calibration_code_sha256": code_sha,
        "source_nested_backtest_report_sha256": sha256_file(source_path),
        "method": "full_matrix_temperature_scaling",
        "leakage_policy": "target-season calibrator uses only earlier outer-fold matrices; activation guardrail uses only validation seasons earlier than target",
        "point_in_time_policy": "season-specific calibrator routing; no target-season outcome may train or activate its own calibrator",
        "unsupported_actual_scores_excluded": unsupported,
        "rolling_folds": rolling_steps,
        "season_calibrators": season_calibrators,
        "target_season": latest_season,
        "fit_predictions": selected["training_predictions"],
        "rolling_validation_predictions": selected["rolling_validation_predictions"],
        "rolling_validation": selected["rolling_validation"],
        "learned_temperature_all_oof": selected["learned_temperature"],
        "selected_live_temperature": selected["temperature"],
        "mode": selected["mode"],
        "operational_status": status,
        "enabled": operational,
        "limitations": [
            "Non-market calibration cannot replace synchronized market coordination.",
            "Identity guardrail T=1 is used when prior rolling OOF evidence does not pass joint and marginal guardrails.",
            "EXACT remains controlled by its independent gate.",
        ],
    }
    artifact = {
        "schema_version": "V4.6.2",
        "competition_id": competition_id,
        "engine_sha256": engine_sha,
        "calibration_code_sha256": code_sha,
        "source_nested_backtest_report_sha256": report["source_nested_backtest_report_sha256"],
        "calibration_report_sha256": sha256_json(report),
        "method": report["method"],
        "target_season": latest_season,
        "season_calibrators": season_calibrators,
        "mode": selected["mode"],
        "temperature": selected["temperature"],
        "learned_temperature_all_oof": selected["learned_temperature"],
        "fit_predictions": selected["training_predictions"],
        "rolling_validation_predictions": selected["rolling_validation_predictions"],
        "rolling_validation": selected["rolling_validation"],
        "operational_status": status,
        "enabled": operational,
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
        atomic_write_json(MODEL_ROOT / competition_id / "oof_matrix_calibrator.json", artifact)
    return report, artifact


def run_all(competition: str | None = None, *, write: bool = True) -> dict[str, Any]:
    ids = [item["competition_id"] for item in load_registry()["competitions"]]
    if competition:
        if competition not in ids:
            raise PlatformError(f"unknown competition: {competition}")
        ids = [competition]
    reports, failures = {}, []
    for competition_id in ids:
        try:
            report, artifact = validate_competition(competition_id, write=write)
            reports[competition_id] = {key: artifact[key] for key in ("operational_status", "mode", "temperature", "fit_predictions", "rolling_validation_predictions")}
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.2", "engine_sha256": sha256_file(ENGINE_PATH),
        "calibration_code_sha256": sha256_file(CALIBRATION_MODULE_PATH),
        "competition_count_requested": len(ids), "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "calibrator_available_count": sum(item["operational_status"] == "OOF_MATRIX_CALIBRATOR_AVAILABLE" for item in reports.values()),
        "reports": reports, "failures": failures
    }
    if write and not competition:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"OOF matrix calibration failed for {len(failures)} domains: {failures}")
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
