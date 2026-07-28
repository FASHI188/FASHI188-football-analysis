#!/usr/bin/env python3
"""V6.49.4 unified forward-pipeline audit.

Current active prediction targets are locked by V6.49.4 scope:
  1) 90-minute 1X2
  2) direct total goals
  3) exact score from the unified total/margin matrix

Asian-handicap prices remain synchronized market input/audit fields only. Retired handicap
prediction research and the old V6.47.5/V6.47.9 forward ledgers have no current execution
authority. The old artifacts remain visible only under historical_sources because V6.49.2 uses
frozen definitions/library functions derived from them.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_unified_forward_pipeline_v6482_status.json"
PATHS = {
    "scope": ROOT / "manifests" / "v6_current_research_scope_v6494_status.json",
    "identity": ROOT / "config" / "v6_full17_identity_registry_v6482.json",
    "capture": ROOT / "manifests" / "v6_full17_kambi_capture_v6482_status.json",
    "market_ledger": ROOT / "forward" / "v6_market_first_events_v651.json",
    "lead": ROOT / "manifests" / "v6_lead_bucket_forward_v6469_status.json",
    "fresh": ROOT / "manifests" / "v6_fresh_market_forward_challengers_v6492_status.json",
    "legacy_selector": ROOT / "manifests" / "v6_hierarchical_selector_forward_v6475_status.json",
    "legacy_matrix": ROOT / "manifests" / "v6_total_margin_forward_v6479_status.json",
    "legacy_context": ROOT / "manifests" / "v6_match_context_pit_v6310_status.json",
    "context_queue": ROOT / "manifests" / "v6_match_context_resolution_queue_v6488_status.json",
    "priority_queue": ROOT / "manifests" / "v6_context_priority_queue_v6493_status.json",
    "resolver": ROOT / "manifests" / "v6_market_first_result_resolver_v651_status.json",
}
TARGETS = (
    "ARG_Primera", "BRA_SerieA", "ENG_PremierLeague", "ESP_LaLiga", "FRA_Ligue1",
    "GER_Bundesliga", "ITA_SerieA", "JPN_J1", "KOR_KLeague1", "NED_Eredivisie",
    "NOR_Eliteserien", "POR_PrimeiraLiga", "SCO_Premiership", "SUI_SuperLeague",
    "SWE_Allsvenskan", "UEFA_ChampionsLeague", "USA_MLS",
)
EXPECTED_ACTIVE_TARGETS = ["90m_1x2", "direct_total_goals", "exact_score_from_unified_matrix"]


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def base_ledger_metrics(ledger: dict[str, Any]) -> dict[str, Any]:
    events = [e for e in (ledger.get("events") or []) if isinstance(e, dict)]
    preds = [e for e in events if e.get("event_type") == "MARKET_PREDICTION_FROZEN"]
    settlements = [e for e in events if e.get("event_type") == "RESULT_SETTLED"]
    pred_by_domain = Counter()
    for e in preds:
        fi = (e.get("payload") or {}).get("fixture_identity") or {}
        cid = str(fi.get("competition_id") or "")
        if cid:
            pred_by_domain[cid] += 1
    return {
        "role": "market_and_result_infrastructure_not_prediction_target",
        "prediction_count": len(preds),
        "settlement_count": len(settlements),
        "prediction_by_domain": {cid: pred_by_domain.get(cid, 0) for cid in TARGETS},
        "domains_with_predictions": sum(1 for cid in TARGETS if pred_by_domain.get(cid, 0) > 0),
    }


def fresh_selector_metrics(x: dict[str, Any]) -> dict[str, Any]:
    ev = x.get("selector_evaluation") or {}
    selected = ev.get("selected_settled") or {}
    all_settled = ev.get("all_settled") or {}
    gate = ev.get("forward_gate") or {}
    audit = x.get("selector_ledger_audit") or {}
    return {
        "status": x.get("status") or "MISSING",
        "freeze_timestamp_utc": (x.get("freeze") or {}).get("freeze_timestamp_utc"),
        "ledger_event_count": audit.get("event_count"),
        "all_settled": all_settled.get("count", 0),
        "selected_settled": selected.get("count", 0),
        "selected_hits": selected.get("hits"),
        "selected_accuracy": selected.get("accuracy"),
        "forward_gate_met": gate.get("all_pass", False),
        "decision": ev.get("decision"),
    }


def fresh_matrix_metrics(x: dict[str, Any]) -> dict[str, Any]:
    ev = x.get("matrix_evaluation") or {}
    cand = ev.get("candidate") or {}
    gate = ev.get("forward_gate") or {}
    audit = x.get("matrix_ledger_audit") or {}
    return {
        "status": x.get("status") or "MISSING",
        "freeze_timestamp_utc": (x.get("freeze") or {}).get("freeze_timestamp_utc"),
        "ledger_event_count": audit.get("event_count"),
        "settled": cand.get("count", 0),
        "total_top1_accuracy": cand.get("total_top1_accuracy"),
        "score_top1_accuracy": cand.get("exact_score_top1_accuracy"),
        "score_top3_accuracy": cand.get("exact_score_top3_accuracy"),
        "total_log_loss": cand.get("total_log_loss"),
        "total_rps": cand.get("total_rps"),
        "score_log_loss": cand.get("exact_score_log_loss"),
        "forward_gate_met": gate.get("all_pass", False),
        "decision": ev.get("decision"),
    }


def historical_source_summary(selector: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_authority": 0,
        "hourly_execution": False,
        "v6475_selector": {
            "role": "historical_forward_ledger_and_frozen_selector_definition_source",
            "ledger_event_count": (selector.get("ledger_audit") or {}).get("event_count"),
            "decision": (selector.get("evaluation") or {}).get("decision"),
        },
        "v6479_matrix": {
            "role": "historical_forward_ledger_and_library_source_for_v6492",
            "ledger_event_count": (matrix.get("ledger_audit") or {}).get("event_count"),
            "decision": (matrix.get("evaluation") or {}).get("decision"),
        },
    }


def main() -> int:
    data = {k: load(v) for k, v in PATHS.items()}
    scope, identity, capture = data["scope"], data["identity"], data["capture"]
    base = base_ledger_metrics(data["market_ledger"])
    fresh_selector = fresh_selector_metrics(data["fresh"])
    fresh_matrix = fresh_matrix_metrics(data["fresh"])
    legacy_ctx, queue, pqueue = data["legacy_context"], data["context_queue"], data["priority_queue"]

    identity_counts = {cid: int(((identity.get("competitions") or {}).get(cid) or {}).get("team_count") or 0) for cid in TARGETS}
    mapped_events = {cid: int((capture.get("mapped_domain_event_counts") or {}).get(cid) or 0) for cid in TARGETS}
    queue_counts = queue.get("counts") or {}

    hard_checks = {
        "scope_guard_pass": scope.get("status") == "PASS",
        "active_targets_exact": scope.get("active_prediction_targets_in_order") == EXPECTED_ACTIVE_TARGETS,
        "handicap_prediction_retired": scope.get("retired_handicap_prediction_target") is True,
        "asian_handicap_input_retained": scope.get("asian_handicap_market_input_retained") is True,
        "no_forbidden_active_files": not (scope.get("forbidden_active_files") or []),
        "identity_targets_17": int(identity.get("target_competition_count") or 0) == 17,
        "capture_targets_17": int(capture.get("target_domain_count") or 0) == 17,
        "market_ledger_present": bool(data["market_ledger"]),
        "fresh_challenger_pass": data["fresh"].get("status") == "PASS",
        "fresh_future_fixture_only": ((data["fresh"].get("governance") or {}).get("future_fixture_only") is True),
        "fresh_no_historical_result_backfill": ((data["fresh"].get("governance") or {}).get("historical_result_backfill") is False),
        "fresh_formal_weight_zero": ((data["fresh"].get("governance") or {}).get("formal_weight") == 0),
        "legacy_context_audit_present": bool(legacy_ctx),
        "live_context_queue_present": bool(queue),
        "live_context_missing_means_unknown": ((queue.get("governance") or {}).get("missing_means_unknown_not_no_change") is True),
    }
    errors = [k for k, ok in hard_checks.items() if not ok]

    payload = {
        "schema_version": "V6.49.4-unified-forward-pipeline-status-r3",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS" if not errors else "WARN_PIPELINE_GAPS",
        "active_prediction_targets_in_order": EXPECTED_ACTIVE_TARGETS,
        "target_domains": list(TARGETS),
        "acquisition": {
            "identity_registry_status": identity.get("status") or "MISSING",
            "identity_available_domain_count": identity.get("available_competition_count"),
            "identity_team_counts": identity_counts,
            "capture_status": capture.get("status") or "MISSING",
            "target_group_event_count_this_run": capture.get("target_group_event_count", 0),
            "not_started_target_event_count_this_run": capture.get("not_started_target_event_count", 0),
            "identity_resolved_count_this_run": capture.get("identity_resolved_count", 0),
            "identity_unresolved_count_this_run": capture.get("identity_unresolved_count", 0),
            "new_market_snapshots_this_run": capture.get("formal_snapshot_count_written", 0),
            "valid_market_snapshots_this_run": capture.get("formal_snapshot_count_available", 0),
            "mapped_domain_event_counts_this_run": mapped_events,
            "domains_with_current_kambi_target_events": sum(1 for cid in TARGETS if mapped_events[cid] > 0),
            "provider_group_inventory": capture.get("group_inventory") or {},
            "asian_handicap_role": "SYNCHRONIZED_MARKET_INPUT_AND_AUDIT_ONLY",
        },
        "market_result_infrastructure": base,
        "timing_diagnostic": {
            "status": data["lead"].get("status") or "MISSING",
            "ledger_event_count": (data["lead"].get("ledger_audit") or {}).get("event_count"),
            "evaluation": data["lead"].get("evaluation") or {},
        },
        "current_challengers": {
            "v6492_90m_1x2": fresh_selector,
            "v6492_direct_total_and_exact_score": fresh_matrix,
        },
        "historical_sources": historical_source_summary(data["legacy_selector"], data["legacy_matrix"]),
        "match_context_runtime_truth": {
            "legacy_v6310": {
                "status": legacy_ctx.get("status") or "MISSING",
                "frozen_match_count": legacy_ctx.get("frozen_match_count"),
                "accepted_context_document_count": legacy_ctx.get("accepted_source_count"),
                "evidence_document_count": legacy_ctx.get("evidence_document_count"),
                "both_teams_availability_verified": (legacy_ctx.get("coverage") or {}).get("matches_with_both_teams_availability"),
                "both_teams_predicted_xi_verified": (legacy_ctx.get("coverage") or {}).get("matches_with_both_teams_predicted_xi"),
                "full_context_count": (legacy_ctx.get("coverage") or {}).get("matches_with_full_context"),
                "interpretation": "Historical/runtime evidence audit only; PASS means ledger integrity, not context completeness."
            },
            "live_resolution_queue": {
                "status": queue.get("status") or "MISSING",
                "window_hours": queue.get("window_hours"),
                "fixture_count": queue.get("fixture_count", 0),
                "live_resolution_required_count": queue_counts.get("LIVE_RESOLUTION_REQUIRED", 0),
                "missing_availability": queue_counts.get("missing_availability", 0),
                "missing_suspensions": queue_counts.get("missing_suspensions", 0),
                "missing_manager": queue_counts.get("missing_manager", 0),
                "missing_predicted_xi": queue_counts.get("missing_predicted_xi", 0),
                "missing_material_roster_delta": queue_counts.get("missing_material_roster_delta", 0),
                "automatic_context_completeness_claim": False,
            },
            "priority_queue": {
                "status": pqueue.get("status") or "MISSING",
                "future_fixture_count": pqueue.get("future_fixture_count", 0),
                "priority_counts": pqueue.get("priority_counts") or {},
            },
        },
        "official_result_resolver": {
            "status": data["resolver"].get("status") or "MISSING",
            "prediction_count": data["resolver"].get("prediction_count"),
            "settled_count": data["resolver"].get("settled_count"),
            "open_prediction_count": data["resolver"].get("open_prediction_count"),
        },
        "hard_checks": hard_checks,
        "errors": errors,
        "remaining_promotion_gates": {
            "F05_1X2": fresh_selector.get("decision"),
            "F06_total_score_matrix": fresh_matrix.get("decision"),
            "dynamic_team_context": "LIVE_RESOLUTION_REQUIRED" if queue_counts.get("LIVE_RESOLUTION_REQUIRED", 0) else "NO_CURRENT_QUEUE_OR_RESOLVED",
        },
        "governance": {
            "research_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "no_historical_backfill": True,
            "contract_is_not_runtime_evidence": True,
            "missing_context_means_unknown_not_no_change": True,
            "zero_current_fixture_domains_are_not_failures": True,
            "handicap_prediction_target_retired": True,
            "asian_handicap_market_input_retained": True,
            "legacy_v6475_v6479_execution_authority": 0,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "active_targets": payload["active_prediction_targets_in_order"],
        "new_snapshots": payload["acquisition"]["new_market_snapshots_this_run"],
        "market_predictions": base["prediction_count"],
        "current_selector_events": fresh_selector["ledger_event_count"],
        "current_matrix_events": fresh_matrix["ledger_event_count"],
        "legacy_forward_execution_authority": 0,
        "errors": errors,
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
