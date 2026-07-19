#!/usr/bin/env python3
"""V4.6.0 current-season NB + conditional Beta-Binomial score engine.

The engine follows the unique CURRENT chain without granting an A grade:
current-season point-in-time matches -> rolling attack/defence estimates ->
direct Negative-Binomial total goals -> conditional Beta-Binomial home-goal
allocation -> strongly shrunk low-score residual -> one joint score matrix.

Historical seasons may select frozen hyperparameters in the validation module,
but target-team strength is always estimated only from the target season before
the prediction cutoff. Historical odds are never read by this engine.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from platform_core import (
    ROOT,
    MatchRow,
    PlatformError,
    derive_score_marginals,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    read_processed_matches,
    settle_home_handicap,
    settle_over_total,
    sha256_file,
    sha256_json,
    top_scores,
)

CONFIG_PATH = ROOT / "config" / "formal_core_v460.json"
MODEL_ROOT = ROOT / "models" / "formal_core_v460"
VALIDATION_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
ENGINE_PATH = Path(__file__).resolve()
LOW_SCORE_CELLS = ((0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (0, 2))


def load_config() -> dict[str, Any]:
    return load_json(CONFIG_PATH)


def _finite(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _merge_parameters(config: dict[str, Any], selected: dict[str, Any] | None) -> dict[str, float]:
    params = dict(config["default_parameters"])
    if selected:
        params.update(selected)
    return {key: float(value) for key, value in params.items()}


def _strictly_before_cutoff_date(match_date: datetime, cutoff: datetime) -> bool:
    """Conservative point-in-time gate for date-only source rows.

    Processed league sources do not carry reliable kickoff timestamps. Therefore
    every match on the cutoff calendar date is withheld rather than pretending
    midnight UTC is an exact event time. This prevents same-day look-ahead in
    historical replay and current-day calculations.
    """
    return match_date.date() < cutoff.date()


def infer_target_season(matches: Iterable[MatchRow], cutoff: datetime) -> str:
    eligible = [match for match in matches if _strictly_before_cutoff_date(match.date, cutoff)]
    if not eligible:
        raise PlatformError("no completed competition matches strictly before prediction cutoff date")
    latest = max(eligible, key=lambda item: item.date)
    return latest.season


def current_season_history(matches: Iterable[MatchRow], cutoff: datetime, season: str | None = None) -> tuple[str, list[MatchRow]]:
    all_matches = list(matches)
    target_season = season or infer_target_season(all_matches, cutoff)
    history = [
        match for match in all_matches
        if match.season == target_season and _strictly_before_cutoff_date(match.date, cutoff)
    ]
    history.sort(key=lambda item: (item.date, item.home_team, item.away_team))
    return target_season, history


def _weight(match_date: datetime, cutoff: datetime, half_life_days: float) -> float:
    age_days = max(0.0, (cutoff - match_date).total_seconds() / 86400.0)
    return math.exp(-math.log(2.0) * age_days / max(1e-9, half_life_days))


def _team_key(name: str) -> str:
    return normalize_team_token(name)


def fit_current_season_state(history: list[MatchRow], cutoff: datetime, params: dict[str, float], config: dict[str, Any]) -> dict[str, Any]:
    if len(history) < int(config["minimum_competition_history_matches"]):
        raise PlatformError(
            f"current-season history has {len(history)} matches; minimum is {config['minimum_competition_history_matches']}"
        )
    half_life = params["half_life_days"]
    weighted_matches = 0.0
    weighted_home_goals = 0.0
    weighted_away_goals = 0.0
    totals: list[tuple[float, int]] = []
    team = defaultdict(lambda: {
        "raw_matches": 0,
        "home_raw_matches": 0,
        "away_raw_matches": 0,
        "effective_matches": 0.0,
        "home_matches": 0.0,
        "away_matches": 0.0,
        "home_gf": 0.0,
        "home_ga": 0.0,
        "away_gf": 0.0,
        "away_ga": 0.0,
    })
    low_empirical = Counter()
    for match in history:
        w = _weight(match.date, cutoff, half_life)
        weighted_matches += w
        weighted_home_goals += w * match.home_goals
        weighted_away_goals += w * match.away_goals
        totals.append((w, match.home_goals + match.away_goals))
        home = team[_team_key(match.home_team)]
        away = team[_team_key(match.away_team)]
        home["raw_matches"] += 1
        home["home_raw_matches"] += 1
        away["raw_matches"] += 1
        away["away_raw_matches"] += 1
        home["effective_matches"] += w
        away["effective_matches"] += w
        home["home_matches"] += w
        home["home_gf"] += w * match.home_goals
        home["home_ga"] += w * match.away_goals
        away["away_matches"] += w
        away["away_gf"] += w * match.away_goals
        away["away_ga"] += w * match.home_goals
        if (match.home_goals, match.away_goals) in LOW_SCORE_CELLS:
            low_empirical[(match.home_goals, match.away_goals)] += w

    if weighted_matches <= 0:
        raise PlatformError("current-season effective match count is zero")
    league_home = max(0.10, weighted_home_goals / weighted_matches)
    league_away = max(0.10, weighted_away_goals / weighted_matches)
    mean_total = league_home + league_away
    variance_total = sum(w * (total - mean_total) ** 2 for w, total in totals) / max(1e-9, sum(w for w, _ in totals))
    if variance_total > mean_total + 1e-6:
        empirical_k = mean_total * mean_total / (variance_total - mean_total)
    else:
        empirical_k = 100.0
    prior_n = params["dispersion_prior_matches"]
    k = (empirical_k * weighted_matches + params["nb_default_k"] * prior_n) / (weighted_matches + prior_n)
    k = min(100.0, max(1.25, k))
    return {
        "league_home_goals": league_home,
        "league_away_goals": league_away,
        "mean_total_goals": mean_total,
        "nb_dispersion_k": k,
        "competition_effective_matches": weighted_matches,
        "competition_raw_matches": len(history),
        "team": dict(team),
        "low_empirical": low_empirical,
    }


def _shrunk_rate(numerator: float, denominator: float, prior_rate: float, prior_matches: float) -> float:
    return (numerator + prior_rate * prior_matches) / max(1e-12, denominator + prior_matches)


def expected_goals(state: dict[str, Any], home_team: str, away_team: str, params: dict[str, float], config: dict[str, Any]) -> dict[str, float | str]:
    home_key = _team_key(home_team)
    away_key = _team_key(away_team)
    if home_key not in state["team"] or away_key not in state["team"]:
        raise PlatformError("one or both teams have no current-season history")
    home = state["team"][home_key]
    away = state["team"][away_key]
    minimum_raw = int(config["minimum_team_raw_matches"])
    home_relevant_raw = int(home.get("home_raw_matches", home["raw_matches"]))
    away_relevant_raw = int(away.get("away_raw_matches", away["raw_matches"]))
    if home_relevant_raw < minimum_raw or away_relevant_raw < minimum_raw:
        raise PlatformError(
            f"venue-specific sample below minimum: home_home={home_relevant_raw} away_away={away_relevant_raw} minimum={minimum_raw}"
        )

    prior = params["team_prior_matches"]
    league_home = state["league_home_goals"]
    league_away = state["league_away_goals"]
    league_total = state["mean_total_goals"]

    # Conditional allocation signal: attack/defence determines only the share of
    # a directly predicted total, never the total itself.
    home_attack = _shrunk_rate(home["home_gf"], home["home_matches"], league_home, prior) / league_home
    home_defence = _shrunk_rate(home["home_ga"], home["home_matches"], league_away, prior) / league_away
    away_attack = _shrunk_rate(away["away_gf"], away["away_matches"], league_away, prior) / league_away
    away_defence = _shrunk_rate(away["away_ga"], away["away_matches"], league_home, prior) / league_home
    minimum_mu = params["minimum_goal_mean"]
    maximum_mu = params["maximum_goal_mean"]
    home_signal = min(maximum_mu, max(minimum_mu, league_home * home_attack * away_defence))
    away_signal = min(maximum_mu, max(minimum_mu, league_away * away_attack * home_defence))
    allocation_home_share = home_signal / max(1e-12, home_signal + away_signal)

    # Direct total-goal main track: estimate venue-specific match-total rates and
    # combine them geometrically after shrinkage to the competition total rate.
    # This explicitly avoids using mu_home + mu_away as the source of P(T).
    home_total_rate = _shrunk_rate(
        home["home_gf"] + home["home_ga"], home["home_matches"], league_total, prior
    )
    away_total_rate = _shrunk_rate(
        away["away_gf"] + away["away_ga"], away["away_matches"], league_total, prior
    )
    pair_total_rate = math.sqrt(max(1e-12, home_total_rate) * max(1e-12, away_total_rate))
    # Nested-OOS selectable shrinkage of the venue-pair total signal toward the
    # competition total. Weight=1 preserves the original direct-total model;
    # lower weights are allowed only when earlier seasons select them.
    direct_total_signal_weight = min(1.0, max(0.0, float(params.get("direct_total_signal_weight", 1.0))))
    mu_total = math.exp(
        (1.0 - direct_total_signal_weight) * math.log(max(1e-12, league_total))
        + direct_total_signal_weight * math.log(max(1e-12, pair_total_rate))
    )
    mu_total = min(2.0 * maximum_mu, max(2.0 * minimum_mu, mu_total))

    # Implied team means exist only to parameterize the conditional score split.
    mu_home = mu_total * allocation_home_share
    mu_away = mu_total * (1.0 - allocation_home_share)
    return {
        "mu_home": mu_home,
        "mu_away": mu_away,
        "mu_total": mu_total,
        "allocation_home_share": allocation_home_share,
        "home_score_signal": home_signal,
        "away_score_signal": away_signal,
        "home_direct_total_rate": home_total_rate,
        "away_direct_total_rate": away_total_rate,
        "direct_total_method": "nested_oos_shrunk_geometric_venue_total_rates",
        "direct_total_signal_weight": direct_total_signal_weight,
        "pair_direct_total_rate": pair_total_rate,
        "home_raw_matches": float(home_relevant_raw),
        "away_raw_matches": float(away_relevant_raw),
        "home_total_raw_matches": float(home["raw_matches"]),
        "away_total_raw_matches": float(away["raw_matches"]),
        "home_effective_matches": home["home_matches"],
        "away_effective_matches": away["away_matches"],
        "ess": min(home["home_matches"], away["away_matches"]),
    }


def negative_binomial_pmf(total: int, mean: float, dispersion_k: float) -> float:
    if total < 0:
        return 0.0
    k = max(1e-9, dispersion_k)
    mu = max(1e-12, mean)
    logp = (
        math.lgamma(total + k)
        - math.lgamma(k)
        - math.lgamma(total + 1)
        + k * math.log(k / (k + mu))
        + total * math.log(mu / (k + mu))
    )
    return math.exp(logp)


def beta_binomial_pmf(home_goals: int, total: int, alpha: float, beta: float) -> float:
    if home_goals < 0 or home_goals > total:
        return 0.0
    logp = (
        math.lgamma(total + 1)
        - math.lgamma(home_goals + 1)
        - math.lgamma(total - home_goals + 1)
        + math.lgamma(home_goals + alpha)
        + math.lgamma(total - home_goals + beta)
        - math.lgamma(total + alpha + beta)
        + math.lgamma(alpha + beta)
        - math.lgamma(alpha)
        - math.lgamma(beta)
    )
    return math.exp(logp)


def _base_low_score_probabilities(mean_total: float, k: float, home_share: float, concentration: float) -> dict[tuple[int, int], float]:
    alpha = max(0.05, home_share * concentration)
    beta = max(0.05, (1.0 - home_share) * concentration)
    output = {}
    for home, away in LOW_SCORE_CELLS:
        total = home + away
        output[(home, away)] = negative_binomial_pmf(total, mean_total, k) * beta_binomial_pmf(home, total, alpha, beta)
    return output


def low_score_factors(state: dict[str, Any], params: dict[str, float]) -> dict[tuple[int, int], float]:
    shrinkage = min(1.0, max(0.0, params["low_score_shrinkage"]))
    if shrinkage <= 0:
        return {cell: 1.0 for cell in LOW_SCORE_CELLS}
    home_share = state["league_home_goals"] / state["mean_total_goals"]
    base = _base_low_score_probabilities(
        state["mean_total_goals"], state["nb_dispersion_k"], home_share, params["beta_binomial_concentration"]
    )
    denominator = max(1e-12, state["competition_effective_matches"])
    cap_low = params["low_score_ratio_cap_low"]
    cap_high = params["low_score_ratio_cap_high"]
    factors = {}
    for cell in LOW_SCORE_CELLS:
        empirical = state["low_empirical"].get(cell, 0.0) / denominator
        ratio = empirical / max(1e-12, base[cell])
        ratio = min(cap_high, max(cap_low, ratio))
        factors[cell] = math.exp(shrinkage * math.log(ratio))
    return factors


def build_score_matrix(mu_home: float, mu_away: float, dispersion_k: float, concentration: float, max_total: int, factors: dict[tuple[int, int], float] | None = None) -> list[dict[str, float | int]]:
    """Build one joint matrix while preserving the direct NB total marginal.

    Low-score residual factors modify only the conditional score allocation
    within a fixed total. Each total-specific conditional vector is re-normalized
    before multiplication by P(T=t), so the direct 0--7+ NB track cannot be
    silently changed by score-cell adjustments.
    """
    total_mean = mu_home + mu_away
    home_share = mu_home / max(1e-12, total_mean)
    alpha = max(0.05, home_share * concentration)
    beta = max(0.05, (1.0 - home_share) * concentration)
    factors = factors or {}
    cells: list[dict[str, float | int]] = []
    exact_total_probability = 0.0
    for total in range(max_total + 1):
        p_total = negative_binomial_pmf(total, total_mean, dispersion_k)
        exact_total_probability += p_total
        conditional: list[tuple[int, int, float]] = []
        for home_goals in range(total + 1):
            away_goals = total - home_goals
            weight = beta_binomial_pmf(home_goals, total, alpha, beta)
            weight *= factors.get((home_goals, away_goals), 1.0)
            conditional.append((home_goals, away_goals, weight))
        conditional_sum = sum(item[2] for item in conditional)
        if conditional_sum <= 0 or not math.isfinite(conditional_sum):
            raise PlatformError(f"conditional score allocation failed for total={total}")
        for home_goals, away_goals, weight in conditional:
            cells.append({
                "home_goals": home_goals,
                "away_goals": away_goals,
                "probability": p_total * weight / conditional_sum,
            })

    tail = max(0.0, 1.0 - exact_total_probability)
    tail_total = max_total + 1
    tail_weights = [beta_binomial_pmf(home, tail_total, alpha, beta) for home in range(tail_total + 1)]
    tail_sum = sum(tail_weights)
    for home_goals, weight in enumerate(tail_weights):
        cells.append({
            "home_goals": home_goals,
            "away_goals": tail_total - home_goals,
            "probability": tail * weight / max(1e-15, tail_sum),
        })

    probability_sum = sum(float(cell["probability"]) for cell in cells)
    if probability_sum <= 0 or not math.isfinite(probability_sum):
        raise PlatformError("score matrix has a non-finite or zero probability sum")
    # Floating-point-only correction; structural normalization occurs above.
    for cell in cells:
        cell["probability"] = float(cell["probability"]) / probability_sum
    return cells


def conditional_goal_difference_by_total(matrix: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for cell in matrix:
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        grouped[home + away].append((home - away, float(cell["probability"])))
    output: dict[str, dict[str, float]] = {}
    for total, items in sorted(grouped.items()):
        total_probability = sum(probability for _, probability in items)
        if total_probability <= 0:
            continue
        distribution = Counter()
        for difference, probability in items:
            distribution[str(difference)] += probability / total_probability
        output[str(total)] = {key: float(value) for key, value in sorted(distribution.items(), key=lambda item: int(item[0]))}
    return output


def minimum_score_set(matrix: list[dict[str, Any]], target: float) -> dict[str, Any]:
    ranking = top_scores(matrix, len(matrix))
    cumulative = 0.0
    selected = []
    for item in ranking:
        selected.append(item)
        cumulative += float(item["probability"])
        if cumulative + 1e-12 >= target:
            break
    return {"target": target, "size": len(selected), "cumulative_probability": cumulative, "scores": selected}


def _derive_line_market(matrix: list[dict[str, Any]], line: float, settlement_fn) -> dict[str, float]:
    result = {"win": 0.0, "push": 0.0, "loss": 0.0}
    for cell in matrix:
        settlement = settlement_fn(int(cell["home_goals"]), int(cell["away_goals"]), line)
        probability = float(cell["probability"])
        for key in result:
            result[key] += probability * settlement[key]
    return result


def load_model_artifact(competition_id: str) -> dict[str, Any] | None:
    path = MODEL_ROOT / competition_id / "model.json"
    return load_json(path) if path.exists() else None


def _select_point_in_time_parameters(artifact: dict[str, Any], target_season: str) -> dict[str, Any]:
    mapping = artifact.get("point_in_time_parameters")
    if isinstance(mapping, dict) and isinstance(mapping.get(target_season), dict):
        return dict(mapping[target_season])
    raise PlatformError(
        f"no point-in-time validated parameter set for target season {target_season}; "
        "refusing to reuse a live artifact selected with later data"
    )


def predict_joint_distribution(
    competition_id: str,
    home_team: str,
    away_team: str,
    cutoff: datetime,
    *,
    season: str | None = None,
    selected_parameters: dict[str, Any] | None = None,
    use_team_effects: bool = True,
) -> dict[str, Any]:
    config = load_config()
    matches = read_processed_matches(competition_id)
    target_season, history = current_season_history(matches, cutoff, season)

    artifact = load_model_artifact(competition_id)
    parameter_source = "explicit_backtest_parameters"
    if selected_parameters is None:
        if artifact is None:
            raise PlatformError(f"validated formal-core artifact missing for {competition_id}")
        if artifact.get("operational_status") != "NON_A_FORMAL_CORE_AVAILABLE":
            raise PlatformError(
                f"formal core is not operational for {competition_id}: {artifact.get('operational_status')}"
            )
        current_engine_sha = sha256_file(ENGINE_PATH)
        if artifact.get("engine_sha256") != current_engine_sha:
            raise PlatformError("formal-core artifact engine hash does not match current engine")
        validation_report_path = VALIDATION_REPORT_ROOT / f"{competition_id}.json"
        if not validation_report_path.exists():
            raise PlatformError(f"formal-core validation report missing for {competition_id}")
        current_validation_report = load_json(validation_report_path)
        if artifact.get("validation_report_sha256") != sha256_json(current_validation_report):
            raise PlatformError("formal-core artifact validation report hash does not match current report")
        selected_parameters = _select_point_in_time_parameters(artifact, target_season)
        parameter_source = f"point_in_time_parameter_map:{target_season}"

    params = _merge_parameters(config, selected_parameters)
    state = fit_current_season_state(history, cutoff, params, config)
    if use_team_effects:
        means = expected_goals(state, home_team, away_team, params, config)
    else:
        home_key = _team_key(home_team)
        away_key = _team_key(away_team)
        home_raw = state["team"].get(home_key, {}).get("raw_matches", 0)
        away_raw = state["team"].get(away_key, {}).get("raw_matches", 0)
        league_total = state["mean_total_goals"]
        share = state["league_home_goals"] / max(1e-12, league_total)
        means = {
            "mu_home": league_total * share,
            "mu_away": league_total * (1.0 - share),
            "mu_total": league_total,
            "allocation_home_share": share,
            "home_score_signal": state["league_home_goals"],
            "away_score_signal": state["league_away_goals"],
            "home_direct_total_rate": league_total,
            "away_direct_total_rate": league_total,
            "direct_total_method": "competition_direct_total_baseline",
            "home_raw_matches": float(home_raw),
            "away_raw_matches": float(away_raw),
            "home_effective_matches": 0.0,
            "away_effective_matches": 0.0,
            "ess": 0.0,
        }
    factors = low_score_factors(state, params)
    matrix = build_score_matrix(
        float(means["mu_home"]),
        float(means["mu_away"]),
        state["nb_dispersion_k"],
        params["beta_binomial_concentration"],
        int(config["max_total_goals_exact"]),
        factors,
    )
    marginals = derive_score_marginals(matrix)
    ranking = top_scores(matrix, 10)
    tail_total = int(config["max_total_goals_exact"]) + 1
    tail_probability = sum(
        float(cell["probability"]) for cell in matrix
        if int(cell["home_goals"]) + int(cell["away_goals"]) == tail_total
    )
    return {
        "competition_id": competition_id,
        "season": target_season,
        "cutoff_utc": cutoff.astimezone(timezone.utc).isoformat(),
        "history_matches": len(history),
        "latest_history_match_date": history[-1].date.date().isoformat() if history else None,
        "competition_effective_matches": state["competition_effective_matches"],
        "team_sample": means,
        "parameters": params,
        "parameter_source": parameter_source,
        "nb_dispersion_k": state["nb_dispersion_k"],
        "low_score_factors": {f"{home}-{away}": value for (home, away), value in factors.items()},
        "probabilities": {
            "one_x_two": marginals["1x2"],
            "total_goals": marginals["total_goals"],
            "btts_yes": marginals["btts_yes"],
            "score_matrix": matrix,
        },
        "top_scores": ranking,
        "conditional_goal_difference": conditional_goal_difference_by_total(matrix),
        "score_sets": {
            "80": minimum_score_set(matrix, 0.80),
            "90": minimum_score_set(matrix, 0.90),
        },
        "audit": {
            "probability_sum": marginals["probability_sum"],
            "engine_sha256": sha256_file(ENGINE_PATH),
            "config_sha256": sha256_file(CONFIG_PATH),
            "historical_team_strength_injected": False,
            "historical_odds_read": False,
            "same_day_date_only_rows_excluded": True,
            "target_strength_data_policy": "same season and strictly earlier calendar dates only",
            "tail_aggregation_total": tail_total,
            "tail_aggregation_probability": tail_probability,
        },
    }


def calculation_from_context(context: dict[str, Any]) -> dict[str, Any]:
    identity = context["match_identity"]
    cutoff = parse_iso_datetime(identity["freeze_time_utc"], "freeze_time_utc")
    prediction = predict_joint_distribution(
        identity["competition_id"], identity["home_team"], identity["away_team"], cutoff,
        season=identity.get("season"),
    )
    probabilities = prediction["probabilities"]
    matrix = probabilities["score_matrix"]
    tail_probability = float(prediction.get("audit", {}).get("tail_aggregation_probability", 0.0))
    matrix_state = "通过" if tail_probability <= 1e-6 else "降级"
    derived: dict[str, Any] = {}
    market = context.get("original_market_snapshot") or {}
    if isinstance(market.get("asian_handicap"), dict) and isinstance(market["asian_handicap"].get("line"), (int, float)):
        line = float(market["asian_handicap"]["line"])
        derived["home_handicap"] = {"line": line, **_derive_line_market(matrix, line, settle_home_handicap)}
    if isinstance(market.get("total_goals"), dict) and isinstance(market["total_goals"].get("line"), (int, float)):
        line = float(market["total_goals"]["line"])
        derived["over_total"] = {"line": line, **_derive_line_market(matrix, line, settle_over_total)}
    total_rank = sorted(probabilities["total_goals"].items(), key=lambda item: (-item[1], item[0]))
    top = prediction["top_scores"]
    exact_gate = False
    ess = float(prediction["team_sample"]["ess"])
    lineup_status = context.get("lineup_assessment", {}).get("status")
    stable_ess = ess >= float(load_config()["minimum_team_effective_matches_for_stable"])
    if ess >= 15.0 and lineup_status == "通过":
        confidence = "B"
    elif stable_ess and lineup_status in {"通过", "部分通过"}:
        confidence = "C"
    else:
        confidence = "D"
    score_text = (
        f"模型中心比分 {top[0]['score']}；EXACT独立门控未通过。"
        if matrix_state == "通过" else "精确比分不可用。"
    )
    top_score = top[0]["score"] if matrix_state == "通过" else None
    second_score = top[1]["score"] if matrix_state == "通过" and len(top) > 1 else None
    return {
        "schema_version": "1.1",
        "rule_version": "V4.6.1",
        "engine_version": load_config()["engine_version"],
        "freeze_context_hash": context["context_hash"],
        "formal_status": "A-CANDIDATE_NO_DOMAIN_A_RECEIPT",
        "module_states": {
            "direct_total_goals": "通过",
            "conditional_goal_difference": "通过",
            "unified_score_matrix": matrix_state,
            "market_coordination": "未启用",
            "price_ev_no_bet": "降级"
        },
        "probabilities": probabilities,
        "derived_markets": derived,
        "optimization_audit": None,
        "price_analysis": [],
        "model_audit": prediction,
        "conditional_goal_difference_audit": prediction["conditional_goal_difference"],
        "score_set_audit": prediction["score_sets"],
        "conclusions": {
            "result_direction": max(probabilities["one_x_two"], key=probabilities["one_x_two"].get),
            "result_text": (
                f"90分钟模型概率：主胜{probabilities['one_x_two']['home']:.1%}、"
                f"平局{probabilities['one_x_two']['draw']:.1%}、客胜{probabilities['one_x_two']['away']:.1%}。"
            ),
            "total_goals_text": f"模型总进球中心：{total_rank[0][0]}球；0—7+分布已由直接NB主轨生成。",
            "total_goals_primary": total_rank[0][0],
            "total_goals_secondary": total_rank[1][0],
            "top_score": top_score,
            "second_score": second_score,
            "top3_cumulative": sum(item["probability"] for item in top[:3]) if matrix_state == "通过" else None,
            "top1_top2_gap": (top[0]["probability"] - top[1]["probability"]) if matrix_state == "通过" and len(top) > 1 else None,
            "score_set_80": prediction["score_sets"]["80"],
            "score_set_90": prediction["score_sets"]["90"],
            "score_text": score_text,
            "score_label": "模型中心比分" if matrix_state == "通过" else "精确比分不可用",
            "exact_gate": exact_gate,
            "confidence_grade": confidence,
            "price_status": "No Bet",
            "final_line": (
                f"{max(probabilities['one_x_two'], key=probabilities['one_x_two'].get)}；"
                f"可信等级{confidence}；No Bet；"
                + ("比分标签为模型中心比分。" if matrix_state == "通过" else "精确比分不可用。")
            )
        }
    }


# Backtest-safe entry point that never reads future matches or disk after the
# caller supplies the point-in-time history.
def predict_from_history(
    history: list[MatchRow],
    competition_id: str,
    season: str,
    home_team: str,
    away_team: str,
    cutoff: datetime,
    selected_parameters: dict[str, Any] | None = None,
    *,
    use_team_effects: bool = True,
) -> dict[str, Any]:
    config = load_config()
    params = _merge_parameters(config, selected_parameters)
    state = fit_current_season_state(history, cutoff, params, config)
    if use_team_effects:
        means = expected_goals(state, home_team, away_team, params, config)
    else:
        home_key = _team_key(home_team)
        away_key = _team_key(away_team)
        means = {
            "mu_home": state["league_home_goals"],
            "mu_away": state["league_away_goals"],
            "mu_total": state["mean_total_goals"],
            "home_raw_matches": float(state["team"].get(home_key, {}).get("raw_matches", 0)),
            "away_raw_matches": float(state["team"].get(away_key, {}).get("raw_matches", 0)),
            "home_effective_matches": 0.0,
            "away_effective_matches": 0.0,
            "ess": 0.0,
        }
    factors = low_score_factors(state, params)
    matrix = build_score_matrix(
        means["mu_home"], means["mu_away"], state["nb_dispersion_k"],
        params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), factors
    )
    marginals = derive_score_marginals(matrix)
    return {
        "competition_id": competition_id,
        "season": season,
        "cutoff_utc": cutoff.astimezone(timezone.utc).isoformat(),
        "history_matches": len(history),
        "team_sample": means,
        "parameters": params,
        "nb_dispersion_k": state["nb_dispersion_k"],
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
    }
