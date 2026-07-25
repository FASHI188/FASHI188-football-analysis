#!/usr/bin/env python3
"""V6.24.0 fixed-seed random-100 strict-PIT regime-state replay.

Research only. The full eligible chronology is replayed first so every team's
regime ledger is exactly the pre-match ledger available at that historical
cutoff. Only after the eligible pool is frozen do we draw 100 matches without
replacement. Baseline and challenger are scored on the exact same matches.
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
from v624_regime_ledger import RegimeLedger  # noqa: E402
from v624_regime_state_adapter import (  # noqa: E402
    build_post_settlement_proposal,
    build_regime_snapshot,
    settle_regime_day,
)
from v6_team_regime_state_runner_v6240 import (  # noqa: E402
    EXPERT_HALF_LIVES,
    TOTAL_BUCKETS,
    _add_metric,
    _build_regime_signals,
    _delta,
    _finalize_metric,
    _matrix_from_state,
    _mix_team_record,
    _new_metric,
    _one_probs,
    _screen_decision,
    _state_with_team_records,
    _team_key,
    _total_distribution,
)

OUT = ROOT / "manifests" / "v6_team_regime_state_random100_v6240_status.json"
SCHEMA = "V6.24.0-team-regime-state-random100-r1"
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
    if not target:
        raise PlatformError(f"no target-season matches for {competition_id} {season}")

    by_day: dict[Any, list[Any]] = defaultdict(list)
    for match in target:
        by_day[match.date.date()].append(match)

    ledger = RegimeLedger()
    rows: list[dict[str, Any]] = []
    skips = Counter()

    for day in sorted(by_day):
        day_matches = sorted(by_day[day], key=lambda m: (m.home_team, m.away_team))
        day_team_counts = Counter()
        for match in day_matches:
            day_team_counts[_team_key(match.home_team)] += 1
            day_team_counts[_team_key(match.away_team)] += 1
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

                home_snapshot = build_regime_snapshot(home_key, ledger, _build_regime_signals(history, home_key))
                away_snapshot = build_regime_snapshot(away_key, ledger, _build_regime_signals(history, away_key))
                mixed_home = _mix_team_record(
                    home_records,
                    [float(x) for x in home_snapshot["weight_vector"]],
                    baseline_state["team"][home_key],
                    float(home_snapshot["blend_strength"]),
                )
                mixed_away = _mix_team_record(
                    away_records,
                    [float(x) for x in away_snapshot["weight_vector"]],
                    baseline_state["team"][away_key],
                    float(away_snapshot["blend_strength"]),
                )
                challenger_state = _state_with_team_records(
                    baseline_state, home_key, mixed_home, away_key, mixed_away
                )
                challenger_matrix = _matrix_from_state(
                    challenger_state, match.home_team, match.away_team, formal_params, config, baseline_factors
                )

                rows.append({
                    "competition_id": competition_id,
                    "season": season,
                    "date": match.date.isoformat(),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "home_goals": int(match.home_goals),
                    "away_goals": int(match.away_goals),
                    "baseline_matrix": baseline_matrix,
                    "challenger_matrix": challenger_matrix,
                    "home_regime": home_snapshot["regime"],
                    "away_regime": away_snapshot["regime"],
                    "home_blend_strength": float(home_snapshot["blend_strength"]),
                    "away_blend_strength": float(away_snapshot["blend_strength"]),
                })
            except PlatformError as exc:
                skips[str(exc)] += 1

        # Strict same-day barrier: apply results only after every match that date was predicted.
        post_history = [m for m in target if m.date.date() <= day]
        proposals: list[dict[str, Any]] = []
        for team_key, count in sorted(day_team_counts.items()):
            proposals.append(build_post_settlement_proposal(
                team_key,
                ledger,
                _build_regime_signals(post_history, team_key),
                day.isoformat(),
                settled_increment=int(count),
            ))
        settle_regime_day(ledger, proposals)

    return rows, dict(skips)


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

    for competition_id in competitions:
        try:
            rows, competition_skips = _collect_competition(competition_id)
            pool.extend(rows)
            skips[competition_id] = competition_skips
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    if failures:
        raise PlatformError(f"competition failures: {failures}")
    if len(pool) < SAMPLE_N:
        raise PlatformError(f"eligible pool only {len(pool)} < requested {SAMPLE_N}")

    sampled = random.Random(SEED).sample(pool, SAMPLE_N)
    baseline_metric = _new_metric()
    challenger_metric = _new_metric()
    domain_counts = Counter()
    regime_counts = Counter()
    sample_rows: list[dict[str, Any]] = []

    for row in sampled:
        hg = int(row["home_goals"])
        ag = int(row["away_goals"])
        _add_metric(baseline_metric, row["baseline_matrix"], hg, ag)
        _add_metric(challenger_metric, row["challenger_matrix"], hg, ag)
        domain_counts[str(row["competition_id"])] += 1
        regime_counts[str(row["home_regime"])] += 1
        regime_counts[str(row["away_regime"])] += 1
        sample_rows.append({
            "competition_id": row["competition_id"],
            "season": row["season"],
            "date": row["date"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "result": f"{hg}-{ag}",
            "actual_1x2": _actual_result(hg, ag),
            "home_regime": row["home_regime"],
            "away_regime": row["away_regime"],
            "home_blend_strength": row["home_blend_strength"],
            "away_blend_strength": row["away_blend_strength"],
            "baseline_pick": _pick_summary(row["baseline_matrix"]),
            "challenger_pick": _pick_summary(row["challenger_matrix"]),
        })

    baseline = _finalize_metric(baseline_metric)
    challenger = _finalize_metric(challenger_metric)
    delta = _delta(baseline, challenger)
    decision = _screen_decision(delta)

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
        "same_day_predict_then_update_barrier": True,
        "expert_half_lives_days": list(EXPERT_HALF_LIVES),
        "regime_prediction_snapshot_counts_in_sample": dict(sorted(regime_counts.items())),
        "domain_counts": dict(sorted(domain_counts.items())),
        "baseline": baseline,
        "team_regime_state_v6240": challenger,
        "delta_candidate_minus_baseline": delta,
        "screen_decision": decision,
        "market_or_bookmaker_used": False,
        "historical_odds_used": False,
        "target_season_outcome_fitted_calibration_used": False,
        "single_joint_score_matrix_for_1x2_total_score": True,
        "unavailable_signals_fabricated": False,
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
        "team_regime_state_v6240", "delta_candidate_minus_baseline", "screen_decision",
    )}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
