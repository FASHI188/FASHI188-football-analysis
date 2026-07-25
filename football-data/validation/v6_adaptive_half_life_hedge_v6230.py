#!/usr/bin/env python3
"""V6.23.0 fixed-expert adaptive half-life Hedge challenger.

Purpose
-------
Test one narrow increment only: whether team-state history borrowing should adapt online
instead of using one fixed exponential half-life. This is RESEARCH ONLY and never changes
CURRENT, formal weights, promotion receipts, or runtime outputs.

Leakage contract
----------------
* Experts are fixed ex ante at 45/90/180/360 days. No grid search and no test-result choice.
* Each team's expert cumulative losses are updated only after the relevant match has ended.
* All matches on a calendar date are predicted first; all loss-ledger updates are applied only
  after every match from that date has been predicted. This prevents same-day result leakage.
* The target-season formal parameter set is the already-existing PIT/OOS fold selection.
  Every parameter other than half_life_days is identical between baseline and challenger.
* The comparison intentionally does NOT apply a target-season outcome-fitted calibration
  transform. Baseline and challenger are compared on the same uncalibrated core layer so the
  only challenged degree of freedom is team-state borrowing speed.

Hedge rule
----------
For K=4 experts and n previously settled eligible matches for a team:
    eta_n = sqrt(2 * log(K) / n), n>=1
    w_j   = exp(-eta_n * cumulative_loss_j) / sum_k exp(...)
At n=0 the weights are uniform. Per-expert loss is multiclass 1X2 Brier / 2, hence [0,1].

Scope
-----
Fast strict-PIT screen on each of the 17 domains' last complete season. This screen can reject
or justify a deeper nested walk-forward test, but cannot promote the challenger by itself.
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
    PlatformError,
    derive_score_marginals,
    load_json,
    normalize_team_token,
    read_processed_matches,
    score_matrix_rows,
    top_scores,
)

OUT = ROOT / "manifests" / "v6_adaptive_half_life_hedge_v6230_status.json"
SCHEMA = "V6.23.0-adaptive-half-life-hedge-r1"
EXPERT_HALF_LIVES = (45.0, 90.0, 180.0, 360.0)
K = len(EXPERT_HALF_LIVES)
CLASSES = ("home", "draw", "away")
TOTAL_BUCKETS = ("0", "1", "2", "3", "4", "5", "6", "7+")
EPS = 1e-15


def _team_key(name: str) -> str:
    return normalize_team_token(name)


def _new_ledger() -> dict[str, Any]:
    return {"n": 0, "losses": [0.0] * K}


def _hedge_weights(ledger: dict[str, Any]) -> tuple[list[float], float | None]:
    n = int(ledger.get("n", 0))
    losses = [float(x) for x in ledger.get("losses", [0.0] * K)]
    if n <= 0:
        return [1.0 / K] * K, None
    eta = math.sqrt(2.0 * math.log(K) / n)
    logits = [-eta * loss for loss in losses]
    m = max(logits)
    raw = [math.exp(x - m) for x in logits]
    z = sum(raw)
    return [x / z for x in raw], eta


def _mix_team_record(records: list[dict[str, Any]], weights: list[float], baseline: dict[str, Any]) -> dict[str, Any]:
    if len(records) != K or len(weights) != K:
        raise PlatformError("expert record/weight dimension mismatch")
    out = dict(baseline)
    weighted_fields = (
        "effective_matches",
        "home_matches",
        "away_matches",
        "home_gf",
        "home_ga",
        "away_gf",
        "away_ga",
    )
    for field in weighted_fields:
        out[field] = sum(float(w) * float(r.get(field, 0.0)) for w, r in zip(weights, records))
    return out


def _state_with_two_team_records(
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
    m = derive_score_marginals(matrix)
    return {k: float(m["1x2"][k]) for k in CLASSES}


def _norm_brier(prob: dict[str, float], actual: str) -> float:
    return 0.5 * sum((float(prob[k]) - (1.0 if k == actual else 0.0)) ** 2 for k in CLASSES)


def _rps_1x2(prob: dict[str, float], actual: str) -> float:
    truth = {"home": (1.0, 0.0, 0.0), "draw": (0.0, 1.0, 0.0), "away": (0.0, 0.0, 1.0)}[actual]
    c1 = prob["home"] - truth[0]
    c2 = prob["home"] + prob["draw"] - truth[0] - truth[1]
    return (c1 * c1 + c2 * c2) / 2.0


def _total_distribution(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {k: 0.0 for k in TOTAL_BUCKETS}
    for h, a, p in score_matrix_rows(matrix):
        t = int(h + a)
        key = str(t) if t <= 6 else "7+"
        out[key] += float(p)
    return out


def _total_rps(prob: dict[str, float], actual_total: int) -> float:
    actual_idx = min(7, int(actual_total))
    running_p = 0.0
    s = 0.0
    for i, key in enumerate(TOTAL_BUCKETS[:-1]):
        running_p += float(prob[key])
        truth_cdf = 1.0 if actual_idx <= i else 0.0
        s += (running_p - truth_cdf) ** 2
    return s / 7.0


def _joint_log_score(matrix: list[dict[str, Any]], hg: int, ag: int) -> float:
    p = 0.0
    for h, a, q in score_matrix_rows(matrix):
        if int(h) == int(hg) and int(a) == int(ag):
            p += float(q)
    return -math.log(max(EPS, p))


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
    pick = max(CLASSES, key=lambda k: one[k])
    total = _total_distribution(matrix)
    total_pick = max(TOTAL_BUCKETS, key=lambda k: total[k])
    actual_total_key = str(hg + ag) if hg + ag <= 6 else "7+"
    scores = top_scores(matrix, 3)
    actual_score = f"{hg}-{ag}"
    prob_sum = sum(float(p) for _, _, p in score_matrix_rows(matrix))
    metric["count"] += 1
    metric["one_hits"] += int(pick == actual)
    metric["one_brier_sum"] += 2.0 * _norm_brier(one, actual)
    metric["one_rps_sum"] += _rps_1x2(one, actual)
    metric["one_log_sum"] += -math.log(max(EPS, one[actual]))
    metric["score_top1_hits"] += int(bool(scores) and scores[0]["score"] == actual_score)
    metric["score_top3_hits"] += int(any(s["score"] == actual_score for s in scores))
    metric["score_log_sum"] += _joint_log_score(matrix, hg, ag)
    metric["total_top1_hits"] += int(total_pick == actual_total_key)
    metric["total_rps_sum"] += _total_rps(total, hg + ag)
    metric["prob_sum_max_residual"] = max(metric["prob_sum_max_residual"], abs(prob_sum - 1.0))
    metric["pick_counts"][pick] += 1
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


def _delta(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    return {
        "one_x_two_top1_accuracy": cand["one_x_two"]["top1_accuracy"] - base["one_x_two"]["top1_accuracy"],
        "one_x_two_mean_brier": cand["one_x_two"]["mean_brier"] - base["one_x_two"]["mean_brier"],
        "one_x_two_mean_rps": cand["one_x_two"]["mean_rps"] - base["one_x_two"]["mean_rps"],
        "one_x_two_mean_log_loss": cand["one_x_two"]["mean_log_loss"] - base["one_x_two"]["mean_log_loss"],
        "score_top1_accuracy": cand["score"]["top1_accuracy"] - base["score"]["top1_accuracy"],
        "score_top3_accuracy": cand["score"]["top3_accuracy"] - base["score"]["top3_accuracy"],
        "score_mean_joint_log_score": cand["score"]["mean_joint_log_score"] - base["score"]["mean_joint_log_score"],
        "total_goals_top1_accuracy": cand["total_goals_0_7plus"]["top1_accuracy"] - base["total_goals_0_7plus"]["top1_accuracy"],
        "total_goals_mean_rps": cand["total_goals_0_7plus"]["mean_rps"] - base["total_goals_0_7plus"]["mean_rps"],
    }


def _backtest_competition(competition_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
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
    for m in target:
        by_day[m.date.date()].append(m)
    ledgers: dict[str, dict[str, Any]] = defaultdict(_new_ledger)
    baseline_metric = _new_metric()
    adaptive_metric = _new_metric()
    skip = Counter()
    weight_sum = [0.0] * K
    weight_obs = 0
    eta_sum = 0.0
    eta_obs = 0
    expert_dominance = Counter()
    for day in sorted(by_day):
        pending: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "losses": [0.0] * K})
        for match in sorted(by_day[day], key=lambda m: (m.home_team, m.away_team)):
            try:
                history_season, history = current_season_history(all_matches, match.date, season)
                if history_season != season:
                    raise PlatformError("history season mismatch")
                baseline_state = fit_current_season_state(history, match.date, formal_params, config)
                baseline_factors = low_score_factors(baseline_state, formal_params)
                baseline_matrix = _matrix_from_state(baseline_state, match.home_team, match.away_team, formal_params, config, baseline_factors)
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
                home_w, home_eta = _hedge_weights(ledgers[home_key])
                away_w, away_eta = _hedge_weights(ledgers[away_key])
                mixed_home = _mix_team_record(home_records, home_w, baseline_state["team"][home_key])
                mixed_away = _mix_team_record(away_records, away_w, baseline_state["team"][away_key])
                adaptive_state = _state_with_two_team_records(baseline_state, home_key, mixed_home, away_key, mixed_away)
                adaptive_matrix = _matrix_from_state(adaptive_state, match.home_team, match.away_team, formal_params, config, baseline_factors)
                hg, ag = int(match.home_goals), int(match.away_goals)
                actual = _actual_result(hg, ag)
                home_losses = []
                for i in range(K):
                    cf_state = _state_with_two_team_records(baseline_state, home_key, home_records[i], away_key, mixed_away)
                    cf_matrix = _matrix_from_state(cf_state, match.home_team, match.away_team, formal_params, config, baseline_factors)
                    home_losses.append(_norm_brier(_one_probs(cf_matrix), actual))
                away_losses = []
                for i in range(K):
                    cf_state = _state_with_two_team_records(baseline_state, home_key, mixed_home, away_key, away_records[i])
                    cf_matrix = _matrix_from_state(cf_state, match.home_team, match.away_team, formal_params, config, baseline_factors)
                    away_losses.append(_norm_brier(_one_probs(cf_matrix), actual))
                _add_metric(baseline_metric, baseline_matrix, hg, ag)
                _add_metric(adaptive_metric, adaptive_matrix, hg, ag)
                for w in (home_w, away_w):
                    for i, value in enumerate(w):
                        weight_sum[i] += value
                    weight_obs += 1
                    expert_dominance[str(EXPERT_HALF_LIVES[max(range(K), key=lambda j: w[j])])] += 1
                for eta in (home_eta, away_eta):
                    if eta is not None:
                        eta_sum += eta
                        eta_obs += 1
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
    if baseline_metric["count"] == 0:
        raise PlatformError(f"no eligible predictions for {competition_id} {season}")
    if baseline_metric["count"] != adaptive_metric["count"]:
        raise PlatformError("baseline/adaptive prediction count mismatch")
    base = _finalize_metric(baseline_metric)
    cand = _finalize_metric(adaptive_metric)
    team_final = {}
    for team, ledger in sorted(ledgers.items()):
        w, eta = _hedge_weights(ledger)
        team_final[team] = {
            "settled_eligible_matches": int(ledger["n"]),
            "cumulative_normalized_brier_losses": {str(int(EXPERT_HALF_LIVES[i])): float(ledger["losses"][i]) for i in range(K)},
            "next_weights": {str(int(EXPERT_HALF_LIVES[i])): w[i] for i in range(K)},
            "next_eta": eta,
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
        "adaptive_half_life_hedge": cand,
        "delta_candidate_minus_baseline": _delta(base, cand),
        "weight_diagnostics": {
            "expert_half_lives_days": list(EXPERT_HALF_LIVES),
            "mean_prediction_weight": {str(int(EXPERT_HALF_LIVES[i])): weight_sum[i] / weight_obs if weight_obs else None for i in range(K)},
            "dominant_expert_observation_counts": dict(expert_dominance),
            "mean_noninitial_eta": eta_sum / eta_obs if eta_obs else None,
            "team_count": len(team_final),
            "team_final_ledgers": team_final,
        },
        "skips": dict(skip),
        "audit": {
            "fixed_experts_not_test_selected": True,
            "same_day_predict_then_update_barrier": True,
            "loss_update_after_result_only": True,
            "loss_is_normalized_1x2_brier_in_0_1": True,
            "other_formal_parameters_held_fixed": True,
            "competition_level_nb_and_low_score_state_from_baseline": True,
            "only_team_weighted_sufficient_statistics_mixed": True,
            "target_season_outcome_fitted_calibration_used": False,
            "historical_odds_used": False,
            "market_coordination_used": False,
            "formal_weight": 0,
        },
    }
    return result, baseline_metric, adaptive_metric


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    aggregate_base = _new_metric()
    aggregate_cand = _new_metric()
    for cid in competitions:
        try:
            result, raw_base, raw_cand = _backtest_competition(cid)
            reports[cid] = result
            _merge_metric(aggregate_base, raw_base)
            _merge_metric(aggregate_cand, raw_cand)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    base = _finalize_metric(aggregate_base)
    cand = _finalize_metric(aggregate_cand)
    aggregate_delta = _delta(base, cand) if base["count"] else None
    direction = "INCONCLUSIVE"
    if aggregate_delta is not None:
        proper_nonworse = (
            aggregate_delta["one_x_two_mean_brier"] <= 0.0
            and aggregate_delta["one_x_two_mean_rps"] <= 0.0
            and aggregate_delta["one_x_two_mean_log_loss"] <= 0.0
            and aggregate_delta["total_goals_mean_rps"] <= 0.0
            and aggregate_delta["score_mean_joint_log_score"] <= 0.0
        )
        any_accuracy_gain = (
            aggregate_delta["one_x_two_top1_accuracy"] > 0.0
            or aggregate_delta["score_top1_accuracy"] > 0.0
            or aggregate_delta["total_goals_top1_accuracy"] > 0.0
        )
        if proper_nonworse and any_accuracy_gain:
            direction = "PROMISING_DEEPER_NESTED_OOS_REQUIRED"
        elif not proper_nonworse:
            direction = "REJECT_OR_REDESIGN_PROPER_SCORE_REGRESSION"
        else:
            direction = "NEUTRAL_NO_CLEAR_GAIN"
    payload = {
        "schema_version": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_STRICT_PIT_FAST_SCREEN_FORMAL_WEIGHT_0",
        "research_question": "Does online team-specific adaptation over fixed 45/90/180/360-day half-life experts improve the frozen current-season NB/Beta-Binomial core when all other parameters are held fixed?",
        "design": {
            "expert_half_lives_days": list(EXPERT_HALF_LIVES),
            "experts_fixed_before_test": True,
            "expert_selection_from_test_results": False,
            "team_specific_cumulative_loss": True,
            "loss": "multiclass_1x2_brier_divided_by_2_bounded_0_1",
            "learning_rate": "eta_n=sqrt(2*ln(4)/n) for n>=1; uniform weights at n=0",
            "weight_rule": "w_j proportional to exp(-eta_n*cumulative_loss_j)",
            "same_day_update_policy": "predict all matches first; apply all result-loss updates after the date batch",
            "other_nb_beta_binomial_and_core_parameters": "target-season existing PIT/OOS-selected formal parameters, unchanged",
            "competition_level_state": "formal baseline half-life; challenger mixes only team weighted sufficient statistics",
            "calibration": "disabled equally for baseline and challenger in this screen to avoid target-season outcome-fitted transform",
            "scope": "17-domain last-complete-season strict-PIT fast screen",
        },
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "aggregate": {
            "baseline": base,
            "adaptive_half_life_hedge": cand,
            "delta_candidate_minus_baseline": aggregate_delta,
            "screen_decision": direction,
        },
        "four_target_review": {
            "one_x_two": "evaluated",
            "score": "evaluated",
            "total_goals": "evaluated_0_7plus",
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
            "next_gate_if_promising": "per-domain nested cross-season walk-forward + multi-window stability + prospective freeze before any promotion review",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "competition_count_completed": payload["competition_count_completed"],
        "screen_decision": direction,
        "aggregate_delta": aggregate_delta,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
