#!/usr/bin/env python3
"""Consolidate current-season route and rolling OOS model evidence for four active leagues.

This is a promotion review only. It cannot change formal weights or CURRENT.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAN = ROOT / "manifests"
OUT = MAN / "active_league_promotion_review_v470_status.json"
TARGETS = ["SWE_Allsvenskan", "NOR_Eliteserien", "KOR_KLeague1", "BRA_SerieA"]


def load(name: str) -> dict[str, Any]:
    return json.loads((MAN / name).read_text(encoding="utf-8"))


def direct_total_status(cid: str, round1: dict[str, Any], round2: dict[str, Any]) -> dict[str, Any]:
    r2 = (round2.get("reports") or {}).get(cid)
    if r2:
        return {
            "source": "round2",
            "status": r2.get("status"),
            "outer_folds": r2.get("outer_folds"),
            "outer_predictions": r2.get("outer_predictions"),
            "ci95_upper": r2.get("ci95_upper"),
            "passed": r2.get("status") == "TOTAL_GOALS_ROUND2_PASS",
        }
    r1 = (round1.get("reports") or {}).get(cid) or {}
    return {
        "source": "round1",
        "status": r1.get("status"),
        "outer_folds": r1.get("outer_folds"),
        "outer_predictions": r1.get("outer_predictions"),
        "ci95_upper": r1.get("ci95_upper"),
        "passed": r1.get("status") == "TOTAL_GOALS_CHALLENGER_PASS",
    }


def main() -> int:
    current = load("current_season_batch1_v468_status.json")
    round1 = load("total_goals_dynamic_v464_status.json")
    round2 = load("total_goals_round2_v465_status.json")
    joint = load("total_goals_joint_integration_v466_status.json")

    reports = {}
    eligible = []
    for cid in TARGETS:
        route = (current.get("reports") or {}).get(cid) or {}
        direct = direct_total_status(cid, round1, round2)
        joint_report = (joint.get("reports") or {}).get(cid)
        if joint_report is None:
            joint_status = "NOT_RUN_BECAUSE_DIRECT_TOTAL_DID_NOT_PASS_GATE"
            joint_ready = False
            joint_checks = None
        else:
            joint_status = joint_report.get("status")
            joint_ready = joint_status == "READY_FOR_CURRENT_COMPLIANT_PROMOTION_REVIEW"
            joint_checks = joint_report.get("checks")

        current_ready = route.get("status") == "CURRENT_SEASON_ROUTE_READY"
        can_review = bool(current_ready and direct["passed"] and joint_ready)
        if can_review:
            eligible.append(cid)
            promotion_status = "EVIDENCE_READY_FOR_CURRENT_COMPLIANT_PROMOTION_REVIEW"
        else:
            promotion_status = "NOT_ELIGIBLE_KEEP_FORMAL_WEIGHT_ZERO"

        blockers = []
        if not current_ready:
            blockers.append("current_season_route_not_ready")
        if not direct["passed"]:
            blockers.append("direct_total_rolling_oos_gate_not_passed")
        if direct["passed"] and not joint_ready:
            blockers.append("joint_integration_gate_not_passed")

        reports[cid] = {
            "competition_id": cid,
            "current_season_route": {
                "status": route.get("status"),
                "current_season_rows": route.get("current_season_rows"),
            },
            "direct_total_rolling_oos": direct,
            "joint_integration": {
                "status": joint_status,
                "checks": joint_checks,
            },
            "promotion_status": promotion_status,
            "blockers": blockers,
            "formal_weight": 0,
            "automatic_promotion": False,
        }

    payload = {
        "schema_version": "V4.7.0-active-league-promotion-review",
        "target_competitions": TARGETS,
        "competition_count": len(TARGETS),
        "eligible_for_current_compliant_promotion_review": eligible,
        "eligible_count": len(eligible),
        "formal_weight": 0,
        "automatic_promotion": False,
        "reports": reports,
        "policy": "Promotion requires current-season route readiness, direct-total rolling OOS pass, joint-integration pass, and a separate complete CURRENT upgrade procedure.",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "eligible_count": payload["eligible_count"],
        "eligible": eligible,
        "statuses": {cid: item["promotion_status"] for cid, item in reports.items()},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
