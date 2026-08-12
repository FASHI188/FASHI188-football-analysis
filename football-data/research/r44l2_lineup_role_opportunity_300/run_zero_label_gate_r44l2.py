#!/usr/bin/env python3
"""R44L2 zero-label coverage gate.

This stage uses only fixture/team identity plus Transfermarkt formation and
match-specific lineup positions. It does not train, score, or parse outcome/xG
columns for modelling.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import sys
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

FPL_COMMIT = "8c97b2adb123863c3dd581e730f1360e89815ac2"
FPL_BASE = f"https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/{FPL_COMMIT}/data/2025-26"
TM_BASE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
OUT_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "r44l2_zero_label_gate_output")

URLS = {
    "fpl_fixtures": f"{FPL_BASE}/fixtures.csv",
    "fpl_teams": f"{FPL_BASE}/teams.csv",
    "tm_games": f"{TM_BASE}/games.csv.gz",
    "tm_game_lineups": f"{TM_BASE}/game_lineups.csv.gz",
}


def get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "r44l2-zero-label-gate/1.0"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        return resp.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decoded_csv_bytes(data: bytes, gzipped: bool = False) -> bytes:
    return gzip.decompress(data) if gzipped else data


def selected_columns(data: bytes, names: Iterable[str]) -> list[dict[str, str]]:
    """Parse only declared columns from CSV bytes; undeclared outcome fields are ignored."""
    text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", newline="")
    reader = csv.reader(text)
    header = next(reader)
    idx = {name: header.index(name) for name in names}
    out: list[dict[str, str]] = []
    for raw in reader:
        if not raw:
            continue
        out.append({name: raw[i] if i < len(raw) else "" for name, i in idx.items()})
    return out


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = value.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


ALIASES = {
    "arsenal": "arsenal", "arsenal fc": "arsenal",
    "aston villa": "aston villa", "aston villa fc": "aston villa",
    "bournemouth": "bournemouth", "afc bournemouth": "bournemouth",
    "brentford": "brentford", "brentford fc": "brentford",
    "brighton": "brighton", "brighton and hove albion": "brighton",
    "burnley": "burnley", "burnley fc": "burnley",
    "chelsea": "chelsea", "chelsea fc": "chelsea",
    "crystal palace": "crystal palace", "crystal palace fc": "crystal palace",
    "everton": "everton", "everton fc": "everton",
    "fulham": "fulham", "fulham fc": "fulham",
    "leeds": "leeds", "leeds united": "leeds", "leeds united fc": "leeds",
    "liverpool": "liverpool", "liverpool fc": "liverpool",
    "man city": "man city", "manchester city": "man city", "manchester city fc": "man city",
    "man utd": "man utd", "man united": "man utd", "manchester united": "man utd", "manchester united fc": "man utd",
    "newcastle": "newcastle", "newcastle united": "newcastle", "newcastle united fc": "newcastle",
    "nott m forest": "nottingham forest", "nottingham forest": "nottingham forest", "nottingham forest fc": "nottingham forest",
    "sunderland": "sunderland", "sunderland afc": "sunderland",
    "spurs": "tottenham",
    "tottenham": "tottenham", "tottenham hotspur": "tottenham", "tottenham hotspur fc": "tottenham",
    "west ham": "west ham", "west ham united": "west ham", "west ham united fc": "west ham",
    "wolves": "wolves", "wolverhampton wanderers": "wolves", "wolverhampton wanderers fc": "wolves",
}


def canon(value: str) -> str:
    token = norm(value)
    return ALIASES.get(token, token)


def date_from_iso(value: str) -> str:
    # FPL kickoff timestamps are ISO UTC; calendar date is sufficient for match-pair identity.
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    blobs = {name: get_bytes(url) for name, url in URLS.items()}
    hashes = {name: sha256(data) for name, data in blobs.items()}

    team_rows = selected_columns(blobs["fpl_teams"], ["id", "name"])
    team_name = {row["id"]: canon(row["name"]) for row in team_rows}

    fixture_rows = selected_columns(
        blobs["fpl_fixtures"], ["id", "kickoff_time", "team_h", "team_a"]
    )
    fixture_rows = [row for row in fixture_rows if row["id"] and row["kickoff_time"]]
    fixture_rows.sort(key=lambda r: (r["kickoff_time"], int(r["id"])))
    selected = fixture_rows[-300:]

    fpl_matches: dict[tuple[str, str, str], dict[str, str]] = {}
    fpl_duplicate_identity: list[dict[str, str]] = []
    for row in selected:
        key = (
            date_from_iso(row["kickoff_time"]),
            team_name.get(row["team_h"], f"UNKNOWN:{row['team_h']}"),
            team_name.get(row["team_a"], f"UNKNOWN:{row['team_a']}"),
        )
        if key in fpl_matches:
            fpl_duplicate_identity.append(row)
        fpl_matches[key] = row

    games_bytes = decoded_csv_bytes(blobs["tm_games"], gzipped=True)
    game_rows = selected_columns(
        games_bytes,
        [
            "game_id", "competition_id", "date", "home_club_id", "away_club_id",
            "home_club_name", "away_club_name", "home_club_formation", "away_club_formation",
        ],
    )

    selected_dates = {key[0] for key in fpl_matches}
    tm_by_identity: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in game_rows:
        if row["competition_id"] != "GB1" or row["date"] not in selected_dates:
            continue
        key = (row["date"], canon(row["home_club_name"]), canon(row["away_club_name"]))
        if key in fpl_matches:
            tm_by_identity[key].append(row)

    matched_games: dict[str, dict[str, str]] = {}
    missing_identity: list[dict[str, str]] = []
    ambiguous_identity: list[dict[str, object]] = []
    for key, fixture in fpl_matches.items():
        candidates = tm_by_identity.get(key, [])
        if len(candidates) == 1:
            matched_games[candidates[0]["game_id"]] = candidates[0] | {"fpl_fixture_id": fixture["id"]}
        elif len(candidates) == 0:
            missing_identity.append({"fixture_id": fixture["id"], "date": key[0], "home": key[1], "away": key[2]})
        else:
            ambiguous_identity.append({"fixture_id": fixture["id"], "identity": key, "game_ids": [x["game_id"] for x in candidates]})

    lineup_bytes = decoded_csv_bytes(blobs["tm_game_lineups"], gzipped=True)
    lineup_rows = selected_columns(
        lineup_bytes,
        ["game_id", "club_id", "type", "player_id", "player_name", "position"],
    )

    selected_game_ids = set(matched_games)
    starters: dict[tuple[str, str], dict[str, tuple[str, str]]] = defaultdict(dict)
    conflicting_duplicates: list[dict[str, str]] = []
    exact_duplicate_count = 0
    for row in lineup_rows:
        if row["game_id"] not in selected_game_ids or "start" not in norm(row["type"]):
            continue
        key = (row["game_id"], row["club_id"])
        player_id = row["player_id"] or norm(row["player_name"])
        payload = (row["player_name"], row["position"])
        if player_id in starters[key]:
            if starters[key][player_id] == payload:
                exact_duplicate_count += 1
            else:
                conflicting_duplicates.append({
                    "game_id": row["game_id"], "club_id": row["club_id"],
                    "player_id": player_id, "old": str(starters[key][player_id]), "new": str(payload),
                })
        else:
            starters[key][player_id] = payload

    position_counts: Counter[str] = Counter()
    formation_counts: Counter[str] = Counter()
    team_match_rows: list[dict[str, object]] = []
    bad_team_matches: list[dict[str, object]] = []

    for game_id, game in sorted(matched_games.items(), key=lambda item: (item[1]["date"], int(item[0]))):
        for side in ("home", "away"):
            club_id = game[f"{side}_club_id"]
            lineup = starters.get((game_id, club_id), {})
            positions = [payload[1].strip() for payload in lineup.values()]
            formation = (game[f"{side}_club_formation"] or "").strip()
            empty_positions = sum(1 for p in positions if not p)
            if formation:
                formation_counts[formation] += 1
            for p in positions:
                if p:
                    position_counts[p] += 1
            team_name_raw = game[f"{side}_club_name"]
            row = {
                "fpl_fixture_id": game["fpl_fixture_id"],
                "game_id": game_id,
                "date": game["date"],
                "side": side,
                "team": team_name_raw,
                "club_id": club_id,
                "formation": formation,
                "starter_count": len(lineup),
                "empty_position_count": empty_positions,
                "positions": sorted(positions),
            }
            team_match_rows.append(row)
            if len(lineup) != 11 or empty_positions != 0 or not formation:
                bad_team_matches.append(row)

    duplicate_tm_game_use = len(matched_games) != len(set(matched_games))
    gate_checks = {
        "selected_fpl_fixtures_eq_300": len(selected) == 300 and len(fpl_matches) == 300,
        "fpl_identity_unique": not fpl_duplicate_identity,
        "tm_identity_matched_300": len(matched_games) == 300,
        "tm_identity_no_missing": not missing_identity,
        "tm_identity_no_ambiguous": not ambiguous_identity,
        "tm_game_not_reused": not duplicate_tm_game_use,
        "team_match_rows_eq_600": len(team_match_rows) == 600,
        "all_team_matches_11_unique_starters": all(row["starter_count"] == 11 for row in team_match_rows) and len(team_match_rows) == 600,
        "all_starter_positions_nonempty": all(row["empty_position_count"] == 0 for row in team_match_rows) and len(team_match_rows) == 600,
        "all_formations_nonempty": all(bool(row["formation"]) for row in team_match_rows) and len(team_match_rows) == 600,
        "no_conflicting_starter_duplicates": not conflicting_duplicates,
    }
    passed = all(gate_checks.values())
    terminal = "PASS_R44L2_ZERO_LABEL_GATE" if passed else "STOP_R44L2_LINEUP_ROLE_COVERAGE_INCOMPLETE"

    result = {
        "study_id": "r44l2_lineup_role_opportunity_300",
        "stage": "zero_label_gate",
        "formal_weight": 0,
        "pit_eligible": False,
        "terminal_status": terminal,
        "source_pins": {
            "fpl_commit": FPL_COMMIT,
            "transfermarkt_schema_commit": "154367dfa6d6eb0b86332e332f9df0a080c7ddce",
        },
        "source_sha256": hashes,
        "sample": {
            "selected_fpl_rows": len(selected),
            "unique_fpl_identity": len(fpl_matches),
            "first_kickoff": selected[0]["kickoff_time"] if selected else None,
            "last_kickoff": selected[-1]["kickoff_time"] if selected else None,
            "matched_tm_games": len(matched_games),
            "team_match_rows": len(team_match_rows),
        },
        "gate_checks": gate_checks,
        "diagnostics": {
            "missing_identity": missing_identity,
            "ambiguous_identity": ambiguous_identity,
            "fpl_duplicate_identity_count": len(fpl_duplicate_identity),
            "exact_duplicate_starter_rows_ignored": exact_duplicate_count,
            "conflicting_duplicate_starter_rows": conflicting_duplicates,
            "bad_team_matches": bad_team_matches,
        },
        "formation_counts": dict(sorted(formation_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "position_counts": dict(sorted(position_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    }

    (OUT_DIR / "zero_label_gate_r44l2.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (OUT_DIR / "team_match_schema_rows.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in team_match_rows),
        encoding="utf-8",
    )
    md = [
        "# R44L2 Zero-label Gate Result",
        "",
        f"- terminal_status: `{terminal}`",
        f"- selected fixtures: {len(selected)}",
        f"- matched Transfermarkt games: {len(matched_games)}/300",
        f"- team-match rows: {len(team_match_rows)}/600",
        f"- bad team-match rows: {len(bad_team_matches)}",
        f"- exact duplicate starter rows ignored: {exact_duplicate_count}",
        f"- conflicting duplicate starter rows: {len(conflicting_duplicates)}",
        "",
        "## Gate checks",
    ]
    md.extend(f"- {name}: {'PASS' if ok else 'FAIL'}" for name, ok in gate_checks.items())
    md.extend(["", "## Formation counts"])
    md.extend(f"- {name}: {count}" for name, count in result["formation_counts"].items())
    md.extend(["", "## Position counts"])
    md.extend(f"- {name}: {count}" for name, count in result["position_counts"].items())
    md.extend(["", "## Source SHA-256"])
    md.extend(f"- {name}: `{digest}`" for name, digest in hashes.items())
    (OUT_DIR / "zero_label_gate_r44l2.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())