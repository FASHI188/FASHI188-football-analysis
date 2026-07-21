#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from platform_core import atomic_write_json, load_json

ROOT = Path(__file__).resolve().parents[1]
FORMAL_STATUS = ROOT / "manifests" / "formal_core_v460_status.json"
REPORT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v501"
OUT = ROOT / "manifests" / "bayesian_dynamic_state_oof_v501_status.json"


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports = {}
    failures = {}
    candidates = []

    for competition_id in competitions:
        path = REPORT_DIR / f"{competition_id}.json"
        if not path.exists():
            failures[competition_id] = "missing same-day-safe domain receipt"
            continue
        try:
            report = load_json(path)
            if report.get("same_day_outcomes_withheld") is not True:
                raise ValueError("same_day_outcomes_withheld is not true")
            reports[competition_id] = report
            if report.get("status") == "RESEARCH_REVIEW_CANDIDATE_AH_PENDING":
                candidates.append(competition_id)
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    payload = {
        "schema_version": "V5.0.1-bayesian-dynamic-state-oof-aggregate-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "same_day_outcomes_withheld": True,
        "replaces_invalidated_v500_evidence": True,
        "invalidation_receipt": "manifests/bayesian_dynamic_state_v500_invalidation_status.json",
        "research_review_candidates_ah_pending": candidates,
        "reports": {
            competition_id: {
                "status": report.get("status"),
                "outer_prediction_count": report.get("outer_prediction_count"),
                "evaluated_outer_season_count": report.get("evaluated_outer_season_count"),
                "pooled_metrics": report.get("pooled_metrics"),
                "paired_block_bootstrap": report.get("paired_block_bootstrap"),
                "checks": report.get("checks"),
                "same_day_outcomes_withheld": report.get("same_day_outcomes_withheld"),
                "handicap_target_status": report.get("handicap_target_status"),
            }
            for competition_id, report in reports.items()
        },
        "failures": failures,
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Replacement 17-domain same-day-safe replay. No result changes formal probabilities without replacement adjudication, fourth-target handicap evidence and a CURRENT-compliant promotion receipt.",
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({"status": payload["status"], "completed": len(reports), "candidates": candidates, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
