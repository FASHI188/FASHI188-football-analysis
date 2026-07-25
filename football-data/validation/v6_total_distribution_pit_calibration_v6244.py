#!/usr/bin/env python3
"""V6.24.4 prior-season PIT total-distribution calibration challenger.

Research only; formal_weight=0.

The baseline direct NB total distribution is retained as the prior. For each
competition, bucket-wise multiplicative calibration factors for total goals
0/1/2/3/4/5/6/7+ are estimated only from strict-PIT predictions in seasons
strictly earlier than the target season and with their own historical fold
parameters. No target-season result is used to fit the factors.

The factors are then applied to the target-season total marginal while
preserving P(score | total) from the baseline matrix. Therefore one coherent
joint score matrix remains the source of 1X2, total goals and exact score.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
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
from football_v460_engine import (  # noqa: E402
    _merge_parameters,
    build_score_matrix,
    current_season_history,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import PlatformError, load_json, read_processed_matches, score_matrix_rows  # noqa: E402
from v6_team_regime_state_runner_v6240 import (  # noqa: E402
    TOTAL_BUCKETS,
    _add_metric,
    _delta,
    _finalize_metric,
    _new_metric,
    _total_distribution,
)

OUT = ROOT / "manifests" / "v6_total_distribution_pit_calibration_v6244_status.json"
SEED = 20260725
SAMPLE_N = 100
PSEUDOCOUNT = 1.0


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _matrix(state: dict[str, Any], home: str, away: str, params: dict[str, float], config: dict[str, Any]) -> list[dict[str, Any]]:
    factors = low_score_factors(state, params)
    means = expected_goals(state, home, away, params, config)
    return build_score_matrix(
        float(means["mu_home"]),
        float(means["mu_away"]),
        float(state["nb_dispersion_k"]),
        float(params["beta_binomial_concentration"]),
        int(config["max_total_goals_exact"]),
        factors,
    )


def _season_rows(cid: str, season: str, params: dict[str, float], config: dict[str, Any]) -> tuple[list[dict[str, Any]], Counter]:
    all_matches = sorted(read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
    target = [m for m in all_matches if str(m.season) == str(season)]
    rows: list[dict[str, Any]] = []
    skips = Counter()
    for match in target:
        try:
            hist_season, history = current_season_history(all_matches, match.date, str(season))
            if hist_season != str(season):
                raise PlatformError("history season mismatch")
            state = fit_current_season_state(history, match.date, params, config)
            matrix = _matrix(state, match.home_team, match.away_team, params, config)
            rows.append({
                "competition_id": cid,
                "season": str(season),
                "date": match.date.isoformat(),
                "home_team": match.home_team,
                "away_team": match.away_team,
                "home_goals": int(match.home_goals),
                "away_goals": int(match.away_goals),
                "matrix": matrix,
            })
        except PlatformError as exc:
            skips[str(exc)] += 1
    return rows, skips


def _fit_factors(training_rows: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, Any]]:
    if not training_rows:
        raise PlatformError("no prior-season PIT calibration rows")
    actual = Counter()
    predicted = Counter()
    for row in training_rows:
        actual[_bucket(int(row["home_goals"]) + int(row["away_goals"]))] += 1
        dist = _total_distribution(row["matrix"])
        for key in TOTAL_BUCKETS:
            predicted[key] += float(dist[key])
    factors = {
        key: (float(actual[key]) + PSEUDOCOUNT) / (float(predicted[key]) + PSEUDOCOUNT)
        for key in TOTAL_BUCKETS
    }
    diagnostics = {
        "count": len(training_rows),
        "actual_counts": dict(actual),
        "predicted_expected_counts": {key: float(predicted[key]) for key in TOTAL_BUCKETS},
        "factors": factors,
    }
    return factors, diagnostics


def _calibrate_matrix(matrix: list[dict[str, Any]], factors: dict[str, float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    total = 0.0
    for h, a, p in score_matrix_rows(matrix):
        value = float(p) * float(factors[_bucket(int(h + a))])
        out.append({"home_goals": int(h), "away_goals": int(a), "probability": value})
        total += value
    if total <= 0.0:
        raise PlatformError("calibrated matrix has zero mass")
    for cell in out:
        cell["probability"] = float(cell["probability"]) / total
    return out


def _score(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    metric = _new_metric()
    for row in rows:
        _add_metric(metric, row[key], int(row["home_goals"]), int(row["away_goals"]))
    return _finalize_metric(metric)


def _top1_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        dist = _total_distribution(row[key])
        counts[max(TOTAL_BUCKETS, key=lambda bucket: dist[bucket])] += 1
    return dict(counts)


def _domain(cid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = load_config()
    report = load_json(REPORT_ROOT / f"{cid}.json")
    target_season = _requested_last_complete_season(cid)
    target_fold = _fold_for_season(report, target_season)
    target_selected = target_fold.get("selected_parameters")
    if not isinstance(target_selected, dict):
        raise PlatformError(f"invalid target parameters for {cid} {target_season}")
    target_params = _merge_parameters(config, target_selected)

    training_rows: list[dict[str, Any]] = []
    training_seasons: list[str] = []
    training_skips: dict[str, Any] = {}
    for season in target_fold.get("prior_seasons") or []:
        try:
            fold = _fold_for_season(report, str(season))
        except Exception:
            continue
        selected = fold.get("selected_parameters")
        if not isinstance(selected, dict):
            continue
        params = _merge_parameters(config, selected)
        rows, skips = _season_rows(cid, str(season), params, config)
        if rows:
            training_rows.extend(rows)
            training_seasons.append(str(season))
            training_skips[str(season)] = dict(skips)

    factors, fit_diag = _fit_factors(training_rows)
    target_rows_raw, target_skips = _season_rows(cid, target_season, target_params, config)
    target_rows: list[dict[str, Any]] = []
    for row in target_rows_raw:
        target_rows.append({
            **row,
            "baseline_matrix": row["matrix"],
            "candidate_matrix": _calibrate_matrix(row["matrix"], factors),
        })
    base = _score(target_rows, "baseline_matrix")
    cand = _score(target_rows, "candidate_matrix")
    result = {
        "competition_id": cid,
        "target_season": target_season,
        "training_seasons": training_seasons,
        "training_prediction_count": len(training_rows),
        "target_prediction_count": len(target_rows),
        "fit": fit_diag,
        "baseline": base,
        "candidate": cand,
        "delta": _delta(base, cand),
        "baseline_top1_bucket_counts": _top1_counts(target_rows, "baseline_matrix"),
        "candidate_top1_bucket_counts": _top1_counts(target_rows, "candidate_matrix"),
        "target_skips": dict(target_skips),
        "training_skips": training_skips,
    }
    return result, target_rows


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

    full_delta = _delta(full_base, full_cand)
    sample_delta = _delta(sample_base, sample_cand)
    payload = {
        "schema_version": "V6.24.4-total-distribution-pit-calibration-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_STRICT_PRIOR_SEASON_PIT_FORMAL_WEIGHT_0",
        "eligible_target_pool_count": len(pool),
        "calibration_rule": "bucket_factor=(prior_actual_count+1)/(prior_predicted_expected_count+1); apply to total marginal and renormalize joint matrix",
        "full_pool": {
            "baseline": full_base,
            "candidate": full_cand,
            "delta": full_delta,
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED,
            "baseline": sample_base,
            "candidate": sample_cand,
            "delta": sample_delta,
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "target_season_results_used_for_calibration": False,
            "calibration_training_strictly_prior_seasons": True,
            "training_predictions_strict_pit": True,
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
