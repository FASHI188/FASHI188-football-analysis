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
STATE_AUDIT_SCHEMA = "football3-current-v2-retrospective-state-integrity-audit-v1"


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj)).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_lower_hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(ch in "0123456789abcdef" for ch in value)


def _load_dict(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError(code) from exc
    if type(value) is not dict:
        raise rt.RuntimeGateError(code)
    return value


def _write_retrospective_state_integrity_audit(out: Path, receipt: dict[str, Any]) -> dict[str, Any] | None:
    """Emit a same-run audit only when the governed production transport is present."""
    transport_path = out / "request_transport.json"
    if not transport_path.is_file():
        return None

    summary_path = out / "summary.json"
    receipt_path = out / "prediction_receipt.json"
    transport = _load_dict(transport_path, "retrospective state audit request transport invalid")
    summary = _load_dict(summary_path, "retrospective state audit summary invalid")

    request_id = transport.get("request_id")
    request_sha = transport.get("request_sha256")
    expected_request_sha = transport.get("expected_request_sha256")
    canonical_sha = transport.get("canonical_execution_sha")
    run_id_text = os.environ.get("GITHUB_RUN_ID")
    github_sha = os.environ.get("GITHUB_SHA")

    if not isinstance(request_id, str) or not request_id.strip():
        raise rt.RuntimeGateError("retrospective state audit request id invalid")
    if not _is_lower_hex(request_sha, 64) or expected_request_sha != request_sha or transport.get("request_sha_verified") is not True:
        raise rt.RuntimeGateError("retrospective state audit request sha binding invalid")
    if not _is_lower_hex(canonical_sha, 40):
        raise rt.RuntimeGateError("retrospective state audit canonical sha invalid")
    if github_sha != canonical_sha:
        raise rt.RuntimeGateError("retrospective state audit canonical execution drift")
    if not isinstance(run_id_text, str) or not run_id_text.isdigit() or int(run_id_text) <= 0:
        raise rt.RuntimeGateError("retrospective state audit formal run id invalid")

    prediction_sha = receipt.get("prediction_sha")
    guard = receipt.get("state_integrity_guard")
    embedded_guard = receipt.get("state_integrity")
    if not _is_lower_hex(prediction_sha, 64):
        raise rt.RuntimeGateError("retrospective state audit prediction sha invalid")
    if type(guard) is not dict or guard.get("status") != "PASS" or embedded_guard != guard:
        raise rt.RuntimeGateError("retrospective state audit guard invalid")
    if summary.get("status") != "PASS" or summary.get("prediction_sha") != prediction_sha or summary.get("state_integrity_status") != "PASS":
        raise rt.RuntimeGateError("retrospective state audit summary conflict")

    try:
        receipt_raw = receipt_path.read_bytes()
    except OSError as exc:
        raise rt.RuntimeGateError("retrospective state audit source receipt unreadable") from exc
    try:
        persisted_receipt = json.loads(receipt_raw)
    except json.JSONDecodeError as exc:
        raise rt.RuntimeGateError("retrospective state audit source receipt invalid") from exc
    if persisted_receipt != receipt:
        raise rt.RuntimeGateError("retrospective state audit source receipt changed")

    audit = {
        "schema_version": STATE_AUDIT_SCHEMA,
        "status": "PASS",
        "request_mode": MODE,
        "request_id": request_id,
        "request_sha256": request_sha,
        "formal_run_id": int(run_id_text),
        "canonical_execution_sha": canonical_sha,
        "prediction_sha": prediction_sha,
        "state_integrity_guard_status": "PASS",
        "state_integrity_guard": guard,
        "source_prediction_receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "source": "SAME_FORMAL_RUN_RETROSPECTIVE_PREDICTION_RECEIPT",
        "manual_or_auxiliary_artifact_generation_used": False,
        "formal_model_head": receipt.get("formal_model_head"),
        "current_sha256": receipt.get("actual_current_sha"),
        "fusion_weights": receipt.get("fusion_weights"),
    }
    (out / "state_integrity_audit.json").write_bytes(_canon(audit))
    return audit


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
    _write_retrospective_state_integrity_audit(out, receipt)
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
