#!/usr/bin/env python3
"""Diagnose draw under-selection in the 17-domain formal football core.

This is a read-only descriptive audit over the same previous-complete-season PIT
replay used by backtest_last_complete_season_all_domains_v470.py. It does not alter
formal probabilities, weights, calibration, market coordination or EV.

The audit distinguishes:
- marginal draw calibration (actual draw rate vs mean predicted draw probability),
- ranking failure (draw probability calibrated on average but rarely Top-1),
- near-miss structure (draw within 2/5/10 percentage points of the strongest side),
- conditional score-allocation concentration (diagonal draw mass relative to even-total mass).
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    derive_score_marginals,
    load_json,
    read_processed_matches,
    score_matrix_rows,
)

FORMAL_STATUS = ROOT / "manifests" / "formal_core_v460_status.json"
OUT = ROOT / "manifests" / "draw_structure_diagnostics_v470_status.json"


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return float(ordered[lo])
    weight = position - lo
    return float(ordered[lo] * (1.0 - weight) + ordered[hi] * weight)


def _binary_ece(rows: list[tuple[float, int]], bins: int = 10) -> dict[str, Any]:
    if not rows:
        return {"ece": None, "bins": []}
    output = []
    weighted = 0.0
    total = len(rows)
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        selected = [
            (p, y) for p, y in rows
            if (low <= p < high) or (index == bins - 1 and p == 1.0)
        ]
        if not selected:
            continue
        avg_p = mean(p for p, _ in selected)
        actual_rate = mean(y for _, y in selected)
        gap = abs(avg_p - actual_rate)
        weighted += len(selected) / total * gap
        output.append({
            "bin_low": low,
            "bin_high": high,
            "count": len(selected),
            "mean_predicted_draw_probability": avg_p,
            "actual_draw_rate": actual_rate,
            "absolute_gap": gap,
        })
    return {"ece": weighted, "bins": output}


def _actual_result(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def _rank_of_draw(one: dict[str, float]) -> int:
    draw = float(one["draw"])
    return 1 + sum(1 for key in ("home", "away") if float(one[key]) > draw + 1e-15)


def _matrix_structure(matrix: list[dict[str, Any]]) -> tuple[float, float, float]:
    draw_mass = 0.0
    even_total_mass = 0.0
    total_mass = 0.0
    for home, away, probability in score_matrix_rows(matrix):
        total_mass += probability
        if (home + away) % 2 == 0:
            even_total_mass += probability
        if home == away:
            draw_mass += probability
    diagonal_share_of_even = draw_mass / even_total_mass if even_total_mass > 0.0 else float("nan")
    return draw_mass, even_total_mass, diagonal_share_of_even


def _diagnose_competition(competition_id: str) -> dict[str, Any]:
    report_path = REPORT_ROOT / f"{competition_id}.json"
    if not report_path.exists():
        raise PlatformError(f"missing formal-core report for {competition_id}")
    report = load_json(report_path)
    season = _requested_last_complete_season(competition_id)
    fold = _fold_for_season(report, season)
    selected_parameters = fold.get("selected_parameters")
    if not isinstance(selected_parameters, dict):
        raise PlatformError(f"invalid selected parameters for {competition_id} season {season}")

    all_matches = read_processed_matches(competition_id)
    matches = [m for m in all_matches if str(m.season) == season]
    matches.sort(key=lambda m: (m.date, m.home_team, m.away_team))
    if not matches:
        raise PlatformError(f"no processed matches for {competition_id} season {season}")

    temperature, calibration_mode = _target_season_temperature(competition_id, season)
    rows: list[dict[str, Any]] = []
    skipped = Counter()
    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(
                all_matches,
                match.home_team,
                match.away_team,
                match.date,
                season,
                selected_parameters,
            )
        except PlatformError as exc:
            skipped[str(exc)] += 1
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        marginals = derive_score_marginals(matrix)
        one = marginals["1x2"]
        actual = _actual_result(int(match.home_goals), int(match.away_goals))
        draw_p, even_mass, diagonal_share = _matrix_structure(matrix)
        max_side = max(float(one["home"]), float(one["away"]))
        gap = max_side - float(one["draw"])
        rows.append({
            "date": match.date.isoformat(),
            "actual": actual,
            "draw_probability": float(one["draw"]),
            "home_probability": float(one["home"]),
            "away_probability": float(one["away"]),
            "draw_rank": _rank_of_draw(one),
            "draw_gap_to_best_side": gap,
            "even_total_mass": even_mass,
            "draw_diagonal_share_of_even_total_mass": diagonal_share,
            "probability_sum": float(marginals["probability_sum"]),
            "matrix_draw_mass_check": draw_p,
        })

    if not rows:
        raise PlatformError(f"no eligible PIT predictions for {competition_id} season {season}")

    draw_rows = [row for row in rows if row["actual"] == "draw"]
    non_draw_rows = [row for row in rows if row["actual"] != "draw"]
    calibration_rows = [(row["draw_probability"], 1 if row["actual"] == "draw" else 0) for row in rows]
    rank_counts = Counter(row["draw_rank"] for row in rows)
    actual_draw_rank_counts = Counter(row["draw_rank"] for row in draw_rows)
    draw_gaps = [row["draw_gap_to_best_side"] for row in draw_rows]

    near_miss = {}
    for threshold in (0.02, 0.05, 0.10):
        count = sum(1 for gap in draw_gaps if gap <= threshold)
        near_miss[f"within_{int(threshold * 100)}pp"] = {
            "count": count,
            "share_of_actual_draws": count / len(draw_rows) if draw_rows else None,
        }

    actual_draw_rate = len(draw_rows) / len(rows)
    mean_draw_probability = mean(row["draw_probability"] for row in rows)
    draw_brier = mean((row["draw_probability"] - (1.0 if row["actual"] == "draw" else 0.0)) ** 2 for row in rows)
    ece = _binary_ece(calibration_rows)
    mean_diagonal_share = mean(row["draw_diagonal_share_of_even_total_mass"] for row in rows)
    mean_even_mass = mean(row["even_total_mass"] for row in rows)

    if mean_draw_probability + 0.03 < actual_draw_rate:
        primary_diagnosis = "MARGINAL_DRAW_UNDERPREDICTION"
    elif rank_counts.get(1, 0) / len(rows) < 0.02 and near_miss["within_5pp"]["share_of_actual_draws"] is not None and near_miss["within_5pp"]["share_of_actual_draws"] >= 0.25:
        primary_diagnosis = "RANKING_COMPRESSION_DRAW_OFTEN_NEAR_TOP"
    elif mean_diagonal_share < 0.45:
        primary_diagnosis = "CONDITIONAL_DIAGONAL_ALLOCATION_WEAK"
    else:
        primary_diagnosis = "MIXED_OR_DOMAIN_SPECIFIC"

    return {
        "competition_id": competition_id,
        "season": season,
        "eligible_prediction_count": len(rows),
        "skipped_by_formal_sample_gates": sum(skipped.values()),
        "oof_calibration": {"temperature": temperature, "mode": calibration_mode},
        "draw_marginal": {
            "actual_draw_count": len(draw_rows),
            "actual_draw_rate": actual_draw_rate,
            "mean_predicted_draw_probability": mean_draw_probability,
            "predicted_minus_actual_draw_rate": mean_draw_probability - actual_draw_rate,
            "mean_predicted_draw_probability_on_actual_draws": mean(row["draw_probability"] for row in draw_rows) if draw_rows else None,
            "mean_predicted_draw_probability_on_non_draws": mean(row["draw_probability"] for row in non_draw_rows) if non_draw_rows else None,
            "draw_binary_brier": draw_brier,
            "draw_ece": ece["ece"],
            "calibration_bins": ece["bins"],
        },
        "draw_ranking": {
            "all_matches_rank_counts": {str(rank): rank_counts.get(rank, 0) for rank in (1, 2, 3)},
            "all_matches_top1_rate": rank_counts.get(1, 0) / len(rows),
            "actual_draw_rank_counts": {str(rank): actual_draw_rank_counts.get(rank, 0) for rank in (1, 2, 3)},
            "actual_draw_top1_rate": actual_draw_rank_counts.get(1, 0) / len(draw_rows) if draw_rows else None,
            "near_miss_actual_draws": near_miss,
            "actual_draw_gap_to_best_side_mean": mean(draw_gaps) if draw_gaps else None,
            "actual_draw_gap_to_best_side_median": median(draw_gaps) if draw_gaps else None,
            "actual_draw_gap_to_best_side_p25": _quantile(draw_gaps, 0.25),
            "actual_draw_gap_to_best_side_p75": _quantile(draw_gaps, 0.75),
        },
        "matrix_structure": {
            "mean_even_total_mass": mean_even_mass,
            "mean_draw_diagonal_share_of_even_total_mass": mean_diagonal_share,
            "interpretation": "Draw probability equals diagonal score mass. The diagonal/even-total ratio isolates conditional score-allocation concentration from the amount of even-total probability available.",
        },
        "primary_diagnosis": primary_diagnosis,
        "audit": {
            "same_previous_complete_season_scope_as_r3_backtest": True,
            "strict_point_in_time": True,
            "formal_sample_gates_preserved": True,
            "historical_market_used": False,
            "lineup_hindsight_used": False,
            "probability_mutation": False,
            "formal_weight_change": False,
            "max_probability_sum_residual": max(abs(row["probability_sum"] - 1.0) for row in rows),
            "skip_reason_counts": dict(skipped),
        },
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid in competitions:
        try:
            reports[cid] = _diagnose_competition(cid)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    total_predictions = sum(int(report["eligible_prediction_count"]) for report in reports.values())
    total_draws = sum(int(report["draw_marginal"]["actual_draw_count"]) for report in reports.values())
    weighted_predicted_draw = sum(
        float(report["draw_marginal"]["mean_predicted_draw_probability"]) * int(report["eligible_prediction_count"])
        for report in reports.values()
    )
    total_draw_top1 = sum(int(report["draw_ranking"]["all_matches_rank_counts"]["1"]) for report in reports.values())
    total_actual_draw_top1 = sum(int(report["draw_ranking"]["actual_draw_rank_counts"]["1"]) for report in reports.values())
    total_actual_draw_within_5pp = sum(int(report["draw_ranking"]["near_miss_actual_draws"]["within_5pp"]["count"]) for report in reports.values())

    payload = {
        "schema_version": "V4.7.0-draw-structure-diagnostics-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "aggregate": {
            "eligible_prediction_count": total_predictions,
            "actual_draw_count": total_draws,
            "actual_draw_rate": total_draws / total_predictions if total_predictions else None,
            "mean_predicted_draw_probability_micro": weighted_predicted_draw / total_predictions if total_predictions else None,
            "predicted_minus_actual_draw_rate": (weighted_predicted_draw - total_draws) / total_predictions if total_predictions else None,
            "draw_top1_count_all_matches": total_draw_top1,
            "draw_top1_rate_all_matches": total_draw_top1 / total_predictions if total_predictions else None,
            "actual_draws_selected_top1_count": total_actual_draw_top1,
            "actual_draws_selected_top1_rate": total_actual_draw_top1 / total_draws if total_draws else None,
            "actual_draws_within_5pp_of_best_side_count": total_actual_draw_within_5pp,
            "actual_draws_within_5pp_of_best_side_rate": total_actual_draw_within_5pp / total_draws if total_draws else None,
        },
        "diagnosis_counts": dict(Counter(report["primary_diagnosis"] for report in reports.values())),
        "reports": reports,
        "failures": failures,
        "governance": {
            "research_or_diagnostic_only": True,
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_draw_boost": False,
            "promotion_receipt_created": False,
            "next_step": "Only after diagnosis, test any draw-structure challenger with competition-specific strict chronological OOF. Do not hand-adjust draw probabilities.",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "competition_count_completed": payload["competition_count_completed"],
        "aggregate": payload["aggregate"],
        "diagnosis_counts": payload["diagnosis_counts"],
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
