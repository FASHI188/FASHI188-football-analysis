#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

import live_delta_acquisition_v1 as live
import live_delta_semantics_v2 as sem

SCHEMA = "football3-live-fast-reuse-audit-v1"
_CALLS: list[dict[str, Any]] = []
_ORIGINAL = live.acquire_verified_delta


def _iso(x: Any) -> str:
    try:
        return x.isoformat()
    except Exception:
        return str(x)


def acquire_verified_delta(repo_root, lower, requested_ceiling, target_fixture_id: str, state: Any):
    call: dict[str, Any] = {
        "call_index": len(_CALLS) + 1,
        "from": _iso(lower),
        "requested_ceiling": _iso(requested_ceiling),
        "target_fixture_id": str(target_fixture_id),
        "cached_v1_seen_n": len(getattr(getattr(state, "base", None), "seen_fixtures", set()) or set()),
        "cached_xg_seen_n": len(getattr(state, "seen", set()) or set()),
        "cached_xg_pending_n": len(getattr(state, "pending", {}) or {}),
    }
    try:
        delta, report = _ORIGINAL(repo_root, lower, requested_ceiling, target_fixture_id, state)
    except live.AcquisitionError as exc:
        call.update({
            "status": "ACQUISITION_ERROR",
            "reason": str(exc),
            "report": exc.report,
        })
        _CALLS.append(call)
        raise
    call.update({
        "status": "VERIFIED_COMPLETE",
        "effective_cutoff": str(report.get("effective_cutoff")),
        "records": int(report.get("records", len(delta))),
        "v1_label_releases": int(report.get("v1_label_releases", 0)),
        "xg_label_releases": int(report.get("xg_label_releases", 0)),
        "quarantined_v1_label_releases": int(report.get("quarantined_v1_label_releases", 0)),
        "records_sha256": report.get("records_sha256"),
        "source_set_sha256": report.get("source_set_sha256"),
    })
    _CALLS.append(call)
    return delta, report


def snapshot() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "call_n": len(_CALLS),
        "calls": list(_CALLS),
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }


def install() -> dict[str, Any]:
    live.acquire_verified_delta = acquire_verified_delta
    sem.acquire_verified_delta = acquire_verified_delta
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "audit_only": True,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
