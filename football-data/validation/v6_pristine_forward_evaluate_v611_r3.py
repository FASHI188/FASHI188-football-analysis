#!/usr/bin/env python3
"""V6.1.1 ledger-native evaluator with V6.1.3 audit invalidation gates.

Only settled V6.1.2 predictions that remain valid under the latest V6.1.3 audit may
enter forward metrics. A global V6.1.3 FAIL status blocks evaluation completely.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import v6_pristine_forward_evaluate_v611_r2 as baseeval
from platform_core import PlatformError, atomic_write_json, load_json

ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "manifests" / "v6_pristine_forward_freeze_v610_status.json"
LEDGER = ROOT / "forward" / "v6_pristine_forward_events_v612.json"
AUDIT = ROOT / "manifests" / "v6_pristine_forward_audit_v613_status.json"
OUT = ROOT / "manifests" / "v6_pristine_forward_evaluation_v611_status.json"
SCHEMA = "V6.1.1-pristine-forward-evaluation-r3-audit-aware"


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    freeze = load_json(FREEZE)
    if freeze.get("status") != "PASS":
        raise PlatformError("V6.1.0 freeze receipt must be PASS")
    if not AUDIT.exists():
        payload = {
            "schema_version": SCHEMA,
            "generated_at_utc": generated.isoformat(),
            "status": "FAIL_V613_AUDIT_MISSING",
            "evaluation_status": "BLOCKED",
            "governance": {"automatic_promotion": False, "current_rule_change": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    audit = load_json(AUDIT)
    audit_status = str(audit.get("status") or "")
    if audit.get("schema_version") != "V6.1.3-forward-audit-status-r1" or audit_status.startswith("FAIL_"):
        payload = {
            "schema_version": SCHEMA,
            "generated_at_utc": generated.isoformat(),
            "status": "FAIL_V613_AUDIT_GATE",
            "evaluation_status": "BLOCKED",
            "v613_audit_status": audit_status,
            "v613_evaluation_blocked": audit.get("evaluation_blocked"),
            "governance": {"automatic_promotion": False, "current_rule_change": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    ledger = load_json(LEDGER) if LEDGER.exists() else {"schema_version": baseeval.ledgerlib.LEDGER_SCHEMA, "events": []}
    chain_audit = baseeval.ledgerlib._audit_chain(ledger)
    source_integrity = baseeval.ledgerlib._source_integrity(freeze)
    rows_all, semantic_errors, open_predictions = baseeval._materialize(freeze, ledger)

    invalidated = {str(value) for value in (audit.get("invalidated_match_ids") or [])}
    rows = [row for row in rows_all if str(row.get("match_id")) not in invalidated]
    excluded_rows = [row for row in rows_all if str(row.get("match_id")) in invalidated]

    integrity_status = "PASS" if (
        chain_audit["status"] == "PASS"
        and source_integrity["status"] == "PASS"
        and not semantic_errors
    ) else "FAIL"
    if integrity_status != "PASS":
        payload = {
            "schema_version": SCHEMA,
            "generated_at_utc": generated.isoformat(),
            "status": "FAIL_LEDGER_INTEGRITY",
            "evaluation_status": "BLOCKED",
            "ledger_chain_audit": chain_audit,
            "frozen_source_integrity": source_integrity,
            "semantic_errors": semantic_errors,
            "v613_audit_status": audit_status,
            "invalidated_match_ids": sorted(invalidated),
            "governance": {"automatic_promotion": False, "current_rule_change": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    summaries = {arm: baseeval._summary(rows, arm, len(rows)) for arm in baseeval.ARMS}
    arm_a = summaries["arm_a_v605_asymmetric"]
    arm_b = summaries["arm_b_home_only"]
    benchmark = summaries["benchmark_v601_pooled_top5"]
    gates = freeze["forward_evaluation_gates"]
    minimums_met = (
        len(rows) >= int(gates["minimum_completed_forward_matches"])
        and int(arm_a["count"]) >= int(gates["minimum_arm_a_selections"])
        and int(arm_b["count"]) >= int(gates["minimum_arm_b_selections"])
        and int(benchmark["count"]) >= int(gates["minimum_benchmark_selections"])
        and int(arm_a["competitions_represented"]) >= int(gates["minimum_competitions_represented"])
    )
    arm_a_bootstrap = baseeval._bootstrap(rows, "arm_a_v605_asymmetric", "benchmark_v601_pooled_top5")
    arm_b_bootstrap = baseeval._bootstrap(rows, "arm_b_home_only", "benchmark_v601_pooled_top5")
    fail_reasons: list[str] = []
    promotion_gate_passed = False
    if minimums_met:
        if arm_a["accuracy"] is None or benchmark["accuracy"] is None or float(arm_a["accuracy"]) < float(benchmark["accuracy"]):
            fail_reasons.append("arm A accuracy below benchmark")
        if arm_a["wilson90_lower"] is None or float(arm_a["wilson90_lower"]) < float(gates["arm_a_primary"]["wilson90_lower_minimum"]):
            fail_reasons.append("arm A Wilson 90% lower bound below gate")
        if arm_a_bootstrap is None or float(arm_a_bootstrap["ci90"][0]) < float(gates["arm_a_primary"]["paired_bootstrap90_lower_minimum"]):
            fail_reasons.append("arm A bootstrap lower bound below gate")
        if arm_b["wilson90_lower"] is None or float(arm_b["wilson90_lower"]) < float(gates["arm_b_secondary"]["wilson90_lower_minimum"]):
            fail_reasons.append("arm B Wilson 90% lower bound below gate")
        if arm_b_bootstrap is None or float(arm_b_bootstrap["ci90"][0]) < float(gates["arm_b_secondary"]["paired_bootstrap90_lower_minimum"]):
            fail_reasons.append("arm B bootstrap lower bound below gate")
        promotion_gate_passed = not fail_reasons

    if not rows:
        evaluation_status = "PENDING_NO_VALID_SETTLED_FORWARD_PREDICTIONS"
    elif not minimums_met:
        evaluation_status = "PENDING_MINIMUM_SAMPLE"
    elif promotion_gate_passed:
        evaluation_status = "FORWARD_GATE_PASS_REQUIRES_MANUAL_REVIEW"
    else:
        evaluation_status = "FORWARD_GATE_FAIL"

    payload = {
        "schema_version": SCHEMA,
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "evaluation_status": evaluation_status,
        "freeze_timestamp_utc": freeze["freeze_timestamp_utc"],
        "forward_start_date_utc": freeze["forward_start_date_utc"],
        "ledger_chain_audit": chain_audit,
        "frozen_source_integrity": source_integrity,
        "semantic_errors": semantic_errors,
        "v613_audit_status": audit_status,
        "v613_audit_generated_at_utc": audit.get("generated_at_utc"),
        "total_settled_before_v613_exclusions": len(rows_all),
        "completed_forward_match_count": len(rows),
        "excluded_invalidated_settled_count": len(excluded_rows),
        "invalidated_match_ids": sorted(invalidated),
        "open_prediction_count": open_predictions,
        "arms": summaries,
        "arm_a_vs_benchmark_bootstrap": arm_a_bootstrap,
        "arm_b_vs_benchmark_bootstrap": arm_b_bootstrap,
        "minimum_sample_gate_met": minimums_met,
        "promotion_gate_passed": promotion_gate_passed,
        "promotion_gate_fail_reasons": fail_reasons,
        "governance": {
            "ledger_native_pre_match_predictions_only": True,
            "v613_invalidated_samples_excluded": True,
            "v613_global_fail_blocks_evaluation": True,
            "postmatch_prediction_reconstruction": False,
            "frozen_forward_evaluation_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "manual_review_required_even_if_gate_passes": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
