#!/usr/bin/env python3
"""V6.24.3 direct-total signal de-shrink challenger.

Research only; formal_weight=0. Challenges exactly one degree of freedom:
for last-complete-season strict-PIT replay, replace the already selected
`direct_total_signal_weight` by 1.0 while holding every other selected
parameter and the current-season team state fixed.

Purpose: determine whether OOS-selected 0.65 shrinkage is causing exact-total
Top-1 modal concentration even when the average total distribution remains
well calibrated.
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
from platform_core import PlatformError, load_json, read_processed_matches  # noqa: E402
from v6_team_regime_state_runner_v6240 import (  # noqa: E402
    TOTAL_BUCKETS,
    _add_metric,
    _delta,
    _finalize_metric,
    _new_metric,
    _total_distribution,
)

OUT = ROOT / "manifests" / "v6_total_signal_deshink_v6243_status.json"
SEED = 20260725
SAMPLE_N = 100


def _matrix(state: dict[str, Any], home: str, away: str, params: dict[str, float], config: dict[str, Any], factors: dict[tuple[int, int], float]) -> list[dict[str, Any]]:
    means = expected_goals(state, home, away, params, config)
    return build_score_matrix(
        float(means["mu_home"]),
        float(means["mu_away"]),
        float(state["nb_dispersion_k"]),
        float(params["beta_binomial_concentration"]),
        int(config["max_total_goals_exact"]),
        factors,
    )


def _collect_competition(cid: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    config = load_config()
    season = _requested_last_complete_season(cid)
    report = load_json(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(report, season)
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        raise PlatformError(f"invalid selected parameters for {cid} {season}")
    base_params = _merge_parameters(config, selected)
    cand_params = dict(base_params)
    cand_params["direct_total_signal_weight"] = 1.0
    all_matches = sorted(read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
    target = [m for m in all_matches if str(m.season) == season]
    rows: list[dict[str, Any]] = []
    skips = Counter()
    for match in target:
        try:
            history_season, history = current_season_history(all_matches, match.date, season)
            if history_season != season:
                raise PlatformError("history season mismatch")
            state = fit_current_season_state(history, match.date, base_params, config)
            factors = low_score_factors(state, base_params)
            base_matrix = _matrix(state, match.home_team, match.away_team, base_params, config, factors)
            cand_matrix = _matrix(state, match.home_team, match.away_team, cand_params, config, factors)
            rows.append({
                "competition_id": cid,
                "season": season,
                "date": match.date.isoformat(),
                "home_team": match.home_team,
                "away_team": match.away_team,
                "home_goals": int(match.home_goals),
                "away_goals": int(match.away_goals),
                "selected_direct_total_signal_weight": float(base_params.get("direct_total_signal_weight", 1.0)),
                "baseline_matrix": base_matrix,
                "candidate_matrix": cand_matrix,
            })
        except PlatformError as exc:
            skips[str(exc)] += 1
    return rows, dict(skips)


def _top1_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out = Counter()
    for row in rows:
        dist = _total_distribution(row[key])
        pick = max(TOTAL_BUCKETS, key=lambda bucket: dist[bucket])
        out[pick] += 1
    return dict(out)


def _score(rows: list[dict[str, Any]], matrix_key: str) -> dict[str, Any]:
    metric = _new_metric()
    for row in rows:
        _add_metric(metric, row[matrix_key], int(row["home_goals"]), int(row["away_goals"]))
    return _finalize_metric(metric)


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    pool: list[dict[str, Any]] = []
    skips: dict[str, Any] = {}
    failures: dict[str, str] = {}
    weight_counts = Counter()
    for cid in competitions:
        try:
            rows, comp_skips = _collect_competition(cid)
            pool.extend(rows)
            skips[cid] = comp_skips
            for row in rows:
                weight_counts[str(row["selected_direct_total_signal_weight"])] += 1
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if failures:
        raise PlatformError(f"competition failures: {failures}")
    if len(pool) < SAMPLE_N:
        raise PlatformError(f"eligible pool only {len(pool)}")

    full_base = _score(pool, "baseline_matrix")
    full_cand = _score(pool, "candidate_matrix")
    sampled = random.Random(SEED).sample(pool, SAMPLE_N)
    sample_base = _score(sampled, "baseline_matrix")
    sample_cand = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.24.3-direct-total-deshink-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_FORMAL_WEIGHT_0",
        "research_question": "Does removing selected direct-total shrinkage reduce exact-total mode concentration without degrading proper scores?",
        "eligible_pool_count": len(pool),
        "selected_weight_prediction_counts": dict(weight_counts),
        "challenger_direct_total_signal_weight": 1.0,
        "full_pool": {
            "baseline": full_base,
            "candidate": full_cand,
            "delta": _delta(full_base, full_cand),
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED,
            "count": SAMPLE_N,
            "baseline": sample_base,
            "candidate": sample_cand,
            "delta": _delta(sample_base, sample_cand),
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "failures": failures,
        "skips": skips,
        "governance": {
            "only_direct_total_signal_weight_changed": True,
            "candidate_weight_fixed_ex_ante": 1.0,
            "target_results_used_for_parameter_choice": False,
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
        "selected_weight_prediction_counts": payload["selected_weight_prediction_counts"],
        "full_pool": payload["full_pool"],
        "random100": payload["random100"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
