#!/usr/bin/env python3
"""Zero-label public availability coverage collector for ENG Premier League 2026/27 MW1.

Research-only. This collector is intentionally independent from market evidence.

It freezes raw public responses from:
- official Fantasy Premier League bootstrap JSON;
- official Premier League injury page;
- official Premier League suspension page.

The scientific target is not evaluated here. The only question is whether all 20
fixture teams can be bound to a timestamped, reproducible pre-kickoff availability
snapshot without secrets, paid providers, result labels, training, or scoring.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "research" / "eng_pl_2026_27_mw1_fixture_freeze_20260816.json"
DEFAULT_OUT = ROOT / "research" / "artifacts" / "xi_availability_public_coverage_r1"

FPL_BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
PL_INJURIES = "https://www.premierleague.com/en/latest-player-injuries"
PL_SUSPENSIONS = (
    "https://www.premierleague.com/en/news/4425344/"
    "which-players-are-suspended-or-close-to-a-ban-in-fantasy"
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


class GateError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise GateError(f"{path}: expected JSON object")
    return data


def fetch(url: str) -> dict[str, Any]:
    observed_at = utc_now()
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=30) as resp:  # nosec B310 - fixed official HTTPS URLs only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        content_type = str(resp.headers.get("content-type") or "")
        final_url = str(resp.geturl())
    retrieved_at = utc_now()
    if status != 200:
        raise GateError(f"HTTP {status}: {url}")
    return {
        "requested_url": url,
        "final_url": final_url,
        "observed_at_utc": observed_at,
        "retrieved_at_utc": retrieved_at,
        "content_type": content_type,
        "sha256": sha256(raw),
        "raw": raw,
    }


def write_raw(out: Path, name: str, item: dict[str, Any]) -> str:
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_bytes(item["raw"])
    return str(path.relative_to(ROOT))


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    fixtures = load_json(args.fixtures)
    if fixtures.get("label_access") is not False:
        raise GateError("fixture freeze must explicitly set label_access=false")
    rows = fixtures.get("fixtures")
    expected_codes = fixtures.get("expected_team_short_names")
    if not isinstance(rows, list) or len(rows) != 10:
        raise GateError("fixture freeze must contain exactly 10 MW1 fixtures")
    if not isinstance(expected_codes, list) or len(set(expected_codes)) != 20:
        raise GateError("fixture freeze must contain exactly 20 unique expected team codes")

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    sources: dict[str, dict[str, Any]] = {}
    source_errors: dict[str, str] = {}
    for key, url, filename in (
        ("fpl_bootstrap", FPL_BOOTSTRAP, "fpl_bootstrap.json"),
        ("pl_injuries", PL_INJURIES, "pl_injuries.html"),
        ("pl_suspensions", PL_SUSPENSIONS, "pl_suspensions.html"),
    ):
        try:
            item = fetch(url)
            item["raw_path"] = write_raw(out, filename, item)
            item.pop("raw", None)
            sources[key] = item
        except Exception as exc:
            source_errors[key] = f"{type(exc).__name__}: {exc}"

    audit: dict[str, Any] = {
        "schema_version": "xi-availability-public-coverage-r1",
        "research_only": True,
        "label_access": False,
        "generated_at_utc": utc_now(),
        "fixture_freeze_path": str(args.fixtures.relative_to(ROOT)),
        "source_snapshots": sources,
        "source_errors": source_errors,
        "team_rows": [],
        "fixture_rows": [],
        "hard_violations": [],
    }

    fpl_path = out / "raw" / "fpl_bootstrap.json"
    if not fpl_path.exists():
        audit["hard_violations"].append("FPL_BOOTSTRAP_UNAVAILABLE")
    else:
        try:
            fpl = json.loads(fpl_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise GateError(f"invalid FPL JSON: {exc}") from exc

        teams = fpl.get("teams") if isinstance(fpl, dict) else None
        elements = fpl.get("elements") if isinstance(fpl, dict) else None
        if not isinstance(teams, list) or not isinstance(elements, list):
            audit["hard_violations"].append("FPL_SCHEMA_MISSING_TEAMS_OR_ELEMENTS")
            teams, elements = [], []

        by_code: dict[str, dict[str, Any]] = {}
        for team in teams:
            if not isinstance(team, dict):
                continue
            code = str(team.get("short_name") or "").strip().upper()
            if code:
                by_code[code] = team

        observed_codes = sorted(by_code)
        expected = sorted(str(x).upper() for x in expected_codes)
        audit["fpl_observed_team_codes"] = observed_codes
        audit["expected_team_codes"] = expected
        missing = sorted(set(expected) - set(observed_codes))
        extras = sorted(set(observed_codes) - set(expected))
        if missing:
            audit["hard_violations"].append(f"FPL_EXPECTED_TEAMS_MISSING:{','.join(missing)}")
        if extras:
            audit["hard_violations"].append(f"FPL_UNEXPECTED_TEAMS_PRESENT:{','.join(extras)}")

        players_by_team_id: dict[int, list[dict[str, Any]]] = {}
        for player in elements:
            if not isinstance(player, dict):
                continue
            tid = safe_int(player.get("team"))
            if tid is None:
                continue
            players_by_team_id.setdefault(tid, []).append(player)

        for code in expected:
            team = by_code.get(code)
            if not team:
                continue
            tid = safe_int(team.get("id"))
            player_rows = players_by_team_id.get(tid or -1, [])
            if not player_rows:
                audit["hard_violations"].append(f"FPL_TEAM_HAS_NO_PLAYERS:{code}")
            availability = []
            flagged = 0
            latest_news_added = None
            for p in player_rows:
                status = str(p.get("status") or "").strip()
                news = str(p.get("news") or "").strip()
                news_added = p.get("news_added")
                if status and status != "a":
                    flagged += 1
                if news:
                    flagged += 1
                if isinstance(news_added, str) and news_added:
                    if latest_news_added is None or news_added > latest_news_added:
                        latest_news_added = news_added
                availability.append(
                    {
                        "player_id": p.get("id"),
                        "player_name": p.get("web_name") or p.get("second_name"),
                        "status": status or None,
                        "news": news or None,
                        "news_added": news_added,
                        "chance_of_playing_this_round": p.get("chance_of_playing_this_round"),
                        "chance_of_playing_next_round": p.get("chance_of_playing_next_round"),
                    }
                )
            audit["team_rows"].append(
                {
                    "team_code": code,
                    "team_id": tid,
                    "team_name": team.get("name"),
                    "player_count": len(availability),
                    "flagged_availability_rows": flagged,
                    "latest_news_added": latest_news_added,
                    "availability": availability,
                }
            )

        team_row_by_code = {row["team_code"]: row for row in audit["team_rows"]}
        fpl_source = sources.get("fpl_bootstrap") or {}
        for fixture in rows:
            if not isinstance(fixture, dict):
                audit["hard_violations"].append("NON_OBJECT_FIXTURE")
                continue
            home_code = str(fixture.get("home_short") or "").upper()
            away_code = str(fixture.get("away_short") or "").upper()
            home = team_row_by_code.get(home_code)
            away = team_row_by_code.get(away_code)
            fixture_violations = []
            if home is None:
                fixture_violations.append(f"HOME_TEAM_UNBOUND:{home_code}")
            if away is None:
                fixture_violations.append(f"AWAY_TEAM_UNBOUND:{away_code}")
            audit["fixture_rows"].append(
                {
                    "match_id": fixture.get("match_id"),
                    "kickoff_at_utc": fixture.get("kickoff_at_utc"),
                    "home_team": fixture.get("home_team"),
                    "away_team": fixture.get("away_team"),
                    "home_short": home_code,
                    "away_short": away_code,
                    "home_player_count": home.get("player_count") if home else 0,
                    "away_player_count": away.get("player_count") if away else 0,
                    "source_name": "Fantasy Premier League bootstrap-static",
                    "source_url": FPL_BOOTSTRAP,
                    "source_tier": "OFFICIAL_LEAGUE_STRUCTURED_PUBLIC",
                    "source_observed_at_utc": fpl_source.get("observed_at_utc"),
                    "source_retrieved_at_utc": fpl_source.get("retrieved_at_utc"),
                    "source_sha256": fpl_source.get("sha256"),
                    "violations": fixture_violations,
                }
            )

    all_sources_fetched = len(sources) == 3 and not source_errors
    all_fixtures_bound = (
        len(audit["fixture_rows"]) == 10
        and all(not row["violations"] for row in audit["fixture_rows"])
    )
    no_hard_violations = not audit["hard_violations"]
    audit["coverage"] = {
        "expected_teams": 20,
        "bound_teams": len(audit["team_rows"]),
        "expected_fixtures": 10,
        "bound_fixtures": sum(1 for x in audit["fixture_rows"] if not x["violations"]),
        "all_sources_fetched": all_sources_fetched,
        "all_fixtures_bound": all_fixtures_bound,
    }
    audit["verdict"] = (
        "PASS_ZERO_LABEL_PUBLIC_AVAILABILITY_COVERAGE"
        if all_sources_fetched and all_fixtures_bound and no_hard_violations
        else "STOP_DATA_COVERAGE"
    )
    audit["scientific_claim"] = "NONE"
    audit["next_if_pass"] = (
        "Freeze repeated pre-kickoff availability snapshots and separately validate "
        "confirmed-XI capture near lineup release; do not read result labels yet."
    )
    audit["next_if_stop"] = (
        "Inspect source/schema/season mismatch; do not read result labels, train, or score."
    )

    status_path = out / "coverage_status.json"
    status_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "verdict": audit["verdict"],
        "coverage": audit["coverage"],
        "hard_violations": audit["hard_violations"],
        "source_errors": audit["source_errors"],
        "status_path": str(status_path.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if audit["verdict"] == "PASS_ZERO_LABEL_PUBLIC_AVAILABILITY_COVERAGE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
