#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import current_v2_retrospective_receipt_contract_v1 as contract
import runtime as rt


FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"


class RetrospectiveStateIntegrityArtifactProducerTests(unittest.TestCase):
    REQUEST_ID = "state-audit-test-request"
    REQUEST_SHA = "a" * 64
    CANONICAL_SHA = "b" * 40
    PREDICTION_SHA = "c" * 64
    FORMAL_HEAD = "d" * 40
    CURRENT_SHA = "e" * 64
    RUN_ID = "123456789"

    def _fixture(self, out: Path) -> dict:
        out.mkdir(parents=True, exist_ok=True)
        guard = {
            "status": "PASS",
            "identity_status": "PASS",
            "target_result_used": False,
            "post_kickoff_state_used": False,
        }
        receipt = {
            "mode": contract.MODE,
            "request_mode": contract.MODE,
            "prediction_sha": self.PREDICTION_SHA,
            "state_integrity_guard": guard,
            "state_integrity": guard,
            "formal_model_head": self.FORMAL_HEAD,
            "actual_current_sha": self.CURRENT_SHA,
            "fusion_weights": {"xg": 0.75, "v1": 0.25},
        }
        (out / "prediction_receipt.json").write_bytes(contract._canon(receipt))
        (out / "summary.json").write_text(json.dumps({
            "status": "PASS",
            "prediction_sha": self.PREDICTION_SHA,
            "state_integrity_status": "PASS",
        }), encoding="utf-8")
        (out / "request_transport.json").write_text(json.dumps({
            "request_id": self.REQUEST_ID,
            "request_sha256": self.REQUEST_SHA,
            "expected_request_sha256": self.REQUEST_SHA,
            "request_sha_verified": True,
            "canonical_execution_sha": self.CANONICAL_SHA,
        }), encoding="utf-8")
        return receipt

    def _emit(self, out: Path, receipt: dict):
        with mock.patch.dict(os.environ, {
            "GITHUB_RUN_ID": self.RUN_ID,
            "GITHUB_SHA": self.CANONICAL_SHA,
        }, clear=False):
            return contract._write_retrospective_state_integrity_audit(out, receipt)

    def test_success_emits_same_run_bound_artifact_and_zip_member(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            receipt = self._fixture(out)
            audit = self._emit(out, receipt)
            path = out / "state_integrity_audit.json"
            self.assertTrue(path.is_file())
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(audit, persisted)
            self.assertEqual(persisted["schema_version"], contract.STATE_AUDIT_SCHEMA)
            self.assertEqual(persisted["request_id"], self.REQUEST_ID)
            self.assertEqual(persisted["request_sha256"], self.REQUEST_SHA)
            self.assertEqual(persisted["formal_run_id"], int(self.RUN_ID))
            self.assertEqual(persisted["canonical_execution_sha"], self.CANONICAL_SHA)
            self.assertEqual(persisted["prediction_sha"], self.PREDICTION_SHA)
            self.assertEqual(persisted["state_integrity_guard"], receipt["state_integrity_guard"])
            self.assertEqual(persisted["state_integrity_guard_status"], "PASS")
            self.assertFalse(persisted["manual_or_auxiliary_artifact_generation_used"])
            self.assertEqual(persisted["formal_model_head"], self.FORMAL_HEAD)
            self.assertEqual(persisted["current_sha256"], self.CURRENT_SHA)
            self.assertEqual(persisted["fusion_weights"], {"xg": 0.75, "v1": 0.25})
            receipt_raw = (out / "prediction_receipt.json").read_bytes()
            self.assertEqual(persisted["source_prediction_receipt_sha256"], hashlib.sha256(receipt_raw).hexdigest())
            zip_path = out / "receipt.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in out.iterdir():
                    if file.is_file() and file != zip_path:
                        zf.write(file, file.name)
            with zipfile.ZipFile(zip_path) as zf:
                self.assertIn("state_integrity_audit.json", zf.namelist())

    def test_stale_or_old_audit_is_overwritten_from_same_run_sources(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            receipt = self._fixture(out)
            stale = {
                "schema_version": contract.STATE_AUDIT_SCHEMA,
                "status": "PASS",
                "request_id": "old-run",
                "request_sha256": "f" * 64,
                "formal_run_id": 1,
                "manual_or_auxiliary_artifact_generation_used": False,
            }
            (out / "state_integrity_audit.json").write_text(json.dumps(stale), encoding="utf-8")
            self._emit(out, receipt)
            persisted = json.loads((out / "state_integrity_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["request_id"], self.REQUEST_ID)
            self.assertEqual(persisted["formal_run_id"], int(self.RUN_ID))
            self.assertNotEqual(persisted, stale)

    def test_wrong_request_sha_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            receipt = self._fixture(out)
            transport = json.loads((out / "request_transport.json").read_text(encoding="utf-8"))
            transport["expected_request_sha256"] = "f" * 64
            (out / "request_transport.json").write_text(json.dumps(transport), encoding="utf-8")
            with self.assertRaises(rt.RuntimeGateError):
                self._emit(out, receipt)
            self.assertFalse((out / "state_integrity_audit.json").exists())

    def test_wrong_canonical_execution_sha_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            receipt = self._fixture(out)
            with mock.patch.dict(os.environ, {
                "GITHUB_RUN_ID": self.RUN_ID,
                "GITHUB_SHA": "f" * 40,
            }, clear=False):
                with self.assertRaises(rt.RuntimeGateError):
                    contract._write_retrospective_state_integrity_audit(out, receipt)

    def test_main_receipt_guard_or_summary_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            receipt = self._fixture(out)
            receipt["state_integrity"]["status"] = "DATA_STATE_ANOMALY"
            (out / "prediction_receipt.json").write_bytes(contract._canon(receipt))
            with self.assertRaises(rt.RuntimeGateError):
                self._emit(out, receipt)

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            receipt = self._fixture(out)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            summary["state_integrity_status"] = "DATA_STATE_ANOMALY"
            (out / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaises(rt.RuntimeGateError):
                self._emit(out, receipt)

    def test_no_governed_transport_means_no_manually_invented_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            receipt = self._fixture(out)
            (out / "request_transport.json").unlink()
            self.assertIsNone(self._emit(out, receipt))
            self.assertFalse((out / "state_integrity_audit.json").exists())

    def test_prospective_install_path_is_unchanged(self):
        class Gateway:
            @staticmethod
            def normal_request(req, state_root, out, repo_root, understat_db, confirmation_dir):
                return {"status": "PASS", "sentinel": req["sentinel"]}

        contract.install(Gateway)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            result = Gateway.normal_request(
                {"request_mode": "PROSPECTIVE_FORMAL_PREDICTION", "sentinel": "unchanged"},
                out, out, out, out, out,
            )
            self.assertEqual(result, {"status": "PASS", "sentinel": "unchanged"})
            self.assertFalse((out / "state_integrity_audit.json").exists())


if __name__ == "__main__":
    unittest.main()
