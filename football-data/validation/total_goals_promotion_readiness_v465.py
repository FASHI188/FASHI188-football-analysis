#!/usr/bin/env python3
"""Evidence-only promotion readiness gate for first-round total-goals winners.

This gate does not grant formal weight. It identifies domains that have:
1) passed the V4.6.4 direct-total RPS challenger;
2) valid rolling-origin fold mechanics;
3) a passing complete final-chain replay receipt for the current formal core.

Those domains are eligible to enter the NEXT step: replace only P(T), preserve the
conditional score allocation structure, rebuild the unified matrix, and re-test all
downstream proper scores and market settlements. No domain is promoted here.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import sys
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, PlatformError, atomic_write_json, load_json, sha256_file, utc_now  # noqa: E402

SCRIPT_PATH = Path(__file__).resolve()
ROUND1 = ROOT / "manifests" / "total_goals_dynamic_v464_status.json"
ROLLING = ROOT / "manifests" / "rolling_outer_v463_status.json"
FINAL_REPLAY = ROOT / "manifests" / "final_chain_replay_v463_status.json"
MANIFEST = ROOT / "manifests" / "total_goals_promotion_readiness_v465_status.json"
REPORT_ROOT = ROOT / "validation" / "reports" / "total_goals_promotion_readiness_v465"


def _load(path: Path) -> dict[str, Any]:
    return load_json(path) if path.exists() else {}


def validate(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    round1 = _load(ROUND1)
    rolling = _load(ROLLING)
    replay = _load(FINAL_REPLAY)
    r1 = (round1.get("reports") or {}).get(competition_id) or {}
    ro = (rolling.get("reports") or {}).get(competition_id) or {}
    fr = (replay.get("reports") or {}).get(competition_id) or {}

    structural_checks = ro.get("checks") or {}
    checks = {
        "round1_total_goals_rps_pass": r1.get("status") == "TOTAL_GOALS_CHALLENGER_PASS",
        "rolling_minimum_outer_predictions": bool(structural_checks.get("minimum_outer_predictions")),
        "rolling_minimum_outer_time_folds": bool(structural_checks.get("minimum_outer_time_folds")),
        "rolling_disjoint_test_windows": bool(structural_checks.get("disjoint_test_windows")),
        "rolling_strictly_prior_selection": bool(structural_checks.get("strictly_prior_selection")),
        "current_core_final_chain_replay_pass": fr.get("status") == "通过",
    }
    ready = all(checks.values())
    report = {
        "schema_version": "V4.6.5-promotion-readiness",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "READY_FOR_JOINT_INTEGRATION_TEST" if ready else "WAITING_FOR_REQUIRED_EVIDENCE",
        "formal_weight": 0,
        "checks": checks,
        "round1_total_goals": r1,
        "rolling_structure": {
            "outer_folds": ro.get("outer_folds"),
            "outer_predictions": ro.get("outer_predictions"),
            "checks": structural_checks,
        },
        "final_chain_replay": fr,
        "next_gate": "Rebuild each OOS unified score matrix with challenger P(T) and frozen conditional allocation; then require joint Log Score, 1X2 Brier/RPS, total-goals RPS, tail calibration, score-set coverage, probability conservation and independent final-chain replay before any formal non-zero weight.",
        "implementation_sha256": sha256_file(SCRIPT_PATH),
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_all(*, write: bool = True) -> dict[str, Any]:
    round1 = _load(ROUND1)
    winners = sorted(
        competition_id
        for competition_id, item in (round1.get("reports") or {}).items()
        if item.get("status") == "TOTAL_GOALS_CHALLENGER_PASS"
    )
    if not winners:
        raise PlatformError("no V4.6.4 total-goals challenger winners found")
    reports = {competition_id: validate(competition_id, write=write) for competition_id in winners}
    manifest = {
        "schema_version": "V4.6.5-promotion-readiness",
        "generated_at_utc": utc_now(),
        "competition_count": len(reports),
        "ready_count": sum(item["status"] == "READY_FOR_JOINT_INTEGRATION_TEST" for item in reports.values()),
        "reports": {
            competition_id: {
                "status": item["status"],
                "checks": item["checks"],
            }
            for competition_id, item in reports.items()
        },
        "formal_weight": 0,
        "implementation_sha256": sha256_file(SCRIPT_PATH),
    }
    if write:
        atomic_write_json(MANIFEST, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        result = run_all(write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
