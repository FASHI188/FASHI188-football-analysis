#!/usr/bin/env python3
"""V5.5.34 all-domain forward-frozen selective 1X2 gate validation.

For every registered competition, choose a simple auditable rule (prediction direction
mask plus Top1-Top2 probability-gap threshold) using strictly prior completed outer
seasons. Freeze it, evaluate on the next unseen season, and repeat. Only competitions
with stable multi-window forward performance are challenge candidates.

No probability, score matrix, formal weight, CURRENT file, or runtime output is changed.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backtest_last_complete_season_all_domains_v470 import (
    FORMAL_STATUS,
    REPORT_ROOT,
    CALENDAR_YEAR_DOMAINS,
    _actual_result,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, derive_score_marginals, load_json, read_processed_matches

OUT = ROOT / "manifests" / "domain_selective_forward_v5534_status.json"
GAP_THRESHOLDS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)
DIRECTION_MASKS = {
    "home_only": {"home"},
    "non_draw": {"home", "away"},
    "away_only": {"away"},
}


def _season_year(season: str) -> int:
    return int(str(season)[:4])


def _next_season(cid: str, completed: str) -> str:
    year = _season_year(completed)
    if cid in CALENDAR_YEAR_DOMAINS:
        return str(year + 1)
    return f"{year + 1}/{str((year + 2) % 100).zfill(2)}"


def _wilson(hits: int, n: int, z: float = 1.959963984540054) -> dict[str, float | None]:
    if n <= 0:
        return {"lower": None, "upper": None}
    p = hits / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return {"lower": max(0.0, center - margin), "upper": min(1.0, center + margin)}


def _completed_seasons(cid: str, report: dict[str, Any]) -> list[str]:
    max_year = _season_year(_requested_last_complete_season(cid))
    seasons: list[str] = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and _season_year(season) <= max_year and season not in seasons:
            seasons.append(season)
    seasons.sort(key=_season_year)
    return seasons


def _season_rows(cid: str, report: dict[str, Any], all_matches, season: str) -> list[dict[str, Any]]:
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"missing selected parameters {cid}:{season}")
    temperature, calibration_mode = _target_season_temperature(cid, season)
    matches = sorted(
        [m for m in all_matches if str(m.season) == season],
        key=lambda m: (m.date, m.home_team, m.away_team),
    )
    rows: list[dict[str, Any]] = []
    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(
                all_matches,
                match.home_team,
                match.away_team,
                match.date,
                season,
                params,
            )
        except PlatformError:
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        one = derive_score_marginals(matrix)["1x2"]
        ranking = sorted(
            ((key, float(one[key])) for key in ("home", "draw", "away")),
            key=lambda item: (-item[1], item[0]),
        )
        actual = _actual_result(int(match.home_goals), int(match.away_goals))
        rows.append(
            {
                "predicted_direction": ranking[0][0],
                "gap": ranking[0][1] - ranking[1][1],
                "top1_probability": ranking[0][1],
                "hit": int(ranking[0][0] == actual),
                "calibration_mode": calibration_mode,
            }
        )
    return rows


def _applies(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    return (
        str(row["predicted_direction"]) in set(rule["directions"])
        and float(row["gap"]) + 1e-15 >= float(rule["min_gap"])
    )


def _rule_stats(rows_by_season: dict[str, list[dict[str, Any]]], rule: dict[str, Any]) -> dict[str, Any]:
    seasons: list[dict[str, Any]] = []
    total_n = total_hits = 0
    for season, rows in rows_by_season.items():
        selected = [row for row in rows if _applies(row, rule)]
        n = len(selected)
        hits = sum(int(row["hit"]) for row in selected)
        total_n += n
        total_hits += hits
        seasons.append(
            {
                "season": season,
                "eligible_predictions": len(rows),
                "selected_count": n,
                "coverage": n / len(rows) if rows else None,
                "hit_count": hits,
                "accuracy": hits / n if n else None,
            }
        )
    accuracies = [float(item["accuracy"]) for item in seasons if item["accuracy"] is not None]
    return {
        "rule": rule,
        "selected_count": total_n,
        "hit_count": total_hits,
        "accuracy": total_hits / total_n if total_n else None,
        "wilson_95": _wilson(total_hits, total_n),
        "min_season_selected": min((int(item["selected_count"]) for item in seasons), default=0),
        "min_season_accuracy": min(accuracies) if accuracies else None,
        "season_accuracy_std": pstdev(accuracies) if len(accuracies) > 1 else 0.0 if accuracies else None,
        "seasons": seasons,
    }


def _select_rule(prior_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for mask_name, directions in DIRECTION_MASKS.items():
        for threshold in GAP_THRESHOLDS:
            rule = {
                "direction_mask": mask_name,
                "directions": sorted(directions),
                "min_gap": threshold,
            }
            stats = _rule_stats(prior_rows, rule)
            qualifies = (
                int(stats["selected_count"]) >= 60
                and int(stats["min_season_selected"]) >= 12
                and stats["accuracy"] is not None
                and float(stats["accuracy"]) >= 0.68
                and stats["min_season_accuracy"] is not None
                and float(stats["min_season_accuracy"]) >= 0.58
                and stats["season_accuracy_std"] is not None
                and float(stats["season_accuracy_std"]) <= 0.12
            )
            if qualifies:
                candidates.append(stats)
    if not candidates:
        return None
    # Training-only choice: maximize stable coverage, then Wilson lower bound, then accuracy.
    candidates.sort(
        key=lambda item: (
            int(item["selected_count"]),
            float((item.get("wilson_95") or {}).get("lower") or -1.0),
            float(item.get("accuracy") or -1.0),
            1 if item["rule"]["direction_mask"] == "home_only" else 0,
        ),
        reverse=True,
    )
    return candidates[0]


def _evaluate_target(rows: list[dict[str, Any]], rule: dict[str, Any]) -> dict[str, Any]:
    selected = [row for row in rows if _applies(row, rule)]
    n = len(selected)
    hits = sum(int(row["hit"]) for row in selected)
    return {
        "eligible_predictions": len(rows),
        "selected_count": n,
        "coverage": n / len(rows) if rows else None,
        "hit_count": hits,
        "accuracy": hits / n if n else None,
        "wilson_95": _wilson(hits, n),
        "direction_counts": {
            direction: sum(1 for row in selected if row["predicted_direction"] == direction)
            for direction in ("home", "draw", "away")
        },
    }


def _validate_domain(cid: str) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    seasons = _completed_seasons(cid, report)
    if len(seasons) < 3:
        raise PlatformError(f"need at least three completed outer seasons for {cid}")
    all_matches = read_processed_matches(cid)
    cache = {season: _season_rows(cid, report, all_matches, season) for season in seasons}

    forward_folds: list[dict[str, Any]] = []
    for index in range(2, len(seasons)):
        target = seasons[index]
        prior = seasons[:index]
        selected = _select_rule({season: cache[season] for season in prior})
        if selected is None:
            forward_folds.append(
                {
                    "target_season": target,
                    "training_seasons": prior,
                    "selection_status": "NO_PRIOR_QUALIFYING_RULE",
                    "selected_rule": None,
                    "evaluation": None,
                }
            )
            continue
        forward_folds.append(
            {
                "target_season": target,
                "training_seasons": prior,
                "selection_status": "FROZEN_FROM_STRICTLY_PRIOR_SEASONS",
                "selected_rule": selected["rule"],
                "prior_selection_stats": selected,
                "evaluation": _evaluate_target(cache[target], selected["rule"]),
            }
        )

    evaluated = [fold for fold in forward_folds if isinstance(fold.get("evaluation"), dict) and fold["evaluation"].get("accuracy") is not None]
    pooled_n = sum(int(fold["evaluation"]["selected_count"]) for fold in evaluated)
    pooled_hits = sum(int(fold["evaluation"]["hit_count"]) for fold in evaluated)
    fold_accuracies = [float(fold["evaluation"]["accuracy"]) for fold in evaluated]
    pooled_accuracy = pooled_hits / pooled_n if pooled_n else None
    pooled_ci = _wilson(pooled_hits, pooled_n)
    checks = {
        "at_least_two_forward_folds": len(evaluated) >= 2,
        "pooled_selected_at_least_60": pooled_n >= 60,
        "pooled_accuracy_at_least_68pct": pooled_accuracy is not None and pooled_accuracy >= 0.68,
        "pooled_wilson_lower_at_least_60pct": pooled_ci["lower"] is not None and float(pooled_ci["lower"]) >= 0.60,
        "minimum_forward_fold_accuracy_at_least_60pct": bool(fold_accuracies) and min(fold_accuracies) >= 0.60,
        "forward_accuracy_std_at_most_10pp": len(fold_accuracies) > 1 and pstdev(fold_accuracies) <= 0.10,
        "every_evaluated_fold_selected_at_least_10": bool(evaluated) and all(int(fold["evaluation"]["selected_count"]) >= 10 for fold in evaluated),
    }
    candidate = all(checks.values())

    final_rule = _select_rule(cache) if candidate else None
    return {
        "competition_id": cid,
        "completed_seasons": seasons,
        "status": "FORWARD_STABLE_SELECTIVE_CANDIDATE" if candidate else "KEEP_WEIGHT_0",
        "forward_folds": forward_folds,
        "forward_summary": {
            "evaluated_fold_count": len(evaluated),
            "pooled_selected_count": pooled_n,
            "pooled_hit_count": pooled_hits,
            "pooled_accuracy": pooled_accuracy,
            "pooled_wilson_95": pooled_ci,
            "minimum_fold_accuracy": min(fold_accuracies) if fold_accuracies else None,
            "accuracy_std": pstdev(fold_accuracies) if len(fold_accuracies) > 1 else None,
            "checks": checks,
        },
        "next_season_frozen_rule": {
            "target_season": _next_season(cid, seasons[-1]),
            "selection_data_seasons": seasons,
            "selection": final_rule,
            "target_season_outcomes_used": False,
        } if final_rule is not None else None,
        "formal_weight": 0,
        "automatic_activation": False,
        "probability_change": False,
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    candidates: list[str] = []
    for cid in competitions:
        try:
            item = _validate_domain(cid)
            reports[cid] = item
            if item["status"] == "FORWARD_STABLE_SELECTIVE_CANDIDATE":
                candidates.append(cid)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    payload = {
        "schema_version": "V5.5.34-all-domain-forward-selective-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "forward_stable_candidates": candidates,
        "reports": reports,
        "failures": failures,
        "governance": {
            "strictly_prior_season_rule_selection": True,
            "target_fold_outcomes_used_for_rule_selection": False,
            "challenge_layer_only": True,
            "automatic_runtime_activation": False,
            "formal_model_promotion": False,
            "formal_weight_change": False,
            "probability_change": False,
            "current_rule_change": False,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({"status": payload["status"], "candidates": candidates, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
