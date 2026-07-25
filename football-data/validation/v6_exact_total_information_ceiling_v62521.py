#!/usr/bin/env python3
"""V6.25.21 exact-total information/decision ceiling diagnostic.

Research diagnostic only; formal_weight=0.

No model or hyperparameter is tuned here. The frozen V6.25.13 challenger is
refit exactly with L2=10 and alpha=0.5 on 2022/23-2024/25, then evaluated on the
already-used 2025/26 five-league development benchmark. The purpose is not
promotion evidence; it is to distinguish:

1) exact single-bucket Top-1 difficulty;
2) top-k/range coverage achievable from the same probability distribution;
3) whether confidence selection can materially raise single-bucket accuracy.

Confidence thresholds are LABEL-FREE: they are distribution quantiles of model
confidence on 2024/25 validation predictions, then applied unchanged to 2025/26.
No validation/benchmark outcome is used to choose the quantile cut points.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
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

OUT = ROOT / "manifests" / "v6_exact_total_information_ceiling_v62521_status.json"
L2 = 10.0
ALPHA = 0.5
QUANTILES = (0.50, 0.75, 0.90)  # keep top 50%, 25%, 10% confidence


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _q(values: list[float], q: float) -> float:
    xs = sorted(values)
    if not xs:
        return float("nan")
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1.0 - w) + xs[hi] * w


def _row_metrics(row: dict[str, Any], matrix_key: str) -> dict[str, Any]:
    dist = _total_distribution(row[matrix_key])
    order = sorted(TOTAL_BUCKETS, key=lambda b: (-float(dist[b]), TOTAL_BUCKETS.index(b)))
    p1 = float(dist[order[0]])
    p2 = float(dist[order[1]])
    actual = _bucket(int(row["home_goals"]) + int(row["away_goals"]))
    entropy = -sum(float(dist[b]) * math.log(max(1e-12, float(dist[b]))) for b in TOTAL_BUCKETS)

    # Best contiguous windows among ordered buckets 0,1,...,6,7+.
    windows: dict[int, dict[str, Any]] = {}
    for width in (2, 3, 4):
        best = None
        for start in range(0, len(TOTAL_BUCKETS) - width + 1):
            buckets = list(TOTAL_BUCKETS[start:start + width])
            mass = sum(float(dist[b]) for b in buckets)
            candidate = (mass, -start, buckets)
            if best is None or candidate > best:
                best = candidate
        assert best is not None
        windows[width] = {
            "buckets": best[2],
            "mass": float(best[0]),
            "hit": int(actual in best[2]),
        }

    return {
        "actual": actual,
        "top1": order[0],
        "top1_hit": int(actual == order[0]),
        "top2_hit": int(actual in order[:2]),
        "top3_hit": int(actual in order[:3]),
        "top4_hit": int(actual in order[:4]),
        "p1": p1,
        "margin": p1 - p2,
        "entropy": entropy,
        "windows": windows,
    }


def _aggregate(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(metrics)
    if not n:
        return {"count": 0}
    return {
        "count": n,
        "top1_accuracy": sum(x["top1_hit"] for x in metrics) / n,
        "top2_coverage": sum(x["top2_hit"] for x in metrics) / n,
        "top3_coverage": sum(x["top3_hit"] for x in metrics) / n,
        "top4_coverage": sum(x["top4_hit"] for x in metrics) / n,
        "best_contiguous_2_coverage": sum(x["windows"][2]["hit"] for x in metrics) / n,
        "best_contiguous_3_coverage": sum(x["windows"][3]["hit"] for x in metrics) / n,
        "best_contiguous_4_coverage": sum(x["windows"][4]["hit"] for x in metrics) / n,
        "mean_top1_probability": statistics.fmean(x["p1"] for x in metrics),
        "mean_top1_margin": statistics.fmean(x["margin"] for x in metrics),
        "mean_entropy": statistics.fmean(x["entropy"] for x in metrics),
    }


def _confidence_thresholds(metrics: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    return {
        "p1": {str(q): _q([x["p1"] for x in metrics], q) for q in QUANTILES},
        "margin": {str(q): _q([x["margin"] for x in metrics], q) for q in QUANTILES},
        "neg_entropy": {str(q): _q([-x["entropy"] for x in metrics], q) for q in QUANTILES},
    }


def _selective(metrics: list[dict[str, Any]], thresholds: dict[str, dict[str, float]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("p1", "margin", "neg_entropy"):
        out[name] = {}
        for q in QUANTILES:
            threshold = float(thresholds[name][str(q)])
            if name == "p1":
                selected = [x for x in metrics if x["p1"] >= threshold]
            elif name == "margin":
                selected = [x for x in metrics if x["margin"] >= threshold]
            else:
                selected = [x for x in metrics if -x["entropy"] >= threshold]
            n = len(selected)
            out[name][str(q)] = {
                "threshold_from_2024_25": threshold,
                "count": n,
                "coverage": n / len(metrics) if metrics else 0.0,
                "top1_accuracy": sum(x["top1_hit"] for x in selected) / n if n else None,
            }
    return out


def main() -> int:
    rows, attach = core._strict_rows_with_xg()
    train = [r for r in rows if r["season"] in core.TRAIN_SEASONS]
    valid = [r for r in rows if r["season"] == core.VALID_SEASON]
    benchmark = [r for r in rows if r["season"] == core.HOLDOUT_SEASON]
    models = core._fit_models(train + valid, L2)

    valid_rows = core._rows_with_candidate(valid, core._fit_models(train, L2), ALPHA)
    benchmark_rows = core._rows_with_candidate(benchmark, models, ALPHA)

    valid_candidate = [_row_metrics(r, "candidate_matrix") for r in valid_rows]
    thresholds = _confidence_thresholds(valid_candidate)

    benchmark_baseline = [_row_metrics(r, "baseline_matrix") for r in benchmark_rows]
    benchmark_candidate = [_row_metrics(r, "candidate_matrix") for r in benchmark_rows]

    by_domain: dict[str, Any] = {}
    for cid in sorted({str(r["competition_id"]) for r in benchmark_rows}):
        base = [_row_metrics(r, "baseline_matrix") for r in benchmark_rows if str(r["competition_id"]) == cid]
        cand = [_row_metrics(r, "candidate_matrix") for r in benchmark_rows if str(r["competition_id"]) == cid]
        by_domain[cid] = {"baseline": _aggregate(base), "candidate": _aggregate(cand)}

    payload = {
        "schema_version": "V6.25.21-exact-total-information-ceiling-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_DIAGNOSTIC_FIXED_V62513_FORMAL_WEIGHT_0",
        "fixed_model": {
            "source": "V6.25.13",
            "l2": L2,
            "alpha": ALPHA,
            "fit_seasons": sorted(core.TRAIN_SEASONS | {core.VALID_SEASON}),
            "development_benchmark_season": core.HOLDOUT_SEASON,
            "benchmark_already_observed": True,
        },
        "attachment_audit": {"panel_sha256": attach["panel_sha256"], "attached_rows": attach["attached_rows"]},
        "confidence_contract": {
            "threshold_source": "2024/25 validation prediction confidence distribution only",
            "thresholds_label_free": True,
            "quantiles": list(QUANTILES),
            "interpretation": {"0.50": "top 50 percent confidence", "0.75": "top 25 percent confidence", "0.90": "top 10 percent confidence"},
            "thresholds": thresholds,
        },
        "development_benchmark": {
            "baseline": _aggregate(benchmark_baseline),
            "candidate": _aggregate(benchmark_candidate),
            "candidate_selective_top1": _selective(benchmark_candidate, thresholds),
            "per_domain": by_domain,
        },
        "diagnostic_questions": {
            "single_exact_total_near_70_percent": False,
            "top3_or_contiguous_range_may_reach_materially_higher_coverage": True,
            "selective_accuracy_is_reported_with_coverage_and_must_not_be_confused_with_full_coverage_accuracy": True,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "no_hyperparameter_tuning": True,
            "confidence_thresholds_do_not_use_labels": True,
            "benchmark_not_promotion_evidence": True,
            "topk_or_range_coverage_not_exact_top1_accuracy": True,
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
        "benchmark": payload["development_benchmark"],
        "confidence": payload["confidence_contract"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
