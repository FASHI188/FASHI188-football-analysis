#!/usr/bin/env python3
"""Collect one timestamped historical bookmaker snapshot from The Odds API.

The collector is intentionally fail-closed:
- requires THE_ODDS_API_KEY;
- uses the provider snapshot timestamp equal to or before the requested freeze;
- writes a candidate only when one bookmaker has complete h2h/spreads/totals;
- never fabricates missing handicap/total lines;
- does not declare A-grade evidence or change model weights.

This is a low-level acquisition primitive. Bulk backfill should call it with an
audited task list and respect API quota/cost constraints.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "config" / "global_evidence_routes_v475.json"
OUT_ROOT = ROOT / "evidence" / "markets"
BASE = "https://api.the-odds-api.com/v4/historical/sports"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _iso(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("freeze time must include timezone")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_json(url: str) -> tuple[dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "football-market-v475/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        headers = {k.lower(): v for k, v in response.headers.items()}
        return json.loads(response.read().decode("utf-8")), headers


def _market_map(bookmaker: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("key")): item for item in bookmaker.get("markets") or [] if item.get("key")}


def _h2h(market: dict[str, Any], home: str, away: str) -> dict[str, float] | None:
    prices = {_norm(o.get("name")): o.get("price") for o in market.get("outcomes") or []}
    home_price = prices.get(_norm(home))
    away_price = prices.get(_norm(away))
    draw_price = prices.get("draw")
    if not all(isinstance(x, (int, float)) and x > 1 for x in (home_price, draw_price, away_price)):
        return None
    return {"home": float(home_price), "draw": float(draw_price), "away": float(away_price)}


def _spread(market: dict[str, Any], home: str, away: str) -> dict[str, float] | None:
    outcomes = market.get("outcomes") or []
    by_team = {_norm(o.get("name")): o for o in outcomes}
    h = by_team.get(_norm(home))
    a = by_team.get(_norm(away))
    if not h or not a:
        return None
    hp, ap = h.get("price"), a.get("price")
    hline, aline = h.get("point"), a.get("point")
    if not all(isinstance(x, (int, float)) for x in (hp, ap, hline, aline)):
        return None
    if hp <= 1 or ap <= 1 or abs(float(hline) + float(aline)) > 1e-9:
        return None
    return {"line": float(hline), "home": float(hp), "away": float(ap)}


def _total(market: dict[str, Any]) -> dict[str, float] | None:
    outcomes = {_norm(o.get("name")): o for o in market.get("outcomes") or []}
    over = outcomes.get("over")
    under = outcomes.get("under")
    if not over or not under:
        return None
    op, up = over.get("price"), under.get("price")
    ol, ul = over.get("point"), under.get("point")
    if not all(isinstance(x, (int, float)) for x in (op, up, ol, ul)):
        return None
    if op <= 1 or up <= 1 or abs(float(ol) - float(ul)) > 1e-9:
        return None
    return {"line": float(ol), "over": float(op), "under": float(up)}


def collect(competition_id: str, home: str, away: str, freeze_time: str, regions: str) -> dict[str, Any]:
    api_key = os.environ.get("THE_ODDS_API_KEY")
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is required; refusing to fabricate historical market data")

    routes = json.loads(ROUTES.read_text(encoding="utf-8"))
    route = (routes.get("competitions") or {}).get(competition_id)
    if not route:
        raise SystemExit(f"unknown competition_id: {competition_id}")
    sport_key = route.get("the_odds_api_sport_key")
    if not sport_key:
        raise SystemExit(f"no The Odds API sport key mapped for {competition_id}")

    freeze_iso = _iso(freeze_time)
    params = urllib.parse.urlencode({
        "apiKey": api_key,
        "regions": regions,
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
        "date": freeze_iso,
    })
    url = f"{BASE}/{urllib.parse.quote(str(sport_key), safe='')}/odds?{params}"
    payload, headers = _get_json(url)
    snapshot_time = str(payload.get("timestamp") or "")
    events = payload.get("data") or []

    event = None
    for item in events:
        if _norm(item.get("home_team")) == _norm(home) and _norm(item.get("away_team")) == _norm(away):
            event = item
            break
    if event is None:
        return {
            "status": "NO_MATCHING_EVENT_AT_SNAPSHOT",
            "competition_id": competition_id,
            "requested_freeze_time_utc": freeze_iso,
            "provider_snapshot_time_utc": snapshot_time or None,
            "home_team": home,
            "away_team": away,
            "api_quota": {
                "remaining": headers.get("x-requests-remaining"),
                "used": headers.get("x-requests-used"),
                "last": headers.get("x-requests-last"),
            },
        }

    fixture_key = str(event.get("id") or f"{competition_id}:{home}:{away}:{event.get('commence_time')}")
    rows: list[dict[str, Any]] = []
    for bookmaker in event.get("bookmakers") or []:
        markets = _market_map(bookmaker)
        one = _h2h(markets.get("h2h") or {}, home, away)
        ah = _spread(markets.get("spreads") or {}, home, away)
        ou = _total(markets.get("totals") or {})
        if not (one and ah and ou):
            continue
        observed = str(bookmaker.get("last_update") or snapshot_time)
        rows.append({
            "competition_id": competition_id,
            "fixture_key": fixture_key,
            "home_team": home,
            "away_team": away,
            "commence_time_utc": event.get("commence_time"),
            "freeze_time_utc": freeze_iso,
            "observed_at_utc": observed,
            "provider_snapshot_time_utc": snapshot_time,
            "market_observed_at_utc": {"1x2": observed, "asian_handicap": observed, "over_under": observed},
            "provider_group": "the_odds_api",
            "source_id": "the_odds_api_historical",
            "bookmaker": bookmaker.get("key") or bookmaker.get("title"),
            "bookmaker_group": bookmaker.get("key") or bookmaker.get("title"),
            "one_x_two": one,
            "asian_handicap": ah,
            "over_under": ou,
            "source_snapshot_requested_utc": freeze_iso,
        })

    if rows:
        path = OUT_ROOT / competition_id / "the_odds_api_historical.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[tuple[str, str, str], dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    existing[(str(item.get("fixture_key")), str(item.get("freeze_time_utc")), str(item.get("bookmaker_group")))] = item
        for row in rows:
            existing[(row["fixture_key"], row["freeze_time_utc"], str(row["bookmaker_group"]))] = row
        ordered = sorted(existing.values(), key=lambda x: (str(x.get("freeze_time_utc")), str(x.get("fixture_key")), str(x.get("bookmaker_group"))))
        path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ordered), encoding="utf-8")

    return {
        "status": "COMPLETE_SURFACES_WRITTEN" if rows else "EVENT_FOUND_NO_COMPLETE_1X2_AH_OU_BOOKMAKER_SURFACE",
        "competition_id": competition_id,
        "requested_freeze_time_utc": freeze_iso,
        "provider_snapshot_time_utc": snapshot_time or None,
        "fixture_key": fixture_key,
        "complete_bookmaker_surface_count": len(rows),
        "api_quota": {
            "remaining": headers.get("x-requests-remaining"),
            "used": headers.get("x-requests-used"),
            "last": headers.get("x-requests-last"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--away", required=True)
    parser.add_argument("--freeze-time", required=True, help="ISO8601 timestamp with timezone")
    parser.add_argument("--regions", default="eu,uk")
    args = parser.parse_args()
    result = collect(args.competition, args.home, args.away, args.freeze_time, args.regions)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
