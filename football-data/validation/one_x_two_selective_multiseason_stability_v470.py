#!/usr/bin/env python3
"""Strict multi-season descriptive stability audit for selective 1X2 directions.

Fixed Top1-Top2 probability-gap thresholds are evaluated without tuning them on the
target season. Every season is replayed point-in-time with that season's formal outer-
fold parameters and replay-safe OOF matrix temperature. This is a diagnostic stress
test only; no threshold receives formal runtime rights.
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
    FORMAL_STATUS,
    REPORT_ROOT,
    _actual_result,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, derive_score_marginals, load_json, read_processed_matches

OUT = ROOT / "manifests" / "one_x_two_selective_multiseason_stability_v470_status.json"
THRESHOLDS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]


def _season_year(season: str) -> int:
    token = str(season)
    return int(token[:4])


def _wilson(hits: int, n: int, z: float = 1.959963984540054) -> dict[str, float | None]:
    if n <= 0:
        return {"lower": None, "upper": None}
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"lower": max(0.0, center - margin), "upper": min(1.0, center + margin)}


def _completed_seasons(cid: str, report: dict[str, Any]) -> list[str]:
    max_year = _season_year(_requested_last_complete_season(cid))
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and _season_year(season) <= max_year and season not in seasons:
            seasons.append(season)
    seasons.sort(key=_season_year)
    return seasons


def _season_predictions(cid: str, report: dict[str, Any], all_matches, season: str) -> list[dict[str, Any]]:
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"invalid selected parameters {cid} {season}")
    temperature, mode = _target_season_temperature(cid, season)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    rows = []
    skipped = 0
    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
        except PlatformError:
            skipped += 1
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        one = derive_score_marginals(matrix)["1x2"]
        ranking = sorted(((k, float(one[k])) for k in ("home", "draw", "away")), key=lambda kv: (-kv[1], kv[0]))
        actual = _actual_result(int(match.home_goals), int(match.away_goals))
        rows.append({
            "gap": ranking[0][1] - ranking[1][1],
            "top1_probability": ranking[0][1],
            "hit": int(ranking[0][0] == actual),
        })
    return rows


def _evaluate(rows_by_season: dict[str, list[dict[str, Any]]], threshold: float) -> dict[str, Any]:
    per_season = []
    pooled_hits = pooled_n = pooled_available = 0
    for season, rows in rows_by_season.items():
        selected = [r for r in rows if float(r["gap"]) >= threshold]
        hits = sum(int(r["hit"]) for r in selected)
        n = len(selected)
        pooled_hits += hits
        pooled_n += n
        pooled_available += len(rows)
        per_season.append({
            "season": season,
            "eligible_predictions": len(rows),
            "selected_count": n,
            "coverage": n / len(rows) if rows else None,
            "hit_count": hits,
            "accuracy": hits / n if n else None,
            "ci95_wilson": _wilson(hits, n),
        })
    season_accuracies = [float(x["accuracy"]) for x in per_season if x["accuracy"] is not None]
    season_coverages = [float(x["coverage"]) for x in per_season if x["coverage"] is not None]
    return {
        "gap_threshold": threshold,
        "pooled_selected_count": pooled_n,
        "pooled_available_predictions": pooled_available,
        "pooled_coverage": pooled_n / pooled_available if pooled_available else None,
        "pooled_hit_count": pooled_hits,
        "pooled_accuracy": pooled_hits / pooled_n if pooled_n else None,
        "pooled_ci95_wilson": _wilson(pooled_hits, pooled_n),
        "season_accuracy_mean": mean(season_accuracies) if season_accuracies else None,
        "season_accuracy_std": pstdev(season_accuracies) if len(season_accuracies) > 1 else 0.0 if season_accuracies else None,
        "season_accuracy_min": min(season_accuracies) if season_accuracies else None,
        "season_accuracy_max": max(season_accuracies) if season_accuracies else None,
        "season_coverage_mean": mean(season_coverages) if season_coverages else None,
        "per_season": per_season,
    }


def _domain(cid: str) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    seasons = _completed_seasons(cid, report)
    if len(seasons) < 2:
        raise PlatformError(f"insufficient completed seasons for {cid}")
    all_matches = read_processed_matches(cid)
    rows_by_season = {season: _season_predictions(cid, report, all_matches, season) for season in seasons}
    return {
        "competition_id": cid,
        "completed_seasons": seasons,
        "thresholds": [_evaluate(rows_by_season, t) for t in THRESHOLDS],
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports = {}
    failures = {}
    aggregate_rows: dict[str, list[dict[str, Any]]] = {}
    for cid in competitions:
        try:
            report = load_json(REPORT_ROOT / f"{cid}.json")
            seasons = _completed_seasons(cid, report)
            if len(seasons) < 2:
                raise PlatformError(f"insufficient completed seasons for {cid}")
            all_matches = read_processed_matches(cid)
            rows_by_season = {f"{cid}:{season}": _season_predictions(cid, report, all_matches, season) for season in seasons}
            reports[cid] = {
                "competition_id": cid,
                "completed_seasons": seasons,
                "thresholds": [_evaluate(rows_by_season, t) for t in THRESHOLDS],
            }
            aggregate_rows.update(rows_by_season)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    payload = {
        "schema_version": "V4.7.0-1x2-selective-multiseason-stability-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "fixed_gap_thresholds": THRESHOLDS,
        "aggregate_across_domain_seasons": [_evaluate(aggregate_rows, t) for t in THRESHOLDS],
        "reports": reports,
        "failures": failures,
        "governance": {
            "descriptive_stability_audit_only": True,
            "thresholds_fixed_before_evaluation": True,
            "formal_threshold_selected": False,
            "runtime_change": False,
            "formal_weight_change": False,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload["aggregate_across_domain_seasons"], ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
