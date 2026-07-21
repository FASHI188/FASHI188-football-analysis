#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import io
import json
import statistics
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "transfermarkt_lineup_value_readiness_v521_status.json"
VALUATIONS_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/player_valuations.csv.gz"
DOMAINS = ["ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1"]
SEASON = "2025/26"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_valuations() -> tuple[dict[str, list[tuple[datetime, float]]], dict]:
    req = urllib.request.Request(VALUATIONS_URL, headers={"User-Agent": "football-analysis-research/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        raw = response.read()
    text = gzip.decompress(raw).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    histories: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    row_count = 0
    columns = list(reader.fieldnames or [])
    for row in reader:
        row_count += 1
        player_id = str(row.get("player_id") or "").strip()
        date_token = str(row.get("date") or "").strip()[:10]
        if not player_id or not date_token:
            continue
        try:
            date = datetime.fromisoformat(date_token)
            value = float(row.get("market_value_in_eur") or 0.0)
        except Exception:
            continue
        histories[player_id].append((date, value))
    for player_id in histories:
        histories[player_id].sort(key=lambda item: item[0])
    return dict(histories), {
        "source_url": VALUATIONS_URL,
        "compressed_bytes": len(raw),
        "row_count": row_count,
        "columns": columns,
        "player_history_count": len(histories),
    }


def latest_strictly_before(history: list[tuple[datetime, float]], target: datetime) -> tuple[datetime, float] | None:
    selected = None
    for date, value in history:
        if date >= target:
            break
        selected = (date, value)
    return selected


def read_fixture_lineups(domain: str) -> list[dict]:
    path = ROOT / "lineups" / domain / "historical_lineups.jsonl"
    grouped: dict[str, list[dict]] = defaultdict(list)
    if not path.exists():
        raise RuntimeError(f"missing lineup evidence {path}")
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("season") or "") != SEASON:
                continue
            grouped[str(row.get("fixture_id") or "")].append(row)
    fixtures = []
    for fixture_id, rows in grouped.items():
        home = [row for row in rows if str(row.get("home_away") or "").lower() == "home"]
        away = [row for row in rows if str(row.get("home_away") or "").lower() == "away"]
        if len(home) != 1 or len(away) != 1:
            continue
        h, a = home[0], away[0]
        kickoff = datetime.fromisoformat(str(h.get("kickoff_utc") or "").replace("Z", "+00:00"))
        fixtures.append({
            "fixture_id": fixture_id,
            "kickoff": kickoff.replace(tzinfo=None),
            "game_id": str(h.get("game_id") or ""),
            "home_team": str(h.get("team") or ""),
            "away_team": str(a.get("team") or ""),
            "home_starters": [str(item) for item in (h.get("starters") or [])],
            "away_starters": [str(item) for item in (a.get("starters") or [])],
        })
    fixtures.sort(key=lambda row: (row["kickoff"], row["fixture_id"]))
    return fixtures


def audit_domain(domain: str, histories: dict[str, list[tuple[datetime, float]]]) -> dict:
    fixtures = read_fixture_lineups(domain)
    total_slots = covered_slots = 0
    full22 = full_home11 = full_away11 = 0
    recency_days: list[int] = []
    recent180 = recent365 = 0
    missing_players = defaultdict(int)
    fixture_audit = []

    for fixture in fixtures:
        side_results = {}
        for side in ("home", "away"):
            starters = fixture[f"{side}_starters"]
            covered = 0
            total_value = 0.0
            missing = []
            side_recency = []
            for player_id in starters:
                total_slots += 1
                selected = latest_strictly_before(histories.get(player_id, []), fixture["kickoff"])
                if selected is None:
                    missing.append(player_id)
                    missing_players[player_id] += 1
                    continue
                date, value = selected
                age = (fixture["kickoff"].date() - date.date()).days
                covered += 1
                covered_slots += 1
                total_value += value
                recency_days.append(age)
                side_recency.append(age)
                if age <= 180:
                    recent180 += 1
                if age <= 365:
                    recent365 += 1
            side_results[side] = {
                "starter_count": len(starters),
                "valuation_covered_count": covered,
                "valuation_coverage": covered / max(1, len(starters)),
                "lineup_value_eur": total_value,
                "missing_player_ids": missing,
                "max_valuation_age_days": max(side_recency) if side_recency else None,
            }
        if side_results["home"]["valuation_covered_count"] == 11:
            full_home11 += 1
        if side_results["away"]["valuation_covered_count"] == 11:
            full_away11 += 1
        if side_results["home"]["valuation_covered_count"] == 11 and side_results["away"]["valuation_covered_count"] == 11:
            full22 += 1
        fixture_audit.append({
            "fixture_id": fixture["fixture_id"],
            "game_id": fixture["game_id"],
            "date": fixture["kickoff"].date().isoformat(),
            "home_team": fixture["home_team"],
            "away_team": fixture["away_team"],
            "home": side_results["home"],
            "away": side_results["away"],
        })

    fixture_count = len(fixtures)
    slot_coverage = covered_slots / max(1, total_slots)
    full22_rate = full22 / max(1, fixture_count)
    recency180 = recent180 / max(1, covered_slots)
    recency365 = recent365 / max(1, covered_slots)
    return {
        "competition_id": domain,
        "season": SEASON,
        "fixture_count": fixture_count,
        "starter_slot_count": total_slots,
        "covered_starter_slot_count": covered_slots,
        "starter_slot_valuation_coverage": slot_coverage,
        "full_home11_valuation_count": full_home11,
        "full_away11_valuation_count": full_away11,
        "full_both22_valuation_count": full22,
        "full_both22_valuation_rate": full22_rate,
        "covered_values_within_180d_rate": recency180,
        "covered_values_within_365d_rate": recency365,
        "valuation_age_days_median": statistics.median(recency_days) if recency_days else None,
        "valuation_age_days_p90": sorted(recency_days)[int(0.90 * (len(recency_days) - 1))] if recency_days else None,
        "unique_missing_player_count": len(missing_players),
        "most_frequent_missing_player_ids": sorted(
            ({"player_id": player_id, "missing_fixture_count": count} for player_id, count in missing_players.items()),
            key=lambda item: (-item["missing_fixture_count"], item["player_id"]),
        )[:20],
        "status": "PASS" if slot_coverage >= 0.95 and full22_rate >= 0.70 and recency365 >= 0.90 else "PARTIAL",
        "fixture_audit": fixture_audit,
    }


def main() -> int:
    histories, source = fetch_valuations()
    reports = {}
    failures = {}
    for domain in DOMAINS:
        try:
            reports[domain] = audit_domain(domain, histories)
        except Exception as exc:
            failures[domain] = f"{type(exc).__name__}: {exc}"
    passed = [domain for domain, report in reports.items() if report["status"] == "PASS"]
    payload = {
        "schema_version": "V5.2.1-transfermarkt-lineup-value-readiness-r1",
        "generated_at_utc": utc_now(),
        "source": source,
        "season": SEASON,
        "requested_domains": DOMAINS,
        "passed_domains": passed,
        "reports": reports,
        "failures": failures,
        "status": "PASS" if len(passed) == len(DOMAINS) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "pit_policy": "Each starter value is the latest player_valuations record whose date is strictly earlier than the fixture calendar date. Same-day or later valuations are excluded.",
        "lineup_policy": "Uses the already-audited observed Transfermarkt starting-lineup labels for retrospective research. It does not treat actual target-match XI as a pre-match feature; the purpose here is only to measure whether historical player-value data can support future expected-XI/value-loss modeling.",
        "next_step": "If coverage passes, build expected-lineup value features from only earlier observed lineups plus strictly-prior valuations; never use the target match actual XI as prediction input."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "passed_domains": passed,
        "summary": {
            domain: {
                "fixtures": report["fixture_count"],
                "slot_coverage": report["starter_slot_valuation_coverage"],
                "full22_rate": report["full_both22_valuation_rate"],
                "within365_rate": report["covered_values_within_365d_rate"],
                "median_age_days": report["valuation_age_days_median"],
            }
            for domain, report in reports.items()
        },
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
