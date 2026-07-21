#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fpl_snapshot_readiness_v518_status.json"
BASE = "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data/2025-2026/By%20Gameweek"

FILES = ["players.csv", "playerstats.csv", "player_gameweek_stats.csv", "fixtures.csv", "teams.csv"]
PLAYER_STATE_CANDIDATES = [
    "id", "player_id", "team", "team_id", "status", "news", "news_added",
    "chance_of_playing_next_round", "chance_of_playing_this_round", "form",
    "selected_by_percent", "now_cost", "minutes", "total_points",
    "expected_goals", "expected_assists", "expected_goal_involvements",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_csv(url: str) -> tuple[list[dict[str, str]], list[str]]:
    req = urllib.request.Request(url, headers={"User-Agent": "football-analysis-research/1.0"})
    with urllib.request.urlopen(req, timeout=35) as response:
        text = response.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return rows, list(reader.fieldnames or [])


def main() -> int:
    reports: dict[str, Any] = {}
    file_success = Counter()
    player_columns_union = set()
    stable_player_ids_by_gw: dict[str, int] = {}
    stable_team_ids_by_gw: dict[str, int] = {}
    failures = []

    for gw in range(1, 39):
        gw_report: dict[str, Any] = {"gameweek": gw, "files": {}}
        for filename in FILES:
            url = f"{BASE}/GW{gw}/{filename}"
            try:
                rows, columns = fetch_csv(url)
                file_success[filename] += 1
                item = {
                    "status": "PASS",
                    "row_count": len(rows),
                    "column_count": len(columns),
                    "columns_preview": columns[:40],
                    "source_url": url,
                }
                if filename in ("players.csv", "playerstats.csv", "player_gameweek_stats.csv"):
                    player_columns_union.update(columns)
                    id_col = "id" if "id" in columns else "player_id" if "player_id" in columns else None
                    if id_col:
                        stable_player_ids_by_gw[str(gw)] = len({row.get(id_col) for row in rows if row.get(id_col)})
                if filename == "teams.csv":
                    id_col = "id" if "id" in columns else "team_id" if "team_id" in columns else None
                    if id_col:
                        stable_team_ids_by_gw[str(gw)] = len({row.get(id_col) for row in rows if row.get(id_col)})
                gw_report["files"][filename] = item
            except Exception as exc:
                failures.append({"gameweek": gw, "file": filename, "error": f"{type(exc).__name__}: {exc}"})
                gw_report["files"][filename] = {"status": "FAIL", "source_url": url, "error": f"{type(exc).__name__}: {exc}"}
        reports[str(gw)] = gw_report

    present_state_fields = [field for field in PLAYER_STATE_CANDIDATES if field in player_columns_union]
    team_id_counts = list(stable_team_ids_by_gw.values())
    player_id_counts = list(stable_player_ids_by_gw.values())
    complete_files = {filename: file_success[filename] for filename in FILES}
    full_38 = all(count == 38 for count in complete_files.values())
    team_shape_ok = bool(team_id_counts) and min(team_id_counts) >= 20 and max(team_id_counts) <= 30
    player_shape_ok = bool(player_id_counts) and min(player_id_counts) >= 400

    payload = {
        "schema_version": "V5.1.8-fpl-snapshot-readiness-r1",
        "generated_at_utc": utc_now(),
        "source_repository": "https://github.com/olbauday/FPL-Core-Insights",
        "source_branch": "main",
        "season": "2025/26",
        "competition_id": "ENG_PremierLeague",
        "gameweeks_requested": 38,
        "file_success_counts": complete_files,
        "full_38_gameweeks_all_required_files": full_38,
        "player_columns_union": sorted(player_columns_union),
        "present_player_state_fields": present_state_fields,
        "player_state_field_count": len(present_state_fields),
        "player_id_count_range": [min(player_id_counts) if player_id_counts else None, max(player_id_counts) if player_id_counts else None],
        "team_id_count_range": [min(team_id_counts) if team_id_counts else None, max(team_id_counts) if team_id_counts else None],
        "team_shape_ok": team_shape_ok,
        "player_shape_ok": player_shape_ok,
        "failures": failures,
        "failure_count": len(failures),
        "gameweek_reports": reports,
        "status": "PASS" if full_38 and team_shape_ok and player_shape_ok and len(present_state_fields) >= 6 else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "pit_class": "GAMEWEEK_SNAPSHOT_RESEARCH",
        "pit_policy": "For predicting a fixture in gameweek N, only snapshots from completed gameweek N-1 or earlier may be used unless an independently timestamped pre-deadline snapshot for N is proven. No target-gameweek post-match player statistics may enter features.",
        "formal_promotion_blocker": "Third-party GitHub snapshot commit times and original FPL API collection timing must be bound to each snapshot before historical formal PIT promotion. Until then this layer is shadow research only."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "file_success_counts": complete_files,
        "present_player_state_fields": present_state_fields,
        "player_id_count_range": payload["player_id_count_range"],
        "team_id_count_range": payload["team_id_count_range"],
        "failure_count": len(failures)
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
