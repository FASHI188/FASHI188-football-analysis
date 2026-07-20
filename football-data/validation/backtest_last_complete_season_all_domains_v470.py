#!/usr/bin/env python3
"""Strict point-in-time last-complete-season descriptive backtest for all 17 domains.

Primary descriptive metric: 1X2 Top-1 accuracy (home/draw/away argmax versus actual
90-minute result). Exact-score Top-1 is reported separately and must never be called
1X2 win rate.

The replay uses, per competition:
- the latest outer season already present in the nested chronological formal-core
  validation report;
- that outer fold's parameter set, selected only from prior seasons;
- same-season match history strictly before each match date via the frozen formal
  engine;
- the target-season OOF full-matrix calibrator when replay-safe and available.

No historical odds, market coordination, EV, lineup hindsight, current-season
challenger promotion or post-match information is injected.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from football_v460_engine import predict_joint_distribution
from oof_matrix_calibration import load_oof_matrix_calibrator, temperature_scale_matrix
from platform_core import (
    PlatformError,
    derive_score_marginals,
    load_json,
    read_processed_matches,
    score_matrix_rows,
    top_scores,
)

FORMAL_STATUS = ROOT / "manifests" / "formal_core_v460_status.json"
REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
OUT = ROOT / "manifests" / "last_complete_season_backtest_v470_status.json"


def _actual_result(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def _one_x_two_brier(prob: dict[str, float], actual: str) -> float:
    return sum((float(prob[k]) - (1.0 if k == actual else 0.0)) ** 2 for k in ("home", "draw", "away"))


def _one_x_two_rps(prob: dict[str, float], actual: str) -> float:
    # Ordered categories home, draw, away; standard three-category RPS / (K-1).
    actual_vec = {
        "home": (1.0, 0.0, 0.0),
        "draw": (0.0, 1.0, 0.0),
        "away": (0.0, 0.0, 1.0),
    }[actual]
    p = (float(prob["home"]), float(prob["draw"]), float(prob["away"]))
    c1 = p[0] - actual_vec[0]
    c2 = (p[0] + p[1]) - (actual_vec[0] + actual_vec[1])
    return (c1 * c1 + c2 * c2) / 2.0


def _joint_log_score(matrix: list[dict[str, Any]], hg: int, ag: int) -> float:
    probability = 0.0
    max_total = -1
    for h, a, p in score_matrix_rows(matrix):
        max_total = max(max_total, h + a)
        if h == hg and a == ag:
            probability += p
    # The frozen engine aggregates 7+ to a finite representative support. A true
    # score outside explicit support cannot be assigned an exact-cell log score;
    # report it as unavailable for this descriptive replay rather than invent mass.
    if probability <= 0.0:
        return float("nan")
    return -math.log(max(1e-15, probability))


def _target_season_temperature(competition_id: str, season: str) -> tuple[float, str]:
    loaded = load_oof_matrix_calibrator(competition_id)
    if loaded is None:
        return 1.0, "calibrator_missing_identity"
    _, artifact = loaded
    season_map = artifact.get("season_calibrators") or {}
    item = season_map.get(season) if isinstance(season_map, dict) else None
    if not isinstance(item, dict):
        return 1.0, "target_season_calibrator_missing_identity"
    return float(item.get("temperature", 1.0)), str(item.get("mode") or "unknown")


def _last_outer_fold(report: dict[str, Any]) -> dict[str, Any]:
    folds = report.get("folds") or []
    if not folds:
        raise PlatformError("formal-core validation report has no outer folds")
    return folds[-1]


def _backtest_competition(competition_id: str) -> dict[str, Any]:
    report_path = REPORT_ROOT / f"{competition_id}.json"
    if not report_path.exists():
        raise PlatformError(f"missing formal-core report for {competition_id}")
    report = load_json(report_path)
    fold = _last_outer_fold(report)
    season = str(fold.get("outer_season") or "")
    params = fold.get("selected_parameters")
    if not season or not isinstance(params, dict):
        raise PlatformError(f"invalid latest outer fold for {competition_id}")

    matches = [m for m in read_processed_matches(competition_id) if str(m.season) == season]
    matches.sort(key=lambda m: (m.date, m.home_team, m.away_team))
    if not matches:
        raise PlatformError(f"no processed matches for {competition_id} season {season}")

    temperature, calibration_mode = _target_season_temperature(competition_id, season)
    predicted = 0
    skipped = 0
    one_x_two_hits = 0
    score_top1_hits = 0
    score_top3_hits = 0
    brier_sum = 0.0
    rps_sum = 0.0
    log_sum = 0.0
    log_count = 0
    predicted_direction = Counter()
    actual_direction = Counter()
    hit_by_predicted_direction = Counter()
    skip_reasons = Counter()
    probability_sum_max_residual = 0.0

    for match in matches:
        cutoff = match.date
        try:
            prediction = predict_joint_distribution(
                competition_id,
                match.home_team,
                match.away_team,
                cutoff,
                season=season,
                selected_parameters=params,
            )
        except PlatformError as exc:
            skipped += 1
            skip_reasons[str(exc)] += 1
            continue

        matrix = prediction["probabilities"]["score_matrix"]
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        marginals = derive_score_marginals(matrix)
        probability_sum_max_residual = max(
            probability_sum_max_residual, abs(float(marginals["probability_sum"]) - 1.0)
        )
        one = marginals["1x2"]
        pick = max(("home", "draw", "away"), key=lambda key: float(one[key]))
        actual = _actual_result(int(match.home_goals), int(match.away_goals))
        predicted_direction[pick] += 1
        actual_direction[actual] += 1
        if pick == actual:
            one_x_two_hits += 1
            hit_by_predicted_direction[pick] += 1

        ranking = top_scores(matrix, 3)
        actual_score = f"{int(match.home_goals)}-{int(match.away_goals)}"
        if ranking and ranking[0]["score"] == actual_score:
            score_top1_hits += 1
        if any(item["score"] == actual_score for item in ranking):
            score_top3_hits += 1

        brier_sum += _one_x_two_brier(one, actual)
        rps_sum += _one_x_two_rps(one, actual)
        log_score = _joint_log_score(matrix, int(match.home_goals), int(match.away_goals))
        if math.isfinite(log_score):
            log_sum += log_score
            log_count += 1
        predicted += 1

    if predicted == 0:
        raise PlatformError(f"no eligible PIT predictions for {competition_id} season {season}")

    per_pick = {}
    for direction in ("home", "draw", "away"):
        n = predicted_direction[direction]
        per_pick[direction] = {
            "predicted_count": n,
            "hit_count": hit_by_predicted_direction[direction],
            "hit_rate": hit_by_predicted_direction[direction] / n if n else None,
        }

    return {
        "competition_id": competition_id,
        "season": season,
        "season_match_count": len(matches),
        "eligible_prediction_count": predicted,
        "skipped_by_formal_sample_gates": skipped,
        "coverage_rate": predicted / len(matches),
        "selected_parameters": params,
        "parameter_selection_prior_seasons": fold.get("prior_seasons") or [],
        "oof_calibration": {
            "temperature": temperature,
            "mode": calibration_mode,
        },
        "one_x_two_top1": {
            "hit_count": one_x_two_hits,
            "accuracy": one_x_two_hits / predicted,
            "predicted_direction_counts": dict(predicted_direction),
            "actual_direction_counts": dict(actual_direction),
            "per_predicted_direction": per_pick,
        },
        "exact_score": {
            "top1_hit_count": score_top1_hits,
            "top1_accuracy": score_top1_hits / predicted,
            "top3_hit_count": score_top3_hits,
            "top3_accuracy": score_top3_hits / predicted,
        },
        "proper_scores": {
            "mean_one_x_two_brier": brier_sum / predicted,
            "mean_one_x_two_rps": rps_sum / predicted,
            "mean_joint_log_score_explicit_support_only": log_sum / log_count if log_count else None,
            "joint_log_score_count": log_count,
        },
        "audit": {
            "same_season_strictly_prior_history": True,
            "parameter_selection_uses_only_prior_seasons": True,
            "historical_odds_used": False,
            "market_coordination_applied": False,
            "formal_ev_tested": False,
            "lineup_hindsight_used": False,
            "probability_sum_max_residual": probability_sum_max_residual,
            "skip_reason_counts": dict(skip_reasons),
        },
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    total_predictions = 0
    total_hits = 0
    total_score_top1_hits = 0

    for cid in competitions:
        try:
            item = _backtest_competition(cid)
            reports[cid] = item
            total_predictions += int(item["eligible_prediction_count"])
            total_hits += int(item["one_x_two_top1"]["hit_count"])
            total_score_top1_hits += int(item["exact_score"]["top1_hit_count"])
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    payload = {
        "schema_version": "V4.7.0-last-complete-season-backtest-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "primary_metric_definition": "1X2 Top-1 accuracy: argmax(home/draw/away) versus actual 90-minute result",
        "descriptive_only_warning": "Hit rate is descriptive and is not sufficient for model promotion; proper scoring rules remain required.",
        "aggregate": {
            "eligible_prediction_count": total_predictions,
            "one_x_two_hit_count": total_hits,
            "micro_one_x_two_top1_accuracy": total_hits / total_predictions if total_predictions else None,
            "exact_score_top1_hit_count": total_score_top1_hits,
            "micro_exact_score_top1_accuracy": total_score_top1_hits / total_predictions if total_predictions else None,
        },
        "reports": reports,
        "failures": failures,
        "formal_weight_change": False,
        "probability_change": False,
        "production_promotion_receipt_created": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "competition_count_completed": payload["competition_count_completed"],
        "aggregate": payload["aggregate"],
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
