#!/usr/bin/env python3
"""V6.25.22 split-conformal total-goal prediction-set diagnostic.

Research only; formal_weight=0.

This does NOT claim to improve single exact-total Top-1 accuracy. V6.25.21 showed
that the fixed V6.25.13 distribution reaches ~24% Top-1, ~66% Top-3 and ~79%
Top-4 coverage on the already-used 2025/26 development benchmark. The present
module asks a different, decision-useful question:

    What set size is required for honest 70/80/90% total-goal coverage?

Method
------
- base predictive distribution: fixed V6.25.13 (L2=10, alpha=0.5);
- model fit: 2022/23 + 2023/24;
- split-conformal calibration: 2024/25 only;
- development evaluation: 2025/26 (already observed; never promotion evidence);
- conformity score: APS cumulative probability rank of the realized total-goal
  bucket after sorting buckets by descending predicted probability;
- finite-sample split-conformal quantile uses ceil((n+1)*(1-alpha))/n order;
- prediction set contains highest-probability buckets until cumulative mass
  reaches the frozen conformal threshold;
- a contiguous display interval is the hull of the APS set on ordered buckets
  0,1,2,3,4,5,6,7+; hull coverage is reported separately and never confused
  with exact Top-1 or raw APS-set coverage.

No outcome from 2025/26 is used to select thresholds or tune the model.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_xg_ordinal_residual_v62513 as core  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _total_distribution  # noqa: E402

core.true = True
core.false = False

OUT = ROOT / "manifests" / "v6_total_conformal_prediction_set_v62522_status.json"
L2 = 10.0
ALPHA_MODEL = 0.5
TARGET_COVERAGES = (0.70, 0.80, 0.90)
BUCKET_INDEX = {bucket: i for i, bucket in enumerate(TOTAL_BUCKETS)}
EPS = 1e-12


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _ranked(dist: dict[str, float]) -> list[str]:
    return sorted(TOTAL_BUCKETS, key=lambda b: (-float(dist[b]), BUCKET_INDEX[b]))


def _aps_score(dist: dict[str, float], actual: str) -> float:
    cumulative = 0.0
    for bucket in _ranked(dist):
        cumulative += float(dist[bucket])
        if bucket == actual:
            return min(1.0, cumulative)
    raise RuntimeError(f"actual bucket missing: {actual}")


def _finite_sample_quantile(scores: list[float], coverage: float) -> float:
    if not scores:
        raise RuntimeError("empty calibration scores")
    xs = sorted(float(x) for x in scores)
    # Standard split-conformal finite-sample rank: ceil((n+1)*(1-alpha)).
    rank = int(math.ceil((len(xs) + 1) * float(coverage)))
    rank = min(len(xs), max(1, rank))
    return xs[rank - 1]


def _prediction_set(dist: dict[str, float], threshold: float) -> list[str]:
    selected: list[str] = []
    cumulative = 0.0
    for bucket in _ranked(dist):
        selected.append(bucket)
        cumulative += float(dist[bucket])
        if cumulative + EPS >= threshold:
            break
    return selected


def _hull(selected: list[str]) -> list[str]:
    if not selected:
        return []
    indices = [BUCKET_INDEX[b] for b in selected]
    return list(TOTAL_BUCKETS[min(indices):max(indices) + 1])


def _metrics(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    records = []
    set_sizes = Counter()
    hull_sizes = Counter()
    aps_hits = 0
    hull_hits = 0
    top1_hits = 0
    total_mass = []
    hull_mass = []
    for row in rows:
        dist = _total_distribution(row["candidate_matrix"])
        actual = _bucket(int(row["home_goals"]) + int(row["away_goals"]))
        selected = _prediction_set(dist, threshold)
        hull = _hull(selected)
        ranked = _ranked(dist)
        hit = actual in selected
        hull_hit = actual in hull
        top1_hit = actual == ranked[0]
        aps_hits += int(hit)
        hull_hits += int(hull_hit)
        top1_hits += int(top1_hit)
        set_sizes[len(selected)] += 1
        hull_sizes[len(hull)] += 1
        total_mass.append(sum(float(dist[b]) for b in selected))
        hull_mass.append(sum(float(dist[b]) for b in hull))
        records.append({
            "competition_id": str(row["competition_id"]),
            "actual": actual,
            "top1": ranked[0],
            "aps_set": selected,
            "contiguous_hull": hull,
            "aps_hit": hit,
            "hull_hit": hull_hit,
        })
    n = len(rows)
    return {
        "count": n,
        "exact_top1_accuracy": top1_hits / n if n else None,
        "aps_set_coverage": aps_hits / n if n else None,
        "contiguous_hull_coverage": hull_hits / n if n else None,
        "mean_aps_set_size": sum(k * v for k, v in set_sizes.items()) / n if n else None,
        "median_aps_set_size": statistics.median([len(r["aps_set"]) for r in records]) if records else None,
        "mean_contiguous_hull_size": sum(k * v for k, v in hull_sizes.items()) / n if n else None,
        "median_contiguous_hull_size": statistics.median([len(r["contiguous_hull"]) for r in records]) if records else None,
        "mean_aps_probability_mass": statistics.fmean(total_mass) if total_mass else None,
        "mean_hull_probability_mass": statistics.fmean(hull_mass) if hull_mass else None,
        "aps_set_size_counts": {str(k): int(v) for k, v in sorted(set_sizes.items())},
        "hull_size_counts": {str(k): int(v) for k, v in sorted(hull_sizes.items())},
        "records": records,
    }


def _without_records(metric: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metric.items() if k != "records"}


def main() -> int:
    rows, attach = core._strict_rows_with_xg()
    train = [r for r in rows if r["season"] in core.TRAIN_SEASONS]
    calibration = [r for r in rows if r["season"] == core.VALID_SEASON]
    benchmark = [r for r in rows if r["season"] == core.HOLDOUT_SEASON]
    if min(len(train), len(calibration), len(benchmark)) < 900:
        raise RuntimeError(f"insufficient split train={len(train)} calibration={len(calibration)} benchmark={len(benchmark)}")

    calibration_model = core._fit_models(train, L2)
    calibration_rows = core._rows_with_candidate(calibration, calibration_model, ALPHA_MODEL)
    final_model = core._fit_models(train + calibration, L2)
    benchmark_rows = core._rows_with_candidate(benchmark, final_model, ALPHA_MODEL)

    calibration_scores = []
    for row in calibration_rows:
        dist = _total_distribution(row["candidate_matrix"])
        actual = _bucket(int(row["home_goals"]) + int(row["away_goals"]))
        calibration_scores.append(_aps_score(dist, actual))

    targets: dict[str, Any] = {}
    for coverage in TARGET_COVERAGES:
        threshold = _finite_sample_quantile(calibration_scores, coverage)
        calibration_metric = _metrics(calibration_rows, threshold)
        benchmark_metric = _metrics(benchmark_rows, threshold)
        per_domain: dict[str, Any] = {}
        for cid in sorted({str(r["competition_id"]) for r in benchmark_rows}):
            subset = [r for r in benchmark_rows if str(r["competition_id"]) == cid]
            per_domain[cid] = _without_records(_metrics(subset, threshold))
        targets[str(coverage)] = {
            "conformal_threshold_from_2024_25": threshold,
            "calibration": _without_records(calibration_metric),
            "development_benchmark": _without_records(benchmark_metric),
            "per_domain": per_domain,
        }

    payload = {
        "schema_version": "V6.25.22-split-conformal-total-prediction-set-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_DIAGNOSTIC_CONFORMAL_TOTAL_SET_FORMAL_WEIGHT_0",
        "fixed_model": {
            "source": "V6.25.13",
            "l2": L2,
            "alpha": ALPHA_MODEL,
            "fit_for_calibration": sorted(core.TRAIN_SEASONS),
            "calibration_season": core.VALID_SEASON,
            "development_benchmark_season": core.HOLDOUT_SEASON,
            "benchmark_already_observed": True,
        },
        "attachment_audit": {"panel_sha256": attach["panel_sha256"], "attached_rows": attach["attached_rows"]},
        "method": {
            "conformity_score": "APS cumulative probability rank of realized bucket",
            "finite_sample_quantile": "ceil((n+1)*target_coverage) order statistic",
            "prediction_set": "highest-probability buckets until frozen conformal threshold reached",
            "display_interval": "ordered-bucket convex hull of APS set",
            "top1_not_redefined": True,
        },
        "target_coverages": targets,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "single_exact_total_accuracy_unchanged_by_set_reporting": True,
            "aps_set_coverage_must_not_be_called_exact_accuracy": True,
            "contiguous_hull_coverage_must_not_be_called_exact_accuracy": True,
            "benchmark_not_promotion_evidence": True,
            "no_benchmark_outcome_used_for_thresholds": True,
            "automatic_promotion": False,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "targets": payload["target_coverages"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
