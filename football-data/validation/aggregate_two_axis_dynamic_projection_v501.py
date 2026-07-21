#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from platform_core import atomic_write_json, load_json

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "manifests" / "two_axis_dynamic_projection_v501"
OUT = ROOT / "manifests" / "two_axis_dynamic_projection_v501_status.json"
REQUESTED = ("ESP_LaLiga", "GER_Bundesliga")


def main() -> int:
    reports = {}
    failures = {}
    candidates = []
    rejected = []
    for competition_id in REQUESTED:
        path = REPORT_DIR / f"{competition_id}.json"
        if not path.exists():
            failures[competition_id] = "missing domain receipt"
            continue
        report = load_json(path)
        reports[competition_id] = report
        status = str(report.get("status") or "")
        if status == "FAILED":
            failures[competition_id] = str(report.get("reason") or "domain validation failed")
        elif status == "RESEARCH_CANDIDATE_FORMAL_PIT_BLOCKED":
            candidates.append(competition_id)
        else:
            rejected.append(competition_id)

    payload = {
        "schema_version": "V5.0.1-two-axis-dynamic-projection-aggregate-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(REQUESTED) and not failures else "PARTIAL",
        "requested_domains": list(REQUESTED),
        "completed_domains": sorted(reports),
        "research_candidates_formal_pit_blocked": candidates,
        "rejected_keep_formal_weight_0": rejected,
        "failures": failures,
        "reports": {
            competition_id: {
                "status": report.get("status"),
                "reason": report.get("reason"),
                "selected_configurations": report.get("selected_configurations"),
                "outer_prediction_count": report.get("outer_prediction_count"),
                "pooled_metrics": report.get("pooled_metrics"),
                "checks": report.get("checks"),
            }
            for competition_id, report in reports.items()
        },
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_promotion_authorized": False,
        "policy": "Two-axis research only. Formal V5 probabilities remain unchanged.",
    }
    atomic_write_json(OUT, payload)
    print({"status": payload["status"], "candidates": candidates, "rejected": rejected, "failures": failures})
    return 0 if len(reports) == len(REQUESTED) else 2


if __name__ == "__main__":
    raise SystemExit(main())
