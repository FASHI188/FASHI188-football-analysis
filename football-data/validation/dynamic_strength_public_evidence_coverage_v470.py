#!/usr/bin/env python3
"""Audit public, competition-local evidence coverage for V4.7 dynamic strength.

This script acquires only public observed labels from dcaribou/transfermarkt-datasets.
It never changes formal probabilities or challenger weights.  It reports whether
lagged manager, prior-season starting-XI and dated transfer inputs exist before a
future chronological OOF validator is allowed to run.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "dynamic_strength_public_evidence_v470.json"
OUTPUT_ROOT = ROOT / "manifests" / "dynamic_strength_public_evidence_v470"
STATUS_PATH = ROOT / "manifests" / "dynamic_strength_public_evidence_v470_status.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def download(name: str, config: dict[str, Any], cache: Path) -> Path:
    filename = config["source"]["files"][name]
    target = cache / filename
    if target.exists() and target.stat().st_size > 0:
        return target
    cache.mkdir(parents=True, exist_ok=True)
    primary = config["source"]["dataset_delivery_base"].rstrip("/") + "/" + filename
    fallback = "https://raw.githubusercontent.com/dcaribou/transfermarkt-datasets/master/data/prep/" + filename
    last_error: Exception | None = None
    for url in (primary, fallback):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "FASHI188-football-analysis/4.7"})
            with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            if target.stat().st_size > 0:
                return target
        except Exception as exc:  # pragma: no cover - network dependent
            last_error = exc
            if target.exists():
                target.unlink()
    raise RuntimeError(f"failed to download {name}: {last_error}")


def rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def parse_date(value: str) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def integer(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_starting_lineup(value: Any) -> bool:
    token = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return token in {"starting_lineup", "starting_xi", "starting_eleven", "startelf"} or token.startswith("starting")


def season_sort_key(value: str) -> tuple[int, str]:
    try:
        return int(value), value
    except ValueError:
        return 10**9, value


def audit(cache_dir: Path) -> dict[str, Any]:
    config = load_json(CONFIG_PATH)
    mapping = config["competition_mapping"]
    external_to_internal = {item["transfermarkt_competition_id"]: key for key, item in mapping.items()}

    games_path = download("games", config, cache_dir)
    lineups_path = download("game_lineups", config, cache_dir)
    transfers_path = download("transfers", config, cache_dir)

    games_by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    game_to_comp: dict[int, str] = {}
    game_meta: dict[int, dict[str, Any]] = {}
    club_to_comps: dict[int, set[str]] = defaultdict(set)
    manager_present = Counter()

    for row in rows(games_path):
        internal = external_to_internal.get(str(row.get("competition_id") or ""))
        if not internal:
            continue
        game_id = integer(row.get("game_id"))
        home_id = integer(row.get("home_club_id"))
        away_id = integer(row.get("away_club_id"))
        home_goals = integer(row.get("home_club_goals"))
        away_goals = integer(row.get("away_club_goals"))
        date = parse_date(str(row.get("date") or ""))
        if None in (game_id, home_id, away_id, home_goals, away_goals) or date is None:
            continue
        record = {
            "game_id": game_id,
            "season": str(row.get("season") or ""),
            "date": date,
            "home_club_id": home_id,
            "away_club_id": away_id,
            "home_manager": str(row.get("home_club_manager_name") or "").strip(),
            "away_manager": str(row.get("away_club_manager_name") or "").strip(),
            "home_name": str(row.get("home_club_name") or home_id),
            "away_name": str(row.get("away_club_name") or away_id),
        }
        games_by_comp[internal].append(record)
        game_to_comp[game_id] = internal
        game_meta[game_id] = record
        club_to_comps[home_id].add(internal)
        club_to_comps[away_id].add(internal)
        if record["home_manager"] and record["away_manager"]:
            manager_present[internal] += 1

    starting_players: dict[tuple[int, int], set[int]] = defaultdict(set)
    lineup_type_counts = Counter()
    for row in rows(lineups_path):
        game_id = integer(row.get("game_id"))
        if game_id is None or game_id not in game_to_comp:
            continue
        lineup_type_counts[str(row.get("type") or "")] += 1
        if not is_starting_lineup(row.get("type")):
            continue
        club_id = integer(row.get("club_id"))
        player_id = integer(row.get("player_id"))
        if club_id is None or player_id is None:
            continue
        starting_players[(game_id, club_id)].add(player_id)

    transfer_counts = Counter()
    transfer_dated_counts = Counter()
    for row in rows(transfers_path):
        from_id = integer(row.get("from_club_id"))
        to_id = integer(row.get("to_club_id"))
        touched: set[str] = set()
        if from_id is not None:
            touched.update(club_to_comps.get(from_id, set()))
        if to_id is not None:
            touched.update(club_to_comps.get(to_id, set()))
        if not touched:
            continue
        dated = parse_date(str(row.get("transfer_date") or "")) is not None
        for competition_id in touched:
            transfer_counts[competition_id] += 1
            if dated:
                transfer_dated_counts[competition_id] += 1

    reports: dict[str, Any] = {}
    for competition_id, route in mapping.items():
        games = sorted(games_by_comp.get(competition_id, []), key=lambda item: (item["date"], item["game_id"]))
        seasons = sorted({item["season"] for item in games if item["season"]}, key=season_sort_key)
        complete_xi = 0
        team_season_lineup_games = Counter()
        team_season_games = Counter()
        for game in games:
            home_key = (game["season"], game["home_club_id"])
            away_key = (game["season"], game["away_club_id"])
            team_season_games[home_key] += 1
            team_season_games[away_key] += 1
            home_xi = starting_players.get((game["game_id"], game["home_club_id"]), set())
            away_xi = starting_players.get((game["game_id"], game["away_club_id"]), set())
            if len(home_xi) >= 11:
                team_season_lineup_games[home_key] += 1
            if len(away_xi) >= 11:
                team_season_lineup_games[away_key] += 1
            if len(home_xi) >= 11 and len(away_xi) >= 11:
                complete_xi += 1

        lagged_manager_eligible = 0
        prior_lineup_eligible = 0
        previous_season: dict[str, str] = {seasons[index]: seasons[index - 1] for index in range(1, len(seasons))}
        terminal_manager: dict[tuple[str, int], str] = {}
        last_manager_current: dict[tuple[str, int], str] = {}
        for game in games:
            season = game["season"]
            prev = previous_season.get(season)
            home_id = game["home_club_id"]
            away_id = game["away_club_id"]
            if prev:
                if (
                    terminal_manager.get((prev, home_id))
                    and terminal_manager.get((prev, away_id))
                    and last_manager_current.get((season, home_id))
                    and last_manager_current.get((season, away_id))
                ):
                    lagged_manager_eligible += 1
                if team_season_lineup_games.get((prev, home_id), 0) > 0 and team_season_lineup_games.get((prev, away_id), 0) > 0:
                    prior_lineup_eligible += 1
            if game["home_manager"]:
                last_manager_current[(season, home_id)] = game["home_manager"]
                terminal_manager[(season, home_id)] = game["home_manager"]
            if game["away_manager"]:
                last_manager_current[(season, away_id)] = game["away_manager"]
                terminal_manager[(season, away_id)] = game["away_manager"]

        completed = len(games)
        standard_route = route["validation_route"] in {
            "standard", "standard_regular_league_only"
        }
        source_available = completed > 0
        feature_inputs_observed = bool(
            source_available
            and len(seasons) >= 2
            and complete_xi > 0
            and manager_present[competition_id] > 0
            and transfer_dated_counts[competition_id] > 0
        )
        reports[competition_id] = {
            "competition_id": competition_id,
            "transfermarkt_competition_id": route["transfermarkt_competition_id"],
            "validation_route": route["validation_route"],
            "status": (
                "PUBLIC_EVIDENCE_OBSERVED_STANDARD_ROUTE_READY"
                if feature_inputs_observed and standard_route
                else "PUBLIC_EVIDENCE_OBSERVED_STAGE_ADAPTER_REQUIRED"
                if feature_inputs_observed
                else "PUBLIC_EVIDENCE_PARTIAL_OR_UNAVAILABLE"
            ),
            "formal_weight": 0,
            "automatic_promotion": False,
            "completed_games": completed,
            "seasons": seasons,
            "season_count": len(seasons),
            "games_with_both_managers": manager_present[competition_id],
            "both_manager_coverage": manager_present[competition_id] / completed if completed else 0.0,
            "games_with_two_complete_starting_xi": complete_xi,
            "two_complete_xi_coverage": complete_xi / completed if completed else 0.0,
            "dated_transfers_touching_competition_clubs": transfer_dated_counts[competition_id],
            "all_transfers_touching_competition_clubs": transfer_counts[competition_id],
            "lagged_manager_feature_eligible_matches": lagged_manager_eligible,
            "prior_season_lineup_feature_eligible_matches": prior_lineup_eligible,
            "source_data_available": source_available,
            "feature_inputs_observed": feature_inputs_observed,
            "chronological_oof_may_start": feature_inputs_observed and standard_route,
            "probability_change": False,
            "policy": "Coverage audit only. Target-match managers and lineups are never used as pre-match features; special-format routes require a separate row-level adapter before validation."
        }
        write_json(OUTPUT_ROOT / f"{competition_id}.json", reports[competition_id])

    ready = [key for key, item in reports.items() if item["chronological_oof_may_start"]]
    stage_blocked = [key for key, item in reports.items() if item["feature_inputs_observed"] and not item["chronological_oof_may_start"]]
    unavailable = [key for key, item in reports.items() if not item["feature_inputs_observed"]]
    status = {
        "schema_version": "V4.7.0-dynamic-strength-public-evidence-coverage-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS" if len(reports) == len(mapping) else "PARTIAL",
        "competition_count_requested": len(mapping),
        "competition_count_reported": len(reports),
        "chronological_oof_ready": ready,
        "stage_adapter_required": stage_blocked,
        "partial_or_unavailable": unavailable,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "probability_change": False,
        "lineup_type_counts_observed": dict(lineup_type_counts.most_common()),
        "reports": reports,
        "policy": "Public evidence coverage does not activate dynamic strength. Only competition-specific chronological OOF validation can create a promotion candidate, and CURRENT-compliant promotion is still required."
    }
    write_json(STATUS_PATH, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default="/tmp/football-dynamic-strength-public-cache")
    args = parser.parse_args()
    result = audit(Path(args.cache_dir))
    print(json.dumps({
        "status": result["status"],
        "oof_ready": result["chronological_oof_ready"],
        "stage_adapter_required": result["stage_adapter_required"],
        "partial_or_unavailable": result["partial_or_unavailable"]
    }, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
