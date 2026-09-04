#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

import formal_state_integrity_guard_v1 as guard

SCHEMA = "football3-formal-state-integrity-coverage-patch-v1"
_ORIGINAL = guard.classify_state


def classify_state(
    loaded: dict[str, Any],
    fixture: dict[str, Any],
    identity_audit: dict[str, Any],
    trigger: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    audit = _ORIGINAL(loaded, fixture, identity_audit, trigger, receipt)
    coverage = guard._coverage_audit(loaded)
    spec = None
    if type(coverage) is dict:
        files = coverage.get("files") or {}
        if type(files) is dict:
            candidate = files.get(fixture["competition_id"])
            if type(candidate) is dict:
                spec = candidate

    audit["cross_season_xg_coverage"] = spec
    if spec is not None:
        known = bool(spec.get("source_known_by_target"))
        status = str(spec.get("coverage_status") or "")
        if known and status != "COMPLETE_INGESTED":
            reason = (
                "CROSS_SEASON_XG_COVERAGE_INCOMPLETE:"
                f"status={status},linked={spec.get('linked_rows')},"
                f"joined={spec.get('joined_rows')},formal={spec.get('formal_rows')},"
                f"unmatched={spec.get('unmatched_rows')},overlap={spec.get('overlap_rows')}"
            )
            reasons = list(audit.get("anomaly_reasons") or [])
            if reason not in reasons:
                reasons.append(reason)
            audit["anomaly_reasons"] = reasons
            audit["status"] = "DATA_STATE_ANOMALY"
            audit["fallback_class"] = "DATA_STATE_ANOMALY"
            audit["fallback_reason"] = ";".join(reasons)
    return audit


def install() -> dict[str, Any]:
    guard.classify_state = classify_state
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "target_competition_incomplete_cross_season_xg": "DATA_STATE_ANOMALY",
        "partial_cross_season_ingest": False,
        "normal_fallback_allowed_for_incomplete_known_cross_season_source": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
