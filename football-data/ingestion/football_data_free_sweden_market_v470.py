#!/usr/bin/env python3
"""Ingest free Football-Data Sweden pre-closing odds for LOMO research.

This route is deliberately RESEARCH-ONLY because Football-Data documents a coarse
collection schedule (Friday afternoons for weekend games / Tuesday afternoons for
midweek games) rather than a row-level original bookmaker quote timestamp.
Therefore it may screen LOMO methodology but can never create a production formal
LOMO receipt by itself.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.football-data.co.uk/new/SWE.csv"
OUT = ROOT / "evidence" / "markets" / "SWE_Allsvenskan" / "football_data_free_preclosing_2026.jsonl"
MANIFEST = ROOT / "manifests" / "market_lomo_research" / "SWE_Allsvenskan_football_data_free_coverage.json"


def _download() -> bytes:
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "FASHI188-football-analysis/4.7"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("unable to decode Football-Data Sweden CSV")


def _number(row: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        return value
    return None


def _odds(value: float | None) -> float | None:
    return value if value is not None and value > 1.0 else None


def _line(value: float | None) -> float | None:
    if value is None:
        return None
    rounded = round(value * 4.0) / 4.0
    return rounded if abs(value - rounded) <= 1e-6 else None


def _date(value: str) -> str | None:
    raw = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _surface_b365(row: dict[str, str]) -> dict[str, Any] | None:
    home = _odds(_number(row, "B365H")); draw = _odds(_number(row, "B365D")); away = _odds(_number(row, "B365A"))
    over = _odds(_number(row, "B365>2.5")); under = _odds(_number(row, "B365<2.5"))
    ah_home = _odds(_number(row, "B365AHH")); ah_away = _odds(_number(row, "B365AHA"))
    ah_line = _line(_number(row, "B365AH", "AHh"))
    if None in (home, draw, away, over, under, ah_home, ah_away, ah_line):
        return None
    return {
        "surface_class": "same_bookmaker_b365",
        "bookmaker": "Bet365",
        "one_x_two": {"home": home, "draw": draw, "away": away},
        "asian_handicap": {"line": ah_line, "home": ah_home, "away": ah_away},
        "total_goals": {"line": 2.5, "over": over, "under": under},
    }


def _surface_average(row: dict[str, str]) -> dict[str, Any] | None:
    home = _odds(_number(row, "AvgH")); draw = _odds(_number(row, "AvgD")); away = _odds(_number(row, "AvgA"))
    over = _odds(_number(row, "Avg>2.5")); under = _odds(_number(row, "Avg<2.5"))
    ah_home = _odds(_number(row, "AvgAHH")); ah_away = _odds(_number(row, "AvgAHA"))
    ah_line = _line(_number(row, "AHh", "BbAHh"))
    if None in (home, draw, away, over, under, ah_home, ah_away, ah_line):
        return None
    return {
        "surface_class": "market_average_composite",
        "bookmaker": None,
        "one_x_two": {"home": home, "draw": draw, "away": away},
        "asian_handicap": {"line": ah_line, "home": ah_home, "away": ah_away},
        "total_goals": {"line": 2.5, "over": over, "under": under},
    }


def main() -> int:
    raw = _download()
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = list(reader.fieldnames or [])
    normalized = []
    row_count = 0
    b365_count = 0
    average_count = 0
    dates = []

    for row in reader:
        row_count += 1
        match_date = _date(row.get("Date", ""))
        home = str(row.get("HomeTeam") or "").strip()
        away = str(row.get("AwayTeam") or "").strip()
        if not match_date or not home or not away:
            continue
        try:
            home_goals = int(float(str(row.get("FTHG") or "")))
            away_goals = int(float(str(row.get("FTAG") or "")))
        except ValueError:
            continue
        b365 = _surface_b365(row)
        average = _surface_average(row)
        if b365:
            surface = b365
            b365_count += 1
        elif average:
            surface = average
            average_count += 1
        else:
            continue
        dates.append(match_date)
        normalized.append({
            "competition_id": "SWE_Allsvenskan",
            "season": "2026",
            "match_date": match_date,
            "home_team": home,
            "away_team": away,
            "home_goals": home_goals,
            "away_goals": away_goals,
            **surface,
            "source_id": "football_data_co_uk_free_sweden",
            "source_url": SOURCE_URL,
            "quote_timestamp_utc": None,
            "timestamp_grade": "COARSE_COLLECTION_SCHEDULE_ONLY",
            "documented_collection_schedule": "Friday afternoons for weekend games; Tuesday afternoons for midweek games",
            "formal_lomo_eligible": False,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in normalized), encoding="utf-8")
    manifest = {
        "schema_version": "V4.7.0-free-market-research-coverage-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if normalized else "NO_USABLE_COMPLETE_SURFACES",
        "competition_id": "SWE_Allsvenskan",
        "source_url": SOURCE_URL,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "csv_row_count": row_count,
        "normalized_complete_surface_count": len(normalized),
        "same_bookmaker_b365_count": b365_count,
        "market_average_composite_count": average_count,
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "fieldnames": fieldnames,
        "formal_lomo_eligible": False,
        "formal_ev_enabled": False,
        "production_lomo_receipt_created": False,
        "blocker": "row-level original quote timestamps are unavailable in this free dataset",
        "policy": "Research screening only. This artifact must never be copied into manifests/market_lomo as a formal receipt.",
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if normalized else 2


if __name__ == "__main__":
    raise SystemExit(main())
