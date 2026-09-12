#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import auto_dispatch_bridge_v1 as bridge
import request_contract_v1 as contract


class StaleReservationRetryTest(unittest.TestCase):
    def request(self) -> dict:
        return {
            "schema_version": contract.SCHEMA,
            "mode": "ACTIVE_AT_CUTOFF_REPLAY",
            "request_id": "stale-reservation-replay",
            "match": {
                "competition_id": "JPN_J1",
                "season": "2026",
                "kickoff": "2026-09-12T10:00:00+00:00",
                "cutoff": "2026-09-12T09:00:00+00:00",
                "home_team_name": "Gamba Osaka",
                "away_team_name": "FC Tokyo",
            },
        }

    def body(self, request: dict) -> str:
        return (
            bridge.BEGIN_MARKER
            + "\n"
            + json.dumps(request, separators=(",", ":"))
            + "\n"
            + bridge.END_MARKER
        )

    def ledger_zip(
        self,
        request: dict,
        request_sha: str,
        canonical_sha: str,
        dispatcher_run_id: int,
    ) -> bytes:
        ledger = {
            "schema_version": bridge.SCHEMA,
            "phase": "PREPARED",
            "status": "READY",
            "request_id": request["request_id"],
            "request_sha256": request_sha,
            "request_mode": request["mode"],
            "canonical_execution_sha": canonical_sha,
            "trusted_dispatcher_run_id": dispatcher_run_id,
        }
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ledger.json", json.dumps(ledger, separators=(",", ":")))
        return raw.getvalue()

    def fake_api(
        self,
        *,
        current_head: str,
        prior_head: str,
        prior_conclusion: str,
    ):
        request = bridge.parse_request_body(self.body(self.request()))
        request_sha = contract.request_sha256(request)
        reservation_name, _ = bridge.ledger_names(request["request_id"], request_sha)
        dispatcher_run_id = 77
        artifact_id = 9001
        zip_bytes = self.ledger_zip(
            request,
            request_sha,
            prior_head,
            dispatcher_run_id,
        )
        outer = self

        class FakeAPI:
            repo = "owner/repo"

            def live_pr(self):
                return {
                    "number": bridge.CARRIER_PR_NUMBER,
                    "state": "open",
                    "draft": True,
                    "merged_at": None,
                    "body": outer.body(request),
                    "base": {"ref": bridge.CANONICAL_REF, "sha": "a" * 40},
                    "head": {
                        "ref": bridge.CARRIER_REF,
                        "sha": "b" * 40,
                        "repo": {"full_name": self.repo},
                    },
                }

            def carrier_files(self):
                return [bridge.CARRIER_FILE]

            def actor_permission(self, actor):
                return "write"

            def live_canonical_sha(self):
                return current_head

            def all_artifacts(self):
                return [
                    {
                        "id": artifact_id,
                        "name": reservation_name,
                        "expired": False,
                        "workflow_run": {"id": dispatcher_run_id},
                    }
                ]

            def validate_ledger_artifact_sources(self, artifacts, request_id):
                return None

            def download_artifact_zip(self, requested_artifact_id):
                if requested_artifact_id != artifact_id:
                    raise AssertionError(requested_artifact_id)
                return zip_bytes

            def json_value(self, path):
                if path != f"/repos/{self.repo}/actions/runs/{dispatcher_run_id}":
                    raise AssertionError(path)
                return {
                    "id": dispatcher_run_id,
                    "event": "workflow_run",
                    "name": bridge.TRUSTED_DISPATCHER_WORKFLOW_NAME,
                    "status": "completed",
                    "conclusion": prior_conclusion,
                }

        return FakeAPI(), request_sha, artifact_id

    def prepare(self, fake, current_head: str):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        audit_path = pathlib.Path(td.name) / "audit.json"
        output_path = pathlib.Path(td.name) / "output.txt"
        args = SimpleNamespace(
            repo=fake.repo,
            token="token",
            actor="actor",
            trusted_checkout_sha=current_head,
            trusted_dispatcher_sha="d" * 40,
            trusted_dispatcher_run_id="123",
            receiver_run_id="456",
            audit_out=str(audit_path),
        )
        with mock.patch.object(bridge, "GitHubAPI", return_value=fake), mock.patch.dict(
            os.environ, {"GITHUB_OUTPUT": str(output_path)}, clear=False
        ):
            result = bridge.prepare(args)
        return result, json.loads(audit_path.read_text(encoding="utf-8")), output_path

    def test_failed_reservation_on_different_canonical_head_can_retry(self) -> None:
        current_head = "3" * 40
        prior_head = "1" * 40
        fake, request_sha, artifact_id = self.fake_api(
            current_head=current_head,
            prior_head=prior_head,
            prior_conclusion="failure",
        )
        result, audit, output_path = self.prepare(fake, current_head)
        self.assertEqual(result, 0)
        self.assertEqual(audit["phase"], "PREPARED")
        self.assertEqual(audit["status"], "READY")
        self.assertEqual(
            audit["reservation_retry_policy"],
            "FAILED_PRIOR_DISPATCHER_DIFFERENT_CANONICAL_ONLY",
        )
        self.assertEqual(audit["superseded_failed_reservations"], [
            {
                "reservation_artifact_id": artifact_id,
                "trusted_dispatcher_run_id": 77,
                "canonical_execution_sha": prior_head,
                "trusted_dispatcher_conclusion": "failure",
                "same_canonical_execution_sha": False,
            }
        ])
        output = output_path.read_text(encoding="utf-8")
        self.assertIn("dispatch_required=true", output)
        self.assertIn(f"request_sha256={request_sha}", output)

    def test_failed_reservation_on_same_canonical_head_still_fails_closed(self) -> None:
        current_head = "3" * 40
        fake, _, artifact_id = self.fake_api(
            current_head=current_head,
            prior_head=current_head,
            prior_conclusion="failure",
        )
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        audit_path = pathlib.Path(td.name) / "audit.json"
        args = SimpleNamespace(
            repo=fake.repo,
            token="token",
            actor="actor",
            trusted_checkout_sha=current_head,
            trusted_dispatcher_sha="d" * 40,
            trusted_dispatcher_run_id="123",
            receiver_run_id="456",
            audit_out=str(audit_path),
        )
        with mock.patch.object(bridge, "GitHubAPI", return_value=fake):
            with self.assertRaisesRegex(
                bridge.BridgeError, "AUTO_DISPATCH_REQUEST_ID_RESERVATION_PRESENT"
            ):
                bridge.prepare(args)
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["phase"], "RESERVATION_CONFLICT_FAIL_CLOSED")
        self.assertEqual(audit["status"], "FAIL_CLOSED")
        self.assertEqual(audit["reservation_artifact_ids"], [artifact_id])
        self.assertTrue(audit["reservation_evidence"][0]["same_canonical_execution_sha"])

    def test_nonfailed_old_reservation_on_different_head_still_fails_closed(self) -> None:
        current_head = "3" * 40
        fake, _, _ = self.fake_api(
            current_head=current_head,
            prior_head="1" * 40,
            prior_conclusion="success",
        )
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        audit_path = pathlib.Path(td.name) / "audit.json"
        args = SimpleNamespace(
            repo=fake.repo,
            token="token",
            actor="actor",
            trusted_checkout_sha=current_head,
            trusted_dispatcher_sha="d" * 40,
            trusted_dispatcher_run_id="123",
            receiver_run_id="456",
            audit_out=str(audit_path),
        )
        with mock.patch.object(bridge, "GitHubAPI", return_value=fake):
            with self.assertRaisesRegex(
                bridge.BridgeError, "AUTO_DISPATCH_REQUEST_ID_RESERVATION_PRESENT"
            ):
                bridge.prepare(args)
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["phase"], "RESERVATION_CONFLICT_FAIL_CLOSED")
        self.assertEqual(
            audit["reservation_evidence"][0]["trusted_dispatcher_conclusion"],
            "success",
        )


if __name__ == "__main__":
    unittest.main()
