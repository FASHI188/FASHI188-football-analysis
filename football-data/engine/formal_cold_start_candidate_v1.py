#!/usr/bin/env python3
"""Fail-closed engineering candidate for formal new-season/new-league cold start.

This module is deliberately separate from ``football_v460_engine.py``.  It does
not change the hash-bound formal engine or any production artifact.  Every
cold-start input is explicit and receipt-bound; the result remains a challenger
with formal_weight=0, exact_gate=false and No Bet.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from football_v460_engine import (
    LOW_SCORE_CELLS,
    _merge_parameters,
    _shrunk_rate,
    build_score_matrix,
    conditional_goal_difference_by_total,
    fit_current_season_state,
    load_config as load_formal_config,
    low_score_factors,
    minimum_score_set,
    predict_from_history,
)
from platform_core import (
    ROOT,
    MatchRow,
    PlatformError,
    derive_score_marginals,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    sha256_json,
    top_scores,
)

CANDIDATE_CONFIG_PATH = ROOT / "config" / "formal_cold_start_candidate_v1.json"
ENGINE_PATH = Path(__file__).resolve()

STABLE_CURRENT_SEASON = "STABLE_CURRENT_SEASON"
PRIOR_SEASON_SHRINKAGE = "PRIOR_SEASON_SHRINKAGE"
GENERIC_VALIDATED_FALLBACK = "GENERIC_VALIDATED_FALLBACK"
HARD_FAIL = "HARD_FAIL"


def load_candidate_config() -> dict[str, Any]:
    return load_json(CANDIDATE_CONFIG_PATH)


def _fail(reason: str) -> None:
    raise PlatformError(f"{HARD_FAIL}: {reason}")


def _bounded_number(value: Any, field: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        _fail(f"{field} must be numeric")
    if not math.isfinite(number) or number < minimum or number > maximum:
        _fail(f"{field} outside [{minimum}, {maximum}]")
    return number


def _validate_request(
    history: Iterable[MatchRow],
    competition_id: str,
    season: str,
    home_team: str,
    away_team: str,
    cutoff: datetime,
) -> list[MatchRow]:
    if not competition_id or not season or not home_team or not away_team:
        _fail("competition_id, season, home_team and away_team are required")
    if normalize_team_token(home_team) == normalize_team_token(away_team):
        _fail("home and away teams must be distinct")
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        _fail("cutoff must include a timezone offset")

    rows = list(history)
    seen: set[tuple[Any, ...]] = set()
    for index, match in enumerate(rows):
        if not isinstance(match, MatchRow):
            _fail(f"history[{index}] is not a MatchRow")
        if match.competition_id != competition_id:
            _fail(f"history[{index}] competition mismatch")
        if match.season != season:
            _fail(f"history[{index}] season mismatch")
        if match.date.tzinfo is None or match.date.utcoffset() is None:
            _fail(f"history[{index}] date lacks timezone")
        if match.date.date() >= cutoff.date():
            _fail(f"history[{index}] is not strictly before cutoff calendar date")
        if match.home_goals < 0 or match.away_goals < 0:
            _fail(f"history[{index}] has negative goals")
        key = (match.date, normalize_team_token(match.home_team), normalize_team_token(match.away_team))
        if key in seen:
            _fail(f"duplicate history row at index {index}")
        seen.add(key)
    rows.sort(key=lambda row: (row.date, row.home_team, row.away_team))
    return rows


def _venue_counts(history: list[MatchRow], home_team: str, away_team: str) -> tuple[int, int]:
    home_key = normalize_team_token(home_team)
    away_key = normalize_team_token(away_team)
    home_count = sum(normalize_team_token(row.home_team) == home_key for row in history)
    away_count = sum(normalize_team_token(row.away_team) == away_key for row in history)
    return home_count, away_count


def _receipt_body(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "receipt_sha256"}


def _validate_receipted_artifact(
    artifact: Any,
    receipt: Any,
    *,
    artifact_type: str,
    competition_id: str,
    season: str,
    cutoff: datetime,
    candidate_config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(artifact, dict) or not isinstance(receipt, dict):
        _fail(f"{artifact_type} artifact and receipt must be explicit objects")
    contract = candidate_config["artifact_contract"]
    schema = contract["schema_version"]
    if artifact.get("schema_version") != schema or receipt.get("schema_version") != schema:
        _fail(f"{artifact_type} schema mismatch")
    if artifact.get("artifact_type") != artifact_type or receipt.get("artifact_type") != artifact_type:
        _fail(f"{artifact_type} type binding mismatch")
    if not isinstance(artifact.get("version"), str) or not artifact["version"].strip():
        _fail(f"{artifact_type} version missing")
    if receipt.get("artifact_version") != artifact.get("version"):
        _fail(f"{artifact_type} version receipt mismatch")
    if receipt.get("validation_status") != contract["required_validation_status"]:
        _fail(f"{artifact_type} receipt is not validated")
    if receipt.get("artifact_sha256") != sha256_json(artifact):
        _fail(f"{artifact_type} artifact hash mismatch")
    if receipt.get("receipt_sha256") != sha256_json(_receipt_body(receipt)):
        _fail(f"{artifact_type} receipt hash mismatch")
    validated_at = parse_iso_datetime(receipt.get("validated_at_utc"), f"{artifact_type}.validated_at_utc")
    if validated_at > cutoff.astimezone(timezone.utc):
        _fail(f"{artifact_type} receipt was validated after prediction cutoff")

    scope = artifact.get("scope")
    if not isinstance(scope, dict):
        _fail(f"{artifact_type} scope missing")
    if artifact_type == "PRIOR_SEASON_STRENGTH":
        if scope.get("competition_id") != competition_id or scope.get("target_season") != season:
            _fail("prior-season artifact scope mismatch")
        source_season = str(scope.get("source_season") or "")
        if not source_season or source_season == season:
            _fail("prior-season artifact must name a distinct source season")
    else:
        competitions = scope.get("competition_ids")
        seasons = scope.get("seasons")
        if not isinstance(competitions, list) or competition_id not in competitions:
            _fail("generic fallback competition is outside validated scope")
        if not isinstance(seasons, list) or season not in seasons:
            _fail("generic fallback season is outside validated scope")

    payload = artifact.get("payload")
    if not isinstance(payload, dict):
        _fail(f"{artifact_type} payload missing")
    return payload


def _validated_parameters(payload: dict[str, Any], formal_config: dict[str, Any]) -> dict[str, float]:
    selected = payload.get("selected_parameters")
    required = set(formal_config["default_parameters"])
    if not isinstance(selected, dict) or set(selected) != required:
        _fail("artifact selected_parameters must contain the complete formal parameter set")
    params = _merge_parameters(formal_config, selected)
    for key, value in params.items():
        if not math.isfinite(value):
            _fail(f"selected parameter {key} is non-finite")
    return params


def _artifact_baseline(payload: dict[str, Any], candidate_config: dict[str, Any]) -> tuple[float, float, float]:
    contract = candidate_config["artifact_contract"]
    maximum_rate = float(contract["maximum_goal_rate"])
    league_home = _bounded_number(payload.get("league_home_goals"), "league_home_goals", 0.1, maximum_rate)
    league_away = _bounded_number(payload.get("league_away_goals"), "league_away_goals", 0.1, maximum_rate)
    dispersion = _bounded_number(
        payload.get("nb_dispersion_k"),
        "nb_dispersion_k",
        float(contract["minimum_dispersion_k"]),
        float(contract["maximum_dispersion_k"]),
    )
    return league_home, league_away, dispersion


def _prior_low_score_factors(payload: dict[str, Any], candidate_config: dict[str, Any]) -> dict[tuple[int, int], float]:
    raw = payload.get("low_score_factors")
    if not isinstance(raw, dict):
        _fail("prior artifact low_score_factors missing")
    contract = candidate_config["artifact_contract"]
    output: dict[tuple[int, int], float] = {}
    for home, away in LOW_SCORE_CELLS:
        key = f"{home}-{away}"
        output[(home, away)] = _bounded_number(
            raw.get(key),
            f"low_score_factors.{key}",
            float(contract["low_score_factor_minimum"]),
            float(contract["low_score_factor_maximum"]),
        )
    if set(raw) != {f"{home}-{away}" for home, away in LOW_SCORE_CELLS}:
        _fail("prior artifact low_score_factors keys mismatch")
    return output


def _team_prior(payload: dict[str, Any], team_name: str, candidate_config: dict[str, Any]) -> dict[str, float]:
    teams = payload.get("teams")
    key = normalize_team_token(team_name)
    if not isinstance(teams, dict) or key not in teams or not isinstance(teams[key], dict):
        _fail(f"team identity absent from prior artifact: {team_name}")
    expected = {"home_for_rate", "home_against_rate", "away_for_rate", "away_against_rate"}
    if set(teams[key]) != expected:
        _fail(f"team prior keys mismatch: {team_name}")
    maximum_rate = float(candidate_config["artifact_contract"]["maximum_goal_rate"])
    return {
        field: _bounded_number(teams[key][field], f"teams.{key}.{field}", 0.05, maximum_rate)
        for field in expected
    }


def _partial_state(
    history: list[MatchRow], cutoff: datetime, params: dict[str, float], formal_config: dict[str, Any]
) -> dict[str, Any] | None:
    if not history:
        return None
    permissive_config = dict(formal_config)
    permissive_config["minimum_competition_history_matches"] = 1
    return fit_current_season_state(history, cutoff, params, permissive_config)


def _blend(current: float, prior: float, prior_weight: float) -> float:
    return (1.0 - prior_weight) * current + prior_weight * prior


def _blend_factor(current: float, prior: float, prior_weight: float) -> float:
    return math.exp((1.0 - prior_weight) * math.log(current) + prior_weight * math.log(prior))


def _competition_prior_weight(current_matches: int, threshold: int) -> float:
    return max(0.0, min(1.0, (threshold - current_matches) / max(1, threshold)))


def _venue_prior_weight(current_matches: int, threshold: int) -> float:
    return max(0.0, min(1.0, (threshold - current_matches) / max(1, threshold)))


def _current_team_rates(
    state: dict[str, Any], team_name: str, venue: str, params: dict[str, float]
) -> dict[str, float] | None:
    key = normalize_team_token(team_name)
    team = state["team"].get(key)
    if not team:
        return None
    prior_n = params["team_prior_matches"]
    if venue == "home":
        if int(team["home_raw_matches"]) == 0:
            return None
        return {
            "for_rate": _shrunk_rate(team["home_gf"], team["home_matches"], state["league_home_goals"], prior_n),
            "against_rate": _shrunk_rate(team["home_ga"], team["home_matches"], state["league_away_goals"], prior_n),
        }
    if int(team["away_raw_matches"]) == 0:
        return None
    return {
        "for_rate": _shrunk_rate(team["away_gf"], team["away_matches"], state["league_away_goals"], prior_n),
        "against_rate": _shrunk_rate(team["away_ga"], team["away_matches"], state["league_home_goals"], prior_n),
    }


def _means_from_rates(
    league_home: float,
    league_away: float,
    home_for: float,
    home_against: float,
    away_for: float,
    away_against: float,
    params: dict[str, float],
    home_raw: int,
    away_raw: int,
) -> dict[str, float | str]:
    minimum_mu = params["minimum_goal_mean"]
    maximum_mu = params["maximum_goal_mean"]
    home_signal = min(maximum_mu, max(minimum_mu, home_for * away_against / league_home))
    away_signal = min(maximum_mu, max(minimum_mu, away_for * home_against / league_away))
    share = home_signal / max(1e-12, home_signal + away_signal)
    league_total = league_home + league_away
    home_total = home_for + home_against
    away_total = away_for + away_against
    pair_total = math.sqrt(max(1e-12, home_total) * max(1e-12, away_total))
    signal_weight = min(1.0, max(0.0, params["direct_total_signal_weight"]))
    mu_total = math.exp(
        (1.0 - signal_weight) * math.log(league_total) + signal_weight * math.log(pair_total)
    )
    mu_total = min(2.0 * maximum_mu, max(2.0 * minimum_mu, mu_total))
    return {
        "mu_home": mu_total * share,
        "mu_away": mu_total * (1.0 - share),
        "mu_total": mu_total,
        "allocation_home_share": share,
        "home_score_signal": home_signal,
        "away_score_signal": away_signal,
        "home_direct_total_rate": home_total,
        "away_direct_total_rate": away_total,
        "direct_total_method": "receipt_bound_prior_blended_venue_total_rates",
        "direct_total_signal_weight": signal_weight,
        "pair_direct_total_rate": pair_total,
        "home_raw_matches": float(home_raw),
        "away_raw_matches": float(away_raw),
        "ess": float(min(home_raw, away_raw)),
    }


def _candidate_output(
    *,
    state_name: str,
    competition_id: str,
    season: str,
    cutoff: datetime,
    history: list[MatchRow],
    means: dict[str, Any],
    params: dict[str, float],
    dispersion: float,
    factors: dict[tuple[int, int], float],
    prior_weight: float,
    prior_components: dict[str, float],
    artifact_audit: dict[str, Any] | None,
    formal_config: dict[str, Any],
    candidate_config: dict[str, Any],
) -> dict[str, Any]:
    matrix = build_score_matrix(
        float(means["mu_home"]),
        float(means["mu_away"]),
        dispersion,
        params["beta_binomial_concentration"],
        int(formal_config["max_total_goals_exact"]),
        factors,
    )
    marginals = derive_score_marginals(matrix)
    policy = candidate_config["output_policy"]
    return {
        "competition_id": competition_id,
        "season": season,
        "cutoff_utc": cutoff.astimezone(timezone.utc).isoformat(),
        "history_matches": len(history),
        "team_sample": means,
        "parameters": params,
        "nb_dispersion_k": dispersion,
        "probabilities": {
            "one_x_two": marginals["1x2"],
            "total_goals": marginals["total_goals"],
            "btts_yes": marginals["btts_yes"],
            "score_matrix": matrix,
        },
        "top_scores": top_scores(matrix, 10),
        "conditional_goal_difference": conditional_goal_difference_by_total(matrix),
        "score_sets": {
            "80": minimum_score_set(matrix, 0.80),
            "90": minimum_score_set(matrix, 0.90),
        },
        "cold_start_candidate": {
            "state": state_name,
            "prior_weight": prior_weight,
            "prior_weight_components": prior_components,
            "artifact_audit": artifact_audit,
            "formal_weight": policy["formal_weight"],
            "exact_gate": policy["exact_gate"],
            "ev_decision": policy["ev_decision"],
            "scientific_status": policy["scientific_status"],
            "production_activation": False,
            "historical_odds_read": False,
        },
    }


def _stable_output(
    history: list[MatchRow],
    competition_id: str,
    season: str,
    home_team: str,
    away_team: str,
    cutoff: datetime,
    selected_parameters: dict[str, Any] | None,
    candidate_config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(selected_parameters, dict):
        _fail("stable route requires explicit validated point-in-time parameters")
    formal_config = load_formal_config()
    if set(selected_parameters) != set(formal_config["default_parameters"]):
        _fail("stable selected_parameters must contain the complete formal parameter set")
    output = predict_from_history(
        history,
        competition_id,
        season,
        home_team,
        away_team,
        cutoff,
        selected_parameters=selected_parameters,
    )
    policy = candidate_config["output_policy"]
    output["cold_start_candidate"] = {
        "state": STABLE_CURRENT_SEASON,
        "prior_weight": 0.0,
        "prior_weight_components": {"competition": 0.0, "home_venue": 0.0, "away_venue": 0.0},
        "artifact_audit": None,
        "formal_weight": policy["formal_weight"],
        "exact_gate": policy["exact_gate"],
        "ev_decision": policy["ev_decision"],
        "scientific_status": policy["scientific_status"],
        "production_activation": False,
        "historical_odds_read": False,
        "delegated_to_unchanged_formal_history_engine": True,
    }
    return output


def _prior_output(
    *,
    history: list[MatchRow],
    competition_id: str,
    season: str,
    home_team: str,
    away_team: str,
    cutoff: datetime,
    home_count: int,
    away_count: int,
    artifact: dict[str, Any],
    receipt: dict[str, Any],
    formal_config: dict[str, Any],
    candidate_config: dict[str, Any],
) -> dict[str, Any]:
    payload = _validate_receipted_artifact(
        artifact,
        receipt,
        artifact_type="PRIOR_SEASON_STRENGTH",
        competition_id=competition_id,
        season=season,
        cutoff=cutoff,
        candidate_config=candidate_config,
    )
    params = _validated_parameters(payload, formal_config)
    prior_home, prior_away, prior_k = _artifact_baseline(payload, candidate_config)
    prior_factors = _prior_low_score_factors(payload, candidate_config)
    home_prior = _team_prior(payload, home_team, candidate_config)
    away_prior = _team_prior(payload, away_team, candidate_config)
    state = _partial_state(history, cutoff, params, formal_config)

    stable = candidate_config["stable_current_season"]
    competition_weight = _competition_prior_weight(
        len(history), int(stable["minimum_competition_history_matches"])
    )
    home_weight = max(
        competition_weight,
        _venue_prior_weight(home_count, int(stable["minimum_home_venue_matches"])),
    )
    away_weight = max(
        competition_weight,
        _venue_prior_weight(away_count, int(stable["minimum_away_venue_matches"])),
    )
    if state is None:
        league_home, league_away, dispersion = prior_home, prior_away, prior_k
        current_factors = prior_factors
    else:
        league_home = _blend(state["league_home_goals"], prior_home, competition_weight)
        league_away = _blend(state["league_away_goals"], prior_away, competition_weight)
        dispersion = _blend(state["nb_dispersion_k"], prior_k, competition_weight)
        current_factors = low_score_factors(state, params)
    factors = {
        cell: _blend_factor(current_factors[cell], prior_factors[cell], competition_weight)
        for cell in LOW_SCORE_CELLS
    }

    current_home = _current_team_rates(state, home_team, "home", params) if state else None
    current_away = _current_team_rates(state, away_team, "away", params) if state else None
    home_for = home_prior["home_for_rate"] if current_home is None else _blend(
        current_home["for_rate"], home_prior["home_for_rate"], home_weight
    )
    home_against = home_prior["home_against_rate"] if current_home is None else _blend(
        current_home["against_rate"], home_prior["home_against_rate"], home_weight
    )
    away_for = away_prior["away_for_rate"] if current_away is None else _blend(
        current_away["for_rate"], away_prior["away_for_rate"], away_weight
    )
    away_against = away_prior["away_against_rate"] if current_away is None else _blend(
        current_away["against_rate"], away_prior["away_against_rate"], away_weight
    )
    means = _means_from_rates(
        league_home,
        league_away,
        home_for,
        home_against,
        away_for,
        away_against,
        params,
        home_count,
        away_count,
    )
    components = {"competition": competition_weight, "home_venue": home_weight, "away_venue": away_weight}
    return _candidate_output(
        state_name=PRIOR_SEASON_SHRINKAGE,
        competition_id=competition_id,
        season=season,
        cutoff=cutoff,
        history=history,
        means=means,
        params=params,
        dispersion=dispersion,
        factors=factors,
        prior_weight=max(components.values()),
        prior_components=components,
        artifact_audit={
            "artifact_type": artifact["artifact_type"],
            "artifact_version": artifact["version"],
            "artifact_sha256": receipt["artifact_sha256"],
            "receipt_sha256": receipt["receipt_sha256"],
            "validation_status": receipt["validation_status"],
            "source_season": artifact["scope"]["source_season"],
        },
        formal_config=formal_config,
        candidate_config=candidate_config,
    )


def _generic_output(
    *,
    history: list[MatchRow],
    competition_id: str,
    season: str,
    home_team: str,
    away_team: str,
    cutoff: datetime,
    artifact: dict[str, Any],
    receipt: dict[str, Any],
    formal_config: dict[str, Any],
    candidate_config: dict[str, Any],
) -> dict[str, Any]:
    payload = _validate_receipted_artifact(
        artifact,
        receipt,
        artifact_type="GENERIC_COMPETITION_FALLBACK",
        competition_id=competition_id,
        season=season,
        cutoff=cutoff,
        candidate_config=candidate_config,
    )
    params = _validated_parameters(payload, formal_config)
    fallback_home, fallback_away, fallback_k = _artifact_baseline(payload, candidate_config)
    known = payload.get("known_teams")
    known_tokens = {normalize_team_token(item) for item in known} if isinstance(known, list) else set()
    for team in (home_team, away_team):
        if normalize_team_token(team) not in known_tokens:
            _fail(f"team identity absent from generic fallback scope: {team}")
    state = _partial_state(history, cutoff, params, formal_config)
    threshold = int(candidate_config["stable_current_season"]["minimum_competition_history_matches"])
    weight = _competition_prior_weight(len(history), threshold)
    if state is None:
        league_home, league_away, dispersion = fallback_home, fallback_away, fallback_k
    else:
        league_home = _blend(state["league_home_goals"], fallback_home, weight)
        league_away = _blend(state["league_away_goals"], fallback_away, weight)
        dispersion = _blend(state["nb_dispersion_k"], fallback_k, weight)
    league_total = league_home + league_away
    means = {
        "mu_home": league_home,
        "mu_away": league_away,
        "mu_total": league_total,
        "allocation_home_share": league_home / league_total,
        "home_score_signal": league_home,
        "away_score_signal": league_away,
        "home_direct_total_rate": league_total,
        "away_direct_total_rate": league_total,
        "direct_total_method": "receipt_bound_generic_competition_baseline",
        "home_raw_matches": 0.0,
        "away_raw_matches": 0.0,
        "ess": 0.0,
    }
    factors = {cell: 1.0 for cell in LOW_SCORE_CELLS}
    components = {"competition": weight, "home_venue": 0.0, "away_venue": 0.0}
    return _candidate_output(
        state_name=GENERIC_VALIDATED_FALLBACK,
        competition_id=competition_id,
        season=season,
        cutoff=cutoff,
        history=history,
        means=means,
        params=params,
        dispersion=dispersion,
        factors=factors,
        prior_weight=weight,
        prior_components=components,
        artifact_audit={
            "artifact_type": artifact["artifact_type"],
            "artifact_version": artifact["version"],
            "artifact_sha256": receipt["artifact_sha256"],
            "receipt_sha256": receipt["receipt_sha256"],
            "validation_status": receipt["validation_status"],
        },
        formal_config=formal_config,
        candidate_config=candidate_config,
    )


def predict_cold_start_from_history(
    history: Iterable[MatchRow],
    competition_id: str,
    season: str,
    home_team: str,
    away_team: str,
    cutoff: datetime,
    *,
    stable_selected_parameters: dict[str, Any] | None = None,
    prior_artifact: dict[str, Any] | None = None,
    prior_receipt: dict[str, Any] | None = None,
    generic_fallback_artifact: dict[str, Any] | None = None,
    generic_fallback_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Route one explicit PIT history through A/B/C/D without silent fallback."""
    rows = _validate_request(history, competition_id, season, home_team, away_team, cutoff)
    candidate_config = load_candidate_config()
    formal_config = load_formal_config()
    stable = candidate_config["stable_current_season"]
    home_count, away_count = _venue_counts(rows, home_team, away_team)
    is_stable = (
        len(rows) >= int(stable["minimum_competition_history_matches"])
        and home_count >= int(stable["minimum_home_venue_matches"])
        and away_count >= int(stable["minimum_away_venue_matches"])
    )
    if is_stable:
        return _stable_output(
            rows,
            competition_id,
            season,
            home_team,
            away_team,
            cutoff,
            stable_selected_parameters,
            candidate_config,
        )

    if prior_artifact is not None or prior_receipt is not None:
        if prior_artifact is None or prior_receipt is None:
            _fail("prior artifact and receipt must be supplied together")
        return _prior_output(
            history=rows,
            competition_id=competition_id,
            season=season,
            home_team=home_team,
            away_team=away_team,
            cutoff=cutoff,
            home_count=home_count,
            away_count=away_count,
            artifact=prior_artifact,
            receipt=prior_receipt,
            formal_config=formal_config,
            candidate_config=candidate_config,
        )

    if generic_fallback_artifact is not None or generic_fallback_receipt is not None:
        if generic_fallback_artifact is None or generic_fallback_receipt is None:
            _fail("generic fallback artifact and receipt must be supplied together")
        return _generic_output(
            history=rows,
            competition_id=competition_id,
            season=season,
            home_team=home_team,
            away_team=away_team,
            cutoff=cutoff,
            artifact=generic_fallback_artifact,
            receipt=generic_fallback_receipt,
            formal_config=formal_config,
            candidate_config=candidate_config,
        )

    _fail("cold-start evidence unavailable; no validated prior or generic fallback supplied")

