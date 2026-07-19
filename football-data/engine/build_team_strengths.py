#!/usr/bin/env python3
"""Build point-in-time descriptive team feature snapshots.

This is not a formal probability model. It produces reproducible evidence for
question-time analysis: long/short form, home/away splits and a fixed-parameter
Elo descriptor. Formal weight remains zero until time-ordered validation.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platform_core import (
    ROOT,
    MatchRow,
    PlatformError,
    atomic_write_json,
    load_aliases,
    load_json,
    load_registry,
    normalize_team_token,
    read_processed_matches,
    sha256_file,
    sha256_json,
    stable_team_id,
)

CONFIG_PATH = ROOT / "config" / "team_strength_config.json"
OUTPUT_ROOT = ROOT / "team_strengths"
MANIFEST_PATH = ROOT / "manifests" / "latest_team_strengths.json"


def _empty_accumulator() -> dict[str, float]:
    return {
        "weighted_matches": 0.0,
        "weighted_goals_for": 0.0,
        "weighted_goals_against": 0.0,
        "weighted_points": 0.0,
        "weighted_btts": 0.0,
        "weighted_clean_sheets": 0.0,
        "weighted_failed_to_score": 0.0,
        "raw_matches": 0.0,
        "raw_goals_for": 0.0,
        "raw_goals_against": 0.0,
        "raw_points": 0.0,
    }


def _add_match(acc: dict[str, float], goals_for: int, goals_against: int, weight: float) -> None:
    points = 3 if goals_for > goals_against else 1 if goals_for == goals_against else 0
    acc["weighted_matches"] += weight
    acc["weighted_goals_for"] += goals_for * weight
    acc["weighted_goals_against"] += goals_against * weight
    acc["weighted_points"] += points * weight
    acc["weighted_btts"] += (1.0 if goals_for > 0 and goals_against > 0 else 0.0) * weight
    acc["weighted_clean_sheets"] += (1.0 if goals_against == 0 else 0.0) * weight
    acc["weighted_failed_to_score"] += (1.0 if goals_for == 0 else 0.0) * weight
    acc["raw_matches"] += 1
    acc["raw_goals_for"] += goals_for
    acc["raw_goals_against"] += goals_against
    acc["raw_points"] += points


def _finish_accumulator(acc: dict[str, float]) -> dict[str, Any]:
    weighted_matches = acc["weighted_matches"]
    raw_matches = int(acc["raw_matches"])
    return {
        "matches": raw_matches,
        "effective_matches": round(weighted_matches, 6),
        "weighted_goals_for_per_match": round(acc["weighted_goals_for"] / weighted_matches, 6) if weighted_matches else None,
        "weighted_goals_against_per_match": round(acc["weighted_goals_against"] / weighted_matches, 6) if weighted_matches else None,
        "weighted_points_per_match": round(acc["weighted_points"] / weighted_matches, 6) if weighted_matches else None,
        "weighted_btts_rate": round(acc["weighted_btts"] / weighted_matches, 6) if weighted_matches else None,
        "weighted_clean_sheet_rate": round(acc["weighted_clean_sheets"] / weighted_matches, 6) if weighted_matches else None,
        "weighted_failed_to_score_rate": round(acc["weighted_failed_to_score"] / weighted_matches, 6) if weighted_matches else None,
        "raw_goals_for_per_match": round(acc["raw_goals_for"] / raw_matches, 6) if raw_matches else None,
        "raw_goals_against_per_match": round(acc["raw_goals_against"] / raw_matches, 6) if raw_matches else None,
        "raw_points_per_match": round(acc["raw_points"] / raw_matches, 6) if raw_matches else None,
    }


def _recent_summary(matches: list[dict[str, Any]], window: int) -> dict[str, Any]:
    subset = matches[-window:]
    if not subset:
        return {"window": window, "matches": 0}
    goals_for = sum(item["goals_for"] for item in subset)
    goals_against = sum(item["goals_against"] for item in subset)
    points = sum(item["points"] for item in subset)
    return {
        "window": window,
        "matches": len(subset),
        "goals_for_per_match": round(goals_for / len(subset), 6),
        "goals_against_per_match": round(goals_against / len(subset), 6),
        "points_per_match": round(points / len(subset), 6),
        "results": "".join(item["result"] for item in subset),
        "first_match_date": subset[0]["date"],
        "last_match_date": subset[-1]["date"],
    }


def _elo_snapshots(matches: list[MatchRow], config: dict[str, Any]) -> dict[str, float]:
    elo_config = config["elo"]
    initial = float(elo_config["initial_rating"])
    home_advantage = float(elo_config["home_advantage"])
    k_factor = float(elo_config["k_factor"])
    regression = float(elo_config["season_regression_fraction"])
    ratings: defaultdict[str, float] = defaultdict(lambda: initial)
    last_season: str | None = None

    for match in matches:
        if last_season is not None and match.season != last_season:
            for team in list(ratings):
                ratings[team] = initial + (ratings[team] - initial) * (1.0 - regression)
        last_season = match.season
        home_rating = ratings[match.home_team]
        away_rating = ratings[match.away_team]
        expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - (home_rating + home_advantage)) / 400.0))
        actual_home = 1.0 if match.home_goals > match.away_goals else 0.5 if match.home_goals == match.away_goals else 0.0
        change = k_factor * (actual_home - expected_home)
        ratings[match.home_team] = home_rating + change
        ratings[match.away_team] = away_rating - change
    return dict(ratings)


def build_competition_snapshot(competition: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    competition_id = competition["competition_id"]
    matches = read_processed_matches(competition_id)
    as_of = max(match.date for match in matches)
    half_life_days = float(config["half_life_days"])
    if half_life_days <= 0:
        raise PlatformError("half_life_days must be positive")
    decay = math.log(2.0) / half_life_days
    minimum_matches = int(config["minimum_matches_for_stable_status"])
    minimum_split_matches = int(config["minimum_home_or_away_matches"])
    recent_windows = [int(item) for item in config["recent_windows"]]

    accumulators: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: {"overall": _empty_accumulator(), "home": _empty_accumulator(), "away": _empty_accumulator()}
    )
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seasons_by_team: dict[str, set[str]] = defaultdict(set)
    raw_names_by_token: dict[str, set[str]] = defaultdict(set)

    for match in matches:
        age_days = max(0.0, (as_of - match.date).total_seconds() / 86400.0)
        weight = math.exp(-decay * age_days)
        home_points = 3 if match.home_goals > match.away_goals else 1 if match.home_goals == match.away_goals else 0
        away_points = 3 if match.away_goals > match.home_goals else 1 if match.away_goals == match.home_goals else 0

        _add_match(accumulators[match.home_team]["overall"], match.home_goals, match.away_goals, weight)
        _add_match(accumulators[match.home_team]["home"], match.home_goals, match.away_goals, weight)
        _add_match(accumulators[match.away_team]["overall"], match.away_goals, match.home_goals, weight)
        _add_match(accumulators[match.away_team]["away"], match.away_goals, match.home_goals, weight)

        histories[match.home_team].append({
            "date": match.date.date().isoformat(),
            "venue": "home",
            "opponent": match.away_team,
            "goals_for": match.home_goals,
            "goals_against": match.away_goals,
            "points": home_points,
            "result": "W" if home_points == 3 else "D" if home_points == 1 else "L",
            "season": match.season,
            "stage": match.stage,
        })
        histories[match.away_team].append({
            "date": match.date.date().isoformat(),
            "venue": "away",
            "opponent": match.home_team,
            "goals_for": match.away_goals,
            "goals_against": match.home_goals,
            "points": away_points,
            "result": "W" if away_points == 3 else "D" if away_points == 1 else "L",
            "season": match.season,
            "stage": match.stage,
        })
        seasons_by_team[match.home_team].add(match.season)
        seasons_by_team[match.away_team].add(match.season)
        raw_names_by_token[normalize_team_token(match.home_team)].add(match.home_team)
        raw_names_by_token[normalize_team_token(match.away_team)].add(match.away_team)

    collisions = {
        token: sorted(names)
        for token, names in raw_names_by_token.items()
        if len(names) > 1
    }
    if collisions and config["hard_gates"].get("identity_collision_is_failure", True):
        raise PlatformError(f"team identity collisions in {competition_id}: {collisions}")

    elo = _elo_snapshots(matches, config)
    teams: list[dict[str, Any]] = []
    for name in sorted(accumulators):
        history = sorted(histories[name], key=lambda item: (item["date"], item["opponent"]))
        overall = _finish_accumulator(accumulators[name]["overall"])
        home = _finish_accumulator(accumulators[name]["home"])
        away = _finish_accumulator(accumulators[name]["away"])
        last_date = datetime.fromisoformat(history[-1]["date"]).replace(tzinfo=timezone.utc)
        days_since_last = int((as_of - last_date).days)
        if overall["matches"] < minimum_matches:
            status = "insufficient_sample"
        elif home["matches"] < minimum_split_matches or away["matches"] < minimum_split_matches:
            status = "split_sample_limited"
        elif days_since_last > 180:
            status = "stale_or_inactive"
        else:
            status = "descriptive_features_available"
        teams.append({
            "team_id": stable_team_id(competition_id, name),
            "team_name": name,
            "normalized_token": normalize_team_token(name),
            "status": status,
            "last_match_date": history[-1]["date"],
            "days_since_last_match_at_data_as_of": days_since_last,
            "seasons_observed": sorted(seasons_by_team[name]),
            "elo_descriptor": round(elo.get(name, float(config["elo"]["initial_rating"])), 6),
            "overall": overall,
            "home": home,
            "away": away,
            "recent": {str(window): _recent_summary(history, window) for window in recent_windows},
        })

    source_files = sorted((ROOT / "processed" / competition_id).glob("*.csv"))
    source_hashes = {str(path.relative_to(ROOT)): sha256_file(path) for path in source_files}
    return {
        "schema_version": "1.0",
        "competition_id": competition_id,
        "competition_name_zh": competition["name_zh"],
        "feature_status": config["generated_feature_status"],
        "data_as_of": as_of.date().isoformat(),
        "match_count": len(matches),
        "team_count": len(teams),
        "source_hashes": source_hashes,
        "config_sha256": sha256_file(CONFIG_PATH),
        "input_hash": sha256_json({"source_hashes": source_hashes, "config": config}),
        "limitations": [
            "These features are descriptive and have formal model weight 0 until time-ordered out-of-sample validation.",
            "They do not include point-in-time lineups, injuries, suspensions, task state or synchronized market prices.",
            "They do not generate 1X2, total-goal or exact-score probabilities.",
        ],
        "teams": teams,
    }


def run(write: bool = True) -> dict[str, Any]:
    registry = load_registry()
    config = load_json(CONFIG_PATH)
    snapshots: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    for competition in registry["competitions"]:
        competition_id = competition["competition_id"]
        try:
            snapshot = build_competition_snapshot(competition, config)
        except PlatformError as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
            continue
        snapshots[competition_id] = snapshot
        if write:
            atomic_write_json(OUTPUT_ROOT / competition_id / "latest.json", snapshot)

    manifest = {
        "schema_version": "1.0",
        "feature_status": config["generated_feature_status"],
        "competition_count_requested": len(registry["competitions"]),
        "competition_count_built": len(snapshots),
        "competition_count_failed": len(failures),
        "total_teams": sum(item["team_count"] for item in snapshots.values()),
        "total_matches": sum(item["match_count"] for item in snapshots.values()),
        "config_sha256": sha256_file(CONFIG_PATH),
        "registry_sha256": sha256_file(ROOT / "config" / "platform_registry.json"),
        "competition_inputs": {
            key: {
                "data_as_of": value["data_as_of"],
                "match_count": value["match_count"],
                "team_count": value["team_count"],
                "input_hash": value["input_hash"],
            }
            for key, value in sorted(snapshots.items())
        },
        "failures": failures,
    }
    if write:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"team-strength build failed for {len(failures)} competitions: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true", help="Build in memory without writing files")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = run(write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
