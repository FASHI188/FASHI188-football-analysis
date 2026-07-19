#!/usr/bin/env python3
"""Generate missing A-grade calibration, subgroup, source and drift evidence.

Evidence is fail-closed: unavailable point-in-time data or insufficient live audit
history remains a failed gate rather than being inferred from unrelated metrics.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import sys
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
VALIDATION_DIR = Path(__file__).resolve().parent
for path in (ENGINE_DIR, VALIDATION_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from drift_monitor_v460 import monitor_competition  # noqa: E402
from nested_backtest_v460 import _multiclass_ece, evaluate_season  # noqa: E402
from platform_core import (  # noqa: E402
    ROOT,
    PlatformError,
    atomic_write_json,
    load_json,
    load_registry,
    read_processed_matches,
    sha256_file,
    utc_now,
)

CORE_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
PROFILE_ROOT = ROOT / "league_profiles"
REPORT_ROOT = ROOT / "validation" / "reports" / "a_grade_diagnostics_v463"
MANIFEST_PATH = ROOT / "manifests" / "a_grade_diagnostics_v463_status.json"
MIN_SUBGROUP_SAMPLE = 60
MIN_LIVE_DRIFT_AUDITS = 60


def _clip_probability(value: float) -> float:
    return min(1.0 - 1e-6, max(1e-6, float(value)))


def _fit_binary_calibration(records: list[dict[str, Any]], field: str, outcome: str) -> dict[str, Any]:
    if len(records) < 50:
        return {"count": len(records), "intercept": None, "slope": None, "status": "INSUFFICIENT_SAMPLE"}
    x = [math.log(_clip_probability(record[field]) / (1.0 - _clip_probability(record[field]))) for record in records]
    y = [1.0 if record["actual_outcome"] == outcome else 0.0 for record in records]
    intercept, slope = 0.0, 1.0
    ridge = 1e-8
    for _ in range(50):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for xi, yi in zip(x, y):
            eta = max(-35.0, min(35.0, intercept + slope * xi))
            mu = 1.0 / (1.0 + math.exp(-eta))
            weight = max(1e-9, mu * (1.0 - mu))
            residual = mu - yi
            g0 += residual
            g1 += residual * xi
            h00 += weight
            h01 += weight * xi
            h11 += weight * xi * xi
        h00 += ridge
        h11 += ridge
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        step0 = (h11 * g0 - h01 * g1) / det
        step1 = (-h01 * g0 + h00 * g1) / det
        intercept -= step0
        slope -= step1
        if max(abs(step0), abs(step1)) < 1e-8:
            break
    return {"count": len(records), "intercept": intercept, "slope": slope, "status": "AVAILABLE"}


def _source_manifest_status(competition_id: str) -> dict[str, Any]:
    profile_path = PROFILE_ROOT / competition_id / "profile.json"
    if not profile_path.exists():
        return {"complete": False, "reason": "profile_missing", "profile_path": str(profile_path.relative_to(ROOT))}
    profile = load_json(profile_path)
    source_files = profile.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        return {"complete": False, "reason": "source_files_missing", "profile_sha256": sha256_file(profile_path)}
    failures = []
    for index, source in enumerate(source_files):
        if not isinstance(source, dict):
            failures.append({"index": index, "reason": "invalid_source_entry"})
            continue
        if source.get("required") is False:
            continue
        required_fields = ("url", "raw_sha256", "processed_sha256")
        missing = [field for field in required_fields if not source.get(field)]
        if missing:
            failures.append({"index": index, "reason": "missing_fields", "fields": missing})
        if source.get("validated") is not True:
            failures.append({"index": index, "reason": "not_validated"})
    return {
        "complete": not failures,
        "profile_sha256": sha256_file(profile_path),
        "source_count": len(source_files),
        "failures": failures,
    }


def _outer_records(competition_id: str) -> list[dict[str, Any]]:
    report_path = CORE_REPORT_ROOT / f"{competition_id}.json"
    if not report_path.exists():
        raise PlatformError(f"core report missing: {competition_id}")
    report = load_json(report_path)
    matches = read_processed_matches(competition_id)
    by_season: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        by_season[match.season].append(match)
    records: list[dict[str, Any]] = []
    seen_seasons: set[str] = set()
    for fold in report.get("folds", []):
        season = str(fold.get("outer_season"))
        if not season or season in seen_seasons or season not in by_season:
            continue
        seen_seasons.add(season)
        params = fold.get("selected_parameters")
        if not isinstance(params, dict):
            continue
        ordered = sorted(by_season[season], key=lambda item: (item.date, item.home_team, item.away_team))
        records.extend(evaluate_season(competition_id, ordered, params, use_team_effects=True))
    return records


def validate_competition(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    records = _outer_records(competition_id)
    if not records:
        raise PlatformError(f"no outer records for A-grade diagnostics: {competition_id}")
    calibration = {
        "home": _fit_binary_calibration(records, "p_home", "home"),
        "draw": _fit_binary_calibration(records, "p_draw", "draw"),
        "away": _fit_binary_calibration(records, "p_away", "away"),
    }
    ece = _multiclass_ece(records)

    by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_season[str(record["season"])].append(record)
    subgroup_details: dict[str, Any] = {}
    subgroup_eces: list[float] = []
    all_groups_meet_sample = True
    for season, subset in sorted(by_season.items()):
        season_ece = _multiclass_ece(subset)
        sufficient = len(subset) >= MIN_SUBGROUP_SAMPLE
        all_groups_meet_sample = all_groups_meet_sample and sufficient
        if sufficient:
            subgroup_eces.append(float(season_ece.get("maximum", 1.0)))
        subgroup_details[season] = {
            "count": len(subset),
            "minimum_sample_met": sufficient,
            "one_x_two_ece": season_ece,
        }

    source_manifest = _source_manifest_status(competition_id)
    drift = monitor_competition(competition_id)
    no_unresolved_drift = bool(
        int(drift.get("recent_count", 0)) >= MIN_LIVE_DRIFT_AUDITS
        and drift.get("suspend_a") is False
        and drift.get("status") != "INSUFFICIENT_RECENT_AUDITS"
    )
    report = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "outer_prediction_count": len(records),
        "calibration_diagnostics": {
            "home_intercept": calibration["home"]["intercept"],
            "home_slope": calibration["home"]["slope"],
            "draw_intercept": calibration["draw"]["intercept"],
            "draw_slope": calibration["draw"]["slope"],
            "away_intercept": calibration["away"]["intercept"],
            "away_slope": calibration["away"]["slope"],
            "maximum_ece": ece.get("maximum"),
            "per_outcome": calibration,
            "one_x_two_ece": ece,
        },
        "subgroup_calibration": {
            "definition": "outer-season point-in-time subgroups",
            "minimum_sample": MIN_SUBGROUP_SAMPLE,
            "all_important_groups_meet_minimum_sample": all_groups_meet_sample,
            "maximum_ece": max(subgroup_eces) if subgroup_eces else None,
            "groups": subgroup_details,
        },
        "source_manifest": source_manifest,
        "source_manifest_complete": bool(source_manifest.get("complete")),
        "drift_monitor": drift,
        "minimum_live_drift_audits": MIN_LIVE_DRIFT_AUDITS,
        "no_unresolved_drift": no_unresolved_drift,
        "governance_note": "Insufficient live freeze/post-match audit history keeps the drift gate false. Historical OOF diagnostics are not allowed to impersonate a live drift-clearance receipt.",
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_all(competition: str | None = None, *, write: bool = True) -> dict[str, Any]:
    ids = [item["competition_id"] for item in load_registry()["competitions"]]
    if competition:
        if competition not in ids:
            raise PlatformError(f"unknown competition: {competition}")
        ids = [competition]
    reports: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    for competition_id in ids:
        try:
            report = validate_competition(competition_id, write=write)
            reports[competition_id] = {
                "source_manifest_complete": report["source_manifest_complete"],
                "no_unresolved_drift": report["no_unresolved_drift"],
                "maximum_ece": report["calibration_diagnostics"]["maximum_ece"],
                "subgroup_maximum_ece": report["subgroup_calibration"]["maximum_ece"],
            }
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "competition_count_requested": len(ids),
        "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "reports": reports,
        "failures": failures,
    }
    if write and not competition:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"A-grade diagnostics failed for {len(failures)} domains: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        result = run_all(args.competition, write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
