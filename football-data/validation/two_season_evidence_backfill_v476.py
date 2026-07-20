#!/usr/bin/env python3
"""Audit actual evidence coverage inside the mandatory recent-two-season window.

This is a data-coverage audit, not a model promotion tool. It keeps four evidence
classes separate:
  1) observed starting-XI labels (post-match labels may qualify),
  2) true pre-match point-in-time lineup evidence,
  3) true pre-match point-in-time injury/suspension evidence,
  4) timestamped synchronized 1X2/AH/OU market snapshots.

Missing evidence is reported explicitly and is never inferred or manually filled.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import (  # noqa: E402
    canonical_team_name,
    load_aliases,
    normalize_team_token,
    read_processed_matches,
)

SCOPE_PATH = ROOT / "config" / "two_season_evidence_scope_v476.json"
LINEUP_ROOT = ROOT / "evidence" / "lineups"
INJURY_ROOT = ROOT / "evidence" / "injuries"
MARKET_ROOT = ROOT / "evidence" / "markets"
OUT = ROOT / "manifests" / "two_season_evidence_backfill_v476_status.json"
REPORT = ROOT / "validation" / "reports" / "two_season_evidence_v476" / "summary.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_jsonl(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL {path}:{line_no}: {exc}") from exc
                if isinstance(row, dict):
                    yield row


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date_token(value: Any) -> str | None:
    dt = _parse_time(value)
    return dt.date().isoformat() if dt else None


def _logical_season(raw: Any, scope: dict[str, Any]) -> str | None:
    token = str(raw or "").strip()
    mandatory = [str(x) for x in scope.get("mandatory_seasons", [])]
    if token in mandatory:
        return token
    aliases = scope.get("accepted_evidence_season_aliases") or {}
    for logical, values in aliases.items():
        if token in {str(v) for v in values}:
            return str(logical)
    return None


def _expected_labels(competition_id: str, scope: dict[str, Any]) -> tuple[set[tuple[str, str, str]], dict[str, int]]:
    aliases = load_aliases()
    labels: set[tuple[str, str, str]] = set()
    matches_per_season: Counter[str] = Counter()
    for match in read_processed_matches(competition_id):
        logical = _logical_season(match.season, scope)
        if logical is None:
            continue
        date = match.date.date().isoformat()
        home = normalize_team_token(canonical_team_name(competition_id, match.home_team, aliases))
        away = normalize_team_token(canonical_team_name(competition_id, match.away_team, aliases))
        labels.add((logical, date, home))
        labels.add((logical, date, away))
        matches_per_season[logical] += 1
    return labels, dict(matches_per_season)


def _lineup_coverage(competition_id: str, scope: dict[str, Any], expected: set[tuple[str, str, str]]) -> dict[str, Any]:
    aliases = load_aliases()
    observed: set[tuple[str, str, str]] = set()
    pit: set[tuple[str, str, str]] = set()
    source_groups: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    invalid = 0
    scoped_rows = 0

    paths = sorted((LINEUP_ROOT / competition_id).glob("*.jsonl")) if (LINEUP_ROOT / competition_id).exists() else []
    for row in _iter_jsonl(paths):
        logical = _logical_season(row.get("season"), scope)
        if logical is None:
            continue
        scoped_rows += 1
        starters = row.get("starters")
        if not isinstance(starters, list) or len(starters) != 11 or len({str(x) for x in starters}) != 11:
            invalid += 1
            continue
        date = _date_token(row.get("kickoff_utc"))
        team_raw = str(row.get("team") or "").strip()
        if not date or not team_raw:
            invalid += 1
            continue
        team = normalize_team_token(canonical_team_name(competition_id, team_raw, aliases))
        key = (logical, date, team)
        observed.add(key)
        if row.get("source_group"):
            source_groups[key].add(str(row["source_group"]))

        kickoff = _parse_time(row.get("kickoff_utc"))
        observed_at = _parse_time(row.get("source_observed_at_utc") or row.get("observed_at_utc"))
        if kickoff and observed_at and observed_at <= kickoff:
            pit.add(key)

    matched_observed = expected & observed
    matched_pit = expected & pit
    dual_source = {key for key, groups in source_groups.items() if len(groups) >= 2}
    missing = sorted(expected - observed)

    denominator = len(expected)
    return {
        "scoped_rows": scoped_rows,
        "invalid_rows": invalid,
        "expected_team_match_labels": denominator,
        "observed_unique_labels": len(observed),
        "matched_observed_labels": len(matched_observed),
        "observed_coverage": (len(matched_observed) / denominator) if denominator else None,
        "pit_unique_labels": len(pit),
        "matched_pit_labels": len(matched_pit),
        "pit_coverage": (len(matched_pit) / denominator) if denominator else None,
        "dual_source_observed_labels": len(dual_source & expected),
        "missing_observed_label_samples": [list(x) for x in missing[:25]],
    }


def _injury_coverage(competition_id: str, scope: dict[str, Any]) -> dict[str, Any]:
    directory = INJURY_ROOT / competition_id
    paths = sorted(directory.glob("*.jsonl")) if directory.exists() else []
    scoped_rows = 0
    pit_rows = 0
    sources: Counter[str] = Counter()
    for row in _iter_jsonl(paths):
        logical = _logical_season(row.get("season"), scope)
        if logical is None:
            continue
        scoped_rows += 1
        freeze = _parse_time(row.get("freeze_time_utc") or row.get("kickoff_utc"))
        observed_at = _parse_time(row.get("source_observed_at_utc") or row.get("observed_at_utc"))
        if freeze and observed_at and observed_at <= freeze:
            pit_rows += 1
        sources[str(row.get("source_group") or row.get("source_id") or "unknown")] += 1
    return {
        "files": len(paths),
        "scoped_rows": scoped_rows,
        "pit_rows": pit_rows,
        "source_rows": dict(sources),
        "pit_available": pit_rows > 0,
    }


def _market_complete(row: dict[str, Any]) -> bool:
    one = row.get("one_x_two")
    ah = row.get("asian_handicap")
    ou = row.get("over_under") or row.get("total_goals")
    return (
        isinstance(one, dict)
        and all(k in one for k in ("home", "draw", "away"))
        and isinstance(ah, dict)
        and all(k in ah for k in ("line", "home", "away"))
        and isinstance(ou, dict)
        and all(k in ou for k in ("line", "over", "under"))
    )


def _market_coverage(competition_id: str, scope: dict[str, Any]) -> dict[str, Any]:
    directory = MARKET_ROOT / competition_id
    paths = sorted(directory.glob("*.jsonl")) if directory.exists() else []
    scoped_rows = 0
    valid_rows = 0
    freeze_keys: set[tuple[str, str]] = set()
    bookmakers: Counter[str] = Counter()
    for row in _iter_jsonl(paths):
        logical = _logical_season(row.get("season"), scope)
        if logical is None:
            continue
        scoped_rows += 1
        freeze = _parse_time(row.get("freeze_time_utc"))
        observed = _parse_time(row.get("observed_at_utc"))
        if not freeze or not observed or observed > freeze or not _market_complete(row):
            continue
        valid_rows += 1
        fixture = str(row.get("fixture_key") or row.get("event_id") or "")
        if fixture:
            freeze_keys.add((fixture, freeze.isoformat()))
        bookmakers[str(row.get("bookmaker_group") or row.get("bookmaker") or "unknown")] += 1
    return {
        "files": len(paths),
        "scoped_rows": scoped_rows,
        "valid_synchronized_snapshot_rows": valid_rows,
        "unique_fixture_freezes": len(freeze_keys),
        "bookmaker_rows": dict(bookmakers),
        "available": valid_rows > 0,
    }


def audit() -> dict[str, Any]:
    config = _load_json(SCOPE_PATH)
    competitions = config.get("competitions") or {}
    reports: dict[str, Any] = {}
    structural_errors: list[dict[str, Any]] = []

    for competition_id, scope in sorted(competitions.items()):
        mandatory = scope.get("mandatory_seasons") or []
        if len(mandatory) != 2:
            structural_errors.append({
                "competition_id": competition_id,
                "error": "mandatory_seasons_must_have_exactly_two_entries",
                "actual": mandatory,
            })
            continue
        try:
            expected, matches_per_season = _expected_labels(competition_id, scope)
        except Exception as exc:
            structural_errors.append({"competition_id": competition_id, "error": f"processed_match_read_failed: {exc}"})
            continue

        lineup = _lineup_coverage(competition_id, scope, expected)
        injury = _injury_coverage(competition_id, scope)
        market = _market_coverage(competition_id, scope)
        reports[competition_id] = {
            "mandatory_seasons": mandatory,
            "forward_capture_target": scope.get("forward_capture_target"),
            "stage_gate": scope.get("stage_gate"),
            "completed_matches_in_scope": sum(matches_per_season.values()),
            "completed_matches_by_season": matches_per_season,
            "lineup": lineup,
            "injury_suspension": injury,
            "market": market,
            "completion": {
                "observed_lineup_complete_for_available_completed_matches": bool(expected) and lineup["matched_observed_labels"] == len(expected),
                "pit_lineup_complete": bool(expected) and lineup["matched_pit_labels"] == len(expected),
                "pit_injury_suspension_complete": False,
                "timestamped_market_complete": False,
            },
        }

    observed_complete = sum(
        1 for report in reports.values()
        if report["completion"]["observed_lineup_complete_for_available_completed_matches"]
    )
    pit_lineup_complete = sum(1 for report in reports.values() if report["completion"]["pit_lineup_complete"])
    market_available = sum(1 for report in reports.values() if report["market"]["available"])
    injury_available = sum(1 for report in reports.values() if report["injury_suspension"]["pit_available"])

    status = "PASS" if not structural_errors and len(reports) == 17 else "FAIL"
    result = {
        "schema_version": "V4.7.6-two-season-evidence-backfill-audit",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "mandatory_backfill_window_seasons": 2,
        "older_history_policy": config.get("older_history_policy"),
        "competition_count": len(reports),
        "structural_errors": structural_errors,
        "summary": {
            "observed_lineup_complete_competitions": observed_complete,
            "pit_lineup_complete_competitions": pit_lineup_complete,
            "pit_injury_available_competitions": injury_available,
            "timestamped_market_available_competitions": market_available,
        },
        "reports": reports,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "current_rule_change": False,
        "policy": "Coverage is evaluated only inside each competition's two mandatory recent-season windows. Missing evidence remains explicit and does not block structural PASS, but it blocks any claim of evidence completion or A-grade readiness.",
    }
    return result


def write(result: dict[str, Any]) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    REPORT.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    args = parser.parse_args()
    result = audit()
    if args.write_receipt:
        write(result)
    if args.print_summary:
        print(json.dumps({
            "status": result["status"],
            "competition_count": result["competition_count"],
            "structural_error_count": len(result["structural_errors"]),
            "summary": result["summary"],
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and result["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
