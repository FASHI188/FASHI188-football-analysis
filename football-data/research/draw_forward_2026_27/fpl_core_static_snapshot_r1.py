#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FILES = ("players.csv", "teams.csv", "playerstats.csv", "fixtures.csv")
KEEP_PLAYERSTATS = (
    "id", "status", "chance_of_playing_next_round", "chance_of_playing_this_round",
    "news", "news_added", "now_cost", "selected_by_percent", "ep_next", "ep_this",
    "minutes", "starts", "expected_goals", "expected_assists",
    "expected_goals_conceded", "saves", "clean_sheets", "goals_conceded",
    "tackles", "clearances_blocks_interceptions", "recoveries",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "draw-forward-static-snapshot-r1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def write_csv(path: Path, fieldnames: list[str], values: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-commit-time", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    base = f"https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/{args.source_commit}/data/2026-2027"
    payloads: dict[str, bytes] = {}
    ledger = []
    for name in FILES:
        url = f"{base}/{name}"
        data = fetch(url)
        payloads[name] = data
        ledger.append({"file": name, "url": url, "sha256": sha256(data), "bytes": len(data)})

    players = {r["player_id"]: r for r in rows(payloads["players.csv"])}
    teams = {r.get("team_code", r.get("id", "")): r for r in rows(payloads["teams.csv"])}
    stats = rows(payloads["playerstats.csv"])
    fixtures = rows(payloads["fixtures.csv"])

    snapshot_rows = []
    for stat in stats:
        player = players.get(stat.get("id", ""), {})
        team = teams.get(player.get("team_code", ""), {})
        out = {key: stat.get(key, "") for key in KEEP_PLAYERSTATS}
        out.update({
            "player_code": player.get("player_code", ""),
            "first_name": player.get("first_name", stat.get("first_name", "")),
            "second_name": player.get("second_name", stat.get("second_name", "")),
            "web_name": player.get("web_name", stat.get("web_name", "")),
            "position": player.get("position", ""),
            "team_code": player.get("team_code", ""),
            "team_name": team.get("name", team.get("team_name", "")),
        })
        snapshot_rows.append(out)

    player_fields = [
        "id", "player_code", "first_name", "second_name", "web_name", "position",
        "team_code", "team_name", *[x for x in KEEP_PLAYERSTATS if x != "id"],
    ]
    write_csv(args.out / "FPL_CORE_2026_27_player_availability_snapshot.csv", player_fields, snapshot_rows)
    fixture_fields = list(fixtures[0]) if fixtures else []
    write_csv(args.out / "FPL_CORE_2026_27_fixture_snapshot.csv", fixture_fields, fixtures)
    write_csv(args.out / "FPL_CORE_2026_27_source_ledger.csv", ["file", "url", "sha256", "bytes"], ledger)

    non_available = [r for r in snapshot_rows if r.get("status") not in ("", "a")]
    timestamped_news = [r for r in snapshot_rows if r.get("news_added")]
    chance_rows = [r for r in snapshot_rows if r.get("chance_of_playing_this_round") or r.get("chance_of_playing_next_round")]
    finished_fixtures = [r for r in fixtures if str(r.get("finished", "")).lower() == "true"]
    receipt = {
        "schema_version": "DRAW-FORWARD-FPL-CORE-SNAPSHOT-R1",
        "status": "SOURCE_SNAPSHOT_ONLY_NO_MODEL_SELECTION",
        "source_repository": "olbauday/FPL-Core-Insights",
        "source_commit": args.source_commit,
        "source_commit_time": args.source_commit_time,
        "source_access": "public static GitHub files only; no API credentials",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "season": "2026/27",
        "players": len(snapshot_rows),
        "non_available_players": len(non_available),
        "players_with_news_added": len(timestamped_news),
        "players_with_chance_fields": len(chance_rows),
        "fixtures": len(fixtures),
        "finished_fixtures": len(finished_fixtures),
        "untouched_forward_batch_ready": False,
        "formal_weight": 0,
        "formal_model_data_config_current_writes": [0, 0, 0, 0],
        "consumed_test_sets_reused": [],
        "caveats": [
            "repository has no explicit LICENSE file located during source audit",
            "README permits use but redistribution rights are not formalized; artifact stores research snapshot only",
            "master files are mutable; exact source commit and content hashes are mandatory",
            "news_added and chance fields may be blank before official availability updates",
        ],
    }
    (args.out / "snapshot_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": "DRAW-FORWARD-FPL-CORE-ARTIFACT-R1", "files": {}}
    for path in sorted(args.out.iterdir()):
        if path.is_file() and path.name != "artifact_manifest.json":
            data = path.read_bytes()
            manifest["files"][path.name] = {"sha256": sha256(data), "bytes": len(data)}
    (args.out / "artifact_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
