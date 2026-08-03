#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = {
    "current_players": "data/2026-2027/players.csv",
    "current_teams": "data/2026-2027/teams.csv",
    "current_playerstats": "data/2026-2027/playerstats.csv",
    "prior_players": "data/2025-2026/players.csv",
    "prior_playerstats": "data/2025-2026/By Gameweek/GW38/playerstats.csv",
}
OPTIONAL = {
    "current_fixtures_gw1": "data/2026-2027/By Gameweek/GW1/fixtures.csv",
    "current_gameweek_summaries": "data/2026-2027/gameweek_summaries.csv",
}
CURRENT_FIELDS = (
    "id", "status", "chance_of_playing_next_round", "chance_of_playing_this_round",
    "news", "news_added", "now_cost", "selected_by_percent", "ep_next", "ep_this",
    "minutes", "starts", "expected_goals", "expected_assists",
    "expected_goal_involvements", "expected_goals_conceded", "saves", "clean_sheets",
    "goals_conceded", "total_points", "points_per_game", "bps", "form",
    "tackles", "clearances_blocks_interceptions", "recoveries",
)
PRIOR_FIELDS = (
    "minutes", "starts", "total_points", "points_per_game", "bps",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "saves", "clean_sheets", "goals_conceded",
    "defensive_contribution", "tackles", "clearances_blocks_interceptions", "recoveries",
)
UNAVAILABLE = {"i", "s", "u", "n"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, optional: bool = False) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "draw-forward-static-snapshot-r2"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if optional and exc.code == 404:
            return None
        raise


def rows(data: bytes | None) -> list[dict[str, str]]:
    if data is None:
        return []
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def write_csv(path: Path, fieldnames: list[str], values: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-commit-time", default="")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    repo = "olbauday/FPL-Core-Insights"
    base = f"https://raw.githubusercontent.com/{repo}/{args.source_commit}"
    payloads: dict[str, bytes | None] = {}
    ledger: list[dict[str, object]] = []
    for key, rel in REQUIRED.items():
        url = f"{base}/{rel}"
        data = fetch(url, optional=False)
        payloads[key] = data
        ledger.append({"key": key, "required": 1, "status": "PRESENT", "url": url, "sha256": sha256(data or b""), "bytes": len(data or b"")})
    for key, rel in OPTIONAL.items():
        url = f"{base}/{rel}"
        data = fetch(url, optional=True)
        payloads[key] = data
        ledger.append({
            "key": key,
            "required": 0,
            "status": "PRESENT" if data is not None else "ABSENT_404",
            "url": url,
            "sha256": sha256(data) if data is not None else "",
            "bytes": len(data) if data is not None else 0,
        })

    current_players = {r["player_id"]: r for r in rows(payloads["current_players"])}
    current_teams_rows = rows(payloads["current_teams"])
    current_teams = {r.get("code", ""): r for r in current_teams_rows}
    current_stats = rows(payloads["current_playerstats"])
    prior_players_by_id = {r["player_id"]: r for r in rows(payloads["prior_players"])}
    prior_stats_by_id = {r["id"]: r for r in rows(payloads["prior_playerstats"])}
    prior_by_code: dict[str, dict[str, str]] = {}
    for prior_id, player in prior_players_by_id.items():
        code = player.get("player_code", "")
        if code and prior_id in prior_stats_by_id:
            prior_by_code[code] = prior_stats_by_id[prior_id]

    snapshot_rows: list[dict[str, object]] = []
    for stat in current_stats:
        player = current_players.get(stat.get("id", ""), {})
        team = current_teams.get(player.get("team_code", ""), {})
        prior = prior_by_code.get(player.get("player_code", ""), {})
        out: dict[str, object] = {key: stat.get(key, "") for key in CURRENT_FIELDS}
        out.update({
            "player_code": player.get("player_code", ""),
            "first_name": player.get("first_name", stat.get("first_name", "")),
            "second_name": player.get("second_name", stat.get("second_name", "")),
            "web_name": player.get("web_name", stat.get("web_name", "")),
            "position": player.get("position", ""),
            "team_code": player.get("team_code", ""),
            "team_name": team.get("name", ""),
            "prior_season_matched": int(bool(prior)),
        })
        for field in PRIOR_FIELDS:
            out[f"prior_{field}"] = prior.get(field, "")
        status = str(stat.get("status", "")).strip().lower()
        chance_values = [number(stat.get("chance_of_playing_this_round")), number(stat.get("chance_of_playing_next_round"))]
        chance_known = any(str(stat.get(k, "")).strip() for k in ("chance_of_playing_this_round", "chance_of_playing_next_round"))
        out["unavailable_flag"] = int(status in UNAVAILABLE)
        out["doubtful_flag"] = int(status == "d" or (chance_known and min(chance_values) < 100 and status not in UNAVAILABLE))
        out["prior_regular_flag"] = int(number(prior.get("starts")) >= 10 or number(prior.get("minutes")) >= 900)
        snapshot_rows.append(out)

    team_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in snapshot_rows:
        team_groups[str(row.get("team_name", ""))].append(row)
    team_summary: list[dict[str, object]] = []
    for team_name, team_rows in sorted(team_groups.items()):
        regulars = [r for r in team_rows if int(r.get("prior_regular_flag", 0)) == 1]
        unavailable_regulars = [r for r in regulars if int(r.get("unavailable_flag", 0)) == 1]
        doubtful_regulars = [r for r in regulars if int(r.get("doubtful_flag", 0)) == 1]
        goalkeepers = [r for r in regulars if str(r.get("position")) == "Goalkeeper"]
        top_gk = max(goalkeepers, key=lambda r: (number(r.get("prior_starts")), number(r.get("prior_minutes"))), default=None)
        summary: dict[str, object] = {
            "team_name": team_name,
            "current_players": len(team_rows),
            "prior_matched_players": sum(int(r.get("prior_season_matched", 0)) for r in team_rows),
            "prior_regulars": len(regulars),
            "unavailable_regulars": len(unavailable_regulars),
            "doubtful_regulars": len(doubtful_regulars),
            "unavailable_regular_prior_minutes": sum(number(r.get("prior_minutes")) for r in unavailable_regulars),
            "doubtful_regular_prior_minutes": sum(number(r.get("prior_minutes")) for r in doubtful_regulars),
            "top_prior_gk": top_gk.get("web_name", "") if top_gk else "",
            "top_prior_gk_status": top_gk.get("status", "") if top_gk else "",
            "top_prior_gk_unavailable": int(top_gk.get("unavailable_flag", 0)) if top_gk else 0,
        }
        for position in ("Goalkeeper", "Defender", "Midfielder", "Forward"):
            key = position.lower()
            pos_regs = [r for r in regulars if str(r.get("position")) == position]
            summary[f"{key}_regulars"] = len(pos_regs)
            summary[f"{key}_unavailable_regulars"] = sum(int(r.get("unavailable_flag", 0)) for r in pos_regs)
            summary[f"{key}_doubtful_regulars"] = sum(int(r.get("doubtful_flag", 0)) for r in pos_regs)
        team_summary.append(summary)

    player_fields = [
        "id", "player_code", "first_name", "second_name", "web_name", "position",
        "team_code", "team_name", *[x for x in CURRENT_FIELDS if x != "id"],
        "prior_season_matched", *[f"prior_{x}" for x in PRIOR_FIELDS],
        "unavailable_flag", "doubtful_flag", "prior_regular_flag",
    ]
    write_csv(args.out / "FPL_CORE_2026_27_player_availability_snapshot.csv", player_fields, snapshot_rows)
    team_fields = list(team_summary[0]) if team_summary else []
    write_csv(args.out / "FPL_CORE_2026_27_team_availability_summary.csv", team_fields, team_summary)
    fixtures = rows(payloads.get("current_fixtures_gw1"))
    fixture_fields = list(fixtures[0]) if fixtures else []
    write_csv(args.out / "FPL_CORE_2026_27_fixture_snapshot.csv", fixture_fields, fixtures)
    write_csv(args.out / "FPL_CORE_2026_27_source_ledger.csv", ["key", "required", "status", "url", "sha256", "bytes"], ledger)

    non_available = [r for r in snapshot_rows if int(r.get("unavailable_flag", 0)) == 1]
    doubtful = [r for r in snapshot_rows if int(r.get("doubtful_flag", 0)) == 1]
    timestamped_news = [r for r in snapshot_rows if r.get("news_added")]
    chance_rows = [r for r in snapshot_rows if r.get("chance_of_playing_this_round") or r.get("chance_of_playing_next_round")]
    finished_fixtures = [r for r in fixtures if str(r.get("finished", "")).lower() == "true"]
    receipt = {
        "schema_version": "DRAW-FORWARD-FPL-CORE-SNAPSHOT-R2",
        "status": "SOURCE_SNAPSHOT_ONLY_NO_MODEL_SELECTION",
        "source_repository": repo,
        "source_commit": args.source_commit,
        "source_commit_time": args.source_commit_time,
        "source_access": "public static GitHub files only; no football API credentials",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "season": "2026/27",
        "players": len(snapshot_rows),
        "prior_season_matched_players": sum(int(r.get("prior_season_matched", 0)) for r in snapshot_rows),
        "prior_regular_players": sum(int(r.get("prior_regular_flag", 0)) for r in snapshot_rows),
        "non_available_players": len(non_available),
        "doubtful_players": len(doubtful),
        "players_with_news_added": len(timestamped_news),
        "players_with_chance_fields": len(chance_rows),
        "teams": len(team_summary),
        "fixtures": len(fixtures),
        "finished_fixtures": len(finished_fixtures),
        "optional_sources_absent": [r["key"] for r in ledger if not r["required"] and r["status"] != "PRESENT"],
        "untouched_forward_batch_ready": bool(finished_fixtures),
        "formal_weight": 0,
        "formal_model_data_config_current_writes": [0, 0, 0, 0],
        "consumed_test_sets_reused": [],
        "caveats": [
            "repository has no explicit LICENSE file located during source audit",
            "README permits use but redistribution rights are not formalized; evidence remains research-only",
            "master files are mutable; exact source commit and content hashes are mandatory",
            "news_added and chance fields may be blank before official availability updates",
            "prior-season quality is descriptive evidence only and is not a formal player-strength model",
        ],
    }
    (args.out / "snapshot_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": "DRAW-FORWARD-FPL-CORE-ARTIFACT-R2", "files": {}}
    for path in sorted(args.out.iterdir()):
        if path.is_file() and path.name != "artifact_manifest.json":
            data = path.read_bytes()
            manifest["files"][path.name] = {"sha256": sha256(data), "bytes": len(data)}
    (args.out / "artifact_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
