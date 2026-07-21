#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import gdelt_recent_context_coverage_v517 as core
from platform_core import canonical_team_name, load_aliases

_ORIGINAL_READ = core.read_processed_matches
AUDIT: dict[str, object] = {}


def _timed_matches(competition_id: str):
    matches = _ORIGINAL_READ(competition_id)
    aliases = load_aliases()
    path = ROOT / "processed" / competition_id / "2025-26.csv"
    exact: dict[tuple[str, str, str], datetime] = {}
    parse_failures = []

    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for line_no, row in enumerate(csv.DictReader(handle), start=2):
                if str(row.get("season") or row.get("Season") or "") != core.SEASON:
                    continue
                raw_date = str(row.get("Date") or "").strip()
                raw_time = str(row.get("Time") or "").strip()
                raw_home = str(row.get("HomeTeam") or "").strip()
                raw_away = str(row.get("AwayTeam") or "").strip()
                if not raw_date or not raw_time or not raw_home or not raw_away:
                    continue
                try:
                    # Cross-domain 2025/26 audit against official EPL, LaLiga,
                    # Bundesliga, Serie A and Ligue 1 kickoff announcements shows
                    # Football-Data Time is expressed in UK civil time.
                    local = datetime.strptime(
                        f"{raw_date} {raw_time}", "%d/%m/%Y %H:%M"
                    ).replace(tzinfo=ZoneInfo("Europe/London"))
                    kickoff = local.astimezone(timezone.utc)
                    home = canonical_team_name(competition_id, raw_home, aliases)
                    away = canonical_team_name(competition_id, raw_away, aliases)
                    key = (kickoff.date().isoformat(), home, away)
                    if key in exact:
                        raise RuntimeError(f"duplicate kickoff identity {key}")
                    exact[key] = kickoff
                except Exception as exc:
                    parse_failures.append({
                        "line": line_no,
                        "date": raw_date,
                        "time": raw_time,
                        "home": raw_home,
                        "away": raw_away,
                        "error": f"{type(exc).__name__}: {exc}",
                    })

    output = []
    exact_count = 0
    fallback_count = 0
    fallback_examples = []
    for match in matches:
        key = (match.date.date().isoformat(), match.home_team, match.away_team)
        kickoff = exact.get(key)
        if kickoff is None:
            fallback_count += 1
            if len(fallback_examples) < 10:
                fallback_examples.append({
                    "date": match.date.date().isoformat(),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                })
            output.append(match)
        else:
            exact_count += 1
            output.append(replace(match, date=kickoff))

    AUDIT.clear()
    AUDIT.update({
        "competition_id": competition_id,
        "season": core.SEASON,
        "processed_time_source": str(path.relative_to(ROOT)) if path.exists() else None,
        "time_zone_interpretation": "Europe/London",
        "exact_kickoff_count": exact_count,
        "fallback_midnight_count": fallback_count,
        "parse_failure_count": len(parse_failures),
        "parse_failure_examples": parse_failures[:10],
        "fallback_examples": fallback_examples,
        "audit_basis": [
            "Liverpool-Bournemouth 2025-08-15: Football-Data 20:00; official Premier League/Liverpool 20:00 BST",
            "Girona-Rayo 2025-08-15: Football-Data 18:00; official LaLiga 19:00 Spain local = 18:00 BST",
            "Bayern-Leipzig 2025-08-22: Football-Data 19:30; official 20:30 CEST = 19:30 BST",
            "Genoa-Lecce 2025-08-23: Football-Data 17:30; official 18:30 CEST = 17:30 BST",
            "Rennes-Marseille 2025-08-15: Football-Data 19:45; official 20:45 CEST = 19:45 BST"
        ],
    })
    return output


core.read_processed_matches = _timed_matches


def main() -> int:
    rc = int(core.main())
    try:
        out_index = sys.argv.index("--out") + 1
        out_path = Path(sys.argv[out_index])
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        payload["kickoff_time_audit"] = dict(AUDIT)
        if int(AUDIT.get("fallback_midnight_count") or 0) > 0:
            payload["status"] = "PARTIAL"
        payload["freeze_time_policy"] = (
            "Use Football-Data Date+Time interpreted as Europe/London and converted to UTC; "
            "fall back to UTC midnight only when exact Time is unavailable, with explicit audit."
        )
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"kickoff_audit_postprocess_error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
