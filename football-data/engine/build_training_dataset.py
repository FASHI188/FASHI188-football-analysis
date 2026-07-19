#!/usr/bin/env python3
"""Build leakage-controlled, time-ordered model-development datasets.

The output is train-ready evidence only. It does not train a model, select a
family, calibrate probabilities, or grant formal weight to any model.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from platform_core import ROOT, MatchRow, PlatformError, atomic_write_json, load_json, load_registry, read_processed_matches, sha256_file, sha256_json

CONFIG_PATH = ROOT / "config" / "team_strength_config.json"
OUTPUT_ROOT = ROOT / "training_datasets"
MANIFEST_PATH = ROOT / "manifests" / "latest_training_datasets.json"


def _summary(history: list[dict[str, Any]], venue: str | None = None, last_n: int | None = None) -> dict[str, float | int | None]:
    rows = [item for item in history if venue is None or item["venue"] == venue]
    if last_n is not None:
        rows = rows[-last_n:]
    n = len(rows)
    if not n:
        return {"matches": 0, "gf": None, "ga": None, "ppg": None}
    return {
        "matches": n,
        "gf": round(sum(item["gf"] for item in rows) / n, 8),
        "ga": round(sum(item["ga"] for item in rows) / n, 8),
        "ppg": round(sum(item["points"] for item in rows) / n, 8),
    }


def _season_splits(matches: list[MatchRow], competition: dict[str, Any]) -> dict[str, str]:
    seasons = []
    for match in matches:
        if match.season not in seasons:
            seasons.append(match.season)
    if len(seasons) < 3:
        return {season: "prospective_holdout" if season == seasons[-1] else "train" for season in seasons}
    status = str(competition.get("current_season_status", ""))
    last_name = "prospective_holdout" if "partial" in status or "unavailable" in status else "test"
    splits = {season: "train" for season in seasons}
    splits[seasons[-2]] = "validation"
    splits[seasons[-1]] = last_name
    return splits


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_competition(competition: dict[str, Any], config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    competition_id = competition["competition_id"]
    matches = read_processed_matches(competition_id)
    splits = _season_splits(matches, competition)
    initial = float(config["elo"]["initial_rating"])
    home_advantage = float(config["elo"]["home_advantage"])
    k_factor = float(config["elo"]["k_factor"])
    regression = float(config["elo"]["season_regression_fraction"])
    histories: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    ratings: defaultdict[str, float] = defaultdict(lambda: initial)
    rows: list[dict[str, Any]] = []
    last_season = None

    for match in matches:
        if last_season is not None and match.season != last_season:
            for team in list(ratings):
                ratings[team] = initial + (ratings[team] - initial) * (1.0 - regression)
        last_season = match.season

        home_all = _summary(histories[match.home_team])
        home_venue = _summary(histories[match.home_team], "home")
        home_last5 = _summary(histories[match.home_team], last_n=5)
        away_all = _summary(histories[match.away_team])
        away_venue = _summary(histories[match.away_team], "away")
        away_last5 = _summary(histories[match.away_team], last_n=5)
        home_elo = ratings[match.home_team]
        away_elo = ratings[match.away_team]

        total = match.home_goals + match.away_goals
        row = {
            "competition_id": competition_id,
            "season": match.season,
            "split": splits[match.season],
            "stage": match.stage,
            "date": match.date.date().isoformat(),
            "home_team": match.home_team,
            "away_team": match.away_team,
            "home_history_matches": home_all["matches"],
            "home_history_gf": home_all["gf"],
            "home_history_ga": home_all["ga"],
            "home_history_ppg": home_all["ppg"],
            "home_venue_matches": home_venue["matches"],
            "home_venue_gf": home_venue["gf"],
            "home_venue_ga": home_venue["ga"],
            "home_last5_matches": home_last5["matches"],
            "home_last5_gf": home_last5["gf"],
            "home_last5_ga": home_last5["ga"],
            "home_last5_ppg": home_last5["ppg"],
            "away_history_matches": away_all["matches"],
            "away_history_gf": away_all["gf"],
            "away_history_ga": away_all["ga"],
            "away_history_ppg": away_all["ppg"],
            "away_venue_matches": away_venue["matches"],
            "away_venue_gf": away_venue["gf"],
            "away_venue_ga": away_venue["ga"],
            "away_last5_matches": away_last5["matches"],
            "away_last5_gf": away_last5["gf"],
            "away_last5_ga": away_last5["ga"],
            "away_last5_ppg": away_last5["ppg"],
            "home_elo_pre_match": round(home_elo, 8),
            "away_elo_pre_match": round(away_elo, 8),
            "elo_difference_with_home_advantage": round(home_elo + home_advantage - away_elo, 8),
            "cold_start_flag": int(home_all["matches"] < 5 or away_all["matches"] < 5),
            "stage_unverified_flag": int("unverified" in match.stage),
            "label_home_goals": match.home_goals,
            "label_away_goals": match.away_goals,
            "label_total_goals": total,
            "label_total_goals_bin": str(total) if total <= 6 else "7+",
            "label_goal_difference": match.home_goals - match.away_goals,
            "label_result": "H" if match.home_goals > match.away_goals else "D" if match.home_goals == match.away_goals else "A",
            "source_path": match.source_path,
        }
        rows.append(row)

        home_points = 3 if match.home_goals > match.away_goals else 1 if match.home_goals == match.away_goals else 0
        away_points = 3 if match.away_goals > match.home_goals else 1 if match.away_goals == match.home_goals else 0
        histories[match.home_team].append({"venue": "home", "gf": match.home_goals, "ga": match.away_goals, "points": home_points})
        histories[match.away_team].append({"venue": "away", "gf": match.away_goals, "ga": match.home_goals, "points": away_points})

        expected_home = 1.0 / (1.0 + 10.0 ** ((away_elo - (home_elo + home_advantage)) / 400.0))
        actual_home = 1.0 if home_points == 3 else 0.5 if home_points == 1 else 0.0
        change = k_factor * (actual_home - expected_home)
        ratings[match.home_team] = home_elo + change
        ratings[match.away_team] = away_elo - change

    audit = {
        "competition_id": competition_id,
        "rows": len(rows),
        "seasons": splits,
        "split_counts": {
            split: sum(row["split"] == split for row in rows)
            for split in sorted(set(splits.values()))
        },
        "cold_start_rows": sum(row["cold_start_flag"] for row in rows),
        "stage_unverified_rows": sum(row["stage_unverified_flag"] for row in rows),
        "leakage_controls": {
            "random_split_used": False,
            "features_updated_before_label": False,
            "features_use_only_prior_matches": True,
            "time_ordered_split": True
        },
    }
    return rows, audit


def run(write: bool = True) -> dict[str, Any]:
    registry = load_registry()
    config = load_json(CONFIG_PATH)
    audits: dict[str, Any] = {}
    failures = []
    for competition in registry["competitions"]:
        competition_id = competition["competition_id"]
        try:
            rows, audit = build_competition(competition, config)
        except PlatformError as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
            continue
        if write:
            path = OUTPUT_ROOT / competition_id / "point_in_time.csv"
            _write_csv(path, rows)
            audit["output_path"] = str(path.relative_to(ROOT))
            audit["output_sha256"] = sha256_file(path)
        audits[competition_id] = audit
    manifest = {
        "schema_version": "1.0",
        "status": "train_ready_dataset_only_no_formal_model_weight",
        "competition_count_requested": len(registry["competitions"]),
        "competition_count_built": len(audits),
        "total_rows": sum(item["rows"] for item in audits.values()),
        "competitions": audits,
        "failures": failures,
        "formal_model_promoted": False,
        "required_next_validation": [
            "time-ordered rolling out-of-sample evaluation",
            "Log Score, Brier Score and RPS",
            "calibration slope/intercept and grouped calibration",
            "total-goal tail calibration",
            "score-matrix Top-k and marginal calibration",
            "cross-season and cross-competition stability"
        ]
    }
    if write:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"training dataset build failures: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
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
