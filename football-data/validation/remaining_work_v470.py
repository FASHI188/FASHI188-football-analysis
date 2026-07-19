#!/usr/bin/env python3
"""Consolidate all currently actionable football-engineering follow-up gates.

No formal rule or weight change is performed here. The report distinguishes
completed engineering work from blockers that require future matches or missing
historical point-in-time evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAN = ROOT / "manifests"
OUT = MAN / "remaining_work_v470_status.json"


def load(name: str) -> dict[str, Any]:
    return json.loads((MAN / name).read_text(encoding="utf-8"))


def main() -> int:
    stage = load("stage_gate_resolution_v470_status.json")
    active = load("active_league_promotion_review_v470_status.json")
    cross = load("cross_year_batch2_v469_status.json")
    jpn = load("jpn_j1_promotion_review_v467_status.json")
    registry = json.loads((ROOT / "config" / "platform_registry.json").read_text(encoding="utf-8"))
    a_grade_path = MAN / "a_grade_batch_v470_status.json"
    a_grade = json.loads(a_grade_path.read_text(encoding="utf-8")) if a_grade_path.exists() else {}

    payload = {
        "schema_version": "V4.7.0-remaining-work-consolidated",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_weight": 0,
        "automatic_promotion": False,
        "completed_now": {
            "stage_format_registry": True,
            "stage_gate_resolution": stage.get("summary"),
            "active_league_model_promotion_review": {
                "target_count": active.get("competition_count"),
                "eligible_count": active.get("eligible_count"),
                "eligible": active.get("eligible_for_current_compliant_promotion_review"),
            },
            "cross_year_batch2_audited": cross.get("competition_count_reviewed"),
            "jpn_j1_official_special_route": (jpn.get("official_transition_route") or {}).get("status"),
            "a_grade_audit_competition_count": a_grade.get("competition_count"),
            "a_grade_receipt_count": a_grade.get("a_grade_receipt_count"),
        },
        "blocked_by_future_matches": {
            "cross_year_2026_27": cross.get("deployment_gated_competitions", []),
            "jpn_j1_2026_27": (jpn.get("target_season_deployment_gate") or {}).get("blocker"),
        },
        "blocked_by_model_validation": {
            cid: item.get("blockers", [])
            for cid, item in (active.get("reports") or {}).items()
            if item.get("blockers")
        },
        "blocked_by_missing_evidence": registry.get("known_unfilled_evidence", []),
        "policy": (
            "Engineering tasks that can be completed with current frozen assets are executed. "
            "Future-season match gates and missing timestamped historical market/lineup evidence remain fail-closed. "
            "No research result is promoted automatically and CURRENT remains the sole formal rule authority."
        ),
        "status": "ACTIONABLE_WORK_COMPLETED_REMAINING_ITEMS_EXTERNALLY_GATED",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
