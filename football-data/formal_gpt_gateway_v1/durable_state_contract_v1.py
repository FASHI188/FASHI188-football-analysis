#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import runtime as rt

SCHEMA = "football3-durable-state-contract-v1"
SELECTION_SCHEMA = "football3-durable-state-selection-v1"
TRANSITION_SCHEMA = "football3-state-transition-receipt-v1"
CACHE_KEY_SCHEMA = "football3-durable-cache-key-v1"
NO_OP_DELTA = "NO_OP_DELTA"
DELTA_APPLIED = "DELTA_APPLIED"


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(obj: Any) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def parse_dt(value: str, field: str) -> datetime:
    return rt._parse_dt(str(value), field)


def runtime_contract_payload() -> dict[str, Any]:
    core = {
        "schema_version": SCHEMA,
        "runtime_schema": rt.SCHEMA,
        "bundle_schema": rt.BUNDLE_SCHEMA,
        "input_schema": rt.INPUT_SCHEMA,
        "delta_schema": rt.DELTA_SCHEMA,
        "formal_head": rt.FORMAL_HEAD,
        "current_sha256": rt.CURRENT_SHA256,
        "formal_scope": list(rt.FORMAL_SCOPE),
    }
    return {**core, "runtime_contract_sha256": sha(core)}


def nested_dicts(obj: Any):
    if type(obj) is dict:
        yield obj
        for value in obj.values():
            yield from nested_dicts(value)
    elif type(obj) is list:
        for value in obj:
            yield from nested_dicts(value)


def max_source_observed_at(loaded: dict[str, Any]) -> str:
    """Latest data-availability/PIT timestamp represented by the sealed state.

    Build/upload/acquisition wall-clock timestamps are intentionally excluded. They are
    separate provenance clocks and must never make a historically sealed state appear newer.
    """
    candidates: list[datetime] = []
    meta = loaded.get("meta") or {}
    candidates.append(parse_dt(str(meta.get("historical_cutoff")), "state cutoff"))

    state = loaded.get("state")
    for value in (
        getattr(getattr(state, "base", None), "last_update_time", None),
        getattr(state, "last_apply_time", None),
        getattr(state, "last_prediction_time", None),
    ):
        if value is not None:
            candidates.append(value.astimezone(timezone.utc))

    data_keys = {
        "result_available_at", "release_at", "event_at", "effective_cutoff",
        "source_available_at", "source_observed_at_utc", "data_waterline",
    }
    for item in nested_dicts(loaded.get("source")):
        for key in data_keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    candidates.append(parse_dt(value, key))
                except rt.RuntimeGateError:
                    pass
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, str) and entry.strip():
                        try:
                            candidates.append(parse_dt(entry, key))
                        except rt.RuntimeGateError:
                            pass
    if not candidates:
        raise rt.RuntimeGateError("PIT max_source_observed_at unavailable")
    return max(candidates).astimezone(timezone.utc).isoformat()


def artifact_role_ok(name: str) -> bool:
    return str(name).startswith("formal-gpt-runner-state-")


def eligibility_reasons(candidate: dict[str, Any], target_cutoff: datetime, competition_id: str) -> list[str]:
    reasons: list[str] = []
    if not candidate.get("artifact_role_ok"):
        reasons.append("ARTIFACT_ROLE")
    if not candidate.get("verified"):
        reasons.append("VERIFIED")
    if not candidate.get("schema_ok"):
        reasons.append("SCHEMA")
    if not candidate.get("runtime_ok"):
        reasons.append("RUNTIME")
    if not candidate.get("model_current_ok"):
        reasons.append("MODEL_CURRENT")
    if not candidate.get("competition_scope_ok"):
        reasons.append("COMPETITION_SCOPE")
    if not candidate.get("pit_ok"):
        reasons.append("PIT")
    try:
        state_cutoff = parse_dt(str(candidate.get("state_cutoff")), "candidate state cutoff")
        if state_cutoff > target_cutoff:
            reasons.append("FUTURE_STATE")
    except rt.RuntimeGateError:
        reasons.append("STATE_CUTOFF")
    if candidate.get("competition_id") not in (None, competition_id):
        reasons.append("WRONG_COMPETITION")
    return sorted(set(reasons))


def choose_candidate(candidates: list[dict[str, Any]], target_cutoff: datetime, competition_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    evaluated: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for raw in candidates:
        row = dict(raw)
        row["rejection_reasons"] = eligibility_reasons(row, target_cutoff, competition_id)
        row["eligible"] = not row["rejection_reasons"]
        evaluated.append(row)
        if row["eligible"]:
            eligible.append(row)
    if not eligible:
        return None, evaluated

    def key(row: dict[str, Any]):
        return (
            parse_dt(str(row["state_cutoff"]), "state cutoff"),
            parse_dt(str(row["artifact_created_at"]), "artifact created at"),
            int(row.get("artifact_id") or 0),
        )

    eligible.sort(key=key)
    return eligible[-1], evaluated


def transition_receipt(*, base_state_sha: str, base_cutoff: str, delta_from: str, delta_to: str,
                       delta: list[dict[str, Any]], target_cutoff: str, target_state_sha: str,
                       artifact_created_at: str | None, max_source_observed_at_value: str,
                       route: str) -> dict[str, Any]:
    base_dt = parse_dt(base_cutoff, "base cutoff")
    target_dt = parse_dt(target_cutoff, "target cutoff")
    from_dt = parse_dt(delta_from, "delta from")
    to_dt = parse_dt(delta_to, "delta to")
    if base_dt > target_dt or from_dt > to_dt or to_dt != target_dt:
        raise rt.RuntimeGateError("state transition continuity violation")
    delta_sha = sha(delta)
    core = {
        "schema_version": TRANSITION_SCHEMA,
        "artifact_created_at": artifact_created_at,
        "base_state_sha": base_state_sha,
        "base_cutoff": base_dt.isoformat(),
        "state_cutoff": base_dt.isoformat(),
        "delta_interval": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
        "delta_n": len(delta),
        "delta_sha": delta_sha,
        "target_cutoff": target_dt.isoformat(),
        "target_state_sha": target_state_sha,
        "max_source_observed_at": parse_dt(max_source_observed_at_value, "max source observed at").isoformat(),
        "transition_status": NO_OP_DELTA if len(delta) == 0 else DELTA_APPLIED,
        "runtime_contract_sha256": runtime_contract_payload()["runtime_contract_sha256"],
        "route": route,
    }
    return {**core, "transition_receipt_sha256": sha(core)}


def cache_key(*, fixture_identity: dict[str, Any], cutoff: str, base_state_sha: str,
              target_state_sha: str, delta_sha: str, model_head: str, route: str) -> dict[str, Any]:
    core = {
        "schema_version": CACHE_KEY_SCHEMA,
        "fixture_identity": fixture_identity,
        "cutoff": parse_dt(cutoff, "cache cutoff").isoformat(),
        "base_state_sha": base_state_sha,
        "target_state_sha": target_state_sha,
        "delta_sha": delta_sha,
        "model_head": model_head,
        "runtime_contract_sha256": runtime_contract_payload()["runtime_contract_sha256"],
        "route": route,
    }
    return {**core, "cache_key_sha256": sha(core)}
