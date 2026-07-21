#!/usr/bin/env python3
"""Adjudicate the 17-domain V5 Bayesian dynamic-state OOF screen.

This script never promotes probability weights. It classifies domains into:
- strong shadow candidates: probability-side gates pass, selected profile is stable,
  and every evaluated outer season is non-worse on 1X2 Top-1 accuracy;
- conditional shadow candidates: probability-side gates pass but profile stability or
  worst-season accuracy is not yet sufficient;
- rejected: keep formal_weight=0;
- evidence insufficient: fail closed without lowering sample gates.

The fourth target (handicap) remains a hard blocker for formal promotion whenever
complete point-in-time frozen handicap lines are unavailable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platform_core import PlatformError, atomic_write_json, load_json, sha256_file

ROOT = Path(__file__).resolve().parents[1]
AGGREGATE = ROOT / "manifests" / "bayesian_dynamic_state_oof_v500_status.json"
REPORT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v500"
OUT = ROOT / "manifests" / "bayesian_dynamic_state_adjudication_v500_status.json"
SHADOW = ROOT / "config" / "bayesian_dynamic_state_shadow_registry_v500.json"

CALENDAR_TARGET = {
    "ARG_Primera", "BRA_SerieA", "JPN_J1", "KOR_KLeague1",
    "NOR_Eliteserien", "SWE_Allsvenskan", "USA_MLS",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def target_season(competition_id: str) -> str:
    return "2026" if competition_id in CALENDAR_TARGET else "2026/27"


def probability_checks_pass(report: dict[str, Any]) -> bool:
    checks = report.get("checks") or {}
    required = [
        "at_least_two_forward_outer_seasons",
        "minimum_outer_predictions_500",
        "one_x_two_brier_ci_improves",
        "one_x_two_rps_ci_improves",
        "total_rps_ci_noninferior",
        "joint_log_ci_noninferior",
        "one_x_two_accuracy_nonworse",
        "score_top1_nonworse",
        "score_top3_nonworse",
        "total_top1_nonworse",
        "probability_conservation",
        "total_projection_conservation",
    ]
    return all(checks.get(key) is True for key in required)


def evaluated_folds(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        fold for fold in (report.get("folds") or [])
        if fold.get("status") == "EVALUATED_FORWARD_FROZEN_PROFILE"
    ]


def classify_candidate(competition_id: str, report: dict[str, Any]) -> dict[str, Any]:
    folds = evaluated_folds(report)
    profiles = [str(fold.get("selected_profile") or "") for fold in folds]
    profile_stable = bool(profiles) and len(set(profiles)) == 1
    accuracy_diffs = [
        float(((fold.get("metrics") or {}).get("one_x_two_accuracy") or {}).get("candidate_minus_baseline"))
        for fold in folds
    ]
    worst_accuracy_diff = min(accuracy_diffs) if accuracy_diffs else None
    all_seasons_accuracy_nonworse = worst_accuracy_diff is not None and worst_accuracy_diff >= -1e-12
    probability_pass = probability_checks_pass(report)
    handicap_available = (report.get("checks") or {}).get("handicap_fourth_target_available") is True

    if probability_pass and profile_stable and all_seasons_accuracy_nonworse:
        classification = "STRONG_SHADOW_CANDIDATE_AH_BLOCKED"
    elif probability_pass:
        classification = "CONDITIONAL_SHADOW_CANDIDATE_AH_BLOCKED"
    else:
        classification = "REJECT_KEEP_FORMAL_WEIGHT_0"

    pooled = report.get("pooled_metrics") or {}
    return {
        "competition_id": competition_id,
        "target_season": target_season(competition_id),
        "classification": classification,
        "formal_weight": 0,
        "probability_mutation": False,
        "probability_side_checks_pass": probability_pass,
        "evaluated_outer_seasons": len(folds),
        "outer_prediction_count": report.get("outer_prediction_count"),
        "selected_profiles": profiles,
        "profile_stable": profile_stable,
        "frozen_shadow_profile": profiles[-1] if profiles else None,
        "worst_outer_season_one_x_two_accuracy_difference": worst_accuracy_diff,
        "all_outer_seasons_one_x_two_accuracy_nonworse": all_seasons_accuracy_nonworse,
        "pooled_differences": {
            metric: (pooled.get(metric) or {}).get("candidate_minus_baseline")
            for metric in (
                "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps",
                "joint_log", "score_top1", "score_top3", "total_top1",
                "total_top2", "total_rps",
            )
        },
        "handicap_fourth_target_available": handicap_available,
        "formal_promotion_blockers": [] if handicap_available else [
            "complete point-in-time frozen handicap lines unavailable",
            "four-target joint promotion gate incomplete",
        ],
        "source_report": str((REPORT_DIR / f"{competition_id}.json").relative_to(ROOT)),
        "source_report_sha256": sha256_file(REPORT_DIR / f"{competition_id}.json"),
    }


def main() -> int:
    if not AGGREGATE.exists():
        raise PlatformError("Bayesian dynamic-state aggregate receipt missing")
    aggregate = load_json(AGGREGATE)
    reports_summary = aggregate.get("reports") or {}
    if int(aggregate.get("competition_count_requested", 0)) != 17:
        raise PlatformError("expected 17 requested domains")
    if int(aggregate.get("competition_count_completed", 0)) != 17:
        raise PlatformError("all 17 domain jobs must persist a receipt")

    strong: list[str] = []
    conditional: list[str] = []
    rejected: list[str] = []
    insufficient: list[str] = []
    adjudications: dict[str, Any] = {}

    for competition_id in sorted(reports_summary):
        summary = reports_summary[competition_id]
        report_path = REPORT_DIR / f"{competition_id}.json"
        if not report_path.exists():
            raise PlatformError(f"missing domain receipt: {competition_id}")
        report = load_json(report_path)
        status = str(summary.get("status") or report.get("status") or "")
        if status == "FAILED":
            reason = str(summary.get("reason") or report.get("reason") or "")
            adjudications[competition_id] = {
                "competition_id": competition_id,
                "target_season": target_season(competition_id),
                "classification": "EVIDENCE_INSUFFICIENT_KEEP_FORMAL_WEIGHT_0",
                "formal_weight": 0,
                "probability_mutation": False,
                "reason": reason,
                "sample_gate_lowered": False,
                "required_next_evidence": (
                    "add older UEFA Champions League main-tournament seasons while preserving "
                    "qualifying/main-tournament separation and the fixed prior-selection minimum"
                    if competition_id == "UEFA_ChampionsLeague"
                    else "repair domain evidence without weakening validation gates"
                ),
                "source_report": str(report_path.relative_to(ROOT)),
                "source_report_sha256": sha256_file(report_path),
            }
            insufficient.append(competition_id)
            continue

        item = classify_candidate(competition_id, report)
        adjudications[competition_id] = item
        classification = item["classification"]
        if classification.startswith("STRONG_SHADOW"):
            strong.append(competition_id)
        elif classification.startswith("CONDITIONAL_SHADOW"):
            conditional.append(competition_id)
        else:
            rejected.append(competition_id)

    expected_candidates = set(aggregate.get("research_review_candidates_ah_pending") or [])
    actual_probability_candidates = set(strong) | set(conditional)
    if expected_candidates != actual_probability_candidates:
        raise PlatformError(
            f"candidate mismatch aggregate={sorted(expected_candidates)} adjudicated={sorted(actual_probability_candidates)}"
        )

    payload = {
        "schema_version": "V5.0.0-bayesian-dynamic-state-adjudication-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS_SHADOW_ONLY_NO_FORMAL_PROMOTION",
        "aggregate_receipt": str(AGGREGATE.relative_to(ROOT)),
        "aggregate_receipt_sha256": sha256_file(AGGREGATE),
        "domain_count": len(adjudications),
        "strong_shadow_candidates_ah_blocked": strong,
        "conditional_shadow_candidates_ah_blocked": conditional,
        "rejected_keep_formal_weight_0": rejected,
        "evidence_insufficient_keep_formal_weight_0": insufficient,
        "adjudications": adjudications,
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": (
            "Shadow registration permits side-by-side audit only. It never changes the formal matrix. "
            "Formal promotion remains blocked until competition-specific four-target evidence, including "
            "complete point-in-time frozen handicap lines, is available and a hash-bound promotion receipt is issued."
        ),
    }

    shadow_registry = {
        "schema_version": "V5.0.0-bayesian-dynamic-state-shadow-registry-r1",
        "generated_at_utc": payload["generated_at_utc"],
        "status": "SHADOW_ONLY",
        "formal_weight": 0,
        "probability_mutation": False,
        "domains": {
            competition_id: {
                "target_season": adjudications[competition_id]["target_season"],
                "classification": adjudications[competition_id]["classification"],
                "profile": adjudications[competition_id].get("frozen_shadow_profile"),
                "formal_weight": 0,
                "output_role": "read_only_shadow_audit",
            }
            for competition_id in strong + conditional
        },
        "blocked_domains": insufficient,
        "policy": "No shadow output may replace or modify the formal V5 joint score matrix.",
    }

    atomic_write_json(OUT, payload)
    atomic_write_json(SHADOW, shadow_registry)
    print({
        "status": payload["status"],
        "strong": strong,
        "conditional": conditional,
        "rejected_count": len(rejected),
        "insufficient": insufficient,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
