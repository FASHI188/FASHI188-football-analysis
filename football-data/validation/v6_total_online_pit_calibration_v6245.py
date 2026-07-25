#!/usr/bin/env python3
"""V6.24.5 online strict-PIT total-distribution calibration challenger.

Research only; formal_weight=0.

For each competition and target season, maintain an online ledger of cumulative
actual total-goal bucket counts and cumulative baseline expected bucket counts.
Before each calendar-date batch, apply Gamma-Poisson posterior mean factors
    factor_k = (actual_k + 1) / (expected_k + 1)
to the baseline total marginal. All matches on the date are predicted first;
the ledger is updated only after the full date batch settles.

P(score | total) is preserved, so one coherent joint score matrix remains the
source of 1X2, total goals and exact score.
"""
from __future__ import annotations

import json
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

OUT = ROOT / "manifests" / "v6_total_online_pit_calibration_v6245_status.json"
SEED = 20260725
SAMPLE_N = 100
PRIOR_SHAPE = 1.0
PRIOR_RATE = 1.0


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


def _calibration_factors(actual: Counter, expected: Counter) -> dict[str, float]:
    return {
        key: (float(actual[key]) + PRIOR_SHAPE) / (float(expected[key]) + PRIOR_RATE)
        for key in TOTAL_BUCKETS
    }


def _calibrate_matrix(matrix: list[dict[str, Any]], factors: dict[str, float]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    total_mass = 0.0
    for h, a, p in score_matrix_rows(matrix):
        value = float(p) * float(factors[_bucket(int(h + a))])
        output.append({"home_goals": int(h), "away_goals": int(a), "probability": value})
        total_mass += value
    if total_mass <= 0.0:
        raise PlatformError("online calibrated matrix has zero mass")
    for cell in output:
        cell["probability"] = float(cell["probability"]) / total_mass
    return output


def _domain(cid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = load_config()
    season = _requested_last_complete_season(cid)
    report = load_json(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(report, season)
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        raise PlatformError(f"invalid selected parameters for {cid} {season}")
    params = _merge_parameters(config, selected)
    all_matches = sorted(read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
    target = [m for m in all_matches if str(m.season) == season]
    by_day: dict[Any, list[Any]] = defaultdict(list)
    for match in target:
        by_day[match.date.date()].append(match)

    ledger_actual = Counter()
    ledger_expected = Counter()
    rows: list[dict[str, Any]] = []
    skips = Counter()
    factor_snapshots: list[dict[str, float]] = []

    for day in sorted(by_day):
        factors = _calibration_factors(ledger_actual, ledger_expected)
        pending_actual = Counter()
        pending_expected = Counter()
        for match in sorted(by_day[day], key=lambda m: (m.home_team, m.away_team)):
            try:
                hist_season, history = current_season_history(all_matches, match.date, season)
                if hist_season != season:
                    raise PlatformError("history season mismatch")
                state = fit_current_season_state(history, match.date, params, config)
                baseline_matrix = _matrix(state, match.home_team, match.away_team, params, config)
                candidate_matrix = _calibrate_matrix(baseline_matrix, factors)
                base_dist = _total_distribution(baseline_matrix)
                for key in TOTAL_BUCKETS:
                    pending_expected[key] += float(base_dist[key])
                pending_actual[_bucket(int(match.home_goals) + int(match.away_goals))] += 1
                rows.append({
                    "competition_id": cid,
                    "season": season,
                    "date": match.date.isoformat(),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "home_goals": int(match.home_goals),
                    "away_goals": int(match.away_goals),
                    "baseline_matrix": baseline_matrix,
                    "candidate_matrix": candidate_matrix,
                    "factor_snapshot": dict(factors),
                    "ledger_settled_before_prediction": int(sum(ledger_actual.values())),
                })
                factor_snapshots.append(dict(factors))
            except PlatformError as exc:
                skips[str(exc)] += 1
        # Same-day barrier: update only after every prediction on this date is complete.
        ledger_actual.update(pending_actual)
        ledger_expected.update(pending_expected)

    result = {
        "competition_id": cid,
        "season": season,
        "target_match_count": len(target),
        "eligible_prediction_count": len(rows),
        "final_ledger_actual": dict(ledger_actual),
        "final_ledger_expected": {key: float(ledger_expected[key]) for key in TOTAL_BUCKETS},
        "final_factors": _calibration_factors(ledger_actual, ledger_expected),
        "skips": dict(skips),
    }
    return result, rows


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
        raise PlatformError("insufficient pooled predictions")

    full_base = _score(pool, "baseline_matrix")
    full_cand = _score(pool, "candidate_matrix")
    sampled = random.Random(SEED).sample(pool, SAMPLE_N)
    sample_base = _score(sampled, "baseline_matrix")
    sample_cand = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.24.5-total-online-pit-calibration-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_ONLINE_STRICT_PIT_FORMAL_WEIGHT_0",
        "eligible_pool_count": len(pool),
        "rule": "Gamma-Poisson posterior mean bucket factor=(prior settled actual+1)/(prior baseline expected+1)",
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
            "prediction_uses_prior_settled_matches_only": True,
            "same_day_predict_then_update_barrier": True,
            "target_future_results_used": False,
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
        "eligible_pool_count": len(pool),
        "full_pool": payload["full_pool"],
        "random100": payload["random100"],
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
