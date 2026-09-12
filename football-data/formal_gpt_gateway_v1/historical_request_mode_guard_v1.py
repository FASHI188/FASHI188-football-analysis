#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import runtime as rt

FOOTBALL3_GOVERNED_PRODUCTION_RUNTIME_GOVERNANCE = "football3-formal-production-runtime-governance-v1"

SCHEMA = "football3-historical-request-mode-guard-v1"
ACTIVE_AT_CUTOFF_REPLAY = "ACTIVE_AT_CUTOFF_REPLAY"
CURRENT_MODEL_RETROSPECTIVE_REPLAY = "CURRENT_MODEL_RETROSPECTIVE_REPLAY"
CURRENT_V2_RETROSPECTIVE_REPLAY = "CURRENT_V2_RETROSPECTIVE_REPLAY"


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def _request_cutoff(req: dict[str, Any]):
    match = req.get("match")
    if type(match) is not dict:
        raise rt.RuntimeGateError("HISTORICAL_REQUEST_MODE_MATCH_REQUIRED")
    return rt._parse_dt(str(match.get("cutoff") or ""), "historical replay cutoff")


def _exact_sealed_cutoff(state_root: Path, cutoff):
    try:
        loaded = rt.validate_bundle(state_root / "bundle")
        sealed = rt._parse_dt(
            str(loaded["meta"]["historical_cutoff"]),
            "historical replay sealed cutoff",
        )
    except (rt.RuntimeGateError, KeyError, TypeError, ValueError):
        return False, None
    return sealed == cutoff, sealed


def _fail_closed(
    out: Path,
    *,
    request_mode: str,
    reason: str,
    cutoff,
    sealed_cutoff,
) -> None:
    gap = {
        "schema_version": SCHEMA,
        "status": "HISTORICAL_COVERAGE_INSUFFICIENT",
        "request_mode": request_mode,
        "reason": reason,
        "requested_cutoff": cutoff.isoformat(),
        "sealed_cutoff": sealed_cutoff.isoformat() if sealed_cutoff is not None else None,
        "live_acquisition_calls": 0,
        "model_execution_started": False,
        "prediction_sha": None,
        "receipt_sha": None,
        "result_or_post_kickoff_data_used": False,
        "silent_mode_rewrite_used": False,
    }
    _write_json(out / "formal_gap.json", gap)
    raise rt.RuntimeGateError(reason)


def install(gateway_module) -> dict[str, Any]:
    """Fail closed for legacy historical modes before any live acquisition can start."""
    original = gateway_module.normal_request

    def normal_request(
        req: dict[str, Any],
        state_root: Path,
        out: Path,
        repo_root: Path,
        understat_db: Path,
        confirmation_dir: Path,
    ) -> dict[str, Any]:
        request_mode = req.get("request_mode")
        if request_mode is None:
            # Direct internal engineering probes predate the canonical carrier mode marker.
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        if not isinstance(request_mode, str):
            raise rt.RuntimeGateError("TRUSTED_REQUEST_MODE_INVALID")

        if request_mode == ACTIVE_AT_CUTOFF_REPLAY:
            cutoff = _request_cutoff(req)
            exact, sealed_cutoff = _exact_sealed_cutoff(state_root, cutoff)
            if not exact:
                _fail_closed(
                    out,
                    request_mode=request_mode,
                    reason="HISTORICAL_EXACT_CUTOFF_SEALED_STATE_REQUIRED",
                    cutoff=cutoff,
                    sealed_cutoff=sealed_cutoff,
                )
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)

        if request_mode == CURRENT_MODEL_RETROSPECTIVE_REPLAY:
            cutoff = _request_cutoff(req)
            _fail_closed(
                out,
                request_mode=request_mode,
                reason="CURRENT_MODEL_RETROSPECTIVE_REPLAY_DEPRECATED_USE_CURRENT_V2_RETROSPECTIVE_REPLAY",
                cutoff=cutoff,
                sealed_cutoff=None,
            )

        # CURRENT_V2_RETROSPECTIVE_REPLAY is intercepted by the existing outer
        # current-V2 replay wrapper installed after this guard. Prospective modes
        # remain byte-for-byte on the pre-existing formal/live chain.
        return original(req, state_root, out, repo_root, understat_db, confirmation_dir)

    gateway_module.normal_request = normal_request
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "active_at_cutoff_requires_exact_validated_sealed_state": True,
        "current_model_retrospective_replay_fail_closed": True,
        "current_v2_retrospective_route_owned_by_existing_outer_wrapper": True,
        "live_acquisition_before_historical_mode_adjudication": False,
        "prospective_path_changed": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
        "state_mutation_allowed": False,
        "result_or_post_kickoff_data_used": False,
    }
