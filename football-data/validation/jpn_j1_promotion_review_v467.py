#!/usr/bin/env python3
"""V4.6.7 JPN_J1 CURRENT-compliant promotion-review precheck.

This is a governance/evidence layer only.  It never changes formal weights.
It binds the validated V4.6.6 OOS evidence to the dedicated official 2026
special-season route, then checks whether the actual 2026/27 target season has
started and has point-in-time completed-match history.  The special transition
competition is explicitly forbidden from being pooled into 2026/27.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPETITION_ID = "JPN_J1"
TARGET_SEASON = "2026/27"
TARGET_SEASON_KICKOFF_UTC = datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def count_target_season_rows() -> tuple[int, list[str]]:
    processed_dir = ROOT / "processed" / COMPETITION_ID
    count = 0
    source_paths: list[str] = []
    for path in sorted(processed_dir.glob("*.csv")):
        local_count = 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                season = str(row.get("season") or row.get("Season") or "").strip()
                if season in {"2026/27", "2026-27"}:
                    local_count += 1
        if local_count:
            count += local_count
            source_paths.append(str(path.relative_to(ROOT)))
    return count, source_paths


def build_review() -> dict[str, Any]:
    v466_manifest = load_json(ROOT / "manifests" / "total_goals_joint_integration_v466_status.json")
    v466_report = load_json(
        ROOT / "validation" / "reports" / "total_goals_joint_integration_v466" / "JPN_J1.json"
    )
    transition = load_json(ROOT / "manifests" / "jpn_j1_2026_special_official_v467_status.json")
    bootstrap = load_json(ROOT / "manifests" / "runtime_bootstrap.json")

    checks = {
        "runtime_authority_is_new_repository": (
            bootstrap.get("runtime_authority", {}).get("repository")
            == "FASHI188/FASHI188-football-analysis"
        ),
        "v466_execution_complete": (
            v466_manifest.get("competition_count_failed") == 0
            and v466_manifest.get("competition_count_built") == 10
        ),
        "jpn_v466_ready_for_promotion_review": (
            v466_report.get("status") == "READY_FOR_CURRENT_COMPLIANT_PROMOTION_REVIEW"
        ),
        "v466_formal_weight_still_zero": v466_manifest.get("formal_weight") == 0,
        "official_2026_special_route_validated": (
            transition.get("status") == "OFFICIAL_TRANSITION_ROUTE_VALIDATED"
            and transition.get("audit", {}).get("match_count") == 200
        ),
        "special_transition_kept_separate": (
            transition.get("transition_season_is_separate_domain") is True
            and transition.get("must_not_pool_into_2026_27_target_season") is True
        ),
    }

    target_rows, target_sources = count_target_season_rows()
    now = datetime.now(timezone.utc)
    target_started = now >= TARGET_SEASON_KICKOFF_UTC
    has_target_history = target_rows > 0

    evidence_ready = all(checks.values())
    if not evidence_ready:
        status = "PROMOTION_REVIEW_BLOCKED_EVIDENCE_FAILURE"
    elif not target_started:
        status = "PROMOTION_EVIDENCE_READY_DEPLOYMENT_BLOCKED_TARGET_SEASON_NOT_STARTED"
    elif not has_target_history:
        status = "PROMOTION_EVIDENCE_READY_DEPLOYMENT_BLOCKED_NO_TARGET_SEASON_HISTORY"
    else:
        status = "READY_FOR_TARGET_SEASON_SAMPLE_GATE_REVIEW"

    return {
        "schema_version": "V4.6.7-jpn-j1-promotion-review",
        "generated_at_utc": now.replace(microsecond=0).isoformat(),
        "competition_id": COMPETITION_ID,
        "formal_weight": 0,
        "automatic_promotion": False,
        "status": status,
        "oos_component_evidence": {
            "v466_status": v466_report.get("status"),
            "paired_predictions": v466_report.get("paired_predictions"),
            "outer_folds": v466_report.get("outer_folds"),
            "checks": v466_report.get("checks"),
        },
        "official_transition_route": {
            "status": transition.get("status"),
            "season": transition.get("season"),
            "match_count": transition.get("audit", {}).get("match_count"),
            "stage_counts": transition.get("audit", {}).get("stage_counts"),
            "settlement_scope": transition.get("settlement_scope"),
            "separate_domain": True,
        },
        "target_season_deployment_gate": {
            "target_season": TARGET_SEASON,
            "scheduled_first_kickoff_utc": TARGET_SEASON_KICKOFF_UTC.isoformat(),
            "started_at_review_time": target_started,
            "completed_target_season_rows_found": target_rows,
            "target_season_source_paths": target_sources,
            "current_season_only_policy": True,
            "special_2026_may_supply_2026_27_team_strength": False,
            "blocker": (
                None
                if status == "READY_FOR_TARGET_SEASON_SAMPLE_GATE_REVIEW"
                else "2026 special transition data cannot be pooled into the 2026/27 target season; "
                "formal deployment must wait for verified completed 2026/27 matches and the normal sample gate."
            ),
        },
        "checks": checks,
        "promotion_policy": (
            "Research/governance evidence only. No formal center or nonzero weight change is permitted "
            "without a complete new CURRENT file and the normal project upgrade acceptance procedure."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    review = build_review()
    out = ROOT / "manifests" / "jpn_j1_promotion_review_v467_status.json"
    out.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0 if review["status"] != "PROMOTION_REVIEW_BLOCKED_EVIDENCE_FAILURE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
