#!/usr/bin/env python3
"""Synchronize the 42-item runtime-truth registry from accepted forward/context ledgers.

Audit-only utility. It never changes probabilities, model weights, picks, CURRENT, or historical
events. Status classes remain governed by the existing registry; only evidence text and live counts
are refreshed from manifests that have already passed their own ledger audits.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "manifests"

TRUTH = M / "v6_42_issue_remediation_v6489_status.json"
CTX = M / "v6_context_enriched_forward_v6486_status.json"
AXES = M / "v6_match_context_axes_v6490_status.json"
DELTA = M / "v6_manager_delta_from_verified_baseline_v6491_status.json"
FRESH = M / "v6_fresh_market_forward_challengers_v6492_status.json"
CONDITIONED = M / "v6_context_conditioned_selector_v6495_status.json"
QUEUE = M / "v6_context_priority_queue_v6493_status.json"
UNIFIED = M / "v6_unified_forward_pipeline_v6482_status.json"


def load(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"not an object: {path}")
    return obj


def require_pass(name: str, obj: dict[str, Any]) -> None:
    if obj.get("status") != "PASS":
        raise RuntimeError(f"{name} status is not PASS: {obj.get('status')!r}")
    audit = obj.get("ledger_audit")
    if isinstance(audit, dict) and audit.get("status") != "PASS":
        raise RuntimeError(f"{name} ledger audit is not PASS")


def item_by_id(truth: dict[str, Any], item_id: str) -> dict[str, Any]:
    for row in truth.get("items") or []:
        if isinstance(row, dict) and row.get("id") == item_id:
            return row
    raise RuntimeError(f"missing item {item_id}")


def count_statuses(truth: dict[str, Any]) -> dict[str, int]:
    out = {
        "PASS": 0,
        "PARTIAL": 0,
        "FORWARD_GATED": 0,
        "EXTERNAL_DATA_GAP": 0,
        "TODO": 0,
        "BLOCKED": 0,
    }
    for row in truth.get("items") or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        out[status] = out.get(status, 0) + 1
    return out


def main() -> int:
    truth = load(TRUTH)
    ctx = load(CTX)
    axes = load(AXES)
    delta = load(DELTA)
    fresh = load(FRESH)
    conditioned = load(CONDITIONED)
    queue = load(QUEUE)
    unified = load(UNIFIED)

    for name, obj in (
        ("context", ctx),
        ("axes", axes),
        ("manager_delta", delta),
        ("fresh_forward", fresh),
        ("conditioned_selector", conditioned),
        ("priority_queue", queue),
        ("unified_pipeline", unified),
    ):
        require_pass(name, obj)

    ctx_audit = ctx.get("ledger_audit") or {}
    ctx_features = ctx.get("feature_counts") or {}
    ctx_count = int(ctx_audit.get("event_count") or 0)
    full_xi = int(ctx_features.get("full_predicted_xi") or 0)
    home_av = int(ctx_features.get("home_availability") or 0)
    away_av = int(ctx_features.get("away_availability") or 0)

    axes_audit = axes.get("ledger_audit") or {}
    axes_cov = axes.get("coverage") or {}
    axes_count = int(axes_audit.get("event_count") or 0)
    both_mgr = int(axes_cov.get("manager_both_verified") or 0)
    confirmed_deltas = int(axes_cov.get("confirmed_material_roster_deltas") or 0)
    pending_deltas = int(axes_cov.get("pending_deltas_not_applied") or 0)

    delta_counts = delta.get("delta_counts") or {}
    same_mgr = int(delta_counts.get("SAME_MANAGER") or 0)
    changed_mgr = int(delta_counts.get("MANAGER_CHANGED") or 0)
    no_baseline = int(delta_counts.get("NO_PRIOR_VERIFIED_BASELINE") or 0)
    manager_observations = int(delta.get("current_manager_observation_count") or 0)

    selector_events = int((fresh.get("selector_ledger_audit") or {}).get("event_count") or 0)
    matrix_events = int((fresh.get("matrix_ledger_audit") or {}).get("event_count") or 0)
    selector_eval = fresh.get("selector_evaluation") or {}
    matrix_eval = fresh.get("matrix_evaluation") or {}
    selector_all_settled = int((selector_eval.get("all_settled") or {}).get("count") or 0)
    selector_selected_settled = int((selector_eval.get("selected_settled") or {}).get("count") or 0)
    matrix_settled = int((matrix_eval.get("candidate") or {}).get("count") or 0)

    conditioned_events = int((conditioned.get("ledger_audit") or {}).get("event_count") or 0)
    conditioned_selected_settled = int(
        ((conditioned.get("evaluation") or {}).get("selected_settled") or {}).get("count") or 0
    )
    priority_counts = queue.get("priority_counts") or {}

    item_by_id(truth, "E03")["evidence"] = (
        f"Latest verified context ledger={ctx_count} hash-audited context+market decisions; "
        f"full predicted XI {full_xi}/{ctx_count}; home availability evidence {home_av}; "
        f"away availability evidence {away_av}; competition coverage={ctx.get('by_competition') or {}}. "
        "Missing or conflicted availability remains UNKNOWN and is never converted to no absence."
    )
    item_by_id(truth, "E04")["evidence"] = (
        f"Latest verified manager/roster-axis ledger={axes_count} fixtures; both managers verified "
        f"for {both_mgr}; by competition={axes.get('by_competition') or {}}. Broad future-fixture "
        "coverage remains a live point-in-time requirement."
    )
    item_by_id(truth, "E05")["evidence"] = (
        f"Verified current manager observations={manager_observations}; baseline comparison: "
        f"SAME_MANAGER={same_mgr}, MANAGER_CHANGED={changed_mgr}, "
        f"NO_PRIOR_VERIFIED_BASELINE={no_baseline}. Current identity alone never means no change; "
        "a strictly earlier verified baseline is required."
    )
    item_by_id(truth, "E06")["evidence"] = (
        f"End-to-end decision-freeze refresh is verified on {ctx_count} context+market decisions and "
        f"{both_mgr} both-manager axis decisions. Current priority queue="
        f"{priority_counts}. Runtime truth is sourced from accepted ledgers, not stale scheduling state."
    )
    item_by_id(truth, "F05")["evidence"] = (
        "Historical source only: V6.47.4 fixed1000 challenge=208/322=64.60%. "
        f"Current V6.49.2 chain has {selector_events} frozen 1X2 events, "
        f"{selector_all_settled} all-settled and {selector_selected_settled} selected-settled; "
        f"decision={selector_eval.get('decision')}. V6.49.5 has {conditioned_events} post-epoch "
        f"context-conditioned events and {conditioned_selected_settled} selected-settled. "
        ">=100 selected settled plus quality/domain gates remain required before promotion or context-veto review."
    )
    item_by_id(truth, "F06")["evidence"] = (
        "Historical source only: V6.47.8 nominated total=comp|margin=full. "
        f"Current V6.49.2 chain has {matrix_events} frozen matrix events and {matrix_settled} settled "
        f"candidate evaluations; decision={matrix_eval.get('decision')}. >=100 paired settled with "
        "total/score proper-score nonworsening remains required."
    )

    truth["schema_version"] = "V6.49.5-42-issue-runtime-truth-registry-r6"
    truth["generated_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    truth["formal_current_version"] = "V5.0.1"
    truth["formal_probability_change"] = False
    truth["formal_weight_change"] = False
    truth["summary"] = count_statuses(truth)

    acq = truth.setdefault("current_forward_acquisition", {})
    pipeline_infra = unified.get("market_result_infrastructure") or {}
    acq.update({
        "full17_identity_domains": int((unified.get("acquisition") or {}).get("identity_available_domain_count") or 0),
        "market_result_infrastructure_prediction_count": int(pipeline_infra.get("prediction_count") or 0),
        "market_result_infrastructure_settled_count": int(pipeline_infra.get("settlement_count") or 0),
        "current_v6492_selector_events": selector_events,
        "current_v6492_matrix_events": matrix_events,
        "current_v6492_settled": selector_all_settled,
        "current_v6492_selected_settled": selector_selected_settled,
        "current_v6492_matrix_settled": matrix_settled,
        "clean_context_market_decision_events_verified": ctx_count,
        "full_predicted_xi_context_events_verified": full_xi,
        "home_availability_context_events_verified": home_av,
        "away_availability_context_events_verified": away_av,
        "manager_axis_events_verified": axes_count,
        "manager_both_verified_events": both_mgr,
        "confirmed_material_roster_deltas": confirmed_deltas,
        "pending_roster_deltas_not_applied": pending_deltas,
        "manager_delta_same": same_mgr,
        "manager_delta_changed": changed_mgr,
        "manager_delta_no_prior_verified_baseline": no_baseline,
        "v6495_context_effect_epoch": (conditioned.get("freeze") or {}).get("freeze_timestamp_utc"),
        "v6495_latest_verified_event_count": conditioned_events,
        "v6495_latest_selected_settled": conditioned_selected_settled,
        "v6495_latest_verified_status": conditioned.get("status"),
        "v6493_priority_queue_generated_at_utc": queue.get("generated_at_utc"),
        "v6493_priority_counts": priority_counts,
    })
    truth["runtime_sync_sources"] = {
        "context": ctx.get("generated_at_utc"),
        "axes": axes.get("generated_at_utc"),
        "manager_delta": delta.get("generated_at_utc"),
        "fresh_forward": fresh.get("generated_at_utc"),
        "conditioned_selector": conditioned.get("generated_at_utc"),
        "priority_queue": queue.get("generated_at_utc"),
        "unified_pipeline": unified.get("generated_at_utc"),
    }
    truth.setdefault("hard_rules", {})["runtime_truth_counts_are_ledger_derived"] = True
    truth["hard_rules"]["status_sync_cannot_change_probabilities_or_weights"] = True

    if truth["summary"].get("PASS") != 36 or truth["summary"].get("PARTIAL") != 4 or truth["summary"].get("FORWARD_GATED") != 2:
        raise RuntimeError(f"unexpected 42-item status composition: {truth['summary']}")
    if ctx_count < full_xi or axes_count < both_mgr:
        raise RuntimeError("coverage count invariant failed")
    if fresh.get("governance", {}).get("formal_weight") != 0:
        raise RuntimeError("fresh forward formal-weight guard failed")
    if conditioned.get("governance", {}).get("context_probability_adjustment") is not False:
        raise RuntimeError("conditioned probability-adjustment guard failed")

    TRUTH.write_text(json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "context_events": ctx_count,
        "both_managers": both_mgr,
        "selector_events": selector_events,
        "matrix_events": matrix_events,
        "conditioned_events": conditioned_events,
        "priority_counts": priority_counts,
        "summary": truth["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
