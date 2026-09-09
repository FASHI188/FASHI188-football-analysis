#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import current_v2_retrospective_replay_v1 as replay
import runtime as rt

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
SCHEMA = "football3-current-v2-retrospective-receipt-contract-v1"


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj)).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _enrich(out: Path, result: dict[str, Any]) -> dict[str, Any]:
    receipt_path = out / "prediction_receipt.json"
    if result.get("status") != "PASS" or not receipt_path.exists():
        return result
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError("retrospective prediction receipt unreadable") from exc
    if type(receipt) is not dict or receipt.get("mode") != MODE:
        raise rt.RuntimeGateError("retrospective prediction receipt mode mismatch")
    reconstruction = receipt.get("reconstruction_audit") or {}
    binding = receipt.get("formal_binding") or {}
    integrity = receipt.get("state_integrity_guard") or {}
    if type(reconstruction) is not dict or type(binding) is not dict or type(integrity) is not dict:
        raise rt.RuntimeGateError("retrospective receipt provenance contract invalid")
    required_true = (
        receipt.get("result_excluded") is True
        and receipt.get("target_fixture_excluded") is True
        and receipt.get("post_kickoff_events_excluded") is True
        and receipt.get("strict_pit_claimed") is False
        and integrity.get("status") == "PASS"
    )
    if not required_true:
        raise rt.RuntimeGateError("retrospective receipt trust-boundary contract failed")
    history_cutoff = reconstruction.get("history_upper_exclusive")
    if not isinstance(history_cutoff, str) or not history_cutoff:
        raise rt.RuntimeGateError("retrospective history event cutoff missing")
    current_sha = binding.get("runtime_current_sha256")
    formal_head = binding.get("runtime_formal_head")
    if not isinstance(current_sha, str) or not current_sha or not isinstance(formal_head, str) or not formal_head:
        raise rt.RuntimeGateError("retrospective current/formal model binding missing")
    receipt.update({
        "request_mode": MODE,
        "retrospective": True,
        "research_only": True,
        "history_event_cutoff": history_cutoff,
        "current_observation_timestamp": _utc_now(),
        "same_batch_predict_before_update": True,
        "actual_current_sha": current_sha,
        "formal_model_head": formal_head,
        "state_integrity": integrity,
        "run_id": os.environ.get("GITHUB_RUN_ID") or "LOCAL_TEST",
        "receipt_artifact_id": os.environ.get("FOOTBALL3_RECEIPT_ARTIFACT_ID") or "PENDING_TERMINAL_ARTIFACT_BINDING",
        "receipt_artifact_binding_required": True,
        "receipt_contract_schema": SCHEMA,
    })
    old_receipt_sha = receipt.pop("receipt_sha", None)
    receipt_sha = _sha(receipt)
    receipt["receipt_sha"] = receipt_sha
    receipt["pre_contract_receipt_sha"] = old_receipt_sha
    receipt_path.write_bytes(_canon(receipt))
    result = dict(result)
    result["receipt_sha"] = receipt_sha
    result["request_mode"] = MODE
    result["retrospective"] = True
    result["research_only"] = True
    result["history_event_cutoff"] = history_cutoff
    result["same_batch_predict_before_update"] = True
    result["receipt_artifact_binding_required"] = True
    return result


def install(gateway_module) -> dict[str, Any]:
    original = gateway_module.normal_request

    def normal_request(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        result = original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        if str(req.get("request_mode") or "") != MODE:
            return result
        return _enrich(out, result)

    gateway_module.normal_request = normal_request
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "request_mode": MODE,
        "prediction_or_matrix_changed": False,
        "route_or_fallback_changed": False,
        "current_or_model_or_weight_changed": False,
        "terminal_artifact_binding_required": True,
    }
