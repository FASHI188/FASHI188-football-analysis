#!/usr/bin/env python3
"""Structural diagnostic for exact-score and exact-total Top-1 behavior across 17 domains.

Detects mode concentration, expected-total bias and whether Top1-Top2 confidence gaps
actually discriminate hits. Uses strict last-complete-season PIT replay. Research only.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
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
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, load_json, read_processed_matches, score_matrix_rows, top_scores

OUT = ROOT / "manifests" / "score_total_structural_diagnostic_v470_status.json"


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx, my = mean(xs), mean(ys)
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sx * sy)


def _bucket_total(t: int) -> str:
    return str(t) if t <= 6 else "7+"


def _total_probs(matrix) -> dict[int, float]:
    out: dict[int, float] = {}
    for h, a, p in score_matrix_rows(matrix):
        out[h + a] = out.get(h + a, 0.0) + float(p)
    return out


def _entropy(probs: list[float]) -> float:
    return -sum(p * math.log(max(1e-15, p)) for p in probs if p > 0)


def _domain(cid: str) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    season = _requested_last_complete_season(cid)
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"invalid selected parameters {cid} {season}")
    all_matches = read_processed_matches(cid)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    temperature, mode = _target_season_temperature(cid, season)

    score_pick = Counter()
    score_actual = Counter()
    total_pick = Counter()
    total_actual = Counter()
    total_confusion: dict[str, Counter] = defaultdict(Counter)
    score_gaps: list[float] = []
    score_hits: list[float] = []
    total_gaps: list[float] = []
    total_hits: list[float] = []
    total_entropies: list[float] = []
    expected_totals: list[float] = []
    actual_totals: list[int] = []
    skipped = 0

    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
        except PlatformError:
            skipped += 1
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)

        score_rank = top_scores(matrix, 2)
        actual_score = f"{int(match.home_goals)}-{int(match.away_goals)}"
        if len(score_rank) >= 2:
            score_pick[score_rank[0]["score"]] += 1
            score_actual[actual_score] += 1
            score_gaps.append(float(score_rank[0]["probability"]) - float(score_rank[1]["probability"]))
            score_hits.append(float(score_rank[0]["score"] == actual_score))

        totals = _total_probs(matrix)
        ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
        actual_total = int(match.home_goals) + int(match.away_goals)
        top_total = int(ranked[0][0])
        total_pick[_bucket_total(top_total)] += 1
        total_actual[_bucket_total(actual_total)] += 1
        total_confusion[_bucket_total(top_total)][_bucket_total(actual_total)] += 1
        if len(ranked) >= 2:
            total_gaps.append(float(ranked[0][1]) - float(ranked[1][1]))
            total_hits.append(float(top_total == actual_total))
        total_entropies.append(_entropy([float(v) for v in totals.values()]))
        expected_totals.append(sum(int(t) * float(p) for t, p in totals.items()))
        actual_totals.append(actual_total)

    n = len(actual_totals)
    if n <= 0:
        raise PlatformError(f"no eligible predictions {cid} {season}")
    top_score_share = max(score_pick.values()) / n if score_pick else None
    top_total_share = max(total_pick.values()) / n if total_pick else None
    return {
        "competition_id": cid,
        "season": season,
        "eligible_prediction_count": n,
        "skipped": skipped,
        "oof_calibration": {"temperature": temperature, "mode": mode},
        "score": {
            "predicted_top1_counts": dict(score_pick.most_common()),
            "actual_score_counts": dict(score_actual.most_common()),
            "unique_predicted_top1_scores": len(score_pick),
            "largest_single_predicted_score_share": top_score_share,
            "confidence_gap_hit_pearson": _pearson(score_gaps, score_hits),
            "mean_gap": mean(score_gaps) if score_gaps else None,
            "gap_std": pstdev(score_gaps) if len(score_gaps) > 1 else 0.0 if score_gaps else None,
        },
        "total_goals": {
            "predicted_top1_counts": dict(total_pick),
            "actual_counts": dict(total_actual),
            "confusion_predicted_to_actual": {k: dict(v) for k, v in total_confusion.items()},
            "unique_predicted_top1_totals": len(total_pick),
            "largest_single_predicted_total_share": top_total_share,
            "confidence_gap_hit_pearson": _pearson(total_gaps, total_hits),
            "mean_gap": mean(total_gaps) if total_gaps else None,
            "gap_std": pstdev(total_gaps) if len(total_gaps) > 1 else 0.0 if total_gaps else None,
            "mean_distribution_entropy": mean(total_entropies),
            "mean_predicted_expected_total": mean(expected_totals),
            "mean_actual_total": mean(actual_totals),
            "mean_total_bias": mean(expected_totals) - mean(actual_totals),
        },
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports = {}
    failures = {}
    aggregate_score_pick = Counter()
    aggregate_total_pick = Counter()
    aggregate_total_actual = Counter()
    weighted_total_bias_num = 0.0
    weighted_n = 0
    for cid in competitions:
        try:
            item = _domain(cid)
            reports[cid] = item
            n = int(item["eligible_prediction_count"])
            aggregate_score_pick.update(item["score"]["predicted_top1_counts"])
            aggregate_total_pick.update(item["total_goals"]["predicted_top1_counts"])
            aggregate_total_actual.update(item["total_goals"]["actual_counts"])
            weighted_total_bias_num += float(item["total_goals"]["mean_total_bias"]) * n
            weighted_n += n
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    payload = {
        "schema_version": "V4.7.0-score-total-structural-diagnostic-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "aggregate": {
            "predicted_score_top1_counts": dict(aggregate_score_pick.most_common()),
            "predicted_total_top1_counts": dict(aggregate_total_pick),
            "actual_total_counts": dict(aggregate_total_actual),
            "weighted_mean_total_bias": weighted_total_bias_num / weighted_n if weighted_n else None,
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "diagnostic_only": True,
            "formal_weight_change": False,
            "probability_change": False,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload["aggregate"], ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
