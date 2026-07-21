#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "clubelo_direct_api_readiness_v515_status.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch(url: str) -> tuple[str, bytes, int]:
    req = urllib.request.Request(url, headers={"User-Agent": "football-analysis-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.geturl(), response.read(), int(getattr(response, "status", 200))


def parse_csv(payload: bytes) -> list[dict[str, str]]:
    text = payload.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def main() -> int:
    attempts = []
    rows = []
    successful_url = None
    for scheme in ("https", "http"):
        url = f"{scheme}://api.clubelo.com/2026-05-01"
        try:
            final_url, payload, status = fetch(url)
            parsed = parse_csv(payload)
            attempts.append({
                "requested_url": url,
                "final_url": final_url,
                "http_status": status,
                "byte_count": len(payload),
                "row_count": len(parsed),
                "columns": list(parsed[0].keys()) if parsed else [],
                "status": "PASS" if parsed else "EMPTY"
            })
            if parsed:
                rows = parsed
                successful_url = final_url
                break
        except Exception as exc:
            attempts.append({"requested_url": url, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})

    required = {"Club", "Country", "Level", "Elo", "From", "To"}
    columns = set(rows[0].keys()) if rows else set()
    country_counts = {}
    for country in ("ENG", "ESP", "GER", "ITA", "FRA"):
        country_counts[country] = sum(1 for row in rows if str(row.get("Country") or "") == country and str(row.get("Level") or "") == "1")

    payload = {
        "schema_version": "V5.1.5-clubelo-direct-api-readiness-r1",
        "generated_at_utc": utc_now(),
        "pit_date_tested": "2026-05-01",
        "attempts": attempts,
        "successful_url": successful_url,
        "row_count": len(rows),
        "columns": sorted(columns),
        "required_columns_present": required.issubset(columns),
        "top_level_country_counts": country_counts,
        "five_major_top_levels_present": all(count > 0 for count in country_counts.values()),
        "status": "PASS" if rows and required.issubset(columns) and all(count > 0 for count in country_counts.values()) else "FAIL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "pit_policy": "For date-only target fixtures, use ClubElo rating dated strictly before the match calendar date; never use same-day post-result rating updates."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
