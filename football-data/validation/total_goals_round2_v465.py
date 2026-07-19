#!/usr/bin/env python3
"""Second-round direct-total challenger for near-threshold competition domains.

This wrapper deliberately keeps formal weight at zero. It reuses the V4.6.4
true rolling-origin evaluator, but replaces the candidate family with a compact,
predeclared grid focused on stronger mean shrinkage, venue pooling and partial
Poisson/NB blending. Only the seven first-round near-threshold domains are run.

A pass is research evidence only. It cannot change the formal center without a
separate unified-matrix integration test, full final-chain replay and CURRENT-
compliant promotion decision.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import sys
VALIDATION_DIR = Path(__file__).resolve().parent
ENGINE_DIR = VALIDATION_DIR.parents[0] / "engine"
for path in (str(VALIDATION_DIR), str(ENGINE_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import total_goals_dynamic_challenger_v464 as base  # noqa: E402
from platform_core import ROOT, PlatformError, atomic_write_json, load_json, sha256_file, utc_now  # noqa: E402

SCRIPT_PATH = Path(__file__).resolve()
REPORT_ROOT = ROOT / "validation" / "reports" / "total_goals_round2_v465"
MANIFEST_PATH = ROOT / "manifests" / "total_goals_round2_v465_status.json"
ROUND1_MANIFEST = ROOT / "manifests" / "total_goals_dynamic_v464_status.json"

TARGETS = (
    "ITA_SerieA",
    "FRA_Ligue1",
    "POR_PrimeiraLiga",
    "SWE_Allsvenskan",
    "KOR_KLeague1",
    "BRA_SerieA",
    "USA_MLS",
)

# Compact predeclared family. The grid is intentionally limited and is selected
# only on strictly earlier rolling-origin evidence by the imported evaluator.
CANDIDATES: list[dict[str, float | str]] = [
    {"id": "R0_fast45_mix25", "half_life_days": 45.0, "team_prior_matches": 4.0, "venue_weight": 0.50, "signal_weight": 0.65, "poisson_blend": 0.25},
    {"id": "R1_fast75_nb", "half_life_days": 75.0, "team_prior_matches": 5.0, "venue_weight": 0.50, "signal_weight": 0.65, "poisson_blend": 0.00},
    {"id": "R2_90_mix25", "half_life_days": 90.0, "team_prior_matches": 6.0, "venue_weight": 0.50, "signal_weight": 0.50, "poisson_blend": 0.25},
    {"id": "R3_90_venue75_mix25", "half_life_days": 90.0, "team_prior_matches": 6.0, "venue_weight": 0.75, "signal_weight": 0.75, "poisson_blend": 0.25},
    {"id": "R4_120_signal25_nb", "half_life_days": 120.0, "team_prior_matches": 8.0, "venue_weight": 0.25, "signal_weight": 0.25, "poisson_blend": 0.00},
    {"id": "R5_120_signal40_mix25", "half_life_days": 120.0, "team_prior_matches": 8.0, "venue_weight": 0.25, "signal_weight": 0.40, "poisson_blend": 0.25},
    {"id": "R6_150_mix25", "half_life_days": 150.0, "team_prior_matches": 8.0, "venue_weight": 0.50, "signal_weight": 0.50, "poisson_blend": 0.25},
    {"id": "R7_180_allvenue_nb", "half_life_days": 180.0, "team_prior_matches": 8.0, "venue_weight": 0.00, "signal_weight": 0.50, "poisson_blend": 0.00},
    {"id": "R8_180_pool_mix75", "half_life_days": 180.0, "team_prior_matches": 10.0, "venue_weight": 0.25, "signal_weight": 0.25, "poisson_blend": 0.75},
    {"id": "R9_240_slow_mix50", "half_life_days": 240.0, "team_prior_matches": 10.0, "venue_weight": 0.25, "signal_weight": 0.50, "poisson_blend": 0.50},
]


def _round1() -> dict[str, Any]:
    return load_json(ROUND1_MANIFEST) if ROUND1_MANIFEST.exists() else {"reports": {}}


def validate_one(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    if competition_id not in TARGETS:
        raise PlatformError(f"round2 target not registered: {competition_id}")
    previous = (_round1().get("reports") or {}).get(competition_id) or {}
    if previous.get("status") == "TOTAL_GOALS_CHALLENGER_PASS":
        raise PlatformError(f"round2 refuses already-passed domain: {competition_id}")

    original = base.CANDIDATES
    try:
        base.CANDIDATES = CANDIDATES
        report = base.validate_competition(competition_id, write=False)
    finally:
        base.CANDIDATES = original

    old_upper = previous.get("ci95_upper")
    new_upper = (report.get("paired_block_bootstrap") or {}).get("ci95_upper")
    report = dict(report)
    report.update({
        "schema_version": "V4.6.5-round2-challenger",
        "round": 2,
        "formal_weight": 0,
        "first_round_status": previous.get("status"),
        "first_round_ci95_upper": old_upper,
        "round2_ci95_upper": new_upper,
        "ci95_upper_change_vs_round1": (float(new_upper) - float(old_upper)) if new_upper is not None and old_upper is not None else None,
        "status": "TOTAL_GOALS_ROUND2_PASS" if report.get("status") == "TOTAL_GOALS_CHALLENGER_PASS" else "TOTAL_GOALS_ROUND2_NOT_PROMOTED",
        "wrapper_implementation_sha256": sha256_file(SCRIPT_PATH),
        "promotion_note": "Round-2 pass remains research evidence only. Formal integration requires unified-matrix downstream validation and final-chain replay; no automatic weight change.",
    })
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_all(competition: str | None = None, *, write: bool = True) -> dict[str, Any]:
    ids = [competition] if competition else list(TARGETS)
    reports: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    for competition_id in ids:
        try:
            report = validate_one(competition_id, write=write)
            reports[competition_id] = {
                "status": report["status"],
                "outer_folds": report["outer_folds"],
                "outer_predictions": report["outer_predictions"],
                "mean_difference": report["paired_block_bootstrap"]["mean_difference"],
                "ci95_upper": report["paired_block_bootstrap"]["ci95_upper"],
                "ci95_upper_change_vs_round1": report["ci95_upper_change_vs_round1"],
                "checks": report["checks"],
            }
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.5-round2-challenger",
        "generated_at_utc": utc_now(),
        "target_group": "near_threshold_round1",
        "competition_count_requested": len(ids),
        "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "passed_count": sum(item["status"] == "TOTAL_GOALS_ROUND2_PASS" for item in reports.values()),
        "reports": reports,
        "failures": failures,
        "formal_weight": 0,
        "implementation_sha256": sha256_file(SCRIPT_PATH),
    }
    if write and not competition:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"round2 total-goals validation failed for {len(failures)} domains: {failures}")
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
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
