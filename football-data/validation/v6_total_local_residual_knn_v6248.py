#!/usr/bin/env python3
"""V6.24.8 feature-conditioned local residual total-distribution head.

Research only; formal_weight=0.

Instead of one competition-wide bucket correction, estimate a local residual
correction from strictly prior-season matches with similar pre-match total-goal
features. The baseline NB total distribution remains the prior.

For each target match:
1. Build strict-PIT pre-match features.
2. Standardize by prior-season training moments.
3. Select k = round(sqrt(n_train)) nearest prior-season predictions.
4. Compare neighbors' actual bucket counts with neighbors' baseline expected
   counts.
5. Use one equivalent local-neighborhood prior centered at no correction:
      factor_k = (actual_k + expected_k) / (2 * expected_k)
6. Apply factors to P(T) and preserve P(score | T), yielding one joint matrix.

No target-season result enters fitting or neighbor selection.
"""
from __future__ import annotations

import json
import math
import random
import statistics
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
from platform_core import PlatformError, load_json, normalize_team_token, read_processed_matches  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _delta, _total_distribution  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _calibrate_matrix, _score, _top1_counts  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_local_residual_knn_v6248_status.json"
SEED = 20260725
SAMPLE_N = 100
EPS = 1e-9


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _team_key(name: str) -> str:
    return normalize_team_token(name)


def _recent_totals(history: list[Any], team: str, n: int = 5) -> list[float]:
    key = _team_key(team)
    values: list[float] = []
    for match in reversed(history):
        if _team_key(match.home_team) == key or _team_key(match.away_team) == key:
            values.append(float(match.home_goals + match.away_goals))
            if len(values) >= n:
                break
    values.reverse()
    return values


def _mean(xs: list[float], default: float) -> float:
    return sum(xs) / len(xs) if xs else default


def _sd(xs: list[float]) -> float:
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def _build_row(all_matches: list[Any], match: Any, season: str, params: dict[str, float], config: dict[str, Any]) -> dict[str, Any]:
    hist_season, history = current_season_history(all_matches, match.date, season)
    if hist_season != season:
        raise PlatformError("history season mismatch")
    state = fit_current_season_state(history, match.date, params, config)
    means = expected_goals(state, match.home_team, match.away_team, params, config)
    factors = low_score_factors(state, params)
    matrix = build_score_matrix(
        float(means["mu_home"]), float(means["mu_away"]), float(state["nb_dispersion_k"]),
        float(params["beta_binomial_concentration"]), int(config["max_total_goals_exact"]), factors,
    )
    league_total = max(EPS, float(state["mean_total_goals"]))
    home_rate = max(EPS, float(means["home_direct_total_rate"]))
    away_rate = max(EPS, float(means["away_direct_total_rate"]))
    home_recent = _recent_totals(history, match.home_team)
    away_recent = _recent_totals(history, match.away_team)
    feature = [
        math.log(max(EPS, float(means["mu_total"]))),
        math.log(max(EPS, float(state["nb_dispersion_k"]))),
        math.log(home_rate / league_total),
        math.log(away_rate / league_total),
        abs(math.log(home_rate / away_rate)),
        _mean(home_recent, league_total) / league_total,
        _mean(away_recent, league_total) / league_total,
        _sd(home_recent) / max(1.0, league_total),
        _sd(away_recent) / max(1.0, league_total),
    ]
    return {
        "competition_id": match.competition_id,
        "season": season,
        "date": match.date.isoformat(),
        "home_team": match.home_team,
        "away_team": match.away_team,
        "home_goals": int(match.home_goals),
        "away_goals": int(match.away_goals),
        "feature": feature,
        "matrix": matrix,
    }


def _season_rows(cid: str, season: str, params: dict[str, float], config: dict[str, Any]) -> tuple[list[dict[str, Any]], Counter]:
    all_matches = sorted(read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
    target = [m for m in all_matches if str(m.season) == season]
    rows: list[dict[str, Any]] = []
    skips = Counter()
    for match in target:
        try:
            rows.append(_build_row(all_matches, match, season, params, config))
        except PlatformError as exc:
            skips[str(exc)] += 1
    return rows, skips


def _standardizer(rows: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    dim = len(rows[0]["feature"])
    means = []
    sds = []
    for j in range(dim):
        vals = [float(row["feature"][j]) for row in rows]
        mu = sum(vals) / len(vals)
        sd = statistics.pstdev(vals) if len(vals) > 1 else 1.0
        means.append(mu)
        sds.append(max(1e-6, sd))
    return means, sds


def _z(feature: list[float], means: list[float], sds: list[float]) -> list[float]:
    return [(float(x) - means[i]) / sds[i] for i, x in enumerate(feature)]


def _distance(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _local_factors(neighbors: list[dict[str, Any]]) -> dict[str, float]:
    actual = Counter()
    expected = Counter()
    for row in neighbors:
        actual[_bucket(int(row["home_goals"]) + int(row["away_goals"]))] += 1
        dist = _total_distribution(row["matrix"])
        for key in TOTAL_BUCKETS:
            expected[key] += float(dist[key])
    factors: dict[str, float] = {}
    for key in TOTAL_BUCKETS:
        e = max(EPS, float(expected[key]))
        factors[key] = (float(actual[key]) + e) / (2.0 * e)
    return factors


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
            training_rows.extend(rows)
            training_seasons.append(str(season))
    if not training_rows:
        raise PlatformError("no prior-season local-residual training rows")

    means, sds = _standardizer(training_rows)
    for row in training_rows:
        row["z"] = _z(row["feature"], means, sds)
    k = max(1, int(round(math.sqrt(len(training_rows)))))

    target_raw, skips = _season_rows(cid, target_season, target_params, config)
    target_rows: list[dict[str, Any]] = []
    for row in target_raw:
        z = _z(row["feature"], means, sds)
        nearest = sorted(training_rows, key=lambda tr: _distance(z, tr["z"]))[:k]
        factors = _local_factors(nearest)
        target_rows.append({
            **row,
            "baseline_matrix": row["matrix"],
            "candidate_matrix": _calibrate_matrix(row["matrix"], factors),
            "local_k": k,
        })

    base = _score(target_rows, "baseline_matrix")
    cand = _score(target_rows, "candidate_matrix")
    return {
        "competition_id": cid,
        "target_season": target_season,
        "training_seasons": training_seasons,
        "training_prediction_count": len(training_rows),
        "feature_dimension": len(means),
        "local_k": k,
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
        "schema_version": "V6.24.8-total-local-residual-knn-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_FEATURE_CONDITIONED_STRICT_PIT_FORMAL_WEIGHT_0",
        "eligible_target_pool_count": len(pool),
        "method": "prior-season standardized feature kNN local residual calibration; k=round(sqrt(n_train)); one-equivalent-neighborhood shrinkage",
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
            "target_season_results_used_for_training": False,
            "training_seasons_strictly_prior": True,
            "training_predictions_strict_pit": True,
            "feature_values_pre_match_only": True,
            "k_target_tuned": False,
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
