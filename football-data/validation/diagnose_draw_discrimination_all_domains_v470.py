#!/usr/bin/env python3
"""Root-cause audit for draw discrimination across all 17 football domains.

Read-only research diagnostics. The script compares raw and OOF-calibrated draw
probability sharpness/discrimination on the previous complete season, using the
same strict PIT replay and sample gates as the formal core.
"""
from __future__ import annotations

import json
import math
import sys
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

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, derive_score_marginals, load_json, read_processed_matches, score_matrix_rows

FORMAL_STATUS = ROOT / "manifests" / "formal_core_v460_status.json"
OUT = ROOT / "manifests" / "draw_discrimination_diagnostics_v470_status.json"


def _actual_draw(match) -> int:
    return 1 if int(match.home_goals) == int(match.away_goals) else 0


def _auc(scores: list[float], labels: list[int]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    pairs = sorted(zip(scores, labels), key=lambda item: item[0])
    rank_sum_pos = 0.0
    index = 0
    rank = 1
    while index < len(pairs):
        end = index + 1
        while end < len(pairs) and abs(pairs[end][0] - pairs[index][0]) <= 1e-15:
            end += 1
        average_rank = (rank + (rank + end - index - 1)) / 2.0
        rank_sum_pos += average_rank * sum(label for _, label in pairs[index:end])
        rank += end - index
        index = end
    return (rank_sum_pos - positives * (positives + 1) / 2.0) / (positives * negatives)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx, my = mean(xs), mean(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-18 or vy <= 1e-18:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def _quintiles(rows: list[dict[str, Any]], key: str, descending: bool = False) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: float(row[key]), reverse=descending)
    output = []
    n = len(ordered)
    for q in range(5):
        start = q * n // 5
        end = (q + 1) * n // 5
        chunk = ordered[start:end]
        if not chunk:
            continue
        output.append({
            "quintile": q + 1,
            "count": len(chunk),
            "mean_signal": mean(float(row[key]) for row in chunk),
            "actual_draw_rate": mean(int(row["actual_draw"]) for row in chunk),
            "mean_draw_probability": mean(float(row["cal_draw"]) for row in chunk),
        })
    return output


def _diag_share(matrix) -> float:
    even = 0.0
    diagonal = 0.0
    for h, a, p in score_matrix_rows(matrix):
        if (h + a) % 2 == 0:
            even += p
        if h == a:
            diagonal += p
    return diagonal / even if even > 0 else float("nan")


def diagnose(cid: str) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    season = _requested_last_complete_season(cid)
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError("selected parameters missing")
    all_matches = read_processed_matches(cid)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    temperature, mode = _target_season_temperature(cid, season)
    rows = []
    skipped = 0
    for match in matches:
        try:
            raw_matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
        except PlatformError:
            skipped += 1
            continue
        raw_one = derive_score_marginals(raw_matrix)["1x2"]
        calibrated_matrix = temperature_scale_matrix(raw_matrix, temperature) if abs(temperature - 1.0) > 1e-15 else raw_matrix
        cal_one = derive_score_marginals(calibrated_matrix)["1x2"]
        side_gap = abs(float(cal_one["home"]) - float(cal_one["away"]))
        balance_score = 1.0 - side_gap
        rows.append({
            "actual_draw": _actual_draw(match),
            "raw_draw": float(raw_one["draw"]),
            "cal_draw": float(cal_one["draw"]),
            "side_gap": side_gap,
            "balance_score": balance_score,
            "diag_share": _diag_share(calibrated_matrix),
        })
    if not rows:
        raise PlatformError("no eligible predictions")
    labels = [int(row["actual_draw"]) for row in rows]
    raw_scores = [float(row["raw_draw"]) for row in rows]
    cal_scores = [float(row["cal_draw"]) for row in rows]
    balance_scores = [float(row["balance_score"]) for row in rows]
    actual_rows = [row for row in rows if row["actual_draw"] == 1]
    non_rows = [row for row in rows if row["actual_draw"] == 0]
    raw_auc = _auc(raw_scores, labels)
    cal_auc = _auc(cal_scores, labels)
    balance_auc = _auc(balance_scores, labels)
    separation = mean(row["cal_draw"] for row in actual_rows) - mean(row["cal_draw"] for row in non_rows)
    draw_std = pstdev(cal_scores)
    raw_std = pstdev(raw_scores)
    balance_corr_draw = _pearson(balance_scores, cal_scores)
    balance_corr_actual = _pearson(balance_scores, [float(label) for label in labels])
    diag_corr_balance = _pearson([float(row["diag_share"]) for row in rows], balance_scores)

    if cal_auc is not None and cal_auc < 0.53 and abs(separation) < 0.01:
        root_cause = "DRAW_PROBABILITY_LOW_DISCRIMINATION"
    elif balance_auc is not None and cal_auc is not None and balance_auc - cal_auc > 0.03:
        root_cause = "TEAM_BALANCE_SIGNAL_NOT_FULLY_PROPAGATED_TO_DRAW"
    elif draw_std < 0.025:
        root_cause = "DRAW_PROBABILITY_LOW_SHARPNESS"
    else:
        root_cause = "DOMAIN_SPECIFIC_OR_MIXED"

    return {
        "competition_id": cid,
        "season": season,
        "eligible_prediction_count": len(rows),
        "skipped": skipped,
        "oof_calibration": {"temperature": temperature, "mode": mode},
        "draw_discrimination": {
            "actual_draw_rate": mean(labels),
            "raw_draw_probability_mean": mean(raw_scores),
            "raw_draw_probability_std": raw_std,
            "raw_draw_auc": raw_auc,
            "calibrated_draw_probability_mean": mean(cal_scores),
            "calibrated_draw_probability_std": draw_std,
            "calibrated_draw_auc": cal_auc,
            "mean_draw_probability_on_actual_draws": mean(row["cal_draw"] for row in actual_rows),
            "mean_draw_probability_on_non_draws": mean(row["cal_draw"] for row in non_rows),
            "actual_minus_non_draw_probability_separation": separation,
            "calibration_auc_change": (cal_auc - raw_auc) if cal_auc is not None and raw_auc is not None else None,
            "calibration_std_change": draw_std - raw_std,
        },
        "balance_signal": {
            "balance_score_auc_for_actual_draw": balance_auc,
            "correlation_balance_with_predicted_draw_probability": balance_corr_draw,
            "correlation_balance_with_actual_draw_indicator": balance_corr_actual,
            "correlation_diagonal_share_with_balance": diag_corr_balance,
            "draw_probability_quintiles": _quintiles(rows, "cal_draw"),
            "team_balance_quintiles_most_balanced_first": _quintiles(rows, "side_gap"),
        },
        "root_cause": root_cause,
        "audit": {
            "strict_pit": True,
            "same_completed_season_scope": True,
            "formal_sample_gates_preserved": True,
            "probability_change": False,
            "formal_weight_change": False,
        },
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports = {}
    failures = {}
    for cid in competitions:
        try:
            reports[cid] = diagnose(cid)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    weighted_count = sum(report["eligible_prediction_count"] for report in reports.values())
    payload = {
        "schema_version": "V4.7.0-draw-discrimination-diagnostics-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "aggregate": {
            "eligible_prediction_count": weighted_count,
            "mean_domain_calibrated_draw_auc": mean(report["draw_discrimination"]["calibrated_draw_auc"] for report in reports.values() if report["draw_discrimination"]["calibrated_draw_auc"] is not None),
            "mean_domain_balance_auc": mean(report["balance_signal"]["balance_score_auc_for_actual_draw"] for report in reports.values() if report["balance_signal"]["balance_score_auc_for_actual_draw"] is not None),
            "mean_domain_draw_probability_std": mean(report["draw_discrimination"]["calibrated_draw_probability_std"] for report in reports.values()),
            "mean_domain_actual_vs_non_draw_separation": mean(report["draw_discrimination"]["actual_minus_non_draw_probability_separation"] for report in reports.values()),
        },
        "root_cause_counts": {},
        "reports": reports,
        "failures": failures,
        "governance": {"research_only": True, "formal_weight_change": False, "probability_change": False},
    }
    counts = {}
    for report in reports.values():
        counts[report["root_cause"]] = counts.get(report["root_cause"], 0) + 1
    payload["root_cause_counts"] = counts
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "aggregate": payload["aggregate"], "root_cause_counts": counts, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
