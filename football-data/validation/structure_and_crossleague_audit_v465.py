#!/usr/bin/env python3
"""Structure audit for Swiss/Argentina and cross-league audit for UCL.

This module does not invent stage labels or cross-league strength. It quantifies
what the installed result data can support and records the exact evidence gap that
must be fixed before competition-specific total-goals promotion work continues.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import sys
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, PlatformError, atomic_write_json, load_registry, read_processed_matches, sha256_file, utc_now  # noqa: E402

SCRIPT_PATH = Path(__file__).resolve()
REPORT_ROOT = ROOT / "validation" / "reports" / "structure_crossleague_v465"
MANIFEST_PATH = ROOT / "manifests" / "structure_crossleague_v465_status.json"
TARGETS = ("SUI_SuperLeague", "ARG_Primera", "UEFA_ChampionsLeague")


def _registry_entry(competition_id: str) -> dict[str, Any]:
    for item in load_registry()["competitions"]:
        if item["competition_id"] == competition_id:
            return item
    raise PlatformError(f"competition not registered: {competition_id}")


def audit(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    if competition_id not in TARGETS:
        raise PlatformError(f"unsupported structure audit target: {competition_id}")
    entry = _registry_entry(competition_id)
    matches = read_processed_matches(competition_id)
    by_season: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        by_season[match.season].append(match)

    season_reports: dict[str, Any] = {}
    all_stages = Counter()
    for season, rows in sorted(by_season.items()):
        rows = sorted(rows, key=lambda item: (item.date, item.home_team, item.away_team))
        stages = Counter(str(item.stage or "").strip() or "<EMPTY>" for item in rows)
        all_stages.update(stages)
        teams = sorted({item.home_team for item in rows} | {item.away_team for item in rows})
        dates = sorted({item.date.date() for item in rows})
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        totals_by_stage: dict[str, list[int]] = defaultdict(list)
        for item in rows:
            totals_by_stage[str(item.stage or "").strip() or "<EMPTY>"].append(item.home_goals + item.away_goals)
        season_reports[season] = {
            "match_count": len(rows),
            "team_count": len(teams),
            "stage_counts": dict(stages),
            "first_date": rows[0].date.date().isoformat() if rows else None,
            "last_date": rows[-1].date.date().isoformat() if rows else None,
            "maximum_calendar_gap_days": max(gaps) if gaps else 0,
            "mean_total_goals_by_stage": {key: mean(values) for key, values in totals_by_stage.items() if values},
        }

    stage_status = str(entry.get("stage_status") or "")
    stage_verified = "unverified" not in stage_status
    report: dict[str, Any] = {
        "schema_version": "V4.6.5-structure-audit",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "registry_stage_status": stage_status,
        "observed_stage_counts": dict(all_stages),
        "seasons": season_reports,
        "formal_weight_change": 0,
        "implementation_sha256": sha256_file(SCRIPT_PATH),
    }

    if competition_id in ("SUI_SuperLeague", "ARG_Primera"):
        report.update({
            "status": "STRUCTURE_EVIDENCE_READY" if stage_verified else "STRUCTURE_EVIDENCE_REQUIRED",
            "checks": {
                "registry_stage_verified": stage_verified,
                "multiple_observed_stage_labels": len(all_stages) > 1,
                "nonempty_stage_labels": "<EMPTY>" not in all_stages,
            },
            "next_gate": "Obtain and cross-verify point-in-time official stage/round labels, map every match to the correct phase, then rerun stage-aware rolling OOS total-goals validation. Do not infer phase labels from score outcomes.",
        })
    else:
        cross_gate = str(entry.get("cross_league_strength_gate") or "")
        report.update({
            "status": "CROSS_LEAGUE_BRIDGE_REQUIRED",
            "checks": {
                "ucl_stage_registry_verified": stage_verified,
                "qualifiers_excluded": "qualifiers_excluded" in stage_status,
                "cross_league_strength_bridge_installed": False,
                "registry_requires_market_anchor": cross_gate == "market_anchor_required",
            },
            "next_gate": "Build a point-in-time cross-league strength bridge from independently validated domestic/continental evidence or a permitted current market anchor. UCL total-goals promotion remains blocked until team-strength comparability is identifiable.",
        })

    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_all(*, write: bool = True) -> dict[str, Any]:
    reports = {competition_id: audit(competition_id, write=write) for competition_id in TARGETS}
    manifest = {
        "schema_version": "V4.6.5-structure-audit",
        "generated_at_utc": utc_now(),
        "reports": {competition_id: {"status": report["status"], "checks": report["checks"]} for competition_id, report in reports.items()},
        "implementation_sha256": sha256_file(SCRIPT_PATH),
    }
    if write:
        atomic_write_json(MANIFEST_PATH, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    result = run_all(write=not args.check_only)
    if args.print_summary:
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
