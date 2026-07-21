#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import io
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "transfermarkt_value_readiness_v520_status.json"
BASE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
FILES = {
    "competitions": "competitions.csv.gz",
    "clubs": "clubs.csv.gz",
    "players": "players.csv.gz",
    "player_valuations": "player_valuations.csv.gz",
    "games": "games.csv.gz",
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
    file_reports: dict[str, dict] = {}
    data: dict[str, list[dict[str, str]]] = {}
    failures: dict[str, str] = {}
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

    required = set(FILES)
    if not required.issubset(data):
        payload = {
            "schema_version": "V5.2.0-transfermarkt-value-readiness-r2",
            "generated_at_utc": now(),
            "status": "FAIL",
            "file_reports": file_reports,
            "failures": failures,
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    competition_rows = {str(row.get("competition_id") or ""): row for row in data["competitions"]}
    clubs_by_comp: dict[str, set[str]] = defaultdict(set)
    for row in data["clubs"]:
        competition_id = str(row.get("domestic_competition_id") or row.get("competition_id") or "")
        club_id = str(row.get("club_id") or "")
        if competition_id and club_id:
            clubs_by_comp[competition_id].add(club_id)

    player_current_club: dict[str, str] = {}
    for row in data["players"]:
        player_id = str(row.get("player_id") or "")
        club_id = str(row.get("current_club_id") or "")
        if player_id:
            player_current_club[player_id] = club_id

    valuation_columns = file_reports["player_valuations"].get("columns") or []
    valuation_date_field = next((name for name in ("date", "valuation_date", "market_value_date") if name in valuation_columns), None)
    valuation_value_field = next((name for name in ("market_value_in_eur", "market_value", "value") if name in valuation_columns), None)
    valuations_by_player: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    if valuation_date_field and valuation_value_field:
        for row in data["player_valuations"]:
            player_id = str(row.get("player_id") or "")
            valuation_date = parse_date(row.get(valuation_date_field) or "")
            if not player_id or valuation_date is None:
                continue
            try:
                value = float(row.get(valuation_value_field) or 0.0)
            except Exception:
                continue
            valuations_by_player[player_id].append((valuation_date, value))

    domain_reports = {}
    for domain, source_competition in DOMAINS.items():
        competition = competition_rows.get(source_competition)
        current_first_tier_clubs = clubs_by_comp.get(source_competition, set())
        current_players = [player_id for player_id, club_id in player_current_club.items() if club_id in current_first_tier_clubs]
        players_with_history = [player_id for player_id in current_players if valuations_by_player.get(player_id)]
        players_with_preseason_history = [
            player_id for player_id in players_with_history
            if any(date < datetime(2025, 8, 1) for date, _value in valuations_by_player[player_id])
        ]
        players_with_recent_value = [
            player_id for player_id in players_with_history
            if max(date for date, _value in valuations_by_player[player_id]) >= datetime(2025, 7, 1)
        ]
        latest_dates = [max(date for date, _value in valuations_by_player[player_id]).date().isoformat() for player_id in players_with_history]

        games_2025_26 = [
            row for row in data["games"]
            if str(row.get("competition_id") or "") == source_competition
            and str(row.get("season") or "") in ("2025", "2025/26", "2025-26")
        ]
        minimum_games = 280 if domain in ("GER_Bundesliga", "FRA_Ligue1") else 300
        valuation_coverage = len(players_with_history) / max(1, len(current_players))
        preseason_history_coverage = len(players_with_preseason_history) / max(1, len(current_players))
        recent_coverage = len(players_with_recent_value) / max(1, len(current_players))

        domain_reports[domain] = {
            "source_competition_id": source_competition,
            "competition_name": competition.get("name") if competition else None,
            "current_first_tier_club_count": len(current_first_tier_clubs),
            "current_first_tier_player_count": len(current_players),
            "players_with_valuation_history": len(players_with_history),
            "valuation_history_coverage": valuation_coverage,
            "players_with_value_before_2025_08_01": len(players_with_preseason_history),
            "preseason_history_coverage": preseason_history_coverage,
            "players_with_value_at_or_after_2025_07_01": len(players_with_recent_value),
            "recent_valuation_coverage": recent_coverage,
            "latest_valuation_date_max": max(latest_dates) if latest_dates else None,
            "source_2025_26_game_count": len(games_2025_26),
            "minimum_expected_games": minimum_games,
            "status": "PASS" if (
                valuation_coverage >= 0.90
                and preseason_history_coverage >= 0.75
                and len(games_2025_26) >= minimum_games
            ) else "PARTIAL",
        }

    passed_domains = [domain for domain, report in domain_reports.items() if report["status"] == "PASS"]
    payload = {
        "schema_version": "V5.2.0-transfermarkt-value-readiness-r2",
        "generated_at_utc": now(),
        "source_repository": "https://github.com/dcaribou/transfermarkt-datasets",
        "source_base_url": BASE,
        "license": "CC0-1.0",
        "file_reports": file_reports,
        "failures": failures,
        "valuation_date_field": valuation_date_field,
        "valuation_value_field": valuation_value_field,
        "domain_reports": domain_reports,
        "passed_domains": passed_domains,
        "status": "PASS" if len(passed_domains) == len(DOMAINS) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "pit_policy": "For a target fixture, use only player valuations with valuation date strictly before the target fixture date. Current players.market_value_in_eur is never substituted for an unavailable historical valuation.",
        "scope_note": "This readiness receipt measures historical valuation availability for players currently associated with first-tier clubs plus 2025/26 competition game coverage. Historical squad/lineup feature construction remains a separate identity-and-timing gate.",
        "next_step": "If readiness passes, join strictly-before-fixture valuation records to the already audited Transfermarkt observed-lineup identity bridge and test squad-value / key-player-value residual features as competition-specific shadow challengers."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "passed_domains": passed_domains,
        "valuation_date_field": valuation_date_field,
        "valuation_value_field": valuation_value_field,
        "domain_summary": {
            domain: {
                "valuation_history_coverage": report["valuation_history_coverage"],
                "preseason_history_coverage": report["preseason_history_coverage"],
                "source_2025_26_game_count": report["source_2025_26_game_count"],
            }
            for domain, report in domain_reports.items()
        }
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
