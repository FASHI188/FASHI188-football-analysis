#!/usr/bin/env python3
"""V6.24.0 strict-PIT regime-state challenger runner.

Research only; formal_weight=0. This file is intentionally not wired to an
Actions workflow yet. It can be executed later under an explicit validation
instruction, but adding it does not run any test.

Comparison contract
-------------------
* Baseline: frozen V4.6.0 current-season NB/Beta-Binomial core with the already
  selected target-season PIT/OOS parameter set.
* Challenger: same core and same parameters; only home/away team weighted
  sufficient statistics are replaced by a regime-weighted four-half-life mix.
* Four half-life experts are fixed at 45/90/180/360 days.
* Regime state is read from a pre-match ledger snapshot.
* Every match from a calendar date is predicted before any state update from
  that date is applied.
* One regime-adjusted joint score matrix produces 1X2, total goals and score.
* No historical odds, market coordination or target-season fitted calibration.
"""
from __future__ import annotations

import json
import math
import statistics
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
    build_score_matrix,
    current_season_history,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import (  # noqa: E402
    MatchRow,
    PlatformError,
    derive_score_marginals,
    load_json,
    normalize_team_token,
    read_processed_matches,
    score_matrix_rows,
    top_scores,
)
from v624_regime_ledger import RegimeLedger  # noqa: E402
from v624_regime_state_adapter import (  # noqa: E402
    build_post_settlement_proposal,
    build_regime_snapshot,
    settle_regime_day,
)

OUT = ROOT / "manifests" / "v6_team_regime_state_v6240_run_status.json"
SCHEMA = "V6.24.0-team-regime-state-runner-r1"
EXPERT_HALF_LIVES = (45.0, 90.0, 180.0, 360.0)
CLASSES = ("home", "draw", "away")
TOTAL_BUCKETS = ("0", "1", "2", "3", "4", "5", "6", "7+")
EPS = 1e-15
WEIGHTED_FIELDS = (
    "effective_matches",
    "home_matches",
    "away_matches",
    "home_gf",
    "home_ga",
    "away_gf",
    "away_ga",
)


def _team_key(name: str) -> str:
    return normalize_team_token(name)


def _clip(value: float, lo: float = 0.0, hi: float = 2.0) -> float:
    return min(hi, max(lo, float(value)))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _team_signal_rows(history: list[MatchRow], team: str) -> list[dict[str, float]]:
    """Build venue-normalized scoring rows from information known at cutoff.

    Current processed MatchRow data has no xG, red-card, lineup or coach fields.
    Those channels are therefore not fabricated here. The runner uses only goals,
    venue and competition-level scoring rates that are actually available.
    """
    key = _team_key(team)
    if not history:
        return []
    home_avg = _mean([float(m.home_goals) for m in history])
    away_avg = _mean([float(m.away_goals) for m in history])
    rows: list[dict[str, float]] = []
    for match in history:
        hk = _team_key(match.home_team)
        ak = _team_key(match.away_team)
        if hk == key:
            gf = float(match.home_goals)
            ga = float(match.away_goals)
            attack_index = (gf + 0.35) / (home_avg + 0.35)
            defence_index = (ga + 0.35) / (away_avg + 0.35)
        elif ak == key:
            gf = float(match.away_goals)
            ga = float(match.home_goals)
            attack_index = (gf + 0.35) / (away_avg + 0.35)
            defence_index = (ga + 0.35) / (home_avg + 0.35)
        else:
            continue
        rows.append({
            "attack_index": attack_index,
            "defence_index": defence_index,
            "goal_diff": gf - ga,
        })
    return rows


def _build_regime_signals(history: list[MatchRow], team: str) -> dict[str, float]:
    rows = _team_signal_rows(history, team)
    sample_size = len(rows)
    recent = rows[-5:]
    reference = rows[-15:-5]
    # Require a real comparison window. Before then the detector is forced stable
    # by zero statistical deviations and confidence shrinkage.
    if len(recent) < 3 or len(reference) < 5:
        return {
            "attack_deviation": 0.0,
            "defence_deviation": 0.0,
            "volatility": 0.0,
            "structural_event_score": 0.0,
            "sample_size": float(sample_size),
        }
    recent_attack = _mean([r["attack_index"] for r in recent])
    ref_attack = _mean([r["attack_index"] for r in reference])
    recent_defence = _mean([r["defence_index"] for r in recent])
    ref_defence = _mean([r["defence_index"] for r in reference])
    attack_dev = abs(math.log(max(EPS, recent_attack) / max(EPS, ref_attack), 2.0))
    defence_dev = abs(math.log(max(EPS, recent_defence) / max(EPS, ref_defence), 2.0))
    recent_sd = statistics.pstdev([r["goal_diff"] for r in recent]) if len(recent) > 1 else 0.0
    ref_sd = statistics.pstdev([r["goal_diff"] for r in reference]) if len(reference) > 1 else 0.0
    volatility = abs(recent_sd - ref_sd) / max(0.75, ref_sd + 0.25)
    return {
        "attack_deviation": _clip(attack_dev),
        "defence_deviation": _clip(defence_dev),
        "volatility": _clip(volatility),
        "structural_event_score": 0.0,
        "sample_size": float(sample_size),
    }


def _mix_team_record(
    expert_records: list[dict[str, Any]],
    weights: list[float],
    baseline: dict[str, Any],
    blend_strength: float,
) -> dict[str, Any]:
    if len(expert_records) != len(EXPERT_HALF_LIVES) or len(weights) != len(EXPERT_HALF_LIVES):
        raise PlatformError("V6.24 expert record/weight dimension mismatch")
    if abs(sum(float(w) for w in weights) - 1.0) > 1e-9:
        raise PlatformError("V6.24 regime weights do not sum to one")
    alpha = min(1.0, max(0.0, float(blend_strength)))
    out = dict(baseline)
    for field in WEIGHTED_FIELDS:
        regime_value = sum(float(w) * float(record.get(field, 0.0)) for w, record in zip(weights, expert_records))
        baseline_value = float(baseline.get(field, 0.0))
        out[field] = (1.0 - alpha) * baseline_value + alpha * regime_value
    return out


def _state_with_team_records(
    baseline_state: dict[str, Any],
    home_key: str,
    home_record: dict[str, Any],
    away_key: str,
    away_record: dict[str, Any],
) -> dict[str, Any]:
    out = dict(baseline_state)
    teams = dict(baseline_state["team"])
    teams[home_key] = home_record
    teams[away_key] = away_record
    out["team"] = teams
    return out


def _matrix_from_state(
    state: dict[str, Any],
    home_team: str,
    away_team: str,
    params: dict[str, float],
    config: dict[str, Any],
    fixed_low_score_factors: dict[tuple[int, int], float],
) -> list[dict[str, Any]]:
    means = expected_goals(state, home_team, away_team, params, config)
    return build_score_matrix(
        float(means["mu_home"]),
        float(means["mu_away"]),
        float(state["nb_dispersion_k"]),
        float(params["beta_binomial_concentration"]),
        int(config["max_total_goals_exact"]),
        fixed_low_score_factors,
    )


def _one_probs(matrix: list[dict[str, Any]]) -> dict[str, float]:
    marginals = derive_score_marginals(matrix)
    return {key: float(marginals["1x2"][key]) for key in CLASSES}


def _rps_1x2(prob: dict[str, float], actual: str) -> float:
    truth = {
        "home": (1.0, 0.0, 0.0),
        "draw": (0.0, 1.0, 0.0),
        "away": (0.0, 0.0, 1.0),
    }[actual]
    c1 = prob["home"] - truth[0]
    c2 = prob["home"] + prob["draw"] - truth[0] - truth[1]
    return (c1 * c1 + c2 * c2) / 2.0


def _total_distribution(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {key: 0.0 for key in TOTAL_BUCKETS}
    for h, a, p in score_matrix_rows(matrix):
        total = int(h + a)
        key = str(total) if total <= 6 else "7+"
        out[key] += float(p)
    return out


def _total_rps(prob: dict[str, float], actual_total: int) -> float:
    actual_idx = min(7, int(actual_total))
    running = 0.0
    score = 0.0
    for idx, key in enumerate(TOTAL_BUCKETS[:-1]):
        running += float(prob[key])
        truth_cdf = 1.0 if actual_idx <= idx else 0.0
        score += (running - truth_cdf) ** 2
    return score / 7.0


def _joint_log_score(matrix: list[dict[str, Any]], hg: int, ag: int) -> float:
    probability = 0.0
    for h, a, p in score_matrix_rows(matrix):
        if int(h) == int(hg) and int(a) == int(ag):
            probability += float(p)
    return -math.log(max(EPS, probability))


def _new_metric() -> dict[str, Any]:
    return {
        "count": 0,
        "one_hits": 0,
        "one_brier_sum": 0.0,
        "one_rps_sum": 0.0,
        "one_log_sum": 0.0,
        "score_top1_hits": 0,
        "score_top3_hits": 0,
        "score_log_sum": 0.0,
        "total_top1_hits": 0,
        "total_rps_sum": 0.0,
        "prob_sum_max_residual": 0.0,
        "pick_counts": Counter(),
        "total_pick_counts": Counter(),
    }


def _add_metric(metric: dict[str, Any], matrix: list[dict[str, Any]], hg: int, ag: int) -> None:
    actual = _actual_result(hg, ag)
    one = _one_probs(matrix)
    one_pick = max(CLASSES, key=lambda key: one[key])
    total = _total_distribution(matrix)
    total_pick = max(TOTAL_BUCKETS, key=lambda key: total[key])
    actual_total = str(hg + ag) if hg + ag <= 6 else "7+"
    scores = top_scores(matrix, 3)
    actual_score = f"{hg}-{ag}"
    probability_sum = sum(float(p) for _, _, p in score_matrix_rows(matrix))
    metric["count"] += 1
    metric["one_hits"] += int(one_pick == actual)
    metric["one_brier_sum"] += sum((one[key] - (1.0 if key == actual else 0.0)) ** 2 for key in CLASSES)
    metric["one_rps_sum"] += _rps_1x2(one, actual)
    metric["one_log_sum"] += -math.log(max(EPS, one[actual]))
    metric["score_top1_hits"] += int(bool(scores) and scores[0]["score"] == actual_score)
    metric["score_top3_hits"] += int(any(item["score"] == actual_score for item in scores))
    metric["score_log_sum"] += _joint_log_score(matrix, hg, ag)
    metric["total_top1_hits"] += int(total_pick == actual_total)
    metric["total_rps_sum"] += _total_rps(total, hg + ag)
    metric["prob_sum_max_residual"] = max(metric["prob_sum_max_residual"], abs(probability_sum - 1.0))
    metric["pick_counts"][one_pick] += 1
    metric["total_pick_counts"][total_pick] += 1


def _merge_metric(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key in (
        "count", "one_hits", "one_brier_sum", "one_rps_sum", "one_log_sum",
        "score_top1_hits", "score_top3_hits", "score_log_sum", "total_top1_hits", "total_rps_sum",
    ):
        dst[key] += src[key]
    dst["prob_sum_max_residual"] = max(dst["prob_sum_max_residual"], src["prob_sum_max_residual"])
    dst["pick_counts"].update(src["pick_counts"])
    dst["total_pick_counts"].update(src["total_pick_counts"])


def _finalize_metric(metric: dict[str, Any]) -> dict[str, Any]:
    n = int(metric["count"])
    return {
        "count": n,
        "one_x_two": {
            "top1_hits": int(metric["one_hits"]),
            "top1_accuracy": metric["one_hits"] / n if n else None,
            "mean_brier": metric["one_brier_sum"] / n if n else None,
            "mean_rps": metric["one_rps_sum"] / n if n else None,
            "mean_log_loss": metric["one_log_sum"] / n if n else None,
            "pick_counts": dict(metric["pick_counts"]),
        },
        "score": {
            "top1_hits": int(metric["score_top1_hits"]),
            "top1_accuracy": metric["score_top1_hits"] / n if n else None,
            "top3_hits": int(metric["score_top3_hits"]),
            "top3_accuracy": metric["score_top3_hits"] / n if n else None,
            "mean_joint_log_score": metric["score_log_sum"] / n if n else None,
        },
        "total_goals_0_7plus": {
            "top1_hits": int(metric["total_top1_hits"]),
            "top1_accuracy": metric["total_top1_hits"] / n if n else None,
            "mean_rps": metric["total_rps_sum"] / n if n else None,
            "top1_bucket_counts": dict(metric["total_pick_counts"]),
        },
        "probability_sum_max_residual": metric["prob_sum_max_residual"],
    }


def _delta(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, float]:
    return {
        "one_x_two_top1_accuracy": candidate["one_x_two"]["top1_accuracy"] - base["one_x_two"]["top1_accuracy"],
        "one_x_two_mean_brier": candidate["one_x_two"]["mean_brier"] - base["one_x_two"]["mean_brier"],
        "one_x_two_mean_rps": candidate["one_x_two"]["mean_rps"] - base["one_x_two"]["mean_rps"],
        "one_x_two_mean_log_loss": candidate["one_x_two"]["mean_log_loss"] - base["one_x_two"]["mean_log_loss"],
        "score_top1_accuracy": candidate["score"]["top1_accuracy"] - base["score"]["top1_accuracy"],
        "score_top3_accuracy": candidate["score"]["top3_accuracy"] - base["score"]["top3_accuracy"],
        "score_mean_joint_log_score": candidate["score"]["mean_joint_log_score"] - base["score"]["mean_joint_log_score"],
        "total_goals_top1_accuracy": candidate["total_goals_0_7plus"]["top1_accuracy"] - base["total_goals_0_7plus"]["top1_accuracy"],
        "total_goals_mean_rps": candidate["total_goals_0_7plus"]["mean_rps"] - base["total_goals_0_7plus"]["mean_rps"],
    }


def _competition_result(competition_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
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
    by_day: dict[Any, list[MatchRow]] = defaultdict(list)
    for match in target:
        by_day[match.date.date()].append(match)

    ledger = RegimeLedger()
    baseline_metric = _new_metric()
    challenger_metric = _new_metric()
    skips = Counter()
    regime_counts = Counter()
    weight_sums = [0.0] * len(EXPERT_HALF_LIVES)
    blend_sum = 0.0
    snapshot_count = 0

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

                hg, ag = int(match.home_goals), int(match.away_goals)
                _add_metric(baseline_metric, baseline_matrix, hg, ag)
                _add_metric(challenger_metric, challenger_matrix, hg, ag)
                for snapshot in (home_snapshot, away_snapshot):
                    regime_counts[str(snapshot["regime"])] += 1
                    for idx, weight in enumerate(snapshot["weight_vector"]):
                        weight_sums[idx] += float(weight)
                    blend_sum += float(snapshot["blend_strength"])
                    snapshot_count += 1
            except PlatformError as exc:
                skips[str(exc)] += 1

        # Day-end barrier: only now can results from this date affect the ledger.
        post_history = [
            m for m in target
            if m.date.date() <= day
        ]
        proposals: list[dict[str, Any]] = []
        for team_key, count in sorted(day_team_counts.items()):
            proposal = build_post_settlement_proposal(
                team_key,
                ledger,
                _build_regime_signals(post_history, team_key),
                day.isoformat(),
                settled_increment=int(count),
            )
            proposals.append(proposal)
        settle_regime_day(ledger, proposals)

    if baseline_metric["count"] == 0:
        raise PlatformError(f"no eligible predictions for {competition_id} {season}")
    if baseline_metric["count"] != challenger_metric["count"]:
        raise PlatformError("baseline/V6.24 prediction count mismatch")

    base = _finalize_metric(baseline_metric)
    candidate = _finalize_metric(challenger_metric)
    diagnostics = {
        "expert_half_lives_days": list(EXPERT_HALF_LIVES),
        "regime_prediction_snapshot_counts": dict(regime_counts),
        "mean_prediction_weight": {
            str(int(EXPERT_HALF_LIVES[idx])): weight_sums[idx] / snapshot_count if snapshot_count else None
            for idx in range(len(EXPERT_HALF_LIVES))
        },
        "mean_blend_strength": blend_sum / snapshot_count if snapshot_count else None,
        "ledger_final": ledger.export(),
    }
    result = {
        "competition_id": competition_id,
        "season": season,
        "target_season_match_count": len(target),
        "eligible_prediction_count": base["count"],
        "coverage_rate": base["count"] / len(target),
        "formal_selected_parameters": selected,
        "parameter_selection_prior_seasons": fold.get("prior_seasons") or [],
        "baseline_formal_half_life_days": formal_params["half_life_days"],
        "baseline": base,
        "team_regime_state_v6240": candidate,
        "delta_candidate_minus_baseline": _delta(base, candidate),
        "diagnostics": diagnostics,
        "skips": dict(skips),
        "audit": {
            "same_day_predict_then_update_barrier": True,
            "post_settlement_ledger_updates_only": True,
            "experts_fixed_before_test": True,
            "expert_selection_from_target_results": False,
            "other_formal_parameters_held_fixed": True,
            "competition_level_nb_and_low_score_state_from_baseline": True,
            "only_team_weighted_sufficient_statistics_changed": True,
            "single_joint_score_matrix_for_all_targets": True,
            "target_season_outcome_fitted_calibration_used": False,
            "historical_odds_used": False,
            "market_coordination_used": False,
            "xg_signal_used": False,
            "lineup_signal_used": False,
            "coach_signal_used": False,
            "red_card_signal_used": False,
            "unavailable_signals_not_fabricated": True,
            "formal_weight": 0,
        },
    }
    return result, baseline_metric, challenger_metric


def _screen_decision(delta: dict[str, float] | None) -> str:
    if delta is None:
        return "INCONCLUSIVE"
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
        return "PROMISING_DEEPER_NESTED_OOS_REQUIRED"
    if not proper_nonworse:
        return "REJECT_OR_REDESIGN_PROPER_SCORE_REGRESSION"
    return "NEUTRAL_NO_CLEAR_GAIN"


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    aggregate_base = _new_metric()
    aggregate_candidate = _new_metric()
    for competition_id in competitions:
        try:
            result, raw_base, raw_candidate = _competition_result(competition_id)
            reports[competition_id] = result
            _merge_metric(aggregate_base, raw_base)
            _merge_metric(aggregate_candidate, raw_candidate)
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    base = _finalize_metric(aggregate_base)
    candidate = _finalize_metric(aggregate_candidate)
    delta = _delta(base, candidate) if base["count"] else None
    decision = _screen_decision(delta)
    payload = {
        "schema_version": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_STRICT_PIT_FORMAL_WEIGHT_0",
        "research_question": "Can deterministic team-regime detection improve historical borrowing without target-result tuning or breaking the unified joint matrix?",
        "design": {
            "expert_half_lives_days": list(EXPERT_HALF_LIVES),
            "regimes": ["STABLE", "WATCH", "TRANSITION"],
            "same_day_update_policy": "predict all matches first; update regime ledger after the date batch",
            "signals_available_in_processed_match_rows": ["goals", "venue", "competition_scoring_rate", "sample_size"],
            "signals_explicitly_unavailable_in_this_runner": ["xG", "red_cards", "lineups", "coach_change", "squad_turnover"],
            "unavailable_signals_fabricated": False,
            "one_joint_matrix_only": True,
            "formal_runtime_mutated": False,
        },
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "aggregate": {
            "baseline": base,
            "team_regime_state_v6240": candidate,
            "delta_candidate_minus_baseline": delta,
            "screen_decision": decision,
        },
        "four_target_review": {
            "one_x_two": "evaluated_if_runner_is_executed",
            "score": "evaluated_if_runner_is_executed",
            "total_goals": "evaluated_0_7plus_if_runner_is_executed",
            "handicap": "UNAVAILABLE_NO_REAL_FROZEN_HANDICAP_LINE_IN_THIS_REPLAY",
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "runtime_probability_change": False,
            "promotion_receipt_created": False,
            "automatic_promotion": False,
            "next_gate_if_promising": "per-domain nested cross-season walk-forward + multi-window stability + prospective freeze",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "competition_count_completed": payload["competition_count_completed"],
        "screen_decision": decision,
        "aggregate_delta": delta,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
