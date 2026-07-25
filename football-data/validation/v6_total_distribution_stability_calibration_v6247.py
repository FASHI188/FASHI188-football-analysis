#!/usr/bin/env python3
"""V6.24.7 cross-season-stability weighted PIT total calibration.

Research only; formal_weight=0.

Builds on V6.24.6. For each total-goal bucket, compute the direction of the
baseline residual separately in every strictly prior training season. The
one-season-prior shrunk aggregate correction is attenuated by sign consistency:

  consistency = |#seasons factor>1 - #seasons factor<1| / season_count
  final_factor = exp(consistency * log(shrunk_factor))

Unanimous historical direction keeps the shrunk correction. Split historical
seasons drive the correction toward 1. No target-season outcome is used.
"""
from __future__ import annotations

import json
import math
import random
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

from backtest_last_complete_season_all_domains_v470 import (  # noqa: E402
    FORMAL_STATUS,
    REPORT_ROOT,
    _fold_for_season,
    _requested_last_complete_season,
)
from football_v460_engine import _merge_parameters, load_config  # noqa: E402
from platform_core import PlatformError, load_json  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _delta  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import (  # noqa: E402
    _calibrate_matrix,
    _score,
    _season_rows,
    _top1_counts,
    _total_distribution,
)

OUT = ROOT / "manifests" / "v6_total_distribution_stability_calibration_v6247_status.json"
SEED = 20260725
SAMPLE_N = 100


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _season_stats(rows: list[dict[str, Any]]) -> tuple[Counter, Counter]:
    actual = Counter()
    expected = Counter()
    for row in rows:
        actual[_bucket(int(row["home_goals"]) + int(row["away_goals"]))] += 1
        dist = _total_distribution(row["matrix"])
        for key in TOTAL_BUCKETS:
            expected[key] += float(dist[key])
    return actual, expected


def _fit(training_by_season: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, float], dict[str, Any]]:
    if not training_by_season:
        raise PlatformError("no prior-season PIT training rows")
    aggregate_actual = Counter()
    aggregate_expected = Counter()
    raw_factors_by_season: dict[str, dict[str, float]] = {}
    for season, rows in sorted(training_by_season.items()):
        actual, expected = _season_stats(rows)
        aggregate_actual.update(actual)
        aggregate_expected.update(expected)
        raw_factors_by_season[season] = {
            key: (float(actual[key]) + 1.0) / (float(expected[key]) + 1.0)
            for key in TOTAL_BUCKETS
        }

    season_count = len(training_by_season)
    shrunk: dict[str, float] = {}
    consistency: dict[str, float] = {}
    final: dict[str, float] = {}
    direction_counts: dict[str, Any] = {}
    for key in TOTAL_BUCKETS:
        prior = max(1e-9, float(aggregate_expected[key]) / season_count)
        shrunk_factor = (float(aggregate_actual[key]) + prior) / (float(aggregate_expected[key]) + prior)
        pos = sum(1 for f in raw_factors_by_season.values() if f[key] > 1.0)
        neg = sum(1 for f in raw_factors_by_season.values() if f[key] < 1.0)
        tie = season_count - pos - neg
        c = abs(pos - neg) / max(1, season_count)
        factor = math.exp(c * math.log(max(1e-12, shrunk_factor)))
        shrunk[key] = shrunk_factor
        consistency[key] = c
        final[key] = factor
        direction_counts[key] = {"above_one": pos, "below_one": neg, "exact_one": tie}

    return final, {
        "training_season_count": season_count,
        "training_prediction_count": sum(len(v) for v in training_by_season.values()),
        "raw_factors_by_season": raw_factors_by_season,
        "direction_counts": direction_counts,
        "sign_consistency": consistency,
        "one_season_prior_shrunk_factors": shrunk,
        "final_factors": final,
    }


def _domain(cid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = load_config()
    report = load_json(REPORT_ROOT / f"{cid}.json")
    target_season = _requested_last_complete_season(cid)
    target_fold = _fold_for_season(report, target_season)
    target_selected = target_fold.get("selected_parameters")
    if not isinstance(target_selected, dict):
        raise PlatformError(f"invalid target parameters for {cid} {target_season}")
    target_params = _merge_parameters(config, target_selected)

    training_by_season: dict[str, list[dict[str, Any]]] = {}
    for season in target_fold.get("prior_seasons") or []:
        try:
            fold = _fold_for_season(report, str(season))
        except Exception:
            continue
        selected = fold.get("selected_parameters")
        if not isinstance(selected, dict):
            continue
        params = _merge_parameters(config, selected)
        rows, _ = _season_rows(cid, str(season), params, config)
        if rows:
            training_by_season[str(season)] = rows

    factors, fit = _fit(training_by_season)
    target_raw, skips = _season_rows(cid, target_season, target_params, config)
    target_rows = [{
        **row,
        "baseline_matrix": row["matrix"],
        "candidate_matrix": _calibrate_matrix(row["matrix"], factors),
    } for row in target_raw]
    base = _score(target_rows, "baseline_matrix")
    cand = _score(target_rows, "candidate_matrix")
    return {
        "competition_id": cid,
        "target_season": target_season,
        "training_seasons": sorted(training_by_season),
        "fit": fit,
        "baseline": base,
        "candidate": cand,
        "delta": _delta(base, cand),
        "baseline_top1_bucket_counts": _top1_counts(target_rows, "baseline_matrix"),
        "candidate_top1_bucket_counts": _top1_counts(target_rows, "candidate_matrix"),
        "target_skips": dict(skips),
    }, target_rows


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    pool: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    for cid in competitions:
        try:
            result, rows = _domain(cid)
            reports[cid] = result
            pool.extend(rows)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if failures:
        raise PlatformError(f"competition failures: {failures}")
    if len(pool) < SAMPLE_N:
        raise PlatformError("insufficient pooled target predictions")

    full_base = _score(pool, "baseline_matrix")
    full_cand = _score(pool, "candidate_matrix")
    sampled = random.Random(SEED).sample(pool, SAMPLE_N)
    sample_base = _score(sampled, "baseline_matrix")
    sample_cand = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.24.7-total-cross-season-stability-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_STRICT_PRIOR_SEASON_PIT_FORMAL_WEIGHT_0",
        "eligible_target_pool_count": len(pool),
        "stability_rule": "final_factor=exp(sign_consistency*log(one-season-prior-shrunk-factor))",
        "full_pool": {
            "baseline": full_base,
            "candidate": full_cand,
            "delta": _delta(full_base, full_cand),
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED,
            "baseline": sample_base,
            "candidate": sample_cand,
            "delta": _delta(sample_base, sample_cand),
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "target_season_results_used_for_calibration": False,
            "calibration_training_strictly_prior_seasons": True,
            "training_predictions_strict_pit": True,
            "stability_weight_target_tuned": False,
            "one_joint_matrix_only": True,
            "conditional_score_given_total_preserved": True,
            "historical_odds_used": False,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "eligible_target_pool_count": len(pool),
        "full_pool": payload["full_pool"],
        "random100": payload["random100"],
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
