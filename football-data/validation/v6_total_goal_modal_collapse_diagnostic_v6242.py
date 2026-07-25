#!/usr/bin/env python3
"""V6.24.2 strict-PIT total-goal modal-collapse diagnostic.

Research only. Diagnoses whether exact-total Top-1 concentration is caused by
compressed mean totals, excessive concentration, or merely a decision-level
mode effect. No probability model is changed by this script.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backtest_last_complete_season_all_domains_v470 import FORMAL_STATUS  # noqa: E402
from platform_core import load_json, score_matrix_rows  # noqa: E402
from v6_team_regime_state_random100_v6240 import _collect_competition  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_goal_modal_collapse_diagnostic_v6242_status.json"
BUCKETS = ("0", "1", "2", "3", "4", "5", "6", "7+")
EPS = 1e-15


def _dist(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {key: 0.0 for key in BUCKETS}
    for h, a, p in score_matrix_rows(matrix):
        total = int(h + a)
        key = str(total) if total <= 6 else "7+"
        out[key] += float(p)
    return out


def _entropy(prob: dict[str, float]) -> float:
    return -sum(p * math.log(max(EPS, p)) for p in prob.values() if p > 0.0)


def _mean_var_from_matrix(matrix: list[dict[str, Any]]) -> tuple[float, float]:
    rows = [(int(h + a), float(p)) for h, a, p in score_matrix_rows(matrix)]
    mean = sum(t * p for t, p in rows)
    var = sum(((t - mean) ** 2) * p for t, p in rows)
    return mean, var


def _quantiles(values: list[float], probs: tuple[float, ...]) -> dict[str, float]:
    xs = sorted(values)
    if not xs:
        return {}
    result: dict[str, float] = {}
    n = len(xs)
    for q in probs:
        pos = q * (n - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        frac = pos - lo
        value = xs[lo] * (1.0 - frac) + xs[hi] * frac
        result[f"q{int(q*100):02d}"] = value
    return result


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    actual = Counter()
    top1 = Counter()
    second = Counter()
    mean_prob = Counter()
    mean_totals: list[float] = []
    variances: list[float] = []
    entropies: list[float] = []
    top1_probs: list[float] = []
    margins: list[float] = []
    rows_scored = 0
    by_domain: dict[str, Any] = {}
    failures: dict[str, str] = {}

    for cid in competitions:
        try:
            rows, _ = _collect_competition(cid)
            d_actual = Counter()
            d_top1 = Counter()
            d_means: list[float] = []
            for row in rows:
                matrix = row["baseline_matrix"]
                prob = _dist(matrix)
                ranking = sorted(prob.items(), key=lambda kv: kv[1], reverse=True)
                total = int(row["home_goals"]) + int(row["away_goals"])
                actual_key = str(total) if total <= 6 else "7+"
                actual[actual_key] += 1
                d_actual[actual_key] += 1
                top1[ranking[0][0]] += 1
                d_top1[ranking[0][0]] += 1
                second[ranking[1][0]] += 1
                for key, value in prob.items():
                    mean_prob[key] += float(value)
                mu, var = _mean_var_from_matrix(matrix)
                mean_totals.append(mu)
                d_means.append(mu)
                variances.append(var)
                entropies.append(_entropy(prob))
                top1_probs.append(float(ranking[0][1]))
                margins.append(float(ranking[0][1] - ranking[1][1]))
                rows_scored += 1
            by_domain[cid] = {
                "count": len(rows),
                "actual_total_counts": dict(d_actual),
                "top1_bucket_counts": dict(d_top1),
                "mean_total_quantiles": _quantiles(d_means, (0.05, 0.25, 0.50, 0.75, 0.95)),
            }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if rows_scored <= 0:
        raise RuntimeError("no eligible strict-PIT rows")

    mean_prob_norm = {key: mean_prob[key] / rows_scored for key in BUCKETS}
    actual_rate = {key: actual[key] / rows_scored for key in BUCKETS}
    top1_rate = {key: top1[key] / rows_scored for key in BUCKETS}
    payload = {
        "schema_version": "V6.24.2-total-goal-modal-collapse-diagnostic-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_DIAGNOSTIC_FORMAL_WEIGHT_0",
        "eligible_prediction_count": rows_scored,
        "actual_total_counts": dict(actual),
        "actual_total_rates": actual_rate,
        "mean_predicted_total_probabilities": mean_prob_norm,
        "top1_bucket_counts": dict(top1),
        "top1_bucket_rates": top1_rate,
        "second_choice_bucket_counts": dict(second),
        "matrix_mean_total_quantiles": _quantiles(mean_totals, (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)),
        "matrix_variance_quantiles": _quantiles(variances, (0.05, 0.25, 0.50, 0.75, 0.95)),
        "bucket_entropy_quantiles": _quantiles(entropies, (0.05, 0.25, 0.50, 0.75, 0.95)),
        "top1_probability_quantiles": _quantiles(top1_probs, (0.05, 0.25, 0.50, 0.75, 0.95)),
        "top1_minus_second_margin_quantiles": _quantiles(margins, (0.05, 0.25, 0.50, 0.75, 0.95)),
        "diagnostic_flags": {
            "top1_two_goal_rate": top1_rate.get("2", 0.0),
            "actual_two_goal_rate": actual_rate.get("2", 0.0),
            "top1_mode_excess_two_vs_actual": top1_rate.get("2", 0.0) - actual_rate.get("2", 0.0),
            "mean_total_iqr": _quantiles(mean_totals, (0.25, 0.75)).get("q75", 0.0) - _quantiles(mean_totals, (0.25, 0.75)).get("q25", 0.0),
            "median_top1_margin": _quantiles(margins, (0.50,)).get("q50", 0.0),
        },
        "by_domain": by_domain,
        "failures": failures,
        "governance": {
            "probability_model_changed": False,
            "target_results_used_for_prediction": False,
            "historical_odds_used": False,
            "formal_weight": 0,
            "current_rule_change": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "eligible_prediction_count": rows_scored,
        "actual_total_rates": actual_rate,
        "mean_predicted_total_probabilities": mean_prob_norm,
        "top1_bucket_rates": top1_rate,
        "matrix_mean_total_quantiles": payload["matrix_mean_total_quantiles"],
        "diagnostic_flags": payload["diagnostic_flags"],
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
