#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

SEASONS = {
    "2023/24": "2023-24",
    "2024/25": "2024-25",
    "2025/26": "2025-26",
}
BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
ALIASES = {
    "manchesterunited": "manutd",
    "manunited": "manutd",
    "manutd": "manutd",
    "manchestercity": "mancity",
    "mancity": "mancity",
    "nottinghamforest": "nottmforest",
    "nottmforest": "nottmforest",
    "tottenhamhotspur": "spurs",
    "tottenham": "spurs",
    "spurs": "spurs",
    "wolverhamptonwanderers": "wolves",
    "wolverhampton": "wolves",
    "wolves": "wolves",
    "newcastleunited": "newcastle",
    "newcastle": "newcastle",
    "westhamunited": "westham",
    "westham": "westham",
    "brightonandhovealbion": "brighton",
    "brighton": "brighton",
    "sheffieldunited": "sheffieldutd",
    "sheffieldutd": "sheffieldutd",
    "lutontown": "luton",
    "luton": "luton",
    "leicestercity": "leicester",
    "leicester": "leicester",
    "ipswichtown": "ipswich",
    "ipswich": "ipswich",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm(value: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", str(value).lower().replace("&", "and").replace("'", ""))
    return ALIASES.get(key, key)


def truth(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def number(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def read_url(url: str, ledger: list[dict]) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "GOLD1000 static FPL lineup research"})
    with urllib.request.urlopen(req, timeout=120) as response:
        data = response.read()
    ledger.append({"url": url, "sha256": sha256_bytes(data), "bytes": len(data)})
    text = data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def load_seen(paths: list[Path]) -> set[str]:
    seen = set()
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("competition_id") == "ENG_PremierLeague":
                    seen.add(row["match_identity"])
    return seen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--random", type=Path, required=True)
    parser.add_argument("--reserve", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with args.random.open("r", encoding="utf-8-sig", newline="") as handle:
        random_rows = [r for r in csv.DictReader(handle) if r["competition_id"] == "ENG_PremierLeague"]
    random_index = {
        (r["season"], r["date"], norm(r["home_team"]), norm(r["away_team"])): r
        for r in random_rows
    }
    seen_pool = load_seen([args.random, args.reserve])

    ledger: list[dict] = []
    raw_rows = []
    for project_season, folder in SEASONS.items():
        url = f"{BASE}/{folder}/gws/merged_gw.csv"
        for row in read_url(url, ledger):
            row = dict(row)
            row["project_season"] = project_season
            raw_rows.append(row)

    fixture_groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in raw_rows:
        fixture_groups[(row["project_season"], number(row["fixture"]))].append(row)

    fixtures = []
    starters_detail = []
    for (season, fixture_id), rows in fixture_groups.items():
        home_rows = [r for r in rows if truth(r["was_home"])]
        away_rows = [r for r in rows if not truth(r["was_home"])]
        if not home_rows or not away_rows:
            continue
        kickoff = rows[0]["kickoff_time"]
        date = datetime.fromisoformat(kickoff.replace("Z", "+00:00")).date().isoformat()
        home_team = home_rows[0]["team"]
        away_team = away_rows[0]["team"]
        home_starters = [r for r in home_rows if number(r.get("starts")) > 0]
        away_starters = [r for r in away_rows if number(r.get("starts")) > 0]
        home_score = number(rows[0].get("team_h_score"))
        away_score = number(rows[0].get("team_a_score"))
        result = "H" if home_score > away_score else "A" if away_score > home_score else "D"
        fixture = {
            "competition_id": "ENG_PremierLeague",
            "season": season,
            "fixture_id": fixture_id,
            "date": date,
            "kickoff_time": kickoff,
            "home_team": home_team,
            "away_team": away_team,
            "home_score": home_score,
            "away_score": away_score,
            "label_result": result,
            "home_starter_count": len(home_starters),
            "away_starter_count": len(away_starters),
        }
        fixture["match_identity"] = f"ENG_PremierLeague|{season}|{date}|{home_team}|{away_team}"
        for side, starter_rows in (("home", home_starters), ("away", away_starters)):
            for row in starter_rows:
                starters_detail.append({
                    "fixture_id": fixture_id,
                    "season": season,
                    "date": date,
                    "side": side,
                    "team": row["team"],
                    "element": number(row["element"]),
                    "name": row["name"],
                    "position": row["position"],
                    "minutes": number(row["minutes"]),
                    "starts": number(row["starts"]),
                })
            fixture[f"{side}_starter_ids"] = ";".join(str(number(r["element"])) for r in sorted(starter_rows, key=lambda x: number(x["element"])))
            fixture[f"{side}_gk_ids"] = ";".join(str(number(r["element"])) for r in starter_rows if r["position"] == "GK")
            for position in ("GK", "DEF", "MID", "FWD"):
                fixture[f"{side}_{position.lower()}_count"] = sum(r["position"] == position for r in starter_rows)
        fixtures.append(fixture)

    fixtures.sort(key=lambda r: (r["date"], r["fixture_id"]))
    previous: dict[str, dict[str, set[int]]] = {}
    enriched = []
    for row in fixtures:
        out = dict(row)
        for side in ("home", "away"):
            team = row[f"{side}_team"]
            current = {int(x) for x in row[f"{side}_starter_ids"].split(";") if x}
            current_groups = {}
            for pos in ("GK", "DEF", "MID", "FWD"):
                players = {
                    int(r["element"]) for r in starters_detail
                    if r["fixture_id"] == row["fixture_id"] and r["season"] == row["season"] and r["side"] == side and r["position"] == pos
                }
                current_groups[pos] = players
            prev = previous.get(team)
            out[f"{side}_starter_continuity"] = "" if prev is None else len(current & prev["ALL"]) / max(1, len(current))
            out[f"{side}_starter_changes"] = "" if prev is None else len(current - prev["ALL"])
            for pos in ("GK", "DEF", "MID", "FWD"):
                out[f"{side}_{pos.lower()}_changes"] = "" if prev is None else len(current_groups[pos] - prev[pos])
                out[f"{side}_{pos.lower()}_changed"] = "" if prev is None else int(current_groups[pos] != prev[pos])
            previous[team] = {"ALL": current, **current_groups}

        random_hit = random_index.get((row["season"], row["date"], norm(row["home_team"]), norm(row["away_team"])))
        out["gold1000_random_id"] = random_hit.get("gold_sample_id", "") if random_hit else ""
        out["in_gold1000_random"] = int(random_hit is not None)
        normalized_identity = f"ENG_PremierLeague|{row['season']}|{row['date']}|{row['home_team']}|{row['away_team']}"
        out["in_gold1000_or_reserve_pool"] = int(any(
            item.split("|")[:3] == normalized_identity.split("|")[:3]
            and norm(item.split("|")[3]) == norm(row["home_team"])
            and norm(item.split("|")[4]) == norm(row["away_team"])
            for item in seen_pool
        ))
        out["untouched_2025_26_test_eligible"] = int(row["season"] == "2025/26" and not out["in_gold1000_or_reserve_pool"])
        enriched.append(out)

    fixture_fields = sorted({k for row in enriched for k in row})
    detail_fields = ["fixture_id", "season", "date", "side", "team", "element", "name", "position", "minutes", "starts"]

    def write(path: Path, fields: list[str], rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader(); writer.writerows(rows)

    write(args.out / "FPL_STATIC_LINEUP_R1_matches.csv", fixture_fields, enriched)
    write(args.out / "FPL_STATIC_LINEUP_R1_starters.csv", detail_fields, starters_detail)
    write(args.out / "FPL_STATIC_LINEUP_R1_gold1000_overlap.csv", fixture_fields, [r for r in enriched if r["in_gold1000_random"]])
    write(args.out / "FPL_STATIC_LINEUP_R1_source_ledger.csv", ["url", "sha256", "bytes"], ledger)

    receipt = {
        "schema_version": "FPL-STATIC-LINEUP-DATASET-R1",
        "source": "vaastav/Fantasy-Premier-League static GitHub history",
        "source_access": "static merged_gw.csv downloads; no FPL API call",
        "freeze_scope": "actual starting XI; valid only for lineup-confirmed research track",
        "seasons": list(SEASONS),
        "matches": len(enriched),
        "matches_by_season": dict(sorted(Counter(r["season"] for r in enriched).items())),
        "gold1000_random_overlap": sum(r["in_gold1000_random"] for r in enriched),
        "gold1000_or_reserve_pool": sum(r["in_gold1000_or_reserve_pool"] for r in enriched),
        "untouched_2025_26_test_eligible": sum(r["untouched_2025_26_test_eligible"] for r in enriched),
        "bad_home_starter_count": sum(r["home_starter_count"] != 11 for r in enriched),
        "bad_away_starter_count": sum(r["away_starter_count"] != 11 for r in enriched),
        "source_downloads": len(ledger),
    }
    (args.out / "FPL_STATIC_LINEUP_R1_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": "FPL-STATIC-LINEUP-ARTIFACT-R1", "files": {}}
    for path in sorted(args.out.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            data = path.read_bytes()
            manifest["files"][path.name] = {"sha256": sha256_bytes(data), "bytes": len(data)}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
