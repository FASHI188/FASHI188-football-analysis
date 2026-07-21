#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import io
import json
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "transfermarkt_value_readiness_v520_status.json"
BASE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
FILES = {
    "competitions": "competitions.csv.gz",
    "clubs": "clubs.csv.gz",
    "players": "players.csv.gz",
    "player_valuations": "player_valuations.csv.gz",
    "games": "games.csv.gz",
    "game_lineups": "game_lineups.csv.gz",
    "appearances": "appearances.csv.gz",
    "transfers": "transfers.csv.gz",
}
DOMAINS = {
    "ENG_PremierLeague": "GB1",
    "ESP_LaLiga": "ES1",
    "GER_Bundesliga": "L1",
    "ITA_SerieA": "IT1",
    "FRA_Ligue1": "FR1",
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_gz_csv(filename: str) -> tuple[list[dict[str, str]], list[str], int]:
    url = f"{BASE}/{filename}"
    req = urllib.request.Request(url, headers={"User-Agent": "football-analysis-research/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        raw = response.read()
    text = gzip.decompress(raw).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return rows, list(reader.fieldnames or []), len(raw)


def parse_date(value: str) -> datetime | None:
    token = str(value or "").strip()[:10]
    if not token:
        return None
    try:
        return datetime.fromisoformat(token)
    except Exception:
        return None


def main() -> int:
    file_reports = {}
    data = {}
    failures = {}
    for key, filename in FILES.items():
        try:
            rows, columns, compressed_bytes = fetch_gz_csv(filename)
            data[key] = rows
            file_reports[key] = {
                "status": "PASS",
                "filename": filename,
                "row_count": len(rows),
                "column_count": len(columns),
                "columns": columns,
                "compressed_bytes": compressed_bytes,
            }
        except Exception as exc:
            failures[key] = f"{type(exc).__name__}: {exc}"
            file_reports[key] = {"status": "FAIL", "filename": filename, "error": failures[key]}

    required = {"competitions", "clubs", "players", "player_valuations", "games", "game_lineups"}
    if not required.issubset(data):
        payload = {
            "schema_version": "V5.2.0-transfermarkt-value-readiness-r1",
            "generated_at_utc": now(),
            "status": "FAIL",
            "file_reports": file_reports,
            "failures": failures,
            "formal_weight_change": False,
            "probability_change": False,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    competition_rows = {str(r.get("competition_id") or ""): r for r in data["competitions"]}
    clubs_by_comp = defaultdict(set)
    for row in data["clubs"]:
        cid = str(row.get("domestic_competition_id") or row.get("competition_id") or "")
        club_id = str(row.get("club_id") or "")
        if cid and club_id:
            clubs_by_comp[cid].add(club_id)

    player_current_club = {}
    for row in data["players"]:
        pid = str(row.get("player_id") or "")
        club_id = str(row.get("current_club_id") or "")
        if pid:
            player_current_club[pid] = club_id

    valuations_by_player = defaultdict(list)
    valuation_date_field = None
    value_field = None
    val_columns = file_reports["player_valuations"].get("columns") or []
    for candidate in ("date", "valuation_date", "market_value_date"):
        if candidate in val_columns:
            valuation_date_field = candidate
            break
    for candidate in ("market_value_in_eur", "market_value", "value"):
        if candidate in val_columns:
            value_field = candidate
            break
    for row in data["player_valuations"]:
        pid = str(row.get("player_id") or "")
        dt = parse_date(row.get(valuation_date_field or "") or "")
        if not pid or dt is None:
            continue
        try:
            value = float(row.get(value_field or "") or 0.0)
        except Exception:
            continue
        valuations_by_player[pid].append((dt, value))

    game_columns = file_reports["games"].get("columns") or []
    lineup_columns = file_reports["game_lineups"].get("columns") or []
    game_date_field = "date" if "date" in game_columns else None
    lineup_type_field = "type" if "type" in lineup_columns else None

    domain_reports = {}
    for domain, source_comp in DOMAINS.items():
        comp = competition_rows.get(source_comp)
        clubs = clubs_by_comp.get(source_comp, set())
        current_players = [pid for pid, club in player_current_club.items() if club in clubs]
        player_with_history = [pid for pid in current_players if valuations_by_player.get(pid)]
        recent_player_count = 0
        pre_2025_26_player_count = 0
        latest_dates = []
        for pid in player_with_history:
            dates = [item[0] for item in valuations_by_player[pid]]
            latest = max(dates)
            latest_dates.append(latest.date().isoformat())
            if latest >= datetime(2025, 7, 1):
                recent_player_count += 1
            if any(dt < datetime(2025, 8, 1) for dt in dates):
                pre_2025_26_player_count += 1

        games_2526 = []
        for row in data["games"]:
            if str(row.get("competition_id") or "") != source_comp:
                continue
            if str(row.get("season") or "") not in ("2025", "2025/26", "2025-26"):
                continue
            games_2526.append(row)

        source_lineup_rows = 0
        starting_lineup_rows = 0
        game_ids = {str(r.get("game_id") or "") for r in games_2526}
        for row in data["game_lineups"]:
            if str(row.get("game_id") or "") not in game_ids:
                continue
            source_lineup_rows += 1
            if lineup_type_field and str(row.get(lineup_type_field) or "") == "starting_lineup":
                starting_lineup_rows += 1

        valuation_coverage = len(player_with_history) / max(1, len(current_players))
        recent_coverage = recent_player_count / max(1, len(current_players))
        pre_season_history_coverage = pre_2025_26_player_count / max(1, len(current_players))
        domain_reports[domain] = {
            "source_competition_id": source_comp,
            "competition_name": comp.get("name") if comp else None,
            "club_count": len(clubs),
            "current_player_count": len(current_players),
            "players_with_valuation_history": len(player_with_history),
            "valuation_history_coverage": valuation_coverage,
            "players_with_valuation_at_or_after_2025_07_01": recent_player_count,
            "recent_valuation_coverage": recent_coverage,
            "players_with_value_before_2025_08_01": pre_2025_26_player_count,
            "preseason_history_coverage": pre_season_history_coverage,
            "latest_valuation_date_max": max(latest_dates) if latest_dates else None,
            "source_2025_26_game_count": len(games_2526),
            "source_lineup_row_count": source_lineup_rows,
            "source_starting_lineup_row_count": starting_lineup_rows,
            "status": "PASS" if valuation_coverage >= 0.90 and pre_season_history_coverage >= 0.75 and len(games_2526) >= 300 if domain != "GER_Bundesliga" and domain != "FRA_Ligue1" else valuation_coverage >= 0.90 and pre_season_history_coverage >= 0.75 and len(games_2526) >= 280,
        }

    # Fix conditional-expression precedence explicitly for readability/audit.
    for domain, report in domain_reports.items():
        minimum_games = 280 if domain in ("GER_Bundesliga", "FRA_Ligue1") else 300
        report["minimum_expected_games"] = minimum_games
        report["status"] = "PASS" if (
            report["valuation_history_coverage"] >= 0.90
            and report["preseason_history_coverage"] >= 0.75
            and report["source_2025_26_game_count"] >= minimum_games
        ) else "PARTIAL"

    passed = [domain for domain, report in domain_reports.items() if report["status"] == "PASS"]
    payload = {
        "schema_version": "V5.2.0-transfermarkt-value-readiness-r1",
        "generated_at_utc": now(),
        "source_repository": "https://github.com/dcaribou/transfermarkt-datasets",
        "source_base_url": BASE,
        "license": "CC0-1.0",
        "file_reports": file_reports,
        "failures": failures,
        "valuation_date_field": valuation_date_field,
        "valuation_value_field": value_field,
        "game_date_field": game_date_field,
        "lineup_type_field": lineup_type_field,
        "domain_reports": domain_reports,
        "passed_domains": passed,
        "status": "PASS" if len(passed) == len(DOMAINS) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "pit_policy": "For a target fixture, use only player valuations with valuation date strictly before the target fixture date. Current player.market_value_in_eur is never substituted for an unavailable historical valuation.",
        "next_step": "If readiness passes, join historical valuation strictly-before-fixture to the already audited observed lineups, construct team/squad value-loss features from previous observed lineups only, and evaluate as a competition-specific shadow residual layer."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "passed_domains": passed,
        "valuation_date_field": valuation_date_field,
        "valuation_value_field": value_field,
        "domain_summary": {d: {
            "valuation_history_coverage": r["valuation_history_coverage"],
            "preseason_history_coverage": r["preseason_history_coverage"],
            "source_2025_26_game_count": r["source_2025_26_game_count"],
            "starting_lineup_rows": r["source_starting_lineup_row_count"]
        } for d, r in domain_reports.items()}
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
