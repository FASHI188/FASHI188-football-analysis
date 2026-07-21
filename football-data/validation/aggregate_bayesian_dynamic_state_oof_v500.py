#!/usr/bin/env python3
"""Aggregate all per-domain V5 Bayesian dynamic-state OOF receipts."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import atomic_write_json, load_json

FORMAL_STATUS = ROOT / "manifests" / "formal_core_v460_status.json"
REPORT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v500"
OUT = ROOT / "manifests" / "bayesian_dynamic_state_oof_v500_status.json"


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports = {}
    failures = {}
    candidates = []
    for competition_id in competitions:
        path = REPORT_DIR / f"{competition_id}.json"
        if not path.exists():
            failures[competition_id] = "domain receipt missing"
            continue
        report = load_json(path)
        reports[competition_id] = report
        if report.get("status") == "FAILED":
            failures[competition_id] = str(report.get("reason") or "domain validation failed")
        if report.get("status") == "RESEARCH_REVIEW_CANDIDATE_AH_PENDING":
            candidates.append(competition_id)

    summary_reports = {}
    for competition_id, report in reports.items():
        summary_reports[competition_id] = {
            "status": report.get("status"),
            "outer_prediction_count": report.get("outer_prediction_count"),
            "evaluated_outer_season_count": report.get("evaluated_outer_season_count"),
            "pooled_metrics": report.get("pooled_metrics"),
            "paired_block_bootstrap": report.get("paired_block_bootstrap"),
            "checks": report.get("checks"),
            "handicap_target_status": report.get("handicap_target_status"),
            "reason": report.get("reason"),
        }

    payload = {
        "schema_version": "V5.0.0-bayesian-dynamic-state-oof-aggregate-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "research_review_candidates_ah_pending": candidates,
        "reports": summary_reports,
        "failures": failures,
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "17-domain strict chronological research screen. Profiles are frozen from earlier completed seasons. No result alters V5 formal probabilities without competition-specific promotion and fourth-target handicap evidence.",
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": payload["status"],
        "competition_count_completed": payload["competition_count_completed"],
        "candidates": candidates,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
