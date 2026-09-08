#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
RECEIVER = ROOT / ".github/workflows/football3-gpt-auto-dispatch-bridge-v1.yml"
TRUSTED = ROOT / ".github/workflows/football3-gpt-auto-dispatch-trusted-dispatcher-v1.yml"
CANDIDATE = ROOT / ".github/workflows/football3-gpt-auto-dispatch-trusted-dispatcher-candidate.yml"
RECEIVER_NAME = "Football3 GPT Auto Dispatch Receiver V3"
INTEGRATION_BRANCH = "football3/formal-gpt-runner-integration-v1"


def assert_registration_safe(text: str) -> None:
    required = (
        f"name: {RECEIVER_NAME}\n",
        "on:\n  pull_request:\n",
        f"      - {INTEGRATION_BRANCH}\n",
        "    types: [opened, edited, reopened, synchronize]",
        "permissions:\n  contents: read\n\njobs:",
        "  default-branch-registration-safe-receipt:",
        "DEFAULT_BRANCH_RECEIVER_REGISTRATION=REGISTERED",
        "DEFAULT_BRANCH_RECEIVER_EXECUTION=SKIP",
        "DEFAULT_BRANCH_RECEIVER_PRODUCTION_SIDE_EFFECTS=false",
        "DEFAULT_BRANCH_RECEIVER_CARRIER_CODE_EXECUTED=false",
        "DEFAULT_BRANCH_RECEIVER_FORMAL_DISPATCH=false",
    )
    for token in required:
        if token not in text:
            raise AssertionError(f"registration condition missing: {token}")

    forbidden = (
        "pull_request_target:",
        "actions: write",
        "contents: write",
        "actions/checkout",
        "download-artifact",
        "upload-artifact",
        "workflow_dispatch:",
        "repository_dispatch:",
        "auto_dispatch_bridge_v1.py",
        "test_auto_dispatch_bridge_v1.py",
        ".py ",
        ".py\n",
        "python ",
        "python3 ",
        "request_pr_number",
        "/dispatches",
        "gh workflow run",
        "curl ",
        "Airtable",
    )
    for token in forbidden:
        if token in text:
            raise AssertionError(f"registration shell contains forbidden capability: {token}")

    if text.count("permissions:") != 1:
        raise AssertionError("registration shell must have exactly one top-level permissions block")


def integration_receiver_text() -> str:
    path = os.environ.get("FOOTBALL3_INTEGRATION_RECEIVER_PATH", "")
    if not path:
        raise AssertionError("FOOTBALL3_INTEGRATION_RECEIVER_PATH is required")
    p = Path(path)
    if not p.is_file():
        raise AssertionError(f"integration Receiver materialization missing: {p}")
    return p.read_text(encoding="utf-8")


class TrustedDispatcherWorkflowContractTest(unittest.TestCase):
    def test_trusted_dispatcher_is_workflow_run_only(self) -> None:
        text = TRUSTED.read_text(encoding="utf-8")
        self.assertIn("workflow_run:", text)
        self.assertNotIn("pull_request_target:", text)
        on_block = text.split("permissions:", 1)[0]
        self.assertNotIn("\n  pull_request:\n", on_block)
        self.assertIn("actions: write", text)
        self.assertIn(f"      - {RECEIVER_NAME}\n", text)

    def test_default_branch_receiver_is_registration_safe(self) -> None:
        text = RECEIVER.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"name: {RECEIVER_NAME}\n"))
        assert_registration_safe(text)

    def test_integration_receiver_remains_full_formal_receiver(self) -> None:
        text = integration_receiver_text()
        self.assertTrue(text.startswith(f"name: {RECEIVER_NAME}\n"))
        self.assertIn(f"      - {INTEGRATION_BRANCH}\n", text)
        self.assertIn("  carrier-edit-receiver:", text)
        self.assertIn("  candidate-contract-security:", text)
        self.assertIn("AUTO_DISPATCH_RECEIVER_SIGNAL=READY", text)
        self.assertIn("auto_dispatch_bridge_v1.py", text)
        self.assertIn("test_auto_dispatch_bridge_v1.py", text)
        self.assertIn(" audit-live \\", text)
        self.assertNotIn("pull_request_target:", text)
        self.assertNotIn("actions: write", text)
        carrier = text.split("  carrier-edit-receiver:", 1)[1].split(
            "  candidate-contract-security:", 1
        )[0]
        self.assertNotIn("actions/checkout", carrier)
        self.assertIn("AUTO_DISPATCH_RECEIVER_CARRIER_CODE_EXECUTED=false", carrier)

    def test_dispatcher_subscription_exactly_matches_receiver_name(self) -> None:
        receiver = RECEIVER.read_text(encoding="utf-8")
        integration = integration_receiver_text()
        trusted = TRUSTED.read_text(encoding="utf-8")
        self.assertTrue(receiver.startswith(f"name: {RECEIVER_NAME}\n"))
        self.assertTrue(integration.startswith(f"name: {RECEIVER_NAME}\n"))
        self.assertIn(f"      - {RECEIVER_NAME}\n", trusted)
        self.assertEqual(trusted.count(f"      - {RECEIVER_NAME}\n"), 1)
        self.assertIn("    types: [completed]", trusted)

    def test_registration_contract_rejects_removed_conditions_and_privilege_expansion(self) -> None:
        base = RECEIVER.read_text(encoding="utf-8")
        mutations = (
            base.replace(f"name: {RECEIVER_NAME}\n", "", 1),
            base.replace("on:\n  pull_request:\n", "on:\n", 1),
            base.replace(f"      - {INTEGRATION_BRANCH}\n", "", 1),
            base.replace("    types: [opened, edited, reopened, synchronize]\n", "", 1),
            base.replace("  default-branch-registration-safe-receipt:\n", "  registration:\n", 1),
            base.replace("DEFAULT_BRANCH_RECEIVER_EXECUTION=SKIP", "DEFAULT_BRANCH_RECEIVER_EXECUTION=RUN", 1),
            base.replace("contents: read", "contents: write", 1),
            base.replace("permissions:\n  contents: read", "permissions:\n  contents: read\n  actions: write", 1),
            base.replace("on:\n  pull_request:\n", "on:\n  pull_request_target:\n", 1),
            base + "\n# forbidden mutation\npython3 football-data/formal_gpt_gateway_v1/auto_dispatch_bridge_v1.py\n",
            base + "\n# forbidden mutation\n- uses: actions/checkout@v6\n",
            base + "\nworkflow_dispatch:\n",
        )
        for mutated in mutations:
            with self.assertRaises(AssertionError):
                assert_registration_safe(mutated)

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
        self.assertIn("FOOTBALL3_INTEGRATION_RECEIVER_SHA", text)
        self.assertIn("FOOTBALL3_INTEGRATION_RECEIVER_PATH", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
