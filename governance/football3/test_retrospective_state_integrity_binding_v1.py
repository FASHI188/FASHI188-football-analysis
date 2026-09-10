#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import unittest
import zipfile

import formal_terminal_finalizer_v1 as mod


REQUEST_ID = "retrospective-state-binding-test"
REQUEST_SHA = "a" * 64
CANONICAL_SHA = "b" * 40
CARRIER_HEAD = "c" * 40
PREDICTION_SHA = "d" * 64
FORMAL_HEAD = "e" * 40
CURRENT_SHA = "f" * 64
RUN_ID = 987654321


def audit() -> dict:
    return {
        "phase": "DISPATCHED",
        "dispatch_performed": True,
        "formal_run_id": RUN_ID,
        "request_id": REQUEST_ID,
        "request_sha256": REQUEST_SHA,
        "canonical_execution_sha": CANONICAL_SHA,
        "carrier_head_sha": CARRIER_HEAD,
    }


def _canon(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def receipt_zip(*, retrospective: bool = True, mutation: str | None = None) -> bytes:
    guard = {
        "status": "PASS",
        "identity_status": "PASS",
        "target_result_used": False,
        "post_kickoff_state_used": False,
    }
    prediction = {"prediction_sha": PREDICTION_SHA}
    if retrospective:
        prediction.update({
            "mode": mod.RETROSPECTIVE_MODE,
            "request_mode": mod.RETROSPECTIVE_MODE,
            "state_integrity_guard": guard,
            "state_integrity": guard,
            "formal_model_head": FORMAL_HEAD,
            "actual_current_sha": CURRENT_SHA,
            "fusion_weights": {"xg": 0.75, "v1": 0.25},
        })
    prediction_raw = _canon(prediction)
    summary = {"status": "PASS", "prediction_sha": PREDICTION_SHA}
    summary_raw = _canon(summary)
    binding = {
        "status": "PASS",
        "request_id": REQUEST_ID,
        "request_sha256": REQUEST_SHA,
        "expected_request_sha256": REQUEST_SHA,
        "request_sha_verified": True,
        "production_run_id": RUN_ID,
        "canonical_execution_sha": CANONICAL_SHA,
        "carrier_head_sha": CARRIER_HEAD,
    }
    execution = {
        "status": "PASS",
        "production_run_id": RUN_ID,
        "run_id": RUN_ID,
        "request_sha256": REQUEST_SHA,
        "canonical_execution_sha": CANONICAL_SHA,
        "workflow_sha": CANONICAL_SHA,
        "prediction_sha": PREDICTION_SHA,
        "state_integrity_guard_status": "PASS",
        "summary_sha256": hashlib.sha256(summary_raw).hexdigest(),
        "prediction_receipt_sha256": hashlib.sha256(prediction_raw).hexdigest(),
    }
    if retrospective:
        state = {
            "schema_version": mod.RETROSPECTIVE_STATE_AUDIT_SCHEMA,
            "status": "PASS",
            "request_mode": mod.RETROSPECTIVE_MODE,
            "request_id": REQUEST_ID,
            "request_sha256": REQUEST_SHA,
            "formal_run_id": RUN_ID,
            "canonical_execution_sha": CANONICAL_SHA,
            "prediction_sha": PREDICTION_SHA,
            "state_integrity_guard_status": "PASS",
            "state_integrity_guard": guard,
            "source_prediction_receipt_sha256": hashlib.sha256(prediction_raw).hexdigest(),
            "source": "SAME_FORMAL_RUN_RETROSPECTIVE_PREDICTION_RECEIPT",
            "manual_or_auxiliary_artifact_generation_used": False,
            "formal_model_head": FORMAL_HEAD,
            "current_sha256": CURRENT_SHA,
            "fusion_weights": {"xg": 0.75, "v1": 0.25},
        }
    else:
        state = {"status": "PASS"}

    if mutation == "missing_state":
        state = None
    elif mutation == "empty_state":
        state = b""
    elif mutation == "invalid_state_json":
        state = b"{not-json"
    elif mutation == "missing_request_id":
        state.pop("request_id", None)
    elif mutation == "wrong_request_sha":
        state["request_sha256"] = "0" * 64
    elif mutation == "wrong_run":
        state["formal_run_id"] = RUN_ID + 1
    elif mutation == "wrong_canonical":
        state["canonical_execution_sha"] = "0" * 40
    elif mutation == "wrong_prediction":
        state["prediction_sha"] = "0" * 64
    elif mutation == "stale_receipt_sha":
        state["source_prediction_receipt_sha256"] = "0" * 64
    elif mutation == "state_fail":
        state["status"] = "DATA_STATE_ANOMALY"
    elif mutation == "guard_conflict":
        state["state_integrity_guard"] = {**guard, "target_result_used": True}
    elif mutation == "manual_generation":
        state["manual_or_auxiliary_artifact_generation_used"] = True
    elif mutation == "wrong_model":
        state["formal_model_head"] = "0" * 40
    elif mutation == "wrong_current":
        state["current_sha256"] = "0" * 64
    elif mutation == "wrong_weights":
        state["fusion_weights"] = {"xg": 0.5, "v1": 0.5}

    files = {
        "request_sha_binding_receipt.json": _canon(binding),
        "summary.json": summary_raw,
        "prediction_receipt.json": prediction_raw,
        "production_execution_binding_receipt.json": _canon(execution),
    }
    if state is not None:
        files["state_integrity_audit.json"] = state if isinstance(state, bytes) else _canon(state)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, raw in files.items():
            zf.writestr(name, raw)
    return out.getvalue()


def artifact(raw: bytes) -> dict:
    return {"digest": "sha256:" + hashlib.sha256(raw).hexdigest()}


class RetrospectiveStateIntegrityBindingTests(unittest.TestCase):
    def validate(self, raw: bytes):
        return mod.FormalTerminalFinalizer(object()).validate_receipt_zip(audit(), artifact(raw), raw)

    def test_valid_retrospective_same_run_binding_passes(self):
        raw = receipt_zip()
        result = self.validate(raw)
        self.assertEqual(result["prediction_sha"], PREDICTION_SHA)
        self.assertEqual(len(result["state_integrity_sha256"]), 64)

    def test_missing_state_file_fails_closed(self):
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_RECEIPT_FILE_MISSING:state_integrity_audit.json"):
            self.validate(receipt_zip(mutation="missing_state"))

    def test_empty_or_invalid_state_json_rejected(self):
        for mutation in ("empty_state", "invalid_state_json"):
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_RECEIPT_JSON_INVALID:state_integrity_audit.json"):
                    self.validate(receipt_zip(mutation=mutation))

    def test_missing_fields_old_run_and_wrong_request_are_rejected(self):
        for mutation in ("missing_request_id", "wrong_request_sha", "wrong_run", "wrong_canonical", "wrong_prediction", "stale_receipt_sha"):
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID"):
                    self.validate(receipt_zip(mutation=mutation))

    def test_state_or_receipt_guard_conflict_is_rejected(self):
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_NOT_PASS"):
            self.validate(receipt_zip(mutation="state_fail"))
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID"):
            self.validate(receipt_zip(mutation="guard_conflict"))

    def test_manual_artifact_generation_is_rejected(self):
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID"):
            self.validate(receipt_zip(mutation="manual_generation"))

    def test_model_current_and_weights_must_match_main_receipt(self):
        for mutation in ("wrong_model", "wrong_current", "wrong_weights"):
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID"):
                    self.validate(receipt_zip(mutation=mutation))

    def test_prospective_legacy_state_contract_remains_accepted(self):
        raw = receipt_zip(retrospective=False)
        result = self.validate(raw)
        self.assertEqual(result["prediction_sha"], PREDICTION_SHA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
