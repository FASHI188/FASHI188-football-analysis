#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
MODULE_PATH = HERE / "auto_dispatch_bridge_v1.py"
WORKFLOW_PATH = HERE.parents[1] / ".github" / "workflows" / "football3-gpt-auto-dispatch-bridge-v1.yml"

spec = importlib.util.spec_from_file_location("auto_dispatch_bridge_v1", MODULE_PATH)
bridge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bridge)


class BridgeContractTest(unittest.TestCase):
    def request(self, request_id: str = "football3-test-001") -> dict:
        return {
            "schema_version": bridge.REQUEST_SCHEMA,
            "mode": "PROSPECTIVE_FORMAL_PREDICTION",
            "request_id": request_id,
            "competition_id": "TEST_ONLY",
            "season": "2099",
            "home_team_name": "Home",
            "away_team_name": "Away",
            "kickoff": "2099-01-01T12:00:00+00:00",
        }

    def body(self, request: dict) -> str:
        return (
            "carrier text\n"
            + bridge.BEGIN_MARKER
            + "\n"
            + json.dumps(request)
            + "\n"
            + bridge.END_MARKER
            + "\n"
        )

    def event(self) -> dict:
        return {
            "action": "edited",
            "repository": {"full_name": "owner/repo"},
            "sender": {"login": "trusted-user"},
            "pull_request": {
                "number": 341,
                "updated_at": "2099-01-01T00:00:00Z",
                "base": {"ref": bridge.CANONICAL_REF, "sha": "1" * 40},
                "head": {
                    "ref": bridge.CARRIER_REF,
                    "sha": "2" * 40,
                    "repo": {"full_name": "owner/repo"},
                },
            },
        }

    def live_pr(self) -> dict:
        return {
            "number": 341,
            "state": "open",
            "draft": True,
            "merged_at": None,
            "body": self.body(self.request()),
            "base": {"ref": bridge.CANONICAL_REF, "sha": "3" * 40},
            "head": {
                "ref": bridge.CARRIER_REF,
                "sha": "4" * 40,
                "repo": {"full_name": "owner/repo"},
            },
        }

    def test_request_id_required_and_names_are_content_addressed(self) -> None:
        req = bridge.parse_request_body(self.body(self.request("unique-id")))
        self.assertEqual(req["request_id"], "unique-id")
        reservation, completed = bridge.artifact_names("unique-id")
        self.assertTrue(reservation.startswith("football3-auto-dispatch-reservation-"))
        self.assertTrue(completed.startswith("football3-auto-dispatch-completed-"))
        self.assertNotEqual(reservation, completed)
        bad = self.request("")
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_REQUEST_ID_MISSING"):
            bridge.parse_request_body(self.body(bad))

    def test_event_is_exact_carrier_and_same_repository_only(self) -> None:
        audit = bridge.validate_event(self.event(), "owner/repo")
        self.assertEqual(audit["actor"], "trusted-user")
        bad = self.event()
        bad["pull_request"]["head"]["repo"]["full_name"] = "fork/repo"
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_CARRIER_HEAD_UNAUTHORIZED"):
            bridge.validate_event(bad, "owner/repo")

    def test_live_carrier_must_remain_open_draft_and_exact_refs(self) -> None:
        bridge.validate_live_pr(self.live_pr(), "owner/repo")
        for field, value in (("state", "closed"), ("draft", False), ("merged_at", "2099-01-01T00:00:00Z")):
            bad = self.live_pr()
            bad[field] = value
            with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_LIVE_CARRIER_NOT_OPEN_DRAFT"):
                bridge.validate_live_pr(bad, "owner/repo")

    def test_dispatch_payload_cannot_execute_carrier_or_stale_base(self) -> None:
        event_audit = bridge.validate_event(self.event(), "owner/repo")
        payload = bridge.build_dispatch_payload()
        self.assertEqual(
            payload,
            {
                "ref": bridge.CANONICAL_REF,
                "inputs": {"request_pr_number": "341"},
            },
        )
        encoded = json.dumps(payload, sort_keys=True)
        self.assertNotIn(bridge.CARRIER_REF, encoded)
        self.assertNotIn(str(event_audit["event_base_sha"]), encoded)
        self.assertNotIn(str(event_audit["event_carrier_head_sha"]), encoded)

    def test_workflow_executes_trusted_live_canonical_source_only(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("pull_request_target:", text)
        self.assertIn("types: [edited]", text)
        self.assertIn("actions: write", text)
        self.assertIn("ref: ${{ steps.live.outputs.live_sha }}", text)
        self.assertIn("group: football3-gpt-auto-dispatch-bridge-v1", text)
        self.assertIn("cancel-in-progress: false", text)
        trusted = text.split("  trusted-auto-dispatch:", 1)[1]
        self.assertNotIn("ref: ${{ github.event.pull_request.head.sha }}", trusted)
        self.assertIn(
            "python3 football-data/formal_gpt_gateway_v1/test_auto_dispatch_bridge_v1.py",
            text,
        )


if __name__ == "__main__":
    unittest.main()
