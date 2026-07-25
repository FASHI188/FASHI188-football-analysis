#!/usr/bin/env python3
"""V6.23.0 fixed-seed random-100 strict-PIT replay.

Research only. Processes the full eligible chronology first so each team's online Hedge
ledger is exactly the pre-match ledger it would have had in the full replay, then draws a
simple random sample of 100 eligible predictions from the frozen pool. Baseline and
challenger are scored on the exact same sampled matches.
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
    _actual_result,
    _fold_for_season,
    _requested_last_complete_season,
)
from football_v460_engine import (  # noqa: E402
    _merge_parameters,
    current_season_history,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import PlatformError, load_json, read_processed_matches, top_scores  # noqa: E402
from v6_adaptive_half_life_hedge_v6230 import (  # noqa: E402
    EXPERT_HALF_LIVES,
    K,
    TOTAL_BUCKETS,
    _add_metric,
    _delta,
    _finalize_metric,
    _hedge_weights,
    _matrix_from_state,
    _mix_team_record,
    _new_ledger,
    _new_metric,
    _one_probs,
    _state_with_two_team_records,
    _team_key,
    _total_distribution,
)

OUT = ROOT / "manifests" / "v6_adaptive_half_life_hedge_v6230_random100_status.json"
SCHEMA = "V6.23.0-adaptive-half-life-hedge-random100-r1"
SEED = 20260725
SAMPLE_N = 100


def _collect_competition(competition_id: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    config = load_config()
    season = _requested_last_complete_season(competition_id)
    report = load_json(REPORT_ROOT / f"{competition_id}.json")
    fold = _fold_for_season(report, season)
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        raise PlatformError(f"invalid selected parameters for {competition_id} {season}")
    formal_params = _merge_parameters(config, selected)
    all_matches = sorted(read_processed_matches(competition_id), key=lambda m: (m.date, m.home_team, m.away_team))
    target = [m for m in all_matches if str(m.season) == season]
    by_day: dict[Any, list[Any]] = defaultdict(list)
    for m in target:
        by_day[m.date.date()].append(m)

    ledgers: dict[str, dict[str, Any]] = defaultdict(_new_ledger)
    rows: list[dict[str, Any]] = []
    skip = Counter()

    for day in sorted(by_day):
        pending: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "losses": [0.0] * K})
        for match in sorted(by_day[day], key=lambda m: (m.home_team, m.away_team)):
            try:
                history_season, history = current_season_history(all_matches, match.date, season)
                if history_season != season:
                    raise PlatformError("history season mismatch")
                baseline_state = fit_current_season_state(history, match.date, formal_params, config)
                baseline_factors = low_score_factors(baseline_state, formal_params)
                baseline_matrix = _matrix_from_state(
                    baseline_state, match.home_team, match.away_team, formal_params, config, baseline_factors
                )
                home_key = _team_key(match.home_team)
                away_key = _team_key(match.away_team)
                if home_key not in baseline_state["team"] or away_key not in baseline_state["team"]:
                    raise PlatformError("team missing from baseline state")

                home_records: list[dict[str, Any]] = []
                away_records: list[dict[str, Any]] = []
                for half_life in EXPERT_HALF_LIVES:
                    expert_params = dict(formal_params)
                    expert_params["half_life_days"] = half_life
                    expert_state = fit_current_season_state(history, match.date, expert_params, config)
                    home_records.append(expert_state["team"][home_key])
                    away_records.append(expert_state["team"][away_key])

                home_w, _ = _hedge_weights(ledgers[home_key])
                away_w, _ = _hedge_weights(ledgers[away_key])
                mixed_home = _mix_team_record(home_records, home_w, baseline_state["team"][home_key])
                mixed_away = _mix_team_record(away_records, away_w, baseline_state["team"][away_key])
                adaptive_state = _state_with_two_team_records(
                    baseline_state, home_key, mixed_home, away_key, mixed_away
                )
                adaptive_matrix = _matrix_from_state(
                    adaptive_state, match.home_team, match.away_team, formal_params, config, baseline_factors
                )

                hg, ag = int(match.home_goals), int(match.away_goals)
                actual = _actual_result(hg, ag)
                home_losses = []
                away_losses = []
                for i in range(K):
                    cf_home = _state_with_two_team_records(
                        baseline_state, home_key, home_records[i], away_key, mixed_away
                    )
                    cf_home_matrix = _matrix_from_state(
                        cf_home, match.home_team, match.away_team, formal_params, config, baseline_factors
                    )
                    p = _one_probs(cf_home_matrix)
                    home_losses.append(0.5 * sum((p[k] - (1.0 if k == actual else 0.0)) ** 2 for k in ("home", "draw", "away")))

                    cf_away = _state_with_two_team_records(
                        baseline_state, home_key, mixed_home, away_key, away_records[i]
                    )
                    cf_away_matrix = _matrix_from_state(
                        cf_away, match.home_team, match.away_team, formal_params, config, baseline_factors
                    )
                    p = _one_probs(cf_away_matrix)
                    away_losses.append(0.5 * sum((p[k] - (1.0 if k == actual else 0.0)) ** 2 for k in ("home", "draw", "away")))

                rows.append({
                    "competition_id": competition_id,
                    "season": season,
                    "date": match.date.isoformat(),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "home_goals": hg,
                    "away_goals": ag,
                    "baseline_matrix": baseline_matrix,
                    "adaptive_matrix": adaptive_matrix,
                })

                pending[home_key]["count"] += 1
                pending[away_key]["count"] += 1
                for i in range(K):
                    pending[home_key]["losses"][i] += home_losses[i]
                    pending[away_key]["losses"][i] += away_losses[i]
            except PlatformError as exc:
                skip[str(exc)] += 1

        for team_key, update in pending.items():
            ledger = ledgers[team_key]
            ledger["n"] = int(ledger["n"]) + int(update["count"])
            for i in range(K):
                ledger["losses"][i] = float(ledger["losses"][i]) + float(update["losses"][i])

    return rows, dict(skip)


def _pick_summary(matrix: list[dict[str, Any]]) -> dict[str, str]:
    one = _one_probs(matrix)
    one_pick = max(("home", "draw", "away"), key=lambda k: one[k])
    total = _total_distribution(matrix)
    total_pick = max(TOTAL_BUCKETS, key=lambda k: total[k])
    score = top_scores(matrix, 1)
    return {
        "one_x_two": one_pick,
        "total_goals": total_pick,
        "score": score[0]["score"] if score else "",
    }


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    pool: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    skips: dict[str, dict[str, int]] = {}

    for cid in competitions:
        try:
            rows, comp_skips = _collect_competition(cid)
            pool.extend(rows)
            skips[cid] = comp_skips
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if failures:
        raise PlatformError(f"competition failures: {failures}")
    if len(pool) < SAMPLE_N:
        raise PlatformError(f"eligible pool only {len(pool)} < requested {SAMPLE_N}")

    rng = random.Random(SEED)
    sampled = rng.sample(pool, SAMPLE_N)
    base_metric = _new_metric()
    cand_metric = _new_metric()
    sample_rows: list[dict[str, Any]] = []
    domain_counts = Counter()

    for row in sampled:
        hg, ag = int(row["home_goals"]), int(row["away_goals"])
        _add_metric(base_metric, row["baseline_matrix"], hg, ag)
        _add_metric(cand_metric, row["adaptive_matrix"], hg, ag)
        domain_counts[row["competition_id"]] += 1
        sample_rows.append({
            "competition_id": row["competition_id"],
            "season": row["season"],
            "date": row["date"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "result": f"{hg}-{ag}",
            "actual_1x2": _actual_result(hg, ag),
            "baseline_pick": _pick_summary(row["baseline_matrix"]),
            "adaptive_pick": _pick_summary(row["adaptive_matrix"]),
        })

    base = _finalize_metric(base_metric)
    cand = _finalize_metric(cand_metric)
    delta = _delta(base, cand)
    proper_nonworse = (
        delta["one_x_two_mean_brier"] <= 0.0
        and delta["one_x_two_mean_rps"] <= 0.0
        and delta["one_x_two_mean_log_loss"] <= 0.0
        and delta["total_goals_mean_rps"] <= 0.0
        and delta["score_mean_joint_log_score"] <= 0.0
    )
    any_accuracy_gain = (
        delta["one_x_two_top1_accuracy"] > 0.0
        or delta["score_top1_accuracy"] > 0.0
        or delta["total_goals_top1_accuracy"] > 0.0
    )
    if proper_nonworse and any_accuracy_gain:
        decision = "PROMISING_DEEPER_NESTED_OOS_REQUIRED"
    elif not proper_nonworse:
        decision = "REJECT_OR_REDESIGN_PROPER_SCORE_REGRESSION"
    else:
        decision = "NEUTRAL_NO_CLEAR_GAIN"

    payload = {
        "schema_version": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_FIXED_SEED_RANDOM100_FORMAL_WEIGHT_0",
        "seed": SEED,
        "sample_n_requested": SAMPLE_N,
        "sample_n_scored": len(sampled),
        "eligible_pool_count": len(pool),
        "sampling": "simple random sample without replacement after full strict-PIT chronological replay",
        "same_matches_baseline_candidate": True,
        "market_or_bookmaker_used": False,
        "historical_odds_used": False,
        "same_day_predict_then_update_barrier": True,
        "expert_half_lives_days": list(EXPERT_HALF_LIVES),
        "other_nb_beta_binomial_parameters_held_fixed": True,
        "domain_counts": dict(sorted(domain_counts.items())),
        "baseline": base,
        "adaptive_half_life_hedge": cand,
        "delta_candidate_minus_baseline": delta,
        "screen_decision": decision,
        "handicap": "UNAVAILABLE_NO_REAL_FROZEN_HANDICAP_LINE_IN_THIS_REPLAY",
        "sample_rows": sorted(sample_rows, key=lambda r: (r["competition_id"], r["date"], r["home_team"], r["away_team"])),
        "skips": skips,
        "failures": failures,
        "formal_weight": 0,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "status", "seed", "sample_n_scored", "eligible_pool_count", "baseline",
        "adaptive_half_life_hedge", "delta_candidate_minus_baseline", "screen_decision"
    )}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
