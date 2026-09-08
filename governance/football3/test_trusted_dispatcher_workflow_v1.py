#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
TRUSTED = ROOT / ".github/workflows/football3-gpt-auto-dispatch-trusted-dispatcher-v1.yml"
CANDIDATE = ROOT / ".github/workflows/football3-gpt-auto-dispatch-trusted-dispatcher-candidate.yml"


class TrustedDispatcherWorkflowContractTest(unittest.TestCase):
    def test_trusted_dispatcher_is_workflow_run_only(self) -> None:
        text = TRUSTED.read_text(encoding="utf-8")
        self.assertIn("workflow_run:", text)
        self.assertNotIn("pull_request_target:", text)
        on_block = text.split("permissions:", 1)[0]
        self.assertNotIn("\n  pull_request:", on_block)
        self.assertIn("actions: write", text)
        self.assertIn("Football3 GPT Auto Dispatch Receiver V3", text)

    def test_dispatcher_never_consumes_receiver_artifact_or_carrier_checkout(self) -> None:
        text = TRUSTED.read_text(encoding="utf-8")
        self.assertNotIn("download-artifact", text)
        self.assertIn("ref: ${{ steps.trusted_source.outputs.canonical_sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("RECEIVER_HEAD_BRANCH", text)
        self.assertIn("football3/formal-gpt-runner-request-carrier-v1", text)

    def test_dispatcher_revalidates_authority_and_exact_request(self) -> None:
        text = TRUSTED.read_text(encoding="utf-8")
        for token in (
            "--actor",
            "--trusted-checkout-sha",
            "--trusted-dispatcher-sha",
            "--trusted-dispatcher-run-id",
            "--receiver-run-id",
            "prepare",
            "Upload reservation before dispatch",
            "dispatch",
            "finalize",
            "auto_dispatch_final_binding_receipt.json",
        ):
            self.assertIn(token, text)

    def test_candidate_workflow_is_read_only(self) -> None:
        text = CANDIDATE.read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertNotIn("pull_request_target:", text)
        self.assertNotIn("actions: write", text)
        self.assertIn("permissions:", text)
        self.assertIn("contents: read", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
