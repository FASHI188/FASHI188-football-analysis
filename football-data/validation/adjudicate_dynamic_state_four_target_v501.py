#!/usr/bin/env python3
"""Issue the final four-target verdict for the V5.0.1 dynamic-state challenger.

The verdict binds same-day-safe probability evidence, fixed-profile second-stage
validation and retrospective AH research. It never promotes weights.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platform_core import PlatformError, atomic_write_json, load_json, sha256_file

ROOT = Path(__file__).resolve().parents[1]
ADJUDICATION = ROOT / "manifests" / "bayesian_dynamic_state_adjudication_v501_status.json"
SECOND_STAGE = ROOT / "manifests" / "bayesian_dynamic_state_second_stage_v501_status.json"
AH = ROOT / "manifests" / "retrospective_ah_dynamic_state_v501_status.json"
OUT = ROOT / "manifests" / "bayesian_dynamic_state_four_target_verdict_v501_status.json"


def _delta(item: dict[str, Any], metric: str) -> float | None:
    block = (item.get("pooled_metrics") or {}).get(metric) or {}
    value = block.get("candidate_minus_baseline")
    return float(value) if value is not None else None


def main() -> int:
    for path in (ADJUDICATION, SECOND_STAGE, AH):
        if not path.exists():
            raise PlatformError(f"required receipt missing: {path.name}")

    adjudication = load_json(ADJUDICATION)
    second_stage = load_json(SECOND_STAGE)
    ah = load_json(AH)

    if adjudication.get("same_day_outcomes_withheld") is not True:
        raise PlatformError("adjudication is not same-day-safe")
    if second_stage.get("same_day_outcomes_withheld") is not True:
        raise PlatformError("second-stage receipt is not same-day-safe")
    if ah.get("same_day_outcomes_withheld") is not True:
        raise PlatformError("AH comparison is not same-day-safe")

    domains: dict[str, Any] = {}
    formally_rejected: list[str] = []
    evidence_incomplete: list[str] = []

    for competition_id in second_stage.get("second_stage_shadow_pass_ah_blocked") or []:
        probability = (second_stage.get("reports") or {}).get(competition_id) or {}
        ah_report = (ah.get("reports") or {}).get(competition_id)
        record = {
            "competition_id": competition_id,
            "formal_weight": 0,
            "probability_change": False,
            "one_x_two_accuracy_difference": _delta(probability, "one_x_two_accuracy"),
            "one_x_two_brier_difference": _delta(probability, "one_x_two_brier"),
            "one_x_two_rps_difference": _delta(probability, "one_x_two_rps"),
            "joint_log_difference": _delta(probability, "joint_log"),
            "score_top1_difference": _delta(probability, "score_top1"),
            "score_top3_difference": _delta(probability, "score_top3"),
            "total_top1_difference": _delta(probability, "total_top1"),
            "total_top2_difference": _delta(probability, "total_top2"),
            "total_rps_difference": _delta(probability, "total_rps"),
        }

        if ah_report is None:
            record.update({
                "classification": "EVIDENCE_INCOMPLETE_KEEP_FORMAL_WEIGHT_0",
                "four_target_gate": "INCOMPLETE",
                "handicap_evidence": "UNAVAILABLE",
                "reason": "No public historical AH source in the current research chain; fourth target cannot be evaluated.",
            })
            evidence_incomplete.append(competition_id)
        else:
            aggregate = ah_report.get("aggregate") or {}
            hit_delta = aggregate.get("candidate_minus_baseline_hit_rate")
            payoff_delta = aggregate.get("candidate_minus_baseline_mean_settlement_payoff")
            record["retrospective_ah_hit_rate_difference"] = hit_delta
            record["retrospective_ah_mean_settlement_payoff_difference"] = payoff_delta
            record["retrospective_ah_reference_only"] = True
            record["original_quote_timestamp_available"] = False

            ah_nonworse = (
                hit_delta is not None and payoff_delta is not None
                and float(hit_delta) >= -1e-12
                and float(payoff_delta) >= -1e-12
            )
            if ah_nonworse:
                record.update({
                    "classification": "RESEARCH_ONLY_AH_NONWORSE_FORMAL_PIT_GATE_STILL_BLOCKED",
                    "four_target_gate": "RESEARCH_NONWORSE_BUT_FORMAL_PIT_INCOMPLETE",
                    "reason": "Retrospective AH evidence is non-worse, but original quote timestamps are unavailable and cannot authorize formal promotion.",
                })
                evidence_incomplete.append(competition_id)
            else:
                record.update({
                    "classification": "REJECT_FORMAL_PROMOTION_HANDICAP_DEGRADED",
                    "four_target_gate": "FAILED",
                    "reason": "Dynamic-state candidate degraded retrospective AH direction and/or settlement payoff despite probability-side improvements.",
                })
                formally_rejected.append(competition_id)
        domains[competition_id] = record

    payload = {
        "schema_version": "V5.0.1-bayesian-dynamic-state-four-target-verdict-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS_NO_FORMAL_PROMOTION",
        "same_day_outcomes_withheld": True,
        "source_receipts": {
            "adjudication": str(ADJUDICATION.relative_to(ROOT)),
            "adjudication_sha256": sha256_file(ADJUDICATION),
            "second_stage": str(SECOND_STAGE.relative_to(ROOT)),
            "second_stage_sha256": sha256_file(SECOND_STAGE),
            "retrospective_ah": str(AH.relative_to(ROOT)),
            "retrospective_ah_sha256": sha256_file(AH),
        },
        "rejected_due_handicap_degradation": formally_rejected,
        "evidence_incomplete_due_handicap_unavailable_or_non_pit": evidence_incomplete,
        "domains": domains,
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_verdict": {
            "ESP_LaLiga": "REJECT",
            "GER_Bundesliga": "REJECT",
            "JPN_J1": "HOLD_WEIGHT_0_EVIDENCE_INCOMPLETE",
        },
        "next_research_contract": {
            "name": "AH-constrained dynamic-state blend",
            "rule": "Blend weight must be selected using only earlier completed seasons. Any weight that degrades prior-season AH hit rate or mean settlement payoff is ineligible. Target-season evaluation is fully forward-frozen.",
            "formal_status": "RESEARCH_ONLY",
            "default_formal_weight": 0,
        },
        "policy": "Probability-side gains cannot override a failed or unavailable fourth-target handicap gate. The formal V5 joint matrix remains unchanged.",
    }
    atomic_write_json(OUT, payload)
    print({
        "status": payload["status"],
        "rejected": formally_rejected,
        "incomplete": evidence_incomplete,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
