#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
TRUSTED = ROOT / ".github/workflows/football3-gpt-auto-dispatch-trusted-dispatcher-v1.yml"
RUNTIME = ROOT / "football-data/validation/repository_integrity_runtime_v473.py"
EXPECTED_CANONICAL_REF = "football3/formal-gpt-runner-integration-v1"
REQUIRED_HELPERS = (
    "football-data/formal_gpt_gateway_v1/auto_dispatch_bridge_v1.py",
    "football-data/formal_gpt_gateway_v1/request_contract_v1.py",
)

SPEC = importlib.util.spec_from_file_location("football3_repository_integrity_runtime_v473", RUNTIME)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CrossCheckoutDependencyIntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = TRUSTED.read_text(encoding="utf-8")

    def test_current_trusted_workflow_has_complete_exact_checkout_contract(self) -> None:
        for path in REQUIRED_HELPERS:
            contract = MODULE._validate_cross_checkout_reference(self.text, path)
            self.assertIsNotNone(contract, path)
            assert contract is not None
            self.assertEqual(contract["canonical_ref"], EXPECTED_CANONICAL_REF)
            self.assertFalse(contract["persist_credentials"])
            self.assertTrue(contract["head_verified_before_compile"])
            self.assertTrue(contract["compile_verified_before_execution"])

    def test_missing_sha_guard_fails_closed(self) -> None:
        broken = self.text.replace("^[0-9a-f]{40}$", "^[0-9a-f]+$")
        self.assertIsNone(MODULE._validate_cross_checkout_reference(broken, REQUIRED_HELPERS[0]))

    def test_persisted_credentials_fail_closed(self) -> None:
        broken = self.text.replace("persist-credentials: false", "persist-credentials: true", 1)
        self.assertIsNone(MODULE._validate_cross_checkout_reference(broken, REQUIRED_HELPERS[0]))

    def test_missing_head_equality_fails_closed(self) -> None:
        broken = self.text.replace(
            'test "$(git rev-parse HEAD)" = "${{ steps.trusted_source.outputs.canonical_sha }}"',
            'echo "HEAD verification removed"',
            1,
        )
        self.assertIsNone(MODULE._validate_cross_checkout_reference(broken, REQUIRED_HELPERS[0]))

    def test_precheckout_execution_fails_closed(self) -> None:
        broken = (
            "      - name: Invalid precheckout execution\n"
            f"        run: python3 {REQUIRED_HELPERS[0]} prepare\n"
            + self.text
        )
        self.assertIsNone(MODULE._validate_cross_checkout_reference(broken, REQUIRED_HELPERS[0]))

    def test_missing_postcheckout_compile_fails_closed(self) -> None:
        broken = self.text.replace("-m py_compile", "-m compileall", 1)
        self.assertIsNone(MODULE._validate_cross_checkout_reference(broken, REQUIRED_HELPERS[0]))

    def test_dynamic_unfixed_ref_fails_closed(self) -> None:
        broken = self.text.replace(
            "git/ref/heads/football3%2Fformal-gpt-runner-integration-v1",
            "git/ref/heads/${CANONICAL_REF}",
            1,
        )
        self.assertIsNone(MODULE._validate_cross_checkout_reference(broken, REQUIRED_HELPERS[0]))

    def test_candidate_exact_sha_contains_all_external_helpers_when_required(self) -> None:
        sha = os.environ.get("FOOTBALL3_CANONICAL_CANDIDATE_SHA")
        if not sha:
            self.skipTest("candidate exact SHA existence proof is required only in candidate acceptance")
        self.assertRegex(sha, r"^[0-9a-f]{40}$")
        expected_ref = os.environ.get("FOOTBALL3_CANONICAL_REF")
        self.assertEqual(expected_ref, EXPECTED_CANONICAL_REF)
        fetch_head = subprocess.check_output(
            ["git", "rev-parse", "FETCH_HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
        self.assertEqual(fetch_head, sha)
        for path in REQUIRED_HELPERS:
            subprocess.run(
                ["git", "cat-file", "-e", f"{sha}:{path}"],
                cwd=ROOT,
                check=True,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
