#!/usr/bin/env python3
"""Pre-OOF runtime core and fail-closed activation gate for V4.7 dynamic strength.

Historical validation for ESP full dynamic strength and NED allocation-only dynamic
strength applied the challenger to the raw Champion matrix *before* full-matrix OOF
calibration. This module preserves that order. No competition is activated merely
because the code exists: a competition/season promotion receipt is mandatory.
"""
from __future__ import annotations

import copy
import math
from datetime import timedelta
from pathlib import Path
from typing import Any

from dynamic_strength_challenger_v470 import blend_sufficient_statistic
from football_v460_engine import (
    _shrunk_rate,
    build_score_matrix,
    current_season_history,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import (
    ROOT,
    PlatformError,
    derive_score_marginals,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    read_processed_matches,
    settle_home_handicap,
    settle_over_total,
    sha256_file,
    top_scores,
)

RUNTIME_PATH = Path(__file__).resolve()
PROMOTION_ROOT = ROOT / "manifests" / "promotions"
SELECTION_PATH = ROOT / "manifests" / "dynamic_strength_next_season_selection_v470_status.json"
ESP_FINAL_PATH = ROOT / "manifests" / "dynamic_strength_final_chain_replay_v470" / "ESP_LaLiga.json"
NED_FINAL_PATH = ROOT / "manifests" / "dynamic_strength_allocation_only_final_chain_v470" / "NED_Eredivisie.json"
EPS = 1e-15


def _blended_rate(
    current_num: float,
    current_n: float,
    prior_num: float,
    prior_n: float,
    borrowing_weight: float,
    max_prior: float,
) -> tuple[float, float]:
    if current_n <= 0:
        raise PlatformError("current sufficient statistic has zero denominator")
    current_value = current_num / current_n
    if prior_n <= 0 or max_prior <= 0 or borrowing_weight <= 0:
        return current_value, current_n
    prior_value = prior_num / prior_n
    blended = blend_sufficient_statistic(
        current_value,
        current_n,
        prior_value,
        prior_n,
        borrowing_weight,
        max_prior_equivalent_matches=max_prior,
    )
    return float(blended["blended_value"]), float(blended["current_effective_n"] + blended["borrowed_prior_effective_n"])


def build_dynamic_strength_matrix(
    *,
    mode: str,
    current_state: dict[str, Any],
    prior_state: dict[str, Any] | None,
    home_team: str,
    away_team: str,
    home_borrowing_weight: float,
    away_borrowing_weight: float,
    max_prior_equivalent_matches: float,
    params: dict[str, float],
    config: dict[str, Any],
    champion_mu_total: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the raw pre-OOF challenger matrix using the validated research formula."""
    home_key = normalize_team_token(home_team)
    away_key = normalize_team_token(away_team)
    if home_key not in current_state["team"] or away_key not in current_state["team"]:
        raise PlatformError("current team state unavailable")
    current_home = current_state["team"][home_key]
    current_away = current_state["team"][away_key]
    minimum_raw = int(config["minimum_team_raw_matches"])
    if int(current_home["home_raw_matches"]) < minimum_raw or int(current_away["away_raw_matches"]) < minimum_raw:
        raise PlatformError("current-season venue sample below minimum")

    prior_home = prior_state["team"].get(home_key) if prior_state else None
    prior_away = prior_state["team"].get(away_key) if prior_state else None
    home_weight = 0.0 if prior_home is None else min(1.0, max(0.0, float(home_borrowing_weight)))
    away_weight = 0.0 if prior_away is None else min(1.0, max(0.0, float(away_borrowing_weight)))
    max_prior = max(0.0, float(max_prior_equivalent_matches))

    def rate(cur: dict[str, Any], prv: dict[str, Any] | None, num_key: str, den_key: str, weight: float) -> tuple[float, float]:
        return _blended_rate(
            float(cur[num_key]),
            float(cur[den_key]),
            float(prv[num_key]) if prv else 0.0,
            float(prv[den_key]) if prv else 0.0,
            weight,
            max_prior,
        )

    hgf, hgf_n = rate(current_home, prior_home, "home_gf", "home_matches", home_weight)
    hga, hga_n = rate(current_home, prior_home, "home_ga", "home_matches", home_weight)
    agf, agf_n = rate(current_away, prior_away, "away_gf", "away_matches", away_weight)
    aga, aga_n = rate(current_away, prior_away, "away_ga", "away_matches", away_weight)

    league_home = float(current_state["league_home_goals"])
    league_away = float(current_state["league_away_goals"])
    league_total = float(current_state["mean_total_goals"])
    team_prior = float(params["team_prior_matches"])
    home_gf_rate = _shrunk_rate(hgf * hgf_n, hgf_n, league_home, team_prior)
    home_ga_rate = _shrunk_rate(hga * hga_n, hga_n, league_away, team_prior)
    away_gf_rate = _shrunk_rate(agf * agf_n, agf_n, league_away, team_prior)
    away_ga_rate = _shrunk_rate(aga * aga_n, aga_n, league_home, team_prior)
    home_signal = league_home * (home_gf_rate / league_home) * (away_ga_rate / league_home)
    away_signal = league_away * (away_gf_rate / league_away) * (home_ga_rate / league_away)
    minimum_mu = float(params["minimum_goal_mean"])
    maximum_mu = float(params["maximum_goal_mean"])
    home_signal = min(maximum_mu, max(minimum_mu, home_signal))
    away_signal = min(maximum_mu, max(minimum_mu, away_signal))
    share = home_signal / max(EPS, home_signal + away_signal)

    if mode == "full_dynamic_strength":
        htot_cur = float(current_home["home_gf"] + current_home["home_ga"])
        htot_prv = float(prior_home["home_gf"] + prior_home["home_ga"]) if prior_home else 0.0
        atot_cur = float(current_away["away_gf"] + current_away["away_ga"])
        atot_prv = float(prior_away["away_gf"] + prior_away["away_ga"]) if prior_away else 0.0
        htot, htot_n = _blended_rate(
            htot_cur,
            float(current_home["home_matches"]),
            htot_prv,
            float(prior_home["home_matches"]) if prior_home else 0.0,
            home_weight,
            max_prior,
        )
        atot, atot_n = _blended_rate(
            atot_cur,
            float(current_away["away_matches"]),
            atot_prv,
            float(prior_away["away_matches"]) if prior_away else 0.0,
            away_weight,
            max_prior,
        )
        home_total_rate = _shrunk_rate(htot * htot_n, htot_n, league_total, team_prior)
        away_total_rate = _shrunk_rate(atot * atot_n, atot_n, league_total, team_prior)
        pair_total = math.sqrt(max(EPS, home_total_rate) * max(EPS, away_total_rate))
        signal_weight = min(1.0, max(0.0, float(params.get("direct_total_signal_weight", 1.0))))
        mu_total = math.exp(
            (1.0 - signal_weight) * math.log(max(EPS, league_total))
            + signal_weight * math.log(max(EPS, pair_total))
        )
        mu_total = min(2.0 * maximum_mu, max(2.0 * minimum_mu, mu_total))
    elif mode == "allocation_only_preserve_direct_total":
        if champion_mu_total is None or not math.isfinite(float(champion_mu_total)) or float(champion_mu_total) <= 0.0:
            raise PlatformError("allocation-only dynamic strength requires valid Champion mu_total")
        mu_total = float(champion_mu_total)
    else:
        raise PlatformError(f"unsupported dynamic-strength runtime mode: {mode}")

    factors = low_score_factors(current_state, params)
    matrix = build_score_matrix(
        mu_total * share,
        mu_total * (1.0 - share),
        float(current_state["nb_dispersion_k"]),
        float(params["beta_binomial_concentration"]),
        int(config["max_total_goals_exact"]),
        factors,
    )
    return matrix, {
        "mode": mode,
        "home_borrowing_weight": home_weight,
        "away_borrowing_weight": away_weight,
        "max_prior_equivalent_matches": max_prior,
        "mu_total": mu_total,
        "home_share": share,
    }


def _promotion_path(competition_id: str) -> Path:
    return PROMOTION_ROOT / f"{competition_id}_dynamic_strength_v470.json"


def _expected_final_path(competition_id: str) -> Path:
    if competition_id == "ESP_LaLiga":
        return ESP_FINAL_PATH
    if competition_id == "NED_Eredivisie":
        return NED_FINAL_PATH
    raise PlatformError("dynamic-strength pre-OOF runtime is not validated for this competition")


def _load_promotion(competition_id: str, target_season: str, live_audit: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _promotion_path(competition_id)
    if not path.exists():
        raise PlatformError("competition/season dynamic-strength promotion receipt missing")
    receipt = load_json(path)
    selection = load_json(SELECTION_PATH)
    selection_report = (selection.get("reports") or {}).get(competition_id) or {}
    final_path = _expected_final_path(competition_id)
    final = load_json(final_path)
    checks = {
        "promotion_status": receipt.get("promotion_status") == "PROMOTED",
        "competition_match": receipt.get("competition_id") == competition_id,
        "target_season_match": str(receipt.get("target_season") or "") == target_season,
        "module_match": receipt.get("module") == "adaptive_commensurate_strength_v470",
        "activation_order_pre_oof": receipt.get("activation_order") == "pre_oof_matrix_calibration",
        "formal_weight_full_transform": float(receipt.get("formal_weight", 0.0)) == 1.0,
        "candidate_match": receipt.get("candidate_id") == live_audit.get("candidate_id") == selection_report.get("selected_candidate"),
        "mode_match": receipt.get("mode") == live_audit.get("candidate_mode") == selection_report.get("mode"),
        "selection_sha_match": (receipt.get("bound_sha256") or {}).get("next_season_selection") == sha256_file(SELECTION_PATH),
        "final_chain_sha_match": (receipt.get("bound_sha256") or {}).get("final_chain_receipt") == sha256_file(final_path),
        "runtime_sha_match": (receipt.get("bound_sha256") or {}).get("runtime_module") == sha256_file(RUNTIME_PATH),
    }
    if not all(checks.values()):
        raise PlatformError(f"dynamic-strength promotion receipt hash/invariant failure: {checks}")
    return receipt, checks


def _prior_state(competition_id: str, prior_season: str, params: dict[str, float], config: dict[str, Any]) -> dict[str, Any] | None:
    rows = [match for match in read_processed_matches(competition_id) if str(match.season) == prior_season]
    if not rows:
        return None
    rows.sort(key=lambda item: (item.date, item.home_team, item.away_team))
    cutoff = max(item.date for item in rows) + timedelta(days=1)
    try:
        return fit_current_season_state(rows, cutoff, params, config)
    except PlatformError:
        return None


def _line_market(matrix: list[dict[str, Any]], line: float, settlement_fn) -> dict[str, float]:
    result = {"win": 0.0, "push": 0.0, "loss": 0.0}
    for cell in matrix:
        settlement = settlement_fn(int(cell["home_goals"]), int(cell["away_goals"]), line)
        probability = float(cell["probability"])
        for key in result:
            result[key] += probability * settlement[key]
    return result


def apply_promoted_dynamic_strength_pre_oof(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    """Apply only a hash-bound promoted dynamic-strength transform; otherwise no-op."""
    output = copy.deepcopy(calculation)
    identity = context.get("match_identity") or {}
    competition_id = str(identity.get("competition_id") or "")
    target_season = str(identity.get("season") or "")
    live_audit = context.get("dynamic_strength_live_input_audit") or {}
    if competition_id not in {"ESP_LaLiga", "NED_Eredivisie"}:
        output["dynamic_strength_pre_oof_audit"] = {
            "status": "不适用",
            "formal_weight": 0,
            "probability_mutation": False,
            "reason": "competition has no validated dynamic-strength runtime route",
        }
        return output
    if live_audit.get("status") != "通过":
        output["dynamic_strength_pre_oof_audit"] = {
            "status": "不可用",
            "formal_weight": 0,
            "probability_mutation": False,
            "reason": "question-time dynamic-strength PIT evidence did not pass",
        }
        return output
    try:
        receipt, receipt_checks = _load_promotion(competition_id, target_season, live_audit)
    except PlatformError as exc:
        output["dynamic_strength_pre_oof_audit"] = {
            "status": "未启用",
            "formal_weight": 0,
            "probability_mutation": False,
            "reason": str(exc),
        }
        return output

    cutoff = parse_iso_datetime(identity.get("freeze_time_utc"), "freeze_time_utc")
    matches = read_processed_matches(competition_id)
    _, history = current_season_history(matches, cutoff, target_season)
    params = output.get("model_audit", {}).get("parameters")
    if not isinstance(params, dict):
        raise PlatformError("formal calculation parameters missing before dynamic-strength transform")
    params = {key: float(value) for key, value in params.items()}
    config = load_config()
    current_state = fit_current_season_state(history, cutoff, params, config)
    prior_state = _prior_state(competition_id, str(live_audit.get("prior_season") or ""), params, config)
    champion_mu_total = output.get("model_audit", {}).get("team_sample", {}).get("mu_total")
    matrix, transform_audit = build_dynamic_strength_matrix(
        mode=str(receipt["mode"]),
        current_state=current_state,
        prior_state=prior_state,
        home_team=str(identity.get("home_team") or ""),
        away_team=str(identity.get("away_team") or ""),
        home_borrowing_weight=float((live_audit.get("home") or {}).get("borrowing_weight_research_candidate", 0.0)),
        away_borrowing_weight=float((live_audit.get("away") or {}).get("borrowing_weight_research_candidate", 0.0)),
        max_prior_equivalent_matches=float((live_audit.get("home") or {}).get("max_prior_equivalent_matches", 0.0)),
        params=params,
        config=config,
        champion_mu_total=float(champion_mu_total) if champion_mu_total is not None else None,
    )
    marginals = derive_score_marginals(matrix)
    if abs(float(marginals["probability_sum"]) - 1.0) > 1e-10:
        raise PlatformError("dynamic-strength pre-OOF matrix failed probability conservation")
    output["probabilities"] = {
        "one_x_two": marginals["1x2"],
        "total_goals": marginals["total_goals"],
        "btts_yes": marginals["btts_yes"],
        "score_matrix": matrix,
    }
    derived = output.get("derived_markets") or {}
    if isinstance(derived.get("home_handicap"), dict) and isinstance(derived["home_handicap"].get("line"), (int, float)):
        line = float(derived["home_handicap"]["line"])
        derived["home_handicap"] = {"line": line, **_line_market(matrix, line, settle_home_handicap)}
    if isinstance(derived.get("over_total"), dict) and isinstance(derived["over_total"].get("line"), (int, float)):
        line = float(derived["over_total"]["line"])
        derived["over_total"] = {"line": line, **_line_market(matrix, line, settle_over_total)}
    output["derived_markets"] = derived
    ranking = top_scores(matrix, 10)
    output["dynamic_strength_pre_oof_audit"] = {
        "status": "通过",
        "registration_status": "V4.7挑战层逐赛事域已晋级",
        "competition_id": competition_id,
        "target_season": target_season,
        "candidate_id": receipt["candidate_id"],
        "mode": receipt["mode"],
        "activation_order": "pre_oof_matrix_calibration",
        "formal_weight": 1.0,
        "receipt_checks": receipt_checks,
        "transform_audit": transform_audit,
        "probability_sum_residual": float(marginals["probability_sum"]) - 1.0,
        "raw_top_score_after_transform": ranking[0]["score"] if ranking else None,
        "probability_mutation": True,
        "policy": "Competition/season receipt-gated pre-OOF dynamic-strength transform; final formal outputs remain subject to OOF calibration and unified-matrix audit.",
    }
    return output
