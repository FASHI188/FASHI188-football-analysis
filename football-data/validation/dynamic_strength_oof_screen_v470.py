#!/usr/bin/env python3
"""Competition-local rolling OOF screen for V4.7 dynamic strength.

Research only.  The challenger adaptively borrows a capped amount of immediately
preceding same-competition season team information using only evidence observable
strictly before each target match.  Candidate coefficients are selected only from
prior scored predictions.  No result changes V4.7 formal probabilities.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import random
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from dynamic_strength_challenger_v470 import blend_sufficient_statistic, commensurability_score
from football_v460_engine import (
    _merge_parameters,
    _shrunk_rate,
    build_score_matrix,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import MatchRow, PlatformError, derive_score_marginals, load_json, normalize_team_token, top_scores

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_CONFIG = ROOT / "config" / "dynamic_strength_public_evidence_v470.json"
MODEL_ROOT = ROOT / "models" / "formal_core_v460"
REPORT_ROOT = ROOT / "manifests" / "dynamic_strength_oof_screen_v470"
EPS = 1e-15
SEED = 470
BOOTSTRAP_RESAMPLES = 300
WINDOWS_PER_SEASON = 4

CANDIDATES = [
    {
        "id": "identity_no_borrow",
        "coefficients": {"intercept": -20.0, "beta_roster_continuity": 0.0, "beta_coach_continuity": 0.0, "beta_promoted_or_relegated": 0.0, "beta_structural_break": 0.0},
        "max_prior_equivalent_matches": 0.0,
    },
    {
        "id": "conservative_2",
        "coefficients": {"intercept": -1.5, "beta_roster_continuity": 2.0, "beta_coach_continuity": 0.5, "beta_promoted_or_relegated": -4.0, "beta_structural_break": -2.0},
        "max_prior_equivalent_matches": 2.0,
    },
    {
        "id": "moderate_4",
        "coefficients": {"intercept": -1.0, "beta_roster_continuity": 2.5, "beta_coach_continuity": 0.8, "beta_promoted_or_relegated": -5.0, "beta_structural_break": -2.5},
        "max_prior_equivalent_matches": 4.0,
    },
    {
        "id": "adaptive_6",
        "coefficients": {"intercept": -0.5, "beta_roster_continuity": 3.0, "beta_coach_continuity": 1.0, "beta_promoted_or_relegated": -6.0, "beta_structural_break": -3.0},
        "max_prior_equivalent_matches": 6.0,
    },
    {
        "id": "roster_dominant_4",
        "coefficients": {"intercept": -1.0, "beta_roster_continuity": 3.5, "beta_coach_continuity": 0.2, "beta_promoted_or_relegated": -6.0, "beta_structural_break": -3.5},
        "max_prior_equivalent_matches": 4.0,
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def integer(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_date(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_starting(value: Any) -> bool:
    token = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return token in {"starting_lineup", "starting_xi", "starting_eleven", "startelf"} or token.startswith("starting")


def download(name: str, config: dict[str, Any], cache: Path) -> Path:
    filename = config["source"]["files"][name]
    path = cache / filename
    if path.exists() and path.stat().st_size > 0:
        return path
    cache.mkdir(parents=True, exist_ok=True)
    urls = [
        config["source"]["dataset_delivery_base"].rstrip("/") + "/" + filename,
        "https://raw.githubusercontent.com/dcaribou/transfermarkt-datasets/master/data/prep/" + filename,
    ]
    last_error: Exception | None = None
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "FASHI188-football-analysis/4.7"})
            with urllib.request.urlopen(request, timeout=180) as response, path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            if path.stat().st_size > 0:
                return path
        except Exception as exc:  # pragma: no cover
            last_error = exc
            if path.exists():
                path.unlink()
    raise RuntimeError(f"failed to download {name}: {last_error}")


def csv_rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def season_label(raw: str, calendar: str) -> str:
    year = int(raw)
    if calendar == "cross_year":
        return f"{year}/{str(year + 1)[2:]}"
    return str(year)


def load_domain_data(competition_id: str, cache: Path) -> dict[str, Any]:
    config = load_json(EVIDENCE_CONFIG)
    route = config["competition_mapping"][competition_id]
    external_id = route["transfermarkt_competition_id"]
    registry = load_json(ROOT / "config" / "platform_registry.json")
    reg = next(item for item in registry["competitions"] if item["competition_id"] == competition_id)
    calendar = reg["calendar"]
    if calendar not in {"cross_year", "calendar_year"}:
        raise PlatformError(f"stage adapter required before OOF: {competition_id} calendar={calendar}")

    games_path = download("games", config, cache)
    lineups_path = download("game_lineups", config, cache)
    transfers_path = download("transfers", config, cache)

    games: list[dict[str, Any]] = []
    game_ids: set[int] = set()
    clubs: set[int] = set()
    for row in csv_rows(games_path):
        if str(row.get("competition_id") or "") != external_id:
            continue
        game_id = integer(row.get("game_id")); home_id = integer(row.get("home_club_id")); away_id = integer(row.get("away_club_id"))
        home_goals = integer(row.get("home_club_goals")); away_goals = integer(row.get("away_club_goals")); date = parse_date(row.get("date"))
        raw_season = str(row.get("season") or "").strip()
        if None in (game_id, home_id, away_id, home_goals, away_goals) or date is None or not raw_season.isdigit():
            continue
        item = {
            "game_id": game_id, "season_raw": raw_season, "season": season_label(raw_season, calendar), "date": date,
            "home_id": home_id, "away_id": away_id, "home_goals": home_goals, "away_goals": away_goals,
            "home_manager": str(row.get("home_club_manager_name") or "").strip(),
            "away_manager": str(row.get("away_club_manager_name") or "").strip(),
        }
        games.append(item); game_ids.add(game_id); clubs.add(home_id); clubs.add(away_id)
    games.sort(key=lambda x: (x["date"], x["game_id"]))

    starters: dict[tuple[int, int], set[int]] = defaultdict(set)
    for row in csv_rows(lineups_path):
        game_id = integer(row.get("game_id"))
        if game_id is None or game_id not in game_ids or not is_starting(row.get("type")):
            continue
        club_id = integer(row.get("club_id")); player_id = integer(row.get("player_id"))
        if club_id is not None and player_id is not None:
            starters[(game_id, club_id)].add(player_id)

    transfers: dict[int, list[tuple[datetime, int, int | None, int | None]]] = defaultdict(list)
    for row in csv_rows(transfers_path):
        from_id = integer(row.get("from_club_id")); to_id = integer(row.get("to_club_id")); player_id = integer(row.get("player_id")); date = parse_date(row.get("transfer_date"))
        if player_id is None or date is None or (from_id not in clubs and to_id not in clubs):
            continue
        event = (date, player_id, from_id, to_id)
        if from_id in clubs: transfers[from_id].append(event)
        if to_id in clubs and to_id != from_id: transfers[to_id].append(event)
    for events in transfers.values(): events.sort(key=lambda x: (x[0], x[1]))
    return {"games": games, "starters": starters, "transfers": transfers, "calendar": calendar}


def to_match(row: dict[str, Any], competition_id: str) -> MatchRow:
    return MatchRow(
        competition_id=competition_id, season=row["season"], stage="regular", date=row["date"],
        home_team=f"club_{row['home_id']}", away_team=f"club_{row['away_id']}",
        home_goals=int(row["home_goals"]), away_goals=int(row["away_goals"]), source_path="transfermarkt_public_research",
    )


def build_season_indexes(data: dict[str, Any]) -> dict[str, Any]:
    by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    starter_counts: dict[tuple[str, int], Counter] = defaultdict(Counter)
    terminal_manager: dict[tuple[str, int], str] = {}
    teams: dict[str, set[int]] = defaultdict(set)
    season_end: dict[str, datetime] = {}
    for game in data["games"]:
        season = game["season"]
        by_season[season].append(game); teams[season].update((game["home_id"], game["away_id"]))
        season_end[season] = max(season_end.get(season, game["date"]), game["date"])
        if game["home_manager"]: terminal_manager[(season, game["home_id"])] = game["home_manager"]
        if game["away_manager"]: terminal_manager[(season, game["away_id"])] = game["away_manager"]
        for club_id in (game["home_id"], game["away_id"]):
            for player_id in data["starters"].get((game["game_id"], club_id), set()):
                starter_counts[(season, club_id)][player_id] += 1
    for season in by_season: by_season[season].sort(key=lambda x: (x["date"], x["game_id"]))
    ordered = sorted(by_season, key=lambda s: min(g["date"] for g in by_season[s]))
    previous = {ordered[i]: ordered[i - 1] for i in range(1, len(ordered))}
    return {"by_season": by_season, "starter_counts": starter_counts, "terminal_manager": terminal_manager, "teams": teams, "season_end": season_end, "previous": previous}


def current_manager_before(games: list[dict[str, Any]], club_id: int, cutoff: datetime) -> str | None:
    manager = None
    for game in games:
        if game["date"] >= cutoff: break
        if game["home_id"] == club_id and game["home_manager"]: manager = game["home_manager"]
        elif game["away_id"] == club_id and game["away_manager"]: manager = game["away_manager"]
    return manager


def team_features(team_id: int, season: str, cutoff: datetime, indexes: dict[str, Any], transfers: dict[int, list[tuple[datetime, int, int | None, int | None]]]) -> dict[str, Any]:
    previous = indexes["previous"].get(season)
    if not previous or team_id not in indexes["teams"].get(previous, set()):
        return {"promoted_or_relegated": True, "roster_continuity": 0.0, "coach_continuity": 0.0, "structural_break_score": 1.0, "feature_complete": True}
    counts: Counter = indexes["starter_counts"].get((previous, team_id), Counter())
    if not counts:
        return {"feature_complete": False, "reason": "prior-season starter counts unavailable"}
    prior_end = indexes["season_end"][previous]
    retained = {player: True for player in counts}
    moved: set[int] = set()
    for date, player, from_id, to_id in transfers.get(team_id, []):
        if not (prior_end < date < cutoff): continue
        moved.add(player)
        if from_id == team_id: retained[player] = False
        if to_id == team_id and player in retained: retained[player] = True
    total_weight = sum(counts.values())
    continuity = sum(weight for player, weight in counts.items() if retained.get(player, True)) / max(1, total_weight)
    current_manager = current_manager_before(indexes["by_season"][season], team_id, cutoff)
    prior_manager = indexes["terminal_manager"].get((previous, team_id))
    if not current_manager or not prior_manager:
        return {"feature_complete": False, "reason": "lagged manager unavailable"}
    structural = min(1.0, len(moved) / max(1.0, 2.0 * len(counts)))
    return {
        "promoted_or_relegated": False,
        "roster_continuity": continuity,
        "coach_continuity": 1.0 if current_manager == prior_manager else 0.0,
        "structural_break_score": structural,
        "feature_complete": True,
    }


def blended_rate(current_num: float, current_n: float, prior_num: float, prior_n: float, borrowing_weight: float, max_prior: float) -> tuple[float, float]:
    if current_n <= 0:
        raise PlatformError("current sufficient statistic has zero denominator")
    current_value = current_num / current_n
    if prior_n <= 0 or max_prior <= 0 or borrowing_weight <= 0:
        return current_value, current_n
    prior_value = prior_num / prior_n
    blended = blend_sufficient_statistic(current_value, current_n, prior_value, prior_n, borrowing_weight, max_prior_equivalent_matches=max_prior)
    return blended["blended_value"], blended["current_effective_n"] + blended["borrowed_prior_effective_n"]


def challenger_matrix(current_state: dict[str, Any], prior_state: dict[str, Any] | None, home_id: int, away_id: int, home_feat: dict[str, Any], away_feat: dict[str, Any], candidate: dict[str, Any], params: dict[str, float], config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    home_key = normalize_team_token(f"club_{home_id}"); away_key = normalize_team_token(f"club_{away_id}")
    if home_key not in current_state["team"] or away_key not in current_state["team"]:
        raise PlatformError("current team state unavailable")
    current_home = current_state["team"][home_key]; current_away = current_state["team"][away_key]
    minimum_raw = int(config["minimum_team_raw_matches"])
    if current_home["home_raw_matches"] < minimum_raw or current_away["away_raw_matches"] < minimum_raw:
        raise PlatformError("current-season venue sample below minimum")
    prior_home = prior_state["team"].get(home_key) if prior_state else None
    prior_away = prior_state["team"].get(away_key) if prior_state else None
    max_prior = float(candidate["max_prior_equivalent_matches"])
    coeffs = candidate["coefficients"]
    home_weight = 0.0 if home_feat.get("promoted_or_relegated") or prior_home is None else commensurability_score(**{k: home_feat[k] for k in ("roster_continuity", "coach_continuity", "promoted_or_relegated", "structural_break_score")}, coefficients=coeffs)
    away_weight = 0.0 if away_feat.get("promoted_or_relegated") or prior_away is None else commensurability_score(**{k: away_feat[k] for k in ("roster_continuity", "coach_continuity", "promoted_or_relegated", "structural_break_score")}, coefficients=coeffs)

    def rate(cur: dict[str, Any], prv: dict[str, Any] | None, num_key: str, den_key: str, weight: float) -> tuple[float, float]:
        prior_num = float(prv[num_key]) if prv else 0.0; prior_n = float(prv[den_key]) if prv else 0.0
        return blended_rate(float(cur[num_key]), float(cur[den_key]), prior_num, prior_n, weight, max_prior)

    hgf, hgf_n = rate(current_home, prior_home, "home_gf", "home_matches", home_weight)
    hga, hga_n = rate(current_home, prior_home, "home_ga", "home_matches", home_weight)
    agf, agf_n = rate(current_away, prior_away, "away_gf", "away_matches", away_weight)
    aga, aga_n = rate(current_away, prior_away, "away_ga", "away_matches", away_weight)

    league_home = current_state["league_home_goals"]; league_away = current_state["league_away_goals"]; league_total = current_state["mean_total_goals"]
    team_prior = params["team_prior_matches"]
    home_gf_rate = _shrunk_rate(hgf * hgf_n, hgf_n, league_home, team_prior)
    home_ga_rate = _shrunk_rate(hga * hga_n, hga_n, league_away, team_prior)
    away_gf_rate = _shrunk_rate(agf * agf_n, agf_n, league_away, team_prior)
    away_ga_rate = _shrunk_rate(aga * aga_n, aga_n, league_home, team_prior)
    home_signal = league_home * (home_gf_rate / league_home) * (away_ga_rate / league_home)
    away_signal = league_away * (away_gf_rate / league_away) * (home_ga_rate / league_away)
    minimum_mu = params["minimum_goal_mean"]; maximum_mu = params["maximum_goal_mean"]
    home_signal = min(maximum_mu, max(minimum_mu, home_signal)); away_signal = min(maximum_mu, max(minimum_mu, away_signal))
    share = home_signal / max(EPS, home_signal + away_signal)

    htot_cur = float(current_home["home_gf"] + current_home["home_ga"]); htot_prv = float(prior_home["home_gf"] + prior_home["home_ga"]) if prior_home else 0.0
    atot_cur = float(current_away["away_gf"] + current_away["away_ga"]); atot_prv = float(prior_away["away_gf"] + prior_away["away_ga"]) if prior_away else 0.0
    htot, htot_n = blended_rate(htot_cur, float(current_home["home_matches"]), htot_prv, float(prior_home["home_matches"]) if prior_home else 0.0, home_weight, max_prior)
    atot, atot_n = blended_rate(atot_cur, float(current_away["away_matches"]), atot_prv, float(prior_away["away_matches"]) if prior_away else 0.0, away_weight, max_prior)
    home_total_rate = _shrunk_rate(htot * htot_n, htot_n, league_total, team_prior)
    away_total_rate = _shrunk_rate(atot * atot_n, atot_n, league_total, team_prior)
    pair_total = math.sqrt(max(EPS, home_total_rate) * max(EPS, away_total_rate))
    signal_weight = min(1.0, max(0.0, float(params.get("direct_total_signal_weight", 1.0))))
    mu_total = math.exp((1.0 - signal_weight) * math.log(max(EPS, league_total)) + signal_weight * math.log(max(EPS, pair_total)))
    mu_total = min(2.0 * maximum_mu, max(2.0 * minimum_mu, mu_total))
    factors = low_score_factors(current_state, params)
    matrix = build_score_matrix(mu_total * share, mu_total * (1.0 - share), current_state["nb_dispersion_k"], params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), factors)
    return matrix, {"home_borrowing_weight": home_weight, "away_borrowing_weight": away_weight, "max_prior_equivalent_matches": max_prior, "mu_total": mu_total, "home_share": share}


def total8(matrix: list[dict[str, Any]]) -> list[float]:
    out = [0.0] * 8
    for cell in matrix:
        total = int(cell["home_goals"]) + int(cell["away_goals"])
        out[min(7, total)] += float(cell["probability"])
    return out


def score_metrics(matrix: list[dict[str, Any]], home_goals: int, away_goals: int) -> dict[str, float]:
    marg = derive_score_marginals(matrix); one = marg["1x2"]
    actual_result = 0 if home_goals > away_goals else 1 if home_goals == away_goals else 2
    probs_result = [float(one["home"]), float(one["draw"]), float(one["away"])]
    brier = sum((p - (1.0 if i == actual_result else 0.0)) ** 2 for i, p in enumerate(probs_result)) / 3.0
    cumulative_p = [probs_result[0], probs_result[0] + probs_result[1]]; cumulative_y = [1.0 if actual_result <= 0 else 0.0, 1.0 if actual_result <= 1 else 0.0]
    rps1x2 = sum((p - y) ** 2 for p, y in zip(cumulative_p, cumulative_y)) / 2.0
    p_score = next((float(c["probability"]) for c in matrix if int(c["home_goals"]) == home_goals and int(c["away_goals"]) == away_goals), EPS)
    joint_log = -math.log(max(EPS, p_score))
    totals = total8(matrix); actual_total = min(7, home_goals + away_goals)
    cum_t = []; running = 0.0
    for p in totals[:-1]: running += p; cum_t.append(running)
    total_rps = sum((p - (1.0 if actual_total <= i else 0.0)) ** 2 for i, p in enumerate(cum_t)) / 7.0
    ranking = top_scores(matrix, len(matrix)); observed = f"{home_goals}-{away_goals}"
    rank = next((i + 1 for i, item in enumerate(ranking) if item["score"] == observed), len(ranking) + 1)
    cumulative = 0.0; set80 = set(); set90 = set()
    for item in ranking:
        if cumulative < 0.80 - 1e-12: set80.add(item["score"])
        if cumulative < 0.90 - 1e-12: set90.add(item["score"])
        cumulative += float(item["probability"])
    return {"joint_log": joint_log, "one_x_two_brier": brier, "one_x_two_rps": rps1x2, "total_goals_rps": total_rps, "top1": float(rank <= 1), "top3": float(rank <= 3), "top5": float(rank <= 5), "score80": float(observed in set80), "score90": float(observed in set90), "probability_sum_error": abs(sum(float(c["probability"]) for c in matrix) - 1.0)}


def eligible_predictions(competition_id: str, data: dict[str, Any], indexes: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    artifact = load_json(MODEL_ROOT / competition_id / "model.json"); parameter_map = artifact["point_in_time_parameters"]
    config = load_config(); by_season = indexes["by_season"]
    baseline: dict[str, dict[str, Any]] = {}; candidate_records: dict[str, list[dict[str, Any]]] = {c["id"]: [] for c in CANDIDATES}
    for season, selected in parameter_map.items():
        games = by_season.get(season, [])
        previous = indexes["previous"].get(season)
        if not games or not previous or previous not in by_season: continue
        prior_rows = [to_match(g, competition_id) for g in by_season[previous]]
        prior_cutoff = max(g["date"] for g in by_season[previous]) + timedelta(days=1)
        params = _merge_parameters(config, selected)
        try: prior_state = fit_current_season_state(prior_rows, prior_cutoff, params, config)
        except PlatformError: prior_state = None
        for target in games:
            history_games = [g for g in games if g["date"] < target["date"]]
            history = [to_match(g, competition_id) for g in history_games]
            try:
                current_state = fit_current_season_state(history, target["date"], params, config)
                base_means = expected_goals(current_state, f"club_{target['home_id']}", f"club_{target['away_id']}", params, config)
                base_matrix = build_score_matrix(float(base_means["mu_home"]), float(base_means["mu_away"]), current_state["nb_dispersion_k"], params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), low_score_factors(current_state, params))
            except PlatformError:
                continue
            home_feat = team_features(target["home_id"], season, target["date"], indexes, data["transfers"]); away_feat = team_features(target["away_id"], season, target["date"], indexes, data["transfers"])
            if not home_feat.get("feature_complete") or not away_feat.get("feature_complete"): continue
            key = f"{competition_id}:{season}:{target['game_id']}"; block = f"{season}:{target['date'].year}-{target['date'].month:02d}"
            base_metrics = score_metrics(base_matrix, target["home_goals"], target["away_goals"])
            baseline[key] = {"match_key": key, "date": target["date"].date().isoformat(), "season": season, "block_id": block, **base_metrics}
            for candidate in CANDIDATES:
                try: matrix, audit = challenger_matrix(current_state, prior_state, target["home_id"], target["away_id"], home_feat, away_feat, candidate, params, config)
                except PlatformError: continue
                metrics = score_metrics(matrix, target["home_goals"], target["away_goals"])
                candidate_records[candidate["id"]].append({"match_key": key, "date": target["date"].date().isoformat(), "season": season, "block_id": block, "candidate_id": candidate["id"], **metrics, **audit})
    return baseline, candidate_records


def date_windows(records: list[dict[str, Any]], count: int) -> list[set[str]]:
    dates = sorted({r["date"] for r in records})
    if not dates: return []
    out = []
    for i in range(min(count, len(dates))):
        a = i * len(dates) // count; b = (i + 1) * len(dates) // count; selected = set(dates[a:b])
        if selected: out.append(selected)
    return out


def bootstrap_diff(pairs: list[tuple[dict[str, Any], dict[str, Any]]], metric: str) -> dict[str, Any]:
    blocks: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for pair in pairs: blocks[pair[0]["block_id"]].append(pair)
    values = list(blocks.values())
    observed = mean(a[metric] - b[metric] for a, b in pairs)
    rng = random.Random(SEED + sum(ord(ch) for ch in metric)); samples = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sampled = [rng.choice(values) for _ in values]; flat = [pair for block in sampled for pair in block]
        samples.append(mean(a[metric] - b[metric] for a, b in flat))
    samples.sort(); lo = samples[max(0, int(0.025 * len(samples)) - 1)]; hi = samples[min(len(samples) - 1, int(0.975 * len(samples)))]
    return {"count": len(pairs), "blocks": len(values), "mean_difference": observed, "ci95_lower": lo, "ci95_upper": hi}


def validate(competition_id: str, cache: Path) -> dict[str, Any]:
    config = load_json(EVIDENCE_CONFIG); route = config["competition_mapping"][competition_id]
    if route["validation_route"] not in {"standard", "standard_regular_league_only"}:
        raise PlatformError(f"stage adapter required: {route['validation_route']}")
    data = load_domain_data(competition_id, cache); indexes = build_season_indexes(data)
    baseline, candidates = eligible_predictions(competition_id, data, indexes)
    baseline_by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in baseline.values(): baseline_by_season[r["season"]].append(r)
    candidate_maps = {cid: {r["match_key"]: r for r in rows} for cid, rows in candidates.items()}
    selected_model: list[dict[str, Any]] = []; selected_base: list[dict[str, Any]] = []; folds = []; prior_scored_keys: set[str] = set(); seen_test: set[str] = set()
    ordered_seasons = sorted(baseline_by_season, key=lambda s: min(r["date"] for r in baseline_by_season[s]))
    for season in ordered_seasons:
        season_records = sorted(baseline_by_season[season], key=lambda r: (r["date"], r["match_key"]))
        for window_index, test_dates in enumerate(date_windows(season_records, WINDOWS_PER_SEASON), start=1):
            test_start = min(test_dates)
            prior_keys = {key for key, row in baseline.items() if row["date"] < test_start}
            if not prior_keys:
                prior_scored_keys.update(r["match_key"] for r in season_records if r["date"] in test_dates)
                continue
            scored = []
            for candidate in CANDIDATES:
                cmap = candidate_maps[candidate["id"]]; comparable = [key for key in prior_keys if key in cmap]
                if len(comparable) < 100: continue
                scored.append((mean(cmap[key]["one_x_two_rps"] for key in comparable), mean(cmap[key]["joint_log"] for key in comparable), candidate["id"], len(comparable)))
            if not scored: continue
            scored.sort(); _, _, selected_id, selection_count = scored[0]; cmap = candidate_maps[selected_id]
            test_keys = [r["match_key"] for r in season_records if r["date"] in test_dates and r["match_key"] in cmap]
            if seen_test.intersection(test_keys): raise PlatformError("overlapping OOF test windows")
            seen_test.update(test_keys)
            for key in test_keys: selected_model.append(cmap[key]); selected_base.append(baseline[key])
            folds.append({"fold_id": f"{season}:RW{window_index}", "season": season, "test_start": test_start, "test_end": max(test_dates), "selected_candidate": selected_id, "selection_predictions": selection_count, "outer_predictions": len(test_keys)})
            prior_scored_keys.update(test_keys)
    pairs = list(zip(selected_model, selected_base))
    if not pairs: raise PlatformError("no paired dynamic-strength OOF predictions")
    cis = {metric: bootstrap_diff(pairs, metric) for metric in ("joint_log", "one_x_two_brier", "one_x_two_rps", "total_goals_rps")}
    def avg(rows: list[dict[str, Any]], metric: str) -> float: return mean(r[metric] for r in rows)
    coverage = {key: {"current": avg(selected_base, key), "candidate": avg(selected_model, key)} for key in ("top1", "top3", "top5", "score80", "score90")}
    selected_counts = Counter(f["selected_candidate"] for f in folds)
    checks = {
        "minimum_outer_predictions": len(pairs) >= 200,
        "minimum_rolling_time_folds": len(folds) >= 8,
        "one_x_two_rps_ci_improves": cis["one_x_two_rps"]["ci95_upper"] < 0.0,
        "joint_log_noninferior": cis["joint_log"]["ci95_upper"] <= 0.002,
        "one_x_two_brier_noninferior": cis["one_x_two_brier"]["ci95_upper"] <= 0.002,
        "total_goals_rps_noninferior": cis["total_goals_rps"]["ci95_upper"] <= 0.001,
        "top1_nonworse": coverage["top1"]["candidate"] + 1e-12 >= coverage["top1"]["current"],
        "top3_nonworse": coverage["top3"]["candidate"] + 1e-12 >= coverage["top3"]["current"],
        "top5_nonworse": coverage["top5"]["candidate"] + 1e-12 >= coverage["top5"]["current"],
        "score80_calibrated": 0.76 <= coverage["score80"]["candidate"] <= 0.84,
        "score90_calibrated": 0.86 <= coverage["score90"]["candidate"] <= 0.94,
        "probability_conservation": max(r["probability_sum_error"] for r in selected_model) <= 1e-8,
        "non_identity_selected": sum(count for cid, count in selected_counts.items() if cid != "identity_no_borrow") > 0,
    }
    status = "DYNAMIC_STRENGTH_REVIEW_CANDIDATE" if all(checks.values()) else "KEEP_RESEARCH_WEIGHT_0"
    report = {
        "schema_version": "V4.7.0-dynamic-strength-oof-screen-r1", "generated_at_utc": utc_now(), "competition_id": competition_id,
        "status": status, "formal_weight": 0, "automatic_promotion": False, "probability_change": False,
        "candidate_grid": CANDIDATES, "outer_predictions": len(pairs), "rolling_time_folds": len(folds), "selected_candidate_counts": dict(selected_counts),
        "confidence_intervals": cis, "coverage": coverage, "checks": checks, "folds": folds,
        "policy": "Research screen only. Passing creates a second-stage independent promotion review candidate; it never changes V4.7 formal probabilities or weights."
    }
    write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--competition", required=True); parser.add_argument("--cache-dir", default="/tmp/football-dynamic-strength-oof-cache")
    args = parser.parse_args()
    try: report = validate(args.competition, Path(args.cache_dir))
    except Exception as exc:
        report = {"schema_version": "V4.7.0-dynamic-strength-oof-screen-r1", "generated_at_utc": utc_now(), "competition_id": args.competition, "status": "FAILED", "formal_weight": 0, "automatic_promotion": False, "probability_change": False, "reason": str(exc)}
        write_json(REPORT_ROOT / f"{args.competition}.json", report); print(json.dumps(report, ensure_ascii=False, indent=2)); return 1
    print(json.dumps({"competition_id": args.competition, "status": report["status"], "outer_predictions": report["outer_predictions"], "rolling_time_folds": report["rolling_time_folds"]}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
