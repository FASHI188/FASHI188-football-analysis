#!/usr/bin/env python3
"""Backfill historical starting-XI evidence from public independent datasets.

Sources currently supported:
  1) dcaribou/transfermarkt-datasets (CC0 curated dataset; Transfermarkt-derived)
  2) StatsBomb Open Data (research/open-data subset)

The output is normalized JSONL under football-data/evidence/lineups/<competition>/.
This collector never promotes evidence by itself. The multi-source validator decides
whether two records are genuinely independent and consistent enough for a verified
historical lineup label.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "evidence" / "lineups"

TM_BASE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
SB_BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

CROSS_YEAR = {
    "ENG_PremierLeague", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1",
    "ESP_LaLiga", "POR_PrimeiraLiga", "NED_Eredivisie", "SUI_SuperLeague",
    "SCO_Premiership", "UEFA_ChampionsLeague",
}


def _http_bytes(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "HHH1-football-evidence/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _norm(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()
    return value


def _competition_id(name: Any, country: Any) -> str | None:
    n, c = _norm(name), _norm(country)
    rules = [
        ("ENG_PremierLeague", c in {"england", "great britain"} and "premier league" in n),
        ("GER_Bundesliga", c == "germany" and (n == "1 bundesliga" or n == "bundesliga")),
        ("ITA_SerieA", c == "italy" and "serie a" in n),
        ("FRA_Ligue1", c == "france" and "ligue 1" in n),
        ("ESP_LaLiga", c == "spain" and ("la liga" in n or "laliga" in n or "primera division" in n)),
        ("POR_PrimeiraLiga", c == "portugal" and ("primeira liga" in n or "liga portugal" in n)),
        ("NED_Eredivisie", c in {"netherlands", "holland"} and "eredivisie" in n),
        ("SUI_SuperLeague", c == "switzerland" and "super league" in n),
        ("SCO_Premiership", c == "scotland" and "premiership" in n),
        ("SWE_Allsvenskan", c == "sweden" and "allsvenskan" in n),
        ("NOR_Eliteserien", c == "norway" and "eliteserien" in n),
        ("JPN_J1", c == "japan" and ("j1" in n or "j league" in n)),
        ("KOR_KLeague1", c in {"south korea", "korea south", "korea republic"} and "k league 1" in n),
        ("BRA_SerieA", c == "brazil" and ("serie a" in n or "brasileirao" in n)),
        ("ARG_Primera", c == "argentina" and ("primera" in n or "liga profesional" in n)),
        ("USA_MLS", c in {"united states", "usa"} and "major league soccer" in n),
        ("UEFA_ChampionsLeague", (c == "europe" or not c) and "champions league" in n),
    ]
    for competition_id, matched in rules:
        if matched:
            return competition_id
    return None


def _canonical_season(competition_id: str, match_date: str) -> str:
    d = date.fromisoformat(match_date[:10])
    if competition_id not in CROSS_YEAR:
        return str(d.year)
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start}/{str(start + 1)[-2:]}"


def _surrogate_kickoff(match_date: str) -> str:
    # The public Transfermarkt prepared table exposes match date but not kickoff time.
    # Noon UTC is a deterministic ordering surrogate. Evidence consumers must treat
    # same-team/same-date collisions as ambiguous and exclude them from PIT validation.
    return f"{match_date[:10]}T12:00:00+00:00"


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    materialized.sort(key=lambda r: (str(r.get("kickoff_utc")), str(r.get("team")), str(r.get("source_group"))))
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in materialized), encoding="utf-8")
    return len(materialized)


def _csv_gz(url: str) -> list[dict[str, str]]:
    data = gzip.decompress(_http_bytes(url))
    text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", newline="")
    return list(csv.DictReader(text))


def collect_transfermarkt() -> dict[str, int]:
    competitions = _csv_gz(f"{TM_BASE}/competitions.csv.gz")
    comp_map: dict[str, str] = {}
    for row in competitions:
        target = _competition_id(row.get("name"), row.get("country_name"))
        if target:
            comp_map[str(row.get("competition_id"))] = target

    games_rows = _csv_gz(f"{TM_BASE}/games.csv.gz")
    games: dict[str, dict[str, Any]] = {}
    club_to_team: dict[tuple[str, str], str] = {}
    for row in games_rows:
        target = comp_map.get(str(row.get("competition_id")))
        if not target or not row.get("game_id") or not row.get("date"):
            continue
        game_id = str(row["game_id"])
        games[game_id] = {
            "competition_id": target,
            "date": row["date"],
            "season": _canonical_season(target, row["date"]),
            "url": row.get("url") or "",
            "home_club_id": str(row.get("home_club_id") or ""),
            "away_club_id": str(row.get("away_club_id") or ""),
            "home_club_name": row.get("home_club_name") or "",
            "away_club_name": row.get("away_club_name") or "",
            "home_formation": row.get("home_club_formation") or None,
            "away_formation": row.get("away_club_formation") or None,
        }
        club_to_team[(game_id, games[game_id]["home_club_id"])] = games[game_id]["home_club_name"]
        club_to_team[(game_id, games[game_id]["away_club_id"])] = games[game_id]["away_club_name"]

    lineup_rows = _csv_gz(f"{TM_BASE}/game_lineups.csv.gz")
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in lineup_rows:
        game_id = str(row.get("game_id") or "")
        if game_id not in games:
            continue
        lineup_type = _norm(row.get("type"))
        if "start" not in lineup_type:
            continue
        club_id = str(row.get("club_id") or "")
        grouped[(game_id, club_id)].append(row)

    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (game_id, club_id), items in grouped.items():
        meta = games[game_id]
        unique_players: dict[str, str] = {}
        for item in items:
            player_id = str(item.get("player_id") or item.get("player_name") or "")
            if player_id:
                unique_players[player_id] = str(item.get("player_name") or player_id)
        if len(unique_players) != 11:
            continue
        team = club_to_team.get((game_id, club_id))
        if not team:
            continue
        formation = meta["home_formation"] if club_id == meta["home_club_id"] else meta["away_formation"]
        output[meta["competition_id"]].append({
            "competition_id": meta["competition_id"],
            "season": meta["season"],
            "kickoff_utc": _surrogate_kickoff(meta["date"]),
            "kickoff_time_quality": "date_only_surrogate",
            "team": team,
            "team_token": _norm(team),
            "starters": sorted(unique_players.values()),
            "starter_ids": sorted(unique_players.keys()),
            "formation": formation,
            "source_id": "transfermarkt_datasets_lineups",
            "source_group": "transfermarkt_datasets",
            "source_url": meta["url"] or "https://github.com/dcaribou/transfermarkt-datasets",
            "provider_record_id": game_id,
            "retrieved_at_utc": datetime.utcnow().isoformat() + "+00:00",
        })

    return {
        cid: _write_jsonl(OUT_ROOT / cid / "transfermarkt_datasets.jsonl", rows)
        for cid, rows in output.items()
    }


def _sb_starters(team_obj: dict[str, Any]) -> list[tuple[str, str]]:
    starters: list[tuple[str, str]] = []
    for player in team_obj.get("lineup") or []:
        positions = player.get("positions") or []
        started = any(str(position.get("from") or "").startswith("00:00") for position in positions)
        if not started:
            continue
        player_id = str(player.get("player_id") or player.get("player_name") or "")
        player_name = str(player.get("player_name") or player_id)
        if player_id:
            starters.append((player_id, player_name))
    return starters


def collect_statsbomb() -> dict[str, int]:
    competitions = json.loads(_http_bytes(f"{SB_BASE}/competitions.json").decode("utf-8"))
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for comp in competitions:
        if comp.get("competition_gender") != "male":
            continue
        target = _competition_id(comp.get("competition_name"), comp.get("country_name"))
        if not target:
            continue
        comp_id, season_id = comp.get("competition_id"), comp.get("season_id")
        try:
            matches = json.loads(_http_bytes(f"{SB_BASE}/matches/{comp_id}/{season_id}.json").decode("utf-8"))
        except Exception:
            continue
        for match in matches:
            match_id = match.get("match_id")
            match_date = str(match.get("match_date") or "")[:10]
            if not match_id or not match_date:
                continue
            try:
                lineups = json.loads(_http_bytes(f"{SB_BASE}/lineups/{match_id}.json").decode("utf-8"))
            except Exception:
                continue
            for team_obj in lineups:
                starters = _sb_starters(team_obj)
                if len(starters) != 11:
                    continue
                team = str(team_obj.get("team_name") or "")
                if not team:
                    continue
                output[target].append({
                    "competition_id": target,
                    "season": _canonical_season(target, match_date),
                    "kickoff_utc": _surrogate_kickoff(match_date),
                    "kickoff_time_quality": "date_only_surrogate",
                    "team": team,
                    "team_token": _norm(team),
                    "starters": sorted(name for _, name in starters),
                    "starter_ids": sorted(player_id for player_id, _ in starters),
                    "formation": None,
                    "source_id": "statsbomb_open_lineups",
                    "source_group": "statsbomb_open",
                    "source_url": f"https://github.com/statsbomb/open-data/blob/master/data/lineups/{match_id}.json",
                    "provider_record_id": str(match_id),
                    "retrieved_at_utc": datetime.utcnow().isoformat() + "+00:00",
                })
    return {
        cid: _write_jsonl(OUT_ROOT / cid / "statsbomb_open.jsonl", rows)
        for cid, rows in output.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("all", "transfermarkt", "statsbomb"), default="all")
    args = parser.parse_args()
    summary: dict[str, Any] = {}
    if args.source in {"all", "transfermarkt"}:
        summary["transfermarkt"] = collect_transfermarkt()
    if args.source in {"all", "statsbomb"}:
        summary["statsbomb"] = collect_statsbomb()
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
