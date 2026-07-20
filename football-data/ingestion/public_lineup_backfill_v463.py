#!/usr/bin/env python3
"""Backfill historical starting-XI evidence from public independent datasets.

Sources:
  1) dcaribou/transfermarkt-datasets (CC0 curated dataset; Transfermarkt-derived)
  2) StatsBomb Open Data (research/open-data subset)

Large Transfermarkt CSVs are streamed from gzip instead of materialized in
memory. Output is normalized JSONL under football-data/evidence/lineups/<competition>/.
This collector never promotes evidence by itself; the validator decides whether
records are independent and consistent enough for verified historical labels.
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
from contextlib import contextmanager
from dateutil.parser import isoparse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "evidence" / "lineups"

TM_BASE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
SB_BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
USER_AGENT = "football-evidence-v475/1.0"

CROSS_YEAR = {
    "ENG_PremierLeague", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1",
    "ESP_LaLiga", "POR_PrimeiraLiga", "NED_Eredivisie", "SUI_SuperLeague",
    "SCO_Premiership", "UEFA_ChampionsLeague",
}


def _request(url: str, timeout: int = 180):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
        timeout=timeout,
    )


def _http_bytes(url: str, timeout: int = 180) -> bytes:
    with _request(url, timeout=timeout) as response:
        return response.read()


def _iter_csv_gz(url: str) -> Iterator[dict[str, str]]:
    """Stream a remote gzip CSV row-by-row without building a full Python list."""
    with _request(url) as response:
        with gzip.GzipFile(fileobj=response) as gz:
            text = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text)
            for row in reader:
                yield row


def _norm(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _competition_id(name: Any, country: Any) -> str | None:
    n, c = _norm(name), _norm(country)
    rules = [
        ("ENG_PremierLeague", c in {"england", "great britain"} and "premier league" in n),
        ("GER_Bundesliga", c == "germany" and n in {"1 bundesliga", "bundesliga"}),
        ("ITA_SerieA", c == "italy" and "serie a" in n),
        ("FRA_Ligue1", c == "france" and "ligue 1" in n),
        ("ESP_LaLiga", c == "spain" and any(x in n for x in ("la liga", "laliga", "primera division"))),
        ("POR_PrimeiraLiga", c == "portugal" and any(x in n for x in ("primeira liga", "liga portugal"))),
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
    # Public Transfermarkt prepared table exposes date but not exact kickoff time.
    # Noon UTC is only a deterministic matching surrogate. It is not PIT evidence.
    return f"{match_date[:10]}T12:00:00+00:00"


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    materialized.sort(key=lambda r: (str(r.get("kickoff_utc")), str(r.get("team")), str(r.get("source_group"))))
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in materialized),
        encoding="utf-8",
    )
    return len(materialized)


def collect_transfermarkt() -> dict[str, int]:
    comp_map: dict[str, str] = {}
    for row in _iter_csv_gz(f"{TM_BASE}/competitions.csv.gz"):
        target = _competition_id(row.get("name"), row.get("country_name"))
        if target:
            comp_map[str(row.get("competition_id"))] = target

    games: dict[str, dict[str, Any]] = {}
    club_to_team: dict[tuple[str, str], str] = {}
    for row in _iter_csv_gz(f"{TM_BASE}/games.csv.gz"):
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

    # Only selected competitions' starting-XI rows are retained. This avoids
    # materializing the multi-million-row prepared lineup table.
    grouped: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for row in _iter_csv_gz(f"{TM_BASE}/game_lineups.csv.gz"):
        game_id = str(row.get("game_id") or "")
        if game_id not in games or "start" not in _norm(row.get("type")):
            continue
        club_id = str(row.get("club_id") or "")
        player_id = str(row.get("player_id") or row.get("player_name") or "")
        if player_id:
            grouped[(game_id, club_id)][player_id] = str(row.get("player_name") or player_id)

    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    retrieved = datetime.now(timezone.utc).isoformat()
    for (game_id, club_id), unique_players in grouped.items():
        if len(unique_players) != 11:
            continue
        meta = games[game_id]
        team = club_to_team.get((game_id, club_id))
        if not team:
            continue
        formation = meta["home_formation"] if club_id == meta["home_club_id"] else meta["away_formation"]
        output[meta["competition_id"]].append({
            "competition_id": meta["competition_id"],
            "season": meta["season"],
            "kickoff_utc": _surrogate_kickoff(meta["date"]),
            "kickoff_time_quality": "date_only_surrogate_not_pit",
            "team": team,
            "team_token": _norm(team),
            "starters": sorted(unique_players.values()),
            "starter_ids": sorted(unique_players.keys()),
            "formation": formation,
            "source_id": "transfermarkt_datasets_lineups",
            "source_group": "transfermarkt_datasets",
            "source_url": meta["url"] or "https://github.com/dcaribou/transfermarkt-datasets",
            "provider_record_id": game_id,
            "source_observed_at_utc": None,
            "pit_eligible": False,
            "retrieved_at_utc": retrieved,
        })

    return {
        cid: _write_jsonl(OUT_ROOT / cid / "transfermarkt_datasets.jsonl", rows)
        for cid, rows in output.items()
    }


def _sb_starters(team_obj: dict[str, Any]) -> list[tuple[str, str]]:
    starters: list[tuple[str, str]] = []
    for player in team_obj.get("lineup") or []:
        positions = player.get("positions") or []
        if not any(str(position.get("from") or "").startswith("00:00") for position in positions):
            continue
        player_id = str(player.get("player_id") or player.get("player_name") or "")
        player_name = str(player.get("player_name") or player_id)
        if player_id:
            starters.append((player_id, player_name))
    return starters


def collect_statsbomb() -> dict[str, int]:
    competitions = json.loads(_http_bytes(f"{SB_BASE}/competitions.json").decode("utf-8"))
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    retrieved = datetime.now(timezone.utc).isoformat()
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
                    "kickoff_time_quality": "date_only_surrogate_not_pit",
                    "team": team,
                    "team_token": _norm(team),
                    "starters": sorted(name for _, name in starters),
                    "starter_ids": sorted(player_id for player_id, _ in starters),
                    "formation": None,
                    "source_id": "statsbomb_open_lineups",
                    "source_group": "statsbomb_open",
                    "source_url": f"https://github.com/statsbomb/open-data/blob/master/data/lineups/{match_id}.json",
                    "provider_record_id": str(match_id),
                    "source_observed_at_utc": None,
                    "pit_eligible": False,
                    "retrieved_at_utc": retrieved,
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
