#!/usr/bin/env python3
"""Selective-coverage accuracy frontier for 1X2, exact score and total goals.

Uses the same last-complete-season PIT replay and unified score matrix as the formal
baseline. Fixtures are ranked by Top-1 minus Top-2 probability gap independently per
target. This is descriptive only: no threshold is promoted or used at runtime.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
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
from platform_core import PlatformError, atomic_write_json, derive_score_marginals, load_json, read_processed_matches, score_matrix_rows, top_scores

OUT = ROOT / "manifests" / "four_target_selective_frontier_v470_status.json"
COVERAGE_LEVELS = [0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.75, 1.00]


def _total_probs(matrix) -> dict[int, float]:
    out: dict[int, float] = {}
    for h, a, p in score_matrix_rows(matrix):
        out[h + a] = out.get(h + a, 0.0) + float(p)
    return out


def _frontier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda r: (-float(r["gap"]), -float(r["top1_probability"])))
    n = len(ordered)
    result = []
    for coverage in COVERAGE_LEVELS:
        k = max(1, min(n, int(round(n * coverage))))
        selected = ordered[:k]
        hits = sum(int(item["hit"]) for item in selected)
        result.append({
            "target_coverage": coverage,
            "selected_count": k,
            "actual_coverage": k / n if n else None,
            "hit_count": hits,
            "accuracy": hits / k if k else None,
            "minimum_selected_gap": float(selected[-1]["gap"]) if selected else None,
            "mean_selected_top1_probability": sum(float(item["top1_probability"]) for item in selected) / k if k else None,
        })
    return result


def _domain_rows(cid: str) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    season = _requested_last_complete_season(cid)
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"invalid selected parameters for {cid} {season}")
    all_matches = read_processed_matches(cid)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    temperature, mode = _target_season_temperature(cid, season)
    rows = {"one_x_two": [], "exact_score": [], "total_goals": []}
    skipped = 0
    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
        except PlatformError:
            skipped += 1
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)

        marginals = derive_score_marginals(matrix)
        one = marginals["1x2"]
        one_rank = sorted(((k, float(one[k])) for k in ("home", "draw", "away")), key=lambda kv: (-kv[1], kv[0]))
        actual_result = _actual_result(int(match.home_goals), int(match.away_goals))
        rows["one_x_two"].append({
            "gap": one_rank[0][1] - one_rank[1][1],
            "top1_probability": one_rank[0][1],
            "hit": one_rank[0][0] == actual_result,
        })

        score_rank = top_scores(matrix, 2)
        actual_score = f"{int(match.home_goals)}-{int(match.away_goals)}"
        if len(score_rank) >= 2:
            rows["exact_score"].append({
                "gap": float(score_rank[0]["probability"]) - float(score_rank[1]["probability"]),
                "top1_probability": float(score_rank[0]["probability"]),
                "hit": score_rank[0]["score"] == actual_score,
            })

        totals = sorted(_total_probs(matrix).items(), key=lambda kv: (-kv[1], kv[0]))
        actual_total = int(match.home_goals) + int(match.away_goals)
        if len(totals) >= 2:
            rows["total_goals"].append({
                "gap": totals[0][1] - totals[1][1],
                "top1_probability": totals[0][1],
                "hit": totals[0][0] == actual_total,
            })
    return {
        "competition_id": cid,
        "season": season,
        "eligible_prediction_count": len(rows["one_x_two"]),
        "skipped": skipped,
        "oof_calibration": {"temperature": temperature, "mode": mode},
        "frontiers": {key: _frontier(value) for key, value in rows.items()},
        "raw_rows": rows,
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports = {}
    failures = {}
    aggregate_rows = {"one_x_two": [], "exact_score": [], "total_goals": []}
    for cid in competitions:
        try:
            item = _domain_rows(cid)
            reports[cid] = {k: v for k, v in item.items() if k != "raw_rows"}
            for target in aggregate_rows:
                aggregate_rows[target].extend(item["raw_rows"][target])
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    payload = {
        "schema_version": "V4.7.0-four-target-selective-frontier-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "confidence_definition": "Target-specific Top-1 probability minus Top-2 probability from the same unified score matrix.",
        "aggregate_frontiers": {target: _frontier(rows) for target, rows in aggregate_rows.items()},
        "reports": reports,
        "failures": failures,
        "governance": {
            "descriptive_only": True,
            "formal_threshold_selected": False,
            "runtime_change": False,
            "formal_weight_change": False,
            "handicap_excluded_until_real_frozen_historical_lines_exist": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload["aggregate_frontiers"], ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
