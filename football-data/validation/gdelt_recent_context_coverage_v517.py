#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
import sys
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import read_processed_matches

OUT_ROOT = ROOT / "evidence" / "gdelt_context_v517"
SEASON = "2025/26"
START = "20250725000000"
END = "20260615235959"
LOOKBACK_DAYS = 7
MAXRECORDS = 250
INTER_QUERY_SECONDS = 3.0

LEAGUE_TERMS = {
    "ENG_PremierLeague": '"Premier League"',
    "ESP_LaLiga": '("La Liga" OR LaLiga)',
    "GER_Bundesliga": 'Bundesliga',
    "ITA_SerieA": '"Serie A"',
    "FRA_Ligue1": '"Ligue 1"',
}

TEAM_QUERY_ALIASES = {
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Nott'm Forest": "Nottingham Forest",
    "Newcastle": "Newcastle United",
    "Wolves": "Wolverhampton Wanderers",
    "Tottenham": "Tottenham Hotspur",
    "Ath Bilbao": "Athletic Club",
    "Ath Madrid": "Atletico Madrid",
    "Sociedad": "Real Sociedad",
    "Betis": "Real Betis",
    "Vallecano": "Rayo Vallecano",
    "Espanol": "Espanyol",
    "Celta": "Celta Vigo",
    "Oviedo": "Real Oviedo",
    "RB Leipzig": "RB Leipzig",
    "Ein Frankfurt": "Eintracht Frankfurt",
    "FC Koln": "FC Cologne",
    "M'gladbach": "Borussia Monchengladbach",
    "St Pauli": "St Pauli",
    "Inter": "Inter Milan",
    "Milan": "AC Milan",
    "Roma": "AS Roma",
    "Paris SG": "Paris Saint Germain",
}

CONTEXT_TERMS = '(injury OR injured OR suspended OR suspension OR doubtful OR "ruled out" OR rotation OR rotated OR "press conference" OR manager OR coach OR lineup OR "starting eleven" OR rested OR fatigue)'


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_seen(value: str) -> datetime | None:
    token = str(value or "").strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(token, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def request_json(query: str, retries: int = 6) -> dict[str, Any]:
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(MAXRECORDS),
        "startdatetime": START,
        "enddatetime": END,
        "sort": "datedesc",
        "format": "json",
    }
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "football-analysis-research/1.0"})
            with urllib.request.urlopen(req, timeout=45) as response:
                payload = response.read().decode("utf-8", errors="replace")
            return json.loads(payload)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code != 429:
                time.sleep(3.0 * (attempt + 1))
                continue
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = float(retry_after) if retry_after else 12.0 * (attempt + 1)
            except (TypeError, ValueError):
                delay = 12.0 * (attempt + 1)
            time.sleep(min(90.0, max(10.0, delay)))
        except Exception as exc:
            last = exc
            time.sleep(4.0 * (attempt + 1))
    raise RuntimeError(f"GDELT request failed after rate-limit-aware retries: {last}")


def evidence_id(team: str, article: dict[str, Any]) -> str:
    token = f"{team}|{article.get('url','')}|{article.get('seendate','')}|{article.get('title','')}".encode("utf-8")
    return "gdelt_" + hashlib.sha256(token).hexdigest()[:24]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True, choices=sorted(LEAGUE_TERMS))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    cid = args.competition
    accessed = utc_now()
    matches = sorted(
        [m for m in read_processed_matches(cid) if str(m.season) == SEASON],
        key=lambda m: (m.date, m.home_team, m.away_team),
    )
    if not matches:
        raise RuntimeError(f"no {SEASON} matches for {cid}")
    teams = sorted({m.home_team for m in matches} | {m.away_team for m in matches})

    team_articles: dict[str, list[dict[str, Any]]] = {}
    query_failures = {}
    for team in teams:
        search_name = TEAM_QUERY_ALIASES.get(team, team)
        query = f'"{search_name}" {LEAGUE_TERMS[cid]} {CONTEXT_TERMS}'
        try:
            payload = request_json(query)
            articles = []
            seen_urls = set()
            for article in payload.get("articles") or []:
                url = str(article.get("url") or "").strip()
                seen_dt = parse_seen(str(article.get("seendate") or ""))
                if not url or seen_dt is None or url in seen_urls:
                    continue
                seen_urls.add(url)
                articles.append({
                    "evidence_id": evidence_id(team, article),
                    "competition_id": cid,
                    "team": team,
                    "search_name": search_name,
                    "query": query,
                    "title": str(article.get("title") or ""),
                    "source_url": url,
                    "source_domain": str(article.get("domain") or ""),
                    "source_country": str(article.get("sourcecountry") or ""),
                    "language": str(article.get("language") or ""),
                    "source_observed_at_utc": seen_dt.isoformat(),
                    "published_at_utc": None,
                    "accessed_at_utc": accessed,
                    "provenance_class": "HISTORICAL_INDEPENDENT_OBSERVATION_VERIFIED_METADATA_ONLY",
                    "content_sha256": None,
                    "formal_probability_eligible": False,
                })
            team_articles[team] = sorted(articles, key=lambda row: row["source_observed_at_utc"])
        except Exception as exc:
            query_failures[team] = f"{type(exc).__name__}: {exc}"
            team_articles[team] = []
        time.sleep(INTER_QUERY_SECONDS)

    fixture_rows = []
    both_covered = either_covered = 0
    total_home_articles = total_away_articles = 0
    for match in matches:
        freeze = match.date
        window_start = freeze - timedelta(days=LOOKBACK_DAYS)
        def eligible(team: str):
            output = []
            for article in team_articles.get(team, []):
                seen = datetime.fromisoformat(article["source_observed_at_utc"])
                if window_start <= seen < freeze:
                    output.append(article)
            return output
        home = eligible(match.home_team)
        away = eligible(match.away_team)
        total_home_articles += len(home)
        total_away_articles += len(away)
        if home or away:
            either_covered += 1
        if home and away:
            both_covered += 1
        fixture_rows.append({
            "competition_id": cid,
            "season": SEASON,
            "date": match.date.date().isoformat(),
            "home_team": match.home_team,
            "away_team": match.away_team,
            "freeze_proxy_utc": freeze.isoformat(),
            "lookback_days": LOOKBACK_DAYS,
            "home_context_article_count": len(home),
            "away_context_article_count": len(away),
            "home_unique_source_count": len({a["source_domain"] for a in home if a["source_domain"]}),
            "away_unique_source_count": len({a["source_domain"] for a in away if a["source_domain"]}),
            "either_team_covered": bool(home or away),
            "both_teams_covered": bool(home and away),
        })

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    evidence_path = OUT_ROOT / f"{cid}_articles.jsonl"
    with evidence_path.open("w", encoding="utf-8") as handle:
        for team in teams:
            for article in team_articles[team]:
                handle.write(json.dumps(article, ensure_ascii=False, sort_keys=True) + "\n")
    fixture_path = OUT_ROOT / f"{cid}_fixture_coverage.jsonl"
    with fixture_path.open("w", encoding="utf-8") as handle:
        for row in fixture_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    all_articles = [article for team in teams for article in team_articles[team]]
    report = {
        "schema_version": "V5.1.7-gdelt-recent-context-coverage-domain-r2",
        "generated_at_utc": utc_now(),
        "competition_id": cid,
        "season": SEASON,
        "search_start_utc": START,
        "search_end_utc": END,
        "lookback_days": LOOKBACK_DAYS,
        "team_count": len(teams),
        "match_count": len(matches),
        "queried_team_count": len(teams),
        "query_failure_count": len(query_failures),
        "query_failures": query_failures,
        "unique_article_count": len({a["source_url"] for a in all_articles}),
        "article_record_count": len(all_articles),
        "teams_with_any_article": sum(1 for team in teams if team_articles[team]),
        "either_team_fixture_coverage_count": either_covered,
        "either_team_fixture_coverage_rate": either_covered / len(matches),
        "both_team_fixture_coverage_count": both_covered,
        "both_team_fixture_coverage_rate": both_covered / len(matches),
        "mean_home_context_articles_per_fixture": total_home_articles / len(matches),
        "mean_away_context_articles_per_fixture": total_away_articles / len(matches),
        "status": "PASS" if len(query_failures) <= 2 and either_covered / len(matches) >= 0.50 else "PARTIAL",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "timestamp_semantics": "GDELT seendate is stored as source_observed_at_utc, not publisher publication time.",
        "formal_use": "DISCOVERY_METADATA_ONLY_UNTIL_SOURCE_CONTENT_AND_TIMESTAMP_PROVEN",
        "rate_limit_policy": "Sequential league execution, three-second inter-query spacing, explicit HTTP 429 Retry-After/exponential backoff.",
        "article_output": str(evidence_path.relative_to(ROOT)),
        "fixture_output": str(fixture_path.relative_to(ROOT)),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "competition_id": cid,
        "status": report["status"],
        "article_record_count": report["article_record_count"],
        "either_team_fixture_coverage_rate": report["either_team_fixture_coverage_rate"],
        "both_team_fixture_coverage_rate": report["both_team_fixture_coverage_rate"],
        "query_failure_count": report["query_failure_count"]
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
