#!/usr/bin/env python3
"""Build and hard-audit the EPL 2024/25 goal-process chain.

Research-only. The source is pinned by commit and Git blob SHA. No API key,
provider request, production model, configuration, or CURRENT file is used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.request
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

SEASON = "2024/25"
DEFAULT_SOURCE_URL = (
    "https://raw.githubusercontent.com/harryji168/email_solutions-sports/"
    "0d9916f1c1c0679886cc5603ebd268dd31554d23/"
    "public/sports/leagues/ENG_PR/2024-2025.json"
)
DEFAULT_SOURCE_COMMIT = "0d9916f1c1c0679886cc5603ebd268dd31554d23"
DEFAULT_SOURCE_BLOB_SHA = "af481cd91dc3d6033d74dc7531fa398ca8653248"

TEAM_MAP = {
    "AFC Bournemouth": "Bournemouth",
    "Arsenal F.C.": "Arsenal",
    "Aston Villa F.C.": "Aston Villa",
    "Brentford F.C.": "Brentford",
    "Brighton & Hove Albion F.C.": "Brighton",
    "Chelsea F.C.": "Chelsea",
    "Crystal Palace F.C.": "Crystal Palace",
    "Everton F.C.": "Everton",
    "Fulham F.C.": "Fulham",
    "Ipswich Town F.C.": "Ipswich",
    "Leicester City F.C.": "Leicester",
    "Liverpool F.C.": "Liverpool",
    "Manchester City F.C.": "Man City",
    "Manchester United F.C.": "Man United",
    "Newcastle United F.C.": "Newcastle",
    "Nottingham Forest F.C.": "Nott'm Forest",
    "Southampton F.C.": "Southampton",
    "Tottenham Hotspur F.C.": "Tottenham",
    "West Ham United F.C.": "West Ham",
    "Wolverhampton Wanderers F.C.": "Wolves",
}

SCORE_RE = re.compile(r"^\s*(\d+)-(\d+)\s+\((\d+)-(\d+)\)\s*$")
GOAL_RE = re.compile(
    r"^\s*(?P<minute>\d+(?:\+\d+)?)'\s+"
    r"(?P<scorer>.+?)\s+\((?P<home>\d+)-(?P<away>\d+)\)\s*$"
)


@dataclass(frozen=True)
class GoalEvent:
    season: str
    source_match_id: str
    event_index: int
    minute_raw: str
    minute_base: int
    minute_stoppage: int
    period: int
    order_key: str
    scoring_side: str
    home_score_after: int
    away_score_after: int
    scorer_text: str
    raw_event: str


def git_blob_sha1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def read_source(source_url: str, source_file: Path | None) -> bytes:
    if source_file is not None:
        return source_file.read_bytes()
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "football-research-audit/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def parse_minute(raw: str) -> tuple[int, int, int, str]:
    if "+" in raw:
        base_text, extra_text = raw.split("+", 1)
        base = int(base_text)
        extra = int(extra_text)
    else:
        base = int(raw)
        extra = 0

    if base <= 45:
        period = 1
        phase_clock = (45 if extra else base) * 100 + extra
    else:
        period = 2
        phase_clock = (90 if extra else base) * 100 + extra
    return base, extra, period, f"{period}:{phase_clock:05d}"


def split_goal_cells(cells: Iterable[Any]) -> list[str]:
    segments: list[str] = []
    for cell in cells:
        if cell is None:
            continue
        text = str(cell).strip()
        if not text:
            continue
        for segment in re.split(r"\s*<br\s*/?>\s*", text, flags=re.IGNORECASE):
            if segment.strip():
                segments.append(segment.strip())
    return segments


def parse_score(score: str) -> tuple[int, int, int, int]:
    match = SCORE_RE.match(score)
    if not match:
        raise ValueError(f"unparseable score: {score!r}")
    return tuple(int(value) for value in match.groups())  # type: ignore[return-value]


def parse_match_events(match: dict[str, Any], final_home: int, final_away: int) -> list[GoalEvent]:
    source_match_id = str(match["matchId"])
    raw_events = split_goal_cells(match.get("goals", []))
    events: list[GoalEvent] = []
    previous_home = 0
    previous_away = 0
    previous_order: tuple[int, int] | None = None

    for index, raw_event in enumerate(raw_events, start=1):
        parsed = GOAL_RE.match(raw_event)
        if not parsed:
            raise ValueError(f"{source_match_id}: unparseable goal event: {raw_event!r}")
        minute_raw = parsed.group("minute")
        minute_base, minute_stoppage, period, order_key = parse_minute(minute_raw)
        current_order = tuple(int(x) for x in order_key.split(":"))
        if previous_order is not None and current_order < previous_order:
            raise ValueError(
                f"{source_match_id}: non-monotone event time: {raw_event!r} after {events[-1].raw_event!r}"
            )
        previous_order = current_order

        home_after = int(parsed.group("home"))
        away_after = int(parsed.group("away"))
        home_delta = home_after - previous_home
        away_delta = away_after - previous_away
        if (home_delta, away_delta) == (1, 0):
            scoring_side = "H"
        elif (home_delta, away_delta) == (0, 1):
            scoring_side = "A"
        else:
            raise ValueError(
                f"{source_match_id}: invalid score transition "
                f"{previous_home}-{previous_away} -> {home_after}-{away_after}"
            )

        events.append(
            GoalEvent(
                season=SEASON,
                source_match_id=source_match_id,
                event_index=index,
                minute_raw=minute_raw,
                minute_base=minute_base,
                minute_stoppage=minute_stoppage,
                period=period,
                order_key=order_key,
                scoring_side=scoring_side,
                home_score_after=home_after,
                away_score_after=away_after,
                scorer_text=parsed.group("scorer").strip(),
                raw_event=raw_event,
            )
        )
        previous_home, previous_away = home_after, away_after

    if len(events) != final_home + final_away:
        raise ValueError(
            f"{source_match_id}: event count {len(events)} != final goals {final_home + final_away}"
        )
    if events:
        last = events[-1]
        if (last.home_score_after, last.away_score_after) != (final_home, final_away):
            raise ValueError(
                f"{source_match_id}: final replay {last.home_score_after}-{last.away_score_after} "
                f"!= result {final_home}-{final_away}"
            )
    elif (final_home, final_away) != (0, 0):
        raise ValueError(f"{source_match_id}: non-zero final score has no parsed goal events")

    return events


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build(payload: bytes, expected_blob_sha: str, output_dir: Path, source_url: str) -> dict[str, Any]:
    actual_blob_sha = git_blob_sha1(payload)
    if actual_blob_sha != expected_blob_sha:
        raise ValueError(
            f"source blob SHA mismatch: expected {expected_blob_sha}, actual {actual_blob_sha}"
        )

    raw = json.loads(payload.decode("utf-8"))
    if not isinstance(raw, list):
        raise ValueError("source root must be a list")

    match_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    match_ids: list[str] = []
    fixture_keys: list[tuple[str, str]] = []
    appearances: Counter[str] = Counter()
    home_counts: Counter[str] = Counter()
    away_counts: Counter[str] = Counter()
    result_counts: Counter[str] = Counter()
    total_goals = 0
    zero_zero_count = 0

    for source_index, match in enumerate(raw, start=1):
        if not isinstance(match, dict):
            raise ValueError(f"row {source_index}: match must be an object")
        required = {
            "date", "time", "group", "homeTeam", "awayTeam", "score", "matchId", "goals", "status"
        }
        missing = sorted(required - set(match))
        if missing:
            raise ValueError(f"row {source_index}: missing fields {missing}")
        if match["status"] != "FT":
            raise ValueError(f"{match['matchId']}: status is not FT: {match['status']!r}")
        if match["group"] != "ENG PR":
            raise ValueError(f"{match['matchId']}: unexpected group {match['group']!r}")

        home_raw = str(match["homeTeam"])
        away_raw = str(match["awayTeam"])
        if home_raw not in TEAM_MAP or away_raw not in TEAM_MAP:
            raise ValueError(f"{match['matchId']}: unmapped team {home_raw!r} / {away_raw!r}")
        home = TEAM_MAP[home_raw]
        away = TEAM_MAP[away_raw]
        if home == away:
            raise ValueError(f"{match['matchId']}: identical home and away team")

        final_home, final_away, ht_home, ht_away = parse_score(str(match["score"]))
        if ht_home > final_home or ht_away > final_away:
            raise ValueError(f"{match['matchId']}: halftime score exceeds final score")

        events = parse_match_events(match, final_home, final_away)
        event_rows.extend(asdict(event) for event in events)

        source_date = date.fromisoformat(str(match["date"]))
        match_id = str(match["matchId"])
        match_ids.append(match_id)
        fixture_keys.append((home, away))
        appearances.update((home, away))
        home_counts[home] += 1
        away_counts[away] += 1
        total_goals += final_home + final_away
        if final_home == final_away:
            result_counts["D"] += 1
        elif final_home > final_away:
            result_counts["H"] += 1
        else:
            result_counts["A"] += 1
        if final_home == final_away == 0:
            zero_zero_count += 1

        match_rows.append(
            {
                "season": SEASON,
                "source_commit": DEFAULT_SOURCE_COMMIT,
                "source_blob_sha": actual_blob_sha,
                "source_match_id": match_id,
                "source_index": source_index,
                "date_source": source_date.isoformat(),
                "date_alt_minus_1": (source_date - timedelta(days=1)).isoformat(),
                "time_source": str(match["time"]),
                "home_team_raw": home_raw,
                "away_team_raw": away_raw,
                "home_team": home,
                "away_team": away,
                "home_goals": final_home,
                "away_goals": final_away,
                "halftime_home_goals": ht_home,
                "halftime_away_goals": ht_away,
                "result_1x2": "D" if final_home == final_away else ("H" if final_home > final_away else "A"),
                "goal_event_count": len(events),
                "status": str(match["status"]),
            }
        )

    match_rows.sort(key=lambda row: (row["date_source"], row["time_source"], row["source_match_id"]))
    match_order = {row["source_match_id"]: index for index, row in enumerate(match_rows)}
    event_rows.sort(key=lambda row: (match_order[row["source_match_id"]], row["event_index"]))

    checks: dict[str, bool] = {
        "match_count_380": len(match_rows) == 380,
        "unique_match_ids_380": len(set(match_ids)) == 380,
        "unique_home_away_fixtures_380": len(set(fixture_keys)) == 380,
        "team_count_20": len(appearances) == 20,
        "each_team_38_matches": all(value == 38 for value in appearances.values()),
        "each_team_19_home": all(value == 19 for value in home_counts.values()),
        "each_team_19_away": all(value == 19 for value in away_counts.values()),
        "all_status_ft": all(row["status"] == "FT" for row in match_rows),
        "event_count_equals_total_goals": len(event_rows) == total_goals,
        "all_scores_replayed": True,
        "zero_zero_included": zero_zero_count > 0,
        "probability_or_model_files_touched": False,
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)

    match_fields = [
        "season", "source_commit", "source_blob_sha", "source_match_id", "source_index",
        "date_source", "date_alt_minus_1", "time_source", "home_team_raw", "away_team_raw",
        "home_team", "away_team", "home_goals", "away_goals", "halftime_home_goals",
        "halftime_away_goals", "result_1x2", "goal_event_count", "status",
    ]
    event_fields = list(GoalEvent.__dataclass_fields__)
    write_csv(output_dir / "matches.csv", match_rows, match_fields)
    write_csv(output_dir / "goal_events.csv", event_rows, event_fields)

    audit = {
        "audit_id": "R34_POLICY_2425_GOAL_CHAIN_AUDIT_V1",
        "status": "PASS" if not failed_checks else "FAIL",
        "research_only": True,
        "formal_weight": 0,
        "source": {
            "url": source_url,
            "commit": DEFAULT_SOURCE_COMMIT,
            "expected_git_blob_sha1": expected_blob_sha,
            "actual_git_blob_sha1": actual_blob_sha,
        },
        "counts": {
            "matches": len(match_rows),
            "unique_match_ids": len(set(match_ids)),
            "teams": len(appearances),
            "goal_events": len(event_rows),
            "total_goals_from_results": total_goals,
            "home_wins": result_counts["H"],
            "draws": result_counts["D"],
            "away_wins": result_counts["A"],
            "zero_zero_draws": zero_zero_count,
        },
        "team_appearances": dict(sorted(appearances.items())),
        "team_home_counts": dict(sorted(home_counts.items())),
        "team_away_counts": dict(sorted(away_counts.items())),
        "checks": checks,
        "failed_checks": failed_checks,
        "date_note": (
            "Source dates/times are preserved verbatim. date_alt_minus_1 is emitted only as a join candidate; "
            "market linkage must use team identity plus a +/-1 day date tolerance and must not silently rewrite dates."
        ),
        "scope_note": (
            "This audit builds historical event evidence only. It does not train, tune, score, promote, "
            "or modify any formal football model, configuration, data registry, or CURRENT rule file."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--expected-git-blob-sha", default=DEFAULT_SOURCE_BLOB_SHA)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = read_source(args.source_url, args.source_file)
        audit = build(payload, args.expected_git_blob_sha, args.output_dir, args.source_url)
    except Exception as exc:
        print(f"R34_POLICY_2425_GOAL_CHAIN_AUDIT_FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if audit["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
