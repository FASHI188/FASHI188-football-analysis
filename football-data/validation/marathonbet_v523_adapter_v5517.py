#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from direct_marathonbet_html_probe_v5516 import extract_text
from prospective_market_snapshot_v523 import canonical_sha256, validate

MARATHONBET_EN_TIMEZONE = ZoneInfo("Europe/London")
FLOAT = r"([0-9]+(?:\.[0-9]+)?)"
LINE = r"([+-]?[0-9]+(?:\.[0-9]+)?|0)"


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp missing timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _utc(value: str) -> str:
    return _dt(value).replace(microsecond=0).isoformat()


def _name_pattern(value: str) -> str:
    parts = [re.escape(x) for x in str(value).split() if x]
    return r"\s+".join(parts)


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _positive_odds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 1.0:
        raise ValueError(f"invalid decimal odds: {value}")
    return number


def _quarter_line(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"invalid line: {value}")
    if abs(number * 4.0 - round(number * 4.0)) > 1e-9:
        raise ValueError(f"line is not quarter increment: {value}")
    return number


def _page_datetime_to_utc(token: str, year: int) -> str:
    local_naive = datetime.strptime(f"{year} {token}", "%Y %d %b %H:%M")
    local = local_naive.replace(tzinfo=MARATHONBET_EN_TIMEZONE)
    return local.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_fixture(raw_html: bytes, *, home_team: str, away_team: str, target_kickoff_utc: str) -> dict[str, Any]:
    text = _compact(extract_text(raw_html))
    home = _name_pattern(home_team)
    away = _name_pattern(away_team)
    head = re.search(
        rf"{home}\s+—\s+{away}\s+(\d{{1,2}}\s+[A-Za-z]{{3}}\s+\d{{1,2}}:\d{{2}})",
        text,
        flags=re.IGNORECASE,
    )
    if not head:
        raise ValueError("exact Marathonbet target fixture header not found")
    displayed_time = head.group(1)
    target_dt = _dt(target_kickoff_utc)
    page_kickoff_utc = _page_datetime_to_utc(displayed_time, target_dt.year)
    kickoff_skew = abs((_dt(page_kickoff_utc) - target_dt).total_seconds())
    if kickoff_skew > 60.0:
        raise ValueError(
            f"Marathonbet /en/ timezone gate failed: displayed={displayed_time}, converted={page_kickoff_utc}, target={target_kickoff_utc}, skew={kickoff_skew}"
        )

    segment = text[head.start() : head.start() + 1800]
    one = re.search(
        rf"{home}\s+to\s+Win\s+{FLOAT}\s+Draw\s+{FLOAT}\s+{away}\s+to\s+Win\s+{FLOAT}",
        segment,
        flags=re.IGNORECASE,
    )
    if not one:
        raise ValueError("exact Match Result 1X2 block not found")

    ah = re.search(
        rf"{home}\s+\({LINE}\)\s+{FLOAT}\s+{away}\s+\({LINE}\)\s+{FLOAT}",
        segment,
        flags=re.IGNORECASE,
    )
    if not ah:
        raise ValueError("exact two-sided handicap block not found")

    ou = re.search(
        rf"Under\s+{FLOAT}\s+{FLOAT}\s+Over\s+{FLOAT}\s+{FLOAT}",
        segment,
        flags=re.IGNORECASE,
    )
    if not ou:
        raise ValueError("exact two-sided total-goals block not found")

    home_line = _quarter_line(ah.group(1))
    home_ah_odds = _positive_odds(ah.group(2))
    away_line = _quarter_line(ah.group(3))
    away_ah_odds = _positive_odds(ah.group(4))
    if abs(home_line + away_line) > 1e-9:
        raise ValueError(f"handicap lines are not opposite: {home_line}, {away_line}")

    under_line = _quarter_line(ou.group(1))
    under_odds = _positive_odds(ou.group(2))
    over_line = _quarter_line(ou.group(3))
    over_odds = _positive_odds(ou.group(4))
    if abs(under_line - over_line) > 1e-9:
        raise ValueError(f"OU lines differ: under={under_line}, over={over_line}")

    return {
        "displayed_time": displayed_time,
        "display_timezone": "Europe/London",
        "page_kickoff_utc": page_kickoff_utc,
        "kickoff_skew_seconds": kickoff_skew,
        "one_x_two": {
            "home": _positive_odds(one.group(1)),
            "draw": _positive_odds(one.group(2)),
            "away": _positive_odds(one.group(3)),
        },
        "asian_handicap": {
            "line": home_line,
            "home": home_ah_odds,
            "away": away_ah_odds,
            "away_line": away_line,
        },
        "over_under": {
            "line": under_line,
            "over": over_odds,
            "under": under_odds,
        },
        "raw_fixture_context": segment[:1200],
    }


def build_snapshot(
    raw_html: bytes,
    metadata: dict[str, Any],
    *,
    competition_id: str,
    season: str,
    home_team: str,
    away_team: str,
    kickoff_utc: str,
) -> dict[str, Any]:
    observed = _utc(str(metadata.get("observed_at_utc") or ""))
    kickoff = _utc(kickoff_utc)
    if not _dt(observed) < _dt(kickoff):
        raise ValueError("Marathonbet observation must precede kickoff")
    requested_url = str(metadata.get("requested_url") or "")
    final_url = str(metadata.get("final_url") or "")
    if "/en/" not in requested_url or "/en/" not in final_url:
        raise ValueError("Marathonbet timezone contract requires /en/ page")
    expected_sha = str(metadata.get("raw_html_sha256") or "")
    actual_sha = hashlib.sha256(raw_html).hexdigest()
    if not expected_sha or expected_sha != actual_sha:
        raise ValueError("raw HTML sha256 mismatch against immutable metadata")

    parsed = parse_fixture(raw_html, home_team=home_team, away_team=away_team, target_kickoff_utc=kickoff)
    ah = parsed["asian_handicap"]
    ou = parsed["over_under"]
    payload: dict[str, Any] = {
        "competition_id": competition_id,
        "season": season,
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff,
        "settlement_scope": "90m_including_stoppage",
        "freeze_utc": observed,
        "accessed_at_utc": observed,
        "source_observed_at_utc": observed,
        "surface_observed_at_utc": {
            "one_x_two": observed,
            "asian_handicap": observed,
            "over_under": observed,
        },
        "source_url": final_url,
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "one_x_two": parsed["one_x_two"],
        "asian_handicap": {"line": ah["line"], "home": ah["home"], "away": ah["away"]},
        "over_under": {"line": ou["line"], "over": ou["over"], "under": ou["under"]},
        "source_adapter": {
            "schema_version": "V5.5.17-marathonbet-v523-adapter-r1",
            "parent_raw_html_sha256": actual_sha,
            "parent_metadata_sha": str(metadata.get("extracted_text_sha256") or ""),
            "requested_url": requested_url,
            "final_url": final_url,
            "html_locale": "en",
            "display_timezone": parsed["display_timezone"],
            "displayed_kickoff": parsed["displayed_time"],
            "displayed_kickoff_converted_utc": parsed["page_kickoff_utc"],
            "kickoff_skew_seconds": parsed["kickoff_skew_seconds"],
            "handicap_away_line_audit": ah["away_line"],
            "parsing_policy": "Exact fixture header, exact Match Result, exact two-sided handicap and exact two-sided Total Goals from the same first-party HTML response; no cross-page or cross-provider splicing.",
        },
        "observation_semantics": {
            "source_observed_at_utc": "actual GitHub Runner observation time of the direct first-party Marathonbet HTML response",
            "surface_observed_at_utc": "identical observation timestamp because all three market surfaces are parsed from one immutable HTML response",
            "retrospective_backfill": False,
        },
    }
    payload["raw_snapshot_sha256"] = canonical_sha256(payload)
    result = validate(payload)
    if not result.get("passed") or not result.get("formal_pit_eligible"):
        raise ValueError(f"V5.2.3 hard gate failed: {result.get('errors')}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_html")
    parser.add_argument("metadata_json")
    parser.add_argument("--competition-id", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--home-team", required=True)
    parser.add_argument("--away-team", required=True)
    parser.add_argument("--kickoff-utc", required=True)
    args = parser.parse_args()

    raw = Path(args.raw_html).read_bytes()
    metadata = json.loads(Path(args.metadata_json).read_text(encoding="utf-8"))
    snapshot = build_snapshot(
        raw,
        metadata,
        competition_id=args.competition_id,
        season=args.season,
        home_team=args.home_team,
        away_team=args.away_team,
        kickoff_utc=args.kickoff_utc,
    )
    print(json.dumps({
        "status": "V523_MARATHONBET_ADAPTER_PASS",
        "formal_pit_eligible": validate(snapshot).get("formal_pit_eligible"),
        "one_x_two": snapshot["one_x_two"],
        "asian_handicap": snapshot["asian_handicap"],
        "over_under": snapshot["over_under"],
        "source_adapter": snapshot["source_adapter"],
        "raw_snapshot_sha256": snapshot["raw_snapshot_sha256"],
        "snapshot_written": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
