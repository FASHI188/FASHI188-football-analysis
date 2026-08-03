#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import urllib.request
from collections import Counter
from pathlib import Path

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
COMP_MAP = {
    "1. Bundesliga": "GER_Bundesliga",
    "Premier League": "ENG_PremierLeague",
    "La Liga": "ESP_LaLiga",
    "Serie A": "ITA_SerieA",
    "Ligue 1": "FRA_Ligue1",
    "Eredivisie": "NED_Eredivisie",
    "Primeira Liga": "POR_PrimeiraLiga",
    "Scottish Premiership": "SCO_Premiership",
    "Major League Soccer": "USA_MLS",
    "Champions League": "UEFA_ChampionsLeague",
}
ALIASES = {
    "bayer04leverkusen": "bayerleverkusen",
    "bayerleverkusen": "bayerleverkusen",
    "1fckoln": "fckoln",
    "fckoln": "fckoln",
    "vflbochum": "bochum",
    "bochum": "bochum",
    "tsghoffenheim": "hoffenheim",
    "hoffenheim": "hoffenheim",
    "1fsvmainz05": "fsvmainz05",
    "mainz05": "fsvmainz05",
    "vflwolfsburg": "wolfsburg",
    "wolfsburg": "wolfsburg",
    "svdarmstadt98": "darmstadt98",
    "darmstadt98": "darmstadt98",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm(value: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", str(value).lower().replace("&", "and"))
    return ALIASES.get(key, key)


def season_norm(value: str) -> str:
    value = str(value).strip()
    m = re.fullmatch(r"(\d{4})/(\d{4}|\d{2})", value)
    if m:
        return f"{m.group(1)}/{m.group(2)[-2:]}"
    return value


def get_json(url: str, ledger: list[dict]) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "GOLD1000 lineup research audit"})
    with urllib.request.urlopen(req, timeout=90) as response:
        data = response.read()
    ledger.append({"url": url, "sha256": sha256_bytes(data), "bytes": len(data)})
    return json.loads(data.decode("utf-8"))


def group_position(name: str) -> str:
    n = name.lower()
    if "goalkeeper" in n:
        return "GK"
    if "center back" in n or "centre back" in n:
        return "CB"
    if "defensive midfield" in n:
        return "DM"
    if "center forward" in n or "centre forward" in n or "striker" in n:
        return "ST"
    if "back" in n:
        return "FB"
    if "midfield" in n:
        return "MF"
    if "wing" in n:
        return "WG"
    return "OT"


def starting_xi(events: list[dict]) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for event in events:
        if event.get("type", {}).get("name") != "Starting XI":
            continue
        team = event.get("team", {})
        lineup = event.get("tactics", {}).get("lineup", [])
        players = []
        for row in lineup:
            player = row.get("player", {})
            pos = row.get("position", {}).get("name", "")
            players.append({
                "player_id": player.get("id"),
                "player_name": player.get("name"),
                "position": pos,
                "group": group_position(pos),
            })
        result[int(team["id"])] = {
            "team_name": team.get("name"),
            "formation": event.get("tactics", {}).get("formation"),
            "players": players,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with args.sample.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = list(csv.DictReader(handle))
    sample_index = {
        (r["competition_id"], season_norm(r["season"]), r["date"], norm(r["home_team"]), norm(r["away_team"])): r
        for r in sample
    }
    wanted_comp_seasons = {(r["competition_id"], season_norm(r["season"])) for r in sample}

    ledger: list[dict] = []
    competitions = get_json(f"{BASE}/competitions.json", ledger)
    mapped = []
    for row in competitions:
        if row.get("competition_gender") != "male" or row.get("competition_youth"):
            continue
        project_comp = COMP_MAP.get(row.get("competition_name"))
        project_season = season_norm(row.get("season_name"))
        if project_comp and (project_comp, project_season) in wanted_comp_seasons:
            mapped.append((row, project_comp, project_season))

    available_matches: list[dict] = []
    for comp, project_comp, project_season in mapped:
        url = f"{BASE}/matches/{comp['competition_id']}/{comp['season_id']}.json"
        for match in get_json(url, ledger):
            available_matches.append({
                "project_competition": project_comp,
                "project_season": project_season,
                "match_id": int(match["match_id"]),
                "date": match["match_date"],
                "home_team_id": int(match["home_team"]["home_team_id"]),
                "home_team": match["home_team"]["home_team_name"],
                "away_team_id": int(match["away_team"]["away_team_id"]),
                "away_team": match["away_team"]["away_team_name"],
                "home_score": match.get("home_score"),
                "away_score": match.get("away_score"),
                "match_week": match.get("match_week"),
                "match_available": comp.get("match_available"),
            })

    available_matches.sort(key=lambda r: (r["date"], r["match_id"]))
    team_previous: dict[int, set[int]] = {}
    features_all = []
    overlap = []
    starter_rows = []

    for match in available_matches:
        events = get_json(f"{BASE}/events/{match['match_id']}.json", ledger)
        xis = starting_xi(events)
        if match["home_team_id"] not in xis or match["away_team_id"] not in xis:
            continue

        row = dict(match)
        for side in ("home", "away"):
            team_id = match[f"{side}_team_id"]
            xi = xis[team_id]
            ids = {int(p["player_id"]) for p in xi["players"] if p.get("player_id") is not None}
            prev = team_previous.get(team_id)
            row[f"{side}_formation"] = xi.get("formation")
            row[f"{side}_starter_count"] = len(ids)
            row[f"{side}_starter_continuity"] = "" if prev is None else len(ids & prev) / max(1, len(ids))
            row[f"{side}_starter_changes"] = "" if prev is None else len(ids - prev)
            for group in ("GK", "CB", "DM", "ST"):
                group_ids = {int(p["player_id"]) for p in xi["players"] if p.get("player_id") is not None and p["group"] == group}
                row[f"{side}_{group.lower()}_count"] = len(group_ids)
                row[f"{side}_{group.lower()}_changed"] = "" if prev is None else int(any(pid not in prev for pid in group_ids))
            for player in xi["players"]:
                starter_rows.append({
                    "match_id": match["match_id"],
                    "date": match["date"],
                    "side": side,
                    "team_id": team_id,
                    "team_name": xi["team_name"],
                    "formation": xi.get("formation"),
                    **player,
                })
            team_previous[team_id] = ids

        key = (
            match["project_competition"], match["project_season"], match["date"],
            norm(match["home_team"]), norm(match["away_team"]),
        )
        sample_row = sample_index.get(key)
        row["gold_sample_id"] = sample_row.get("gold_sample_id", "") if sample_row else ""
        row["match_identity"] = sample_row.get("match_identity", "") if sample_row else ""
        row["label_result"] = sample_row.get("label_result", "") if sample_row else ""
        row["gold1000_overlap"] = int(sample_row is not None)
        features_all.append(row)
        if sample_row:
            overlap.append(row)

    all_fields = sorted({k for r in features_all for k in r})
    starter_fields = ["match_id", "date", "side", "team_id", "team_name", "formation", "player_id", "player_name", "position", "group"]

    def write(path: Path, fields: list[str], rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    write(args.out / "STATSBOMB_OPEN_R1_available_lineup_features.csv", all_fields, features_all)
    write(args.out / "STATSBOMB_OPEN_R1_gold1000_overlap.csv", all_fields, overlap)
    write(args.out / "STATSBOMB_OPEN_R1_starters.csv", starter_fields, starter_rows)
    write(args.out / "STATSBOMB_OPEN_R1_source_ledger.csv", ["url", "sha256", "bytes"], ledger)

    receipt = {
        "schema_version": "STATSBOMB-OPEN-LINEUP-AUDIT-R1",
        "source": "StatsBomb Open Data",
        "source_base": BASE,
        "source_access": "static GitHub raw files; no StatsBomb API credentials",
        "freeze_scope": "lineup-confirmed / approximately T-60m only",
        "not_valid_for": ["T-24h lineup prediction", "historical injury announcement timing"],
        "gold1000_rows": len(sample),
        "mapped_competition_seasons": len(mapped),
        "open_matches_in_mapped_seasons": len(available_matches),
        "lineup_feature_rows": len(features_all),
        "gold1000_overlap_rows": len(overlap),
        "starter_rows": len(starter_rows),
        "overlap_by_competition": dict(sorted(Counter(r["project_competition"] for r in overlap).items())),
        "overlap_by_season": dict(sorted(Counter(r["project_season"] for r in overlap).items())),
        "source_downloads": len(ledger),
    }
    (args.out / "STATSBOMB_OPEN_R1_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {"schema_version": "STATSBOMB-OPEN-LINEUP-ARTIFACT-R1", "files": {}}
    for path in sorted(args.out.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            data = path.read_bytes()
            manifest["files"][path.name] = {"sha256": sha256_bytes(data), "bytes": len(data)}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
