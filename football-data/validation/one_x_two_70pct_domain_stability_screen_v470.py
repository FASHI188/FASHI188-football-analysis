#!/usr/bin/env python3
"""User-target diagnostic screen for stable ~70% selective 1X2 performance.

Reads the fixed-threshold multi-season audit. This is NOT a formal promotion gate.
A domain-threshold combination is called a diagnostic candidate only when:
- pooled accuracy >= 70%
- pooled selected count >= 100
- every participating season has at least 20 selected fixtures
- minimum season accuracy >= 60%
- season accuracy standard deviation <= 10 percentage points
These criteria operationalize the user's stated 70% + stability target for research only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "manifests" / "one_x_two_selective_multiseason_stability_v470_status.json"
OUT = ROOT / "manifests" / "one_x_two_70pct_domain_stability_screen_v470_status.json"


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    reports = {}
    candidates = []
    for cid, report in (data.get("reports") or {}).items():
        rows = []
        for item in report.get("thresholds") or []:
            per = item.get("per_season") or []
            selected_counts = [int(x.get("selected_count") or 0) for x in per]
            accuracies = [float(x["accuracy"]) for x in per if x.get("accuracy") is not None]
            checks = {
                "pooled_accuracy_at_least_70pct": item.get("pooled_accuracy") is not None and float(item["pooled_accuracy"]) >= 0.70,
                "pooled_selected_at_least_100": int(item.get("pooled_selected_count") or 0) >= 100,
                "every_season_selected_at_least_20": bool(selected_counts) and min(selected_counts) >= 20,
                "minimum_season_accuracy_at_least_60pct": bool(accuracies) and min(accuracies) >= 0.60,
                "season_accuracy_std_at_most_10pp": item.get("season_accuracy_std") is not None and float(item["season_accuracy_std"]) <= 0.10,
            }
            passed = all(checks.values())
            row = {
                "gap_threshold": item.get("gap_threshold"),
                "pooled_accuracy": item.get("pooled_accuracy"),
                "pooled_coverage": item.get("pooled_coverage"),
                "pooled_selected_count": item.get("pooled_selected_count"),
                "season_accuracy_min": item.get("season_accuracy_min"),
                "season_accuracy_std": item.get("season_accuracy_std"),
                "checks": checks,
                "diagnostic_candidate": passed,
            }
            rows.append(row)
            if passed:
                candidates.append({"competition_id": cid, **row})
        reports[cid] = {"competition_id": cid, "thresholds": rows}
    payload = {
        "schema_version": "V4.7.0-1x2-70pct-domain-stability-screen-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "diagnostic_candidates": candidates,
        "candidate_count": len(candidates),
        "reports": reports,
        "governance": {
            "user_target_research_screen_only": True,
            "formal_threshold_selected": False,
            "runtime_change": False,
            "formal_weight_change": False,
            "probability_change": False
        }
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidate_count": len(candidates), "candidates": candidates}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
