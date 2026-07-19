#!/usr/bin/env python3
"""Accuracy controls layered on batch 002 ingestion.

This adapter excludes low-frequency promotion/relegation playoff participants
when a competition explicitly enables that rule and records configured seasons
that are not present in the upstream archive.  It delegates all other parsing,
validation, profile generation and atomic writes to ingest_batch_002.py.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

CORE_PATH = Path(__file__).with_name("ingest_batch_002.py")
SPEC = importlib.util.spec_from_file_location("ingest_batch_002_core", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = CORE
SPEC.loader.exec_module(CORE)

ORIGINAL_PARSE_EXTRA = CORE.parse_extra_archive
ORIGINAL_ENHANCE_PROFILE = CORE.enhance_profile


def _filter_low_frequency_teams(rows: list[dict[str, str]], competition: dict):
    threshold = int(competition.get("exclude_low_frequency_team_rows_below", 0))
    if threshold <= 0:
        return rows, []

    by_season: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_season[row["season"]].append(row)

    kept: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for season, subset in sorted(by_season.items()):
        appearances: Counter[str] = Counter()
        for row in subset:
            appearances[row["HomeTeam"]] += 1
            appearances[row["AwayTeam"]] += 1
        regular_teams = {team for team, count in appearances.items() if count >= threshold}
        for row in subset:
            if row["HomeTeam"] in regular_teams and row["AwayTeam"] in regular_teams:
                kept.append(row)
            else:
                excluded.append(row)
    return kept, excluded


def parse_extra_archive_with_accuracy_controls(content: bytes, competition: dict):
    rows, audit = ORIGINAL_PARSE_EXTRA(content, competition)
    rows, excluded = _filter_low_frequency_teams(rows, competition)
    if not rows:
        raise CORE.DataError(f"{competition['competition_id']}: all rows removed by playoff filter")

    seen = set()
    for row in rows:
        key = (row["season"], row.get("Date", ""), row["HomeTeam"], row["AwayTeam"])
        if key in seen:
            raise CORE.DataError(f"{competition['competition_id']}: duplicate after filter {key}")
        seen.add(key)

    observed_configured = {
        season for season in competition.get("allowed_source_seasons", [])
        if season in audit.get("observed_source_seasons", {})
    }
    missing_configured = sorted(set(competition.get("allowed_source_seasons", [])) - observed_configured)
    per_season = Counter(row["season"] for row in rows)
    audit.update(
        {
            "selected_rows_before_playoff_filter": audit["selected_rows"],
            "selected_rows": len(rows),
            "rows_by_project_season": dict(sorted(per_season.items())),
            "missing_configured_source_seasons": missing_configured,
            "low_frequency_team_threshold": int(competition.get("exclude_low_frequency_team_rows_below", 0)),
            "excluded_playoff_rows": len(excluded),
            "excluded_rows_meaning": competition.get("excluded_rows_meaning"),
            "excluded_match_details": [
                {
                    "season": row["season"],
                    "date": row.get("Date", ""),
                    "home": row["HomeTeam"],
                    "away": row["AwayTeam"],
                    "score": f"{row['FTHG']}-{row['FTAG']}",
                }
                for row in excluded
            ],
        }
    )
    return rows, audit


def enhance_profile_with_accuracy_controls(profile, competition, audit):
    profile = ORIGINAL_ENHANCE_PROFILE(profile, competition, audit)
    if competition["competition_id"] == "JPN_J1":
        missing = audit.get("missing_configured_source_seasons", [])
        if "2026" in missing:
            profile["stage_warning"] = (
                "The upstream archive has no 2026 transition-tournament rows; "
                "2026 is unavailable and is not pooled with ordinary J1 seasons."
            )
    return profile


CORE.parse_extra_archive = parse_extra_archive_with_accuracy_controls
CORE.enhance_profile = enhance_profile_with_accuracy_controls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = CORE.run()
    except (CORE.DataError, CORE.CORE.DataError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    manifest["limitations"] = [
        "K League 1 remains unavailable until the official crawler passes reproducibility and completeness tests.",
        "The upstream J1 archive has no 2026 transition-tournament rows; 2026 is unavailable and is not pooled with ordinary J1 seasons.",
        "J1 promotion/relegation playoff rows outside the regular top flight are excluded and itemized in the profile audit.",
        "Argentina and MLS stage labels are not fully identified from the archive source; stage-specific calibration is disabled.",
        "UCL qualifiers are excluded; ambiguous extra-time or penalty rows are excluded from the 90-minute-safe profile.",
        "All profiles are descriptive priors and cannot override question-time market, lineup and injury evidence.",
    ]
    path = CORE.ROOT / "manifests" / "latest_ingestion_batch_002.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.print_summary:
        print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
