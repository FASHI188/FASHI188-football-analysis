#!/usr/bin/env python3
"""V5.5.33 nested selective 1X2 accuracy experiment.

Purpose
-------
Raise *reported-direction* accuracy without changing the underlying score matrix or
probabilities. The gate is selected only from chronological outer folds before the
last complete season, then evaluated once on the untouched last-complete-season
holdout across all registered domains.

This is a challenge-layer experiment. It may not mutate CURRENT, formal weights,
probabilities, score cells, market snapshots, or runtime decisions.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backtest_last_complete_season_all_domains_v470 import (
    FORMAL_STATUS,
    REPORT_ROOT,
    _actual_result,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    atomic_write_json,
    derive_score_marginals,
    load_json,
    read_processed_matches,
    score_matrix_rows,
    top_scores,
)

OUT = ROOT / "manifests" / "nested_selective_accuracy_v5533_status.json"
TARGET_ACCURACIES = (0.60, 0.65, 0.70)
DIRECTION_MASKS = {
    "all": {"home", "draw", "away"},
    "non_draw": {"home", "away"},
    "home_only": {"home"},
    "away_only": {"away"},
}
GAP_QUANTILES = (0.40, 0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.925, 0.95)
P1_QUANTILES = (0.00, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90)
ENTROPY_QUANTILES = (1.00, 0.90, 0.80, 0.70, 0.60, 0.50)
SCORE3_QUANTILES = (0.00, 0.40, 0.60, 0.75, 0.90)


def _season_order(report: dict[str, Any]) -> dict[str, int]:
    seasons = [str(s) for s in report.get("seasons") or []]
    return {season: idx for idx, season in enumerate(seasons)}


def _total_probabilities(matrix: list[dict[str, Any]]) -> list[tuple[int, float]]:
    totals: dict[int, float] = {}
    for h, a, probability in score_matrix_rows(matrix):
        totals[h + a] = totals.get(h + a, 0.0) + float(probability)
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))


def _normalized_entropy(one: dict[str, float]) -> float:
    value = 0.0
    for key in ("home", "draw", "away"):
        p = max(1e-15, float(one[key]))
        value -= p * math.log(p)
    return value / math.log(3.0)


def _prediction_rows(competition_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    report_path = REPORT_ROOT / f"{competition_id}.json"
    if not report_path.exists():
        raise PlatformError(f"missing formal report: {report_path}")
    report = load_json(report_path)
    order = _season_order(report)
    target_season = _requested_last_complete_season(competition_id)
    if target_season not in order:
        raise PlatformError(f"target season absent from report: {competition_id}:{target_season}")

    all_matches = read_processed_matches(competition_id)
    rows: list[dict[str, Any]] = []
    fold_summaries: list[dict[str, Any]] = []
    folds = sorted(
        report.get("folds") or [],
        key=lambda fold: order.get(str(fold.get("outer_season")), 10**9),
    )
    for fold in folds:
        season = str(fold.get("outer_season") or "")
        if season not in order or order[season] > order[target_season]:
            continue
        selected_parameters = fold.get("selected_parameters")
        if not isinstance(selected_parameters, dict):
            raise PlatformError(f"invalid selected parameters: {competition_id}:{season}")
        matches = sorted(
            [m for m in all_matches if str(m.season) == season],
            key=lambda m: (m.date, m.home_team, m.away_team),
        )
        temperature, calibration_mode = _target_season_temperature(competition_id, season)
        predicted = 0
        skipped = 0
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
            except PlatformError:
                skipped += 1
                continue
            if abs(temperature - 1.0) > 1e-15:
                matrix = temperature_scale_matrix(matrix, temperature)
            marginals = derive_score_marginals(matrix)
            one = {key: float(marginals["1x2"][key]) for key in ("home", "draw", "away")}
            ranking = sorted(one.items(), key=lambda item: (-item[1], item[0]))
            scores = top_scores(matrix, 3)
            score3_sum = sum(float(item["probability"]) for item in scores)
            totals = _total_probabilities(matrix)
            actual = _actual_result(int(match.home_goals), int(match.away_goals))
            rows.append(
                {
                    "competition_id": competition_id,
                    "season": season,
                    "season_rank": order[season],
                    "is_target_holdout": season == target_season,
                    "date": match.date.isoformat(),
                    "predicted_direction": ranking[0][0],
                    "actual_direction": actual,
                    "hit": ranking[0][0] == actual,
                    "top1_probability": ranking[0][1],
                    "top2_probability": ranking[1][1],
                    "gap": ranking[0][1] - ranking[1][1],
                    "entropy": _normalized_entropy(one),
                    "draw_probability": one["draw"],
                    "score_top1_probability": float(scores[0]["probability"]) if scores else 0.0,
                    "score_top3_sum": score3_sum,
                    "total_top1_probability": totals[0][1] if totals else 0.0,
                    "total_gap": totals[0][1] - totals[1][1] if len(totals) >= 2 else 0.0,
                }
            )
            predicted += 1
        fold_summaries.append(
            {
                "season": season,
                "season_rank": order[season],
                "is_target_holdout": season == target_season,
                "prediction_count": predicted,
                "skipped": skipped,
                "temperature": temperature,
                "calibration_mode": calibration_mode,
                "parameter_selection_prior_seasons": fold.get("prior_seasons") or [],
            }
        )
    return rows, {
        "competition_id": competition_id,
        "target_holdout_season": target_season,
        "folds": fold_summaries,
    }


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("quantile on empty values")
    ordered = sorted(float(v) for v in values)
    if q <= 0:
        return ordered[0]
    if q >= 1:
        return ordered[-1]
    pos = (len(ordered) - 1) * q
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return ordered[lower]
    weight = pos - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _wilson_interval(hits: int, count: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if count <= 0:
        return None, None
    p = hits / count
    z2 = z * z
    denominator = 1.0 + z2 / count
    center = (p + z2 / (2.0 * count)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * count)) / count) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _rule_applies(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    return (
        str(row["predicted_direction"]) in set(rule["directions"])
        and float(row["gap"]) + 1e-15 >= float(rule["min_gap"])
        and float(row["top1_probability"]) + 1e-15 >= float(rule["min_top1_probability"])
        and float(row["entropy"]) <= float(rule["max_entropy"]) + 1e-15
        and float(row["score_top3_sum"]) + 1e-15 >= float(rule["min_score_top3_sum"])
    )


def _stats(rows: Iterable[dict[str, Any]], rule: dict[str, Any] | None = None) -> dict[str, Any]:
    materialized = list(rows)
    selected = materialized if rule is None else [row for row in materialized if _rule_applies(row, rule)]
    count = len(selected)
    hits = sum(1 for row in selected if bool(row["hit"]))
    low, high = _wilson_interval(hits, count)
    by_direction: dict[str, dict[str, int]] = {}
    for direction in ("home", "draw", "away"):
        subset = [row for row in selected if row["predicted_direction"] == direction]
        by_direction[direction] = {
            "count": len(subset),
            "hits": sum(1 for row in subset if bool(row["hit"])),
        }
    by_competition = defaultdict(lambda: [0, 0])
    by_rank = defaultdict(lambda: [0, 0])
    for row in selected:
        by_competition[str(row["competition_id"])][0] += 1
        by_competition[str(row["competition_id"])][1] += int(bool(row["hit"]))
        by_rank[str(row["season_rank"])][0] += 1
        by_rank[str(row["season_rank"])][1] += int(bool(row["hit"]))
    return {
        "available_count": len(materialized),
        "selected_count": count,
        "coverage": count / len(materialized) if materialized else None,
        "hit_count": hits,
        "accuracy": hits / count if count else None,
        "wilson_95": {"lower": low, "upper": high},
        "by_predicted_direction": {
            key: {
                **value,
                "accuracy": value["hits"] / value["count"] if value["count"] else None,
            }
            for key, value in by_direction.items()
        },
        "by_competition": {
            key: {"count": value[0], "hits": value[1], "accuracy": value[1] / value[0]}
            for key, value in sorted(by_competition.items())
        },
        "by_training_season_rank": {
            key: {"count": value[0], "hits": value[1], "accuracy": value[1] / value[0]}
            for key, value in sorted(by_rank.items(), key=lambda item: int(item[0]))
        },
    }


def _candidate_rules(training_rows: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    gaps = [float(row["gap"]) for row in training_rows]
    p1s = [float(row["top1_probability"]) for row in training_rows]
    entropies = [float(row["entropy"]) for row in training_rows]
    score3s = [float(row["score_top3_sum"]) for row in training_rows]
    gap_thresholds = sorted({_quantile(gaps, q) for q in GAP_QUANTILES})
    p1_thresholds = sorted({_quantile(p1s, q) for q in P1_QUANTILES})
    entropy_thresholds = sorted({_quantile(entropies, q) for q in ENTROPY_QUANTILES}, reverse=True)
    score3_thresholds = sorted({_quantile(score3s, q) for q in SCORE3_QUANTILES})
    for mask_name, directions in DIRECTION_MASKS.items():
        for gap in gap_thresholds:
            for p1 in p1_thresholds:
                for entropy in entropy_thresholds:
                    for score3 in score3_thresholds:
                        yield {
                            "direction_mask": mask_name,
                            "directions": sorted(directions),
                            "min_gap": gap,
                            "min_top1_probability": p1,
                            "max_entropy": entropy,
                            "min_score_top3_sum": score3,
                        }


def _training_gate_passes(stats: dict[str, Any], target: float, total_training: int) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    count = int(stats["selected_count"])
    accuracy = stats.get("accuracy")
    lower = (stats.get("wilson_95") or {}).get("lower")
    minimum_count = max(250, int(math.ceil(total_training * 0.04)))
    if count < minimum_count:
        reasons.append(f"selected_count<{minimum_count}")
    if accuracy is None or float(accuracy) + 1e-15 < target:
        reasons.append(f"accuracy<{target:.2f}")
    if lower is None or float(lower) + 1e-15 < target - 0.04:
        reasons.append(f"wilson_lower<{target - 0.04:.2f}")

    ranks = stats.get("by_training_season_rank") or {}
    if len(ranks) < 2:
        reasons.append("fewer_than_two_training_outer_ranks")
    for rank, item in ranks.items():
        if int(item["count"]) >= 100 and float(item["accuracy"]) + 1e-15 < target - 0.10:
            reasons.append(f"rank_{rank}_accuracy<{target - 0.10:.2f}")

    competitions = stats.get("by_competition") or {}
    represented = sum(1 for item in competitions.values() if int(item["count"]) >= 10)
    if represented < 10:
        reasons.append("fewer_than_10_competitions_with_10_selections")
    stable_competition_accuracies = [
        float(item["accuracy"]) for item in competitions.values() if int(item["count"]) >= 20
    ]
    if stable_competition_accuracies:
        ordered = sorted(stable_competition_accuracies)
        lower_quartile = ordered[max(0, int(math.floor((len(ordered) - 1) * 0.25)))]
        if lower_quartile < 0.45:
            reasons.append("competition_lower_quartile_accuracy<0.45")
    return not reasons, reasons


def _select_rules(training_rows: list[dict[str, Any]]) -> dict[str, Any]:
    best: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    evaluated = 0
    passing = Counter()
    for rule in _candidate_rules(training_rows):
        evaluated += 1
        stats = _stats(training_rows, rule)
        for target in TARGET_ACCURACIES:
            key = f"{target:.2f}"
            ok, _ = _training_gate_passes(stats, target, len(training_rows))
            if not ok:
                continue
            passing[key] += 1
            incumbent = best.get(key)
            score = (
                int(stats["selected_count"]),
                float((stats.get("wilson_95") or {}).get("lower") or -1.0),
                float(stats.get("accuracy") or -1.0),
                -len(rule["directions"]),
            )
            incumbent_score = None
            if incumbent is not None:
                incumbent_stats = incumbent[1]
                incumbent_rule = incumbent[0]
                incumbent_score = (
                    int(incumbent_stats["selected_count"]),
                    float((incumbent_stats.get("wilson_95") or {}).get("lower") or -1.0),
                    float(incumbent_stats.get("accuracy") or -1.0),
                    -len(incumbent_rule["directions"]),
                )
            if incumbent is None or score > incumbent_score:
                best[key] = (rule, stats)
    return {
        "candidate_count_evaluated": evaluated,
        "passing_candidate_count_by_target": dict(passing),
        "selected": {
            key: {"rule": rule, "training": stats}
            for key, (rule, stats) in sorted(best.items())
        },
    }


def _evaluate_holdout(
    selected: dict[str, Any],
    holdout_rows: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    baseline_accuracy = float(baseline["accuracy"])
    for target_key, item in selected.items():
        stats = _stats(holdout_rows, item["rule"])
        improvement = (
            float(stats["accuracy"]) - baseline_accuracy if stats.get("accuracy") is not None else None
        )
        lower = (stats.get("wilson_95") or {}).get("lower")
        pass_reasons: list[str] = []
        if int(stats["selected_count"]) < 200:
            pass_reasons.append("holdout_selected_count<200")
        if float(stats.get("coverage") or 0.0) < 0.03:
            pass_reasons.append("holdout_coverage<3%")
        if stats.get("accuracy") is None or float(stats["accuracy"]) < 0.60:
            pass_reasons.append("holdout_accuracy<60%")
        if improvement is None or improvement < 0.05:
            pass_reasons.append("improvement<5pp")
        if lower is None or float(lower) <= baseline_accuracy:
            pass_reasons.append("wilson_lower_not_above_baseline")
        out[target_key] = {
            "rule": item["rule"],
            "training": item["training"],
            "untouched_holdout": stats,
            "baseline_accuracy": baseline_accuracy,
            "accuracy_improvement_pp": improvement * 100.0 if improvement is not None else None,
            "challenge_gate_passed": not pass_reasons,
            "challenge_gate_fail_reasons": pass_reasons,
        }
    return out


def main() -> int:
    formal_status = load_json(FORMAL_STATUS)
    competitions = sorted((formal_status.get("reports") or {}).keys())
    all_rows: list[dict[str, Any]] = []
    domain_audit: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for competition_id in competitions:
        try:
            rows, audit = _prediction_rows(competition_id)
            all_rows.extend(rows)
            domain_audit[competition_id] = audit
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    training_rows = [row for row in all_rows if not bool(row["is_target_holdout"])]
    holdout_rows = [row for row in all_rows if bool(row["is_target_holdout"])]
    baseline_training = _stats(training_rows)
    baseline_holdout = _stats(holdout_rows)

    selection = _select_rules(training_rows) if training_rows and holdout_rows and not failures else {
        "candidate_count_evaluated": 0,
        "passing_candidate_count_by_target": {},
        "selected": {},
    }
    evaluations = _evaluate_holdout(selection["selected"], holdout_rows, baseline_holdout)
    passed = [key for key, item in evaluations.items() if item["challenge_gate_passed"]]
    recommended = None
    if passed:
        recommended_key = max(
            passed,
            key=lambda key: (
                float(evaluations[key]["untouched_holdout"]["accuracy"]),
                int(evaluations[key]["untouched_holdout"]["selected_count"]),
            ),
        )
        recommended = {"target": recommended_key, **evaluations[recommended_key]}

    payload = {
        "schema_version": "V5.5.33-nested-selective-accuracy-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": (
            "PASS_CHALLENGE_CANDIDATE_AVAILABLE"
            if recommended is not None and not failures
            else "PASS_NO_PROMOTABLE_CANDIDATE"
            if not failures
            else "PARTIAL"
        ),
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(domain_audit),
        "failures": failures,
        "method": {
            "selection_data": "all chronological outer folds strictly before each domain's last complete season",
            "holdout_data": "each domain's untouched last complete season",
            "candidate_features": [
                "1X2 Top1-Top2 gap",
                "1X2 Top1 probability",
                "normalized 1X2 entropy",
                "unified-matrix Top3 score concentration",
                "predicted direction mask",
            ],
            "candidate_threshold_source": "training quantiles only",
            "target_holdout_used_for_threshold_selection": False,
            "historical_odds_used": False,
            "market_coordination_used": False,
            "probability_mutation": False,
        },
        "row_counts": {
            "training_outer_fold_predictions": len(training_rows),
            "untouched_holdout_predictions": len(holdout_rows),
        },
        "baseline": {
            "training": baseline_training,
            "untouched_holdout": baseline_holdout,
        },
        "selection": selection,
        "evaluations": evaluations,
        "recommended_challenge_candidate": recommended,
        "domain_audit": domain_audit,
        "governance": {
            "challenge_layer_only": True,
            "automatic_runtime_activation": False,
            "formal_model_promotion": False,
            "formal_weight_change": False,
            "probability_change": False,
            "current_rule_change": False,
            "next_requirement_if_candidate_passes": (
                "repeat across multiple rolling terminal seasons and compare Log Score, Brier, RPS, "
                "coverage, direction mix and cross-domain stability before any formal activation"
            ),
        },
    }
    atomic_write_json(OUT, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "training_rows": len(training_rows),
                "holdout_rows": len(holdout_rows),
                "baseline_holdout_accuracy": baseline_holdout.get("accuracy"),
                "selected_targets": list(selection["selected"]),
                "recommended": recommended,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
