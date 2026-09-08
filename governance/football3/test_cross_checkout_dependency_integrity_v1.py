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
CANDIDATE = ROOT / ".github/workflows/football3-gpt-auto-dispatch-trusted-dispatcher-candidate.yml"
RUNTIME = ROOT / "football-data/validation/repository_integrity_runtime_v473.py"
EXPECTED_CANONICAL_REF = "football3/formal-gpt-runner-integration-v1"
EXPECTED_CANONICAL_CANDIDATE_SHA = "f2fdd57c6fe9104c7f031f6a2ac7468757947a12"
EXPECTED_INTEGRATION_RECEIVER_SHA = "0e102ac3689185d3378bab3bc416ccec20d519de"
EXPECTED_CANONICAL_OBJECT_REF = "refs/football3-validation/canonical-helper"
EXPECTED_INTEGRATION_RECEIVER_OBJECT_REF = "refs/football3-validation/integration-receiver"
RECEIVER_WORKFLOW_PATH = ".github/workflows/football3-gpt-auto-dispatch-bridge-v1.yml"
RECEIVER_NAME = "Football3 GPT Auto Dispatch Receiver V3"
TRUSTED_CONTRACT_JOB = "trusted-dispatcher-contract"
CROSS_CHECKOUT_JOB = "cross-checkout-dependency-integrity"
REQUIRED_HELPERS = (
    "football-data/formal_gpt_gateway_v1/auto_dispatch_bridge_v1.py",
    "football-data/formal_gpt_gateway_v1/request_contract_v1.py",
)

SPEC = importlib.util.spec_from_file_location("football3_repository_integrity_runtime_v473", RUNTIME)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _job_span(text: str, job_name: str) -> tuple[int, int]:
    lines = text.splitlines(keepends=True)
    marker = f"  {job_name}:"
    matches = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == marker]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one YAML job {job_name!r}, found {len(matches)}")

    start_line = matches[0]
    end_line = len(lines)
    for index in range(start_line + 1, len(lines)):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0 or (indent == 2 and raw.rstrip("\r\n").endswith(":")):
            end_line = index
            break

    start = sum(len(line) for line in lines[:start_line])
    end = sum(len(line) for line in lines[:end_line])
    return start, end


def _job_block(text: str, job_name: str) -> str:
    start, end = _job_span(text, job_name)
    return text[start:end]


def _replace_job_block(text: str, job_name: str, replacement: str) -> str:
    start, end = _job_span(text, job_name)
    return text[:start] + replacement + text[end:]


def _mutate_cross_checkout_job_once(text: str, job_name: str, old: str, new: str) -> str:
    if job_name != CROSS_CHECKOUT_JOB:
        raise AssertionError(f"mutation must target {CROSS_CHECKOUT_JOB}, not {job_name}")

    trusted_before = _job_block(text, TRUSTED_CONTRACT_JOB)
    target_before = _job_block(text, CROSS_CHECKOUT_JOB)
    hit_count = target_before.count(old)
    if hit_count != 1:
        raise AssertionError(f"target mutation must hit exactly once inside {CROSS_CHECKOUT_JOB}, found {hit_count}")

    target_after = target_before.replace(old, new, 1)
    if target_after.count(old) != 0:
        raise AssertionError("target mutation did not remove exactly one scoped occurrence")

    mutated = _replace_job_block(text, CROSS_CHECKOUT_JOB, target_after)
    trusted_after = _job_block(mutated, TRUSTED_CONTRACT_JOB)
    if trusted_after != trusted_before:
        raise AssertionError("trusted-dispatcher-contract job changed during cross-checkout mutation")
    if _job_block(mutated, CROSS_CHECKOUT_JOB) != target_after:
        raise AssertionError("cross-checkout job reconstruction drifted")
    return mutated


def assert_candidate_dual_object_binding_contract(text: str) -> None:
    cross_job = _job_block(text, CROSS_CHECKOUT_JOB)
    required = (
        f"FOOTBALL3_CANONICAL_CANDIDATE_SHA: {EXPECTED_CANONICAL_CANDIDATE_SHA}",
        f"FOOTBALL3_CANONICAL_OBJECT_REF: {EXPECTED_CANONICAL_OBJECT_REF}",
        f"FOOTBALL3_INTEGRATION_RECEIVER_SHA: {EXPECTED_INTEGRATION_RECEIVER_SHA}",
        f"FOOTBALL3_INTEGRATION_RECEIVER_OBJECT_REF: {EXPECTED_INTEGRATION_RECEIVER_OBJECT_REF}",
        'git fetch --no-tags --depth=1 origin "$FOOTBALL3_CANONICAL_CANDIDATE_SHA"',
        'test "$(git rev-parse FETCH_HEAD)" = "$FOOTBALL3_CANONICAL_CANDIDATE_SHA"',
        'git cat-file -e "${FOOTBALL3_CANONICAL_CANDIDATE_SHA}^{commit}"',
        'git update-ref "$FOOTBALL3_CANONICAL_OBJECT_REF" "$FOOTBALL3_CANONICAL_CANDIDATE_SHA"',
        'test "$(git rev-parse "$FOOTBALL3_CANONICAL_OBJECT_REF")" = "$FOOTBALL3_CANONICAL_CANDIDATE_SHA"',
        'git fetch --no-tags --depth=1 origin "$FOOTBALL3_INTEGRATION_RECEIVER_SHA"',
        'test "$(git rev-parse FETCH_HEAD)" = "$FOOTBALL3_INTEGRATION_RECEIVER_SHA"',
        'git cat-file -e "${FOOTBALL3_INTEGRATION_RECEIVER_SHA}^{commit}"',
        'git update-ref "$FOOTBALL3_INTEGRATION_RECEIVER_OBJECT_REF" "$FOOTBALL3_INTEGRATION_RECEIVER_SHA"',
        'test "$(git rev-parse "$FOOTBALL3_INTEGRATION_RECEIVER_OBJECT_REF")" = "$FOOTBALL3_INTEGRATION_RECEIVER_SHA"',
        'git show "$FOOTBALL3_INTEGRATION_RECEIVER_OBJECT_REF:.github/workflows/football3-gpt-auto-dispatch-bridge-v1.yml"',
        'test "$(git rev-parse HEAD)" = "$CANDIDATE_EXACT_HEAD"',
        "subprocess.run(['git', 'cat-file', '-e', f'{canonical_ref}:{helper}']",
        "['git', 'cat-file', '-e', f'{receiver_ref}:.github/workflows/football3-gpt-auto-dispatch-bridge-v1.yml']",
        '"canonical_object_binding": "PASS"',
        '"integration_receiver_object_binding": "PASS"',
        '"independent_binding_survives_fetch_head_overwrite": "PASS"',
    )
    for token in required:
        if token not in cross_job:
            raise AssertionError(f"dual-object binding contract missing from {CROSS_CHECKOUT_JOB}: {token}")

    canonical_fetch = cross_job.index(
        'git fetch --no-tags --depth=1 origin "$FOOTBALL3_CANONICAL_CANDIDATE_SHA"'
    )
    canonical_fetch_head = cross_job.index(
        'test "$(git rev-parse FETCH_HEAD)" = "$FOOTBALL3_CANONICAL_CANDIDATE_SHA"',
        canonical_fetch,
    )
    canonical_bind = cross_job.index(
        'git update-ref "$FOOTBALL3_CANONICAL_OBJECT_REF" "$FOOTBALL3_CANONICAL_CANDIDATE_SHA"',
        canonical_fetch_head,
    )
    receiver_fetch = cross_job.index(
        'git fetch --no-tags --depth=1 origin "$FOOTBALL3_INTEGRATION_RECEIVER_SHA"',
        canonical_bind,
    )
    receiver_fetch_head = cross_job.index(
        'test "$(git rev-parse FETCH_HEAD)" = "$FOOTBALL3_INTEGRATION_RECEIVER_SHA"',
        receiver_fetch,
    )
    receiver_bind = cross_job.index(
        'git update-ref "$FOOTBALL3_INTEGRATION_RECEIVER_OBJECT_REF" "$FOOTBALL3_INTEGRATION_RECEIVER_SHA"',
        receiver_fetch_head,
    )
    if not (canonical_fetch < canonical_fetch_head < canonical_bind < receiver_fetch < receiver_fetch_head < receiver_bind):
        raise AssertionError("fetch verification and object binding order is invalid")

    if cross_job.count(f"FOOTBALL3_CANONICAL_CANDIDATE_SHA: {EXPECTED_CANONICAL_CANDIDATE_SHA}") != 1:
        raise AssertionError("canonical helper SHA must be uniquely bound in cross-checkout job")
    if cross_job.count(f"FOOTBALL3_INTEGRATION_RECEIVER_SHA: {EXPECTED_INTEGRATION_RECEIVER_SHA}") != 1:
        raise AssertionError("integration Receiver SHA must be uniquely bound in cross-checkout job")
    if text.count(f"FOOTBALL3_INTEGRATION_RECEIVER_SHA: {EXPECTED_INTEGRATION_RECEIVER_SHA}") < 2:
        raise AssertionError("integration Receiver SHA must remain bound in both candidate jobs")


class CrossCheckoutDependencyIntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = TRUSTED.read_text(encoding="utf-8")
        self.candidate = CANDIDATE.read_text(encoding="utf-8")

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

    def test_candidate_dual_object_binding_workflow_contract(self) -> None:
        assert_candidate_dual_object_binding_contract(self.candidate)

    def test_candidate_dual_object_binding_negative_mutations_fail_closed(self) -> None:
        base = self.candidate
        receiver_existence = 'git cat-file -e "${FOOTBALL3_INTEGRATION_RECEIVER_SHA}^{commit}"'
        receiver_existence_removed = 'echo "receiver object existence verification removed"'

        trusted_before = _job_block(base, TRUSTED_CONTRACT_JOB)
        target_before = _job_block(base, CROSS_CHECKOUT_JOB)
        self.assertEqual(target_before.count(receiver_existence), 1)

        scoped_receiver_mutation = _mutate_cross_checkout_job_once(
            base,
            CROSS_CHECKOUT_JOB,
            receiver_existence,
            receiver_existence_removed,
        )
        target_after = _job_block(scoped_receiver_mutation, CROSS_CHECKOUT_JOB)
        self.assertEqual(target_after.count(receiver_existence), 0)
        self.assertEqual(_job_block(scoped_receiver_mutation, TRUSTED_CONTRACT_JOB), trusted_before)

        invariant_tokens = (
            f"FOOTBALL3_CANONICAL_CANDIDATE_SHA: {EXPECTED_CANONICAL_CANDIDATE_SHA}",
            f"FOOTBALL3_INTEGRATION_RECEIVER_SHA: {EXPECTED_INTEGRATION_RECEIVER_SHA}",
            'test "$(git rev-parse FETCH_HEAD)" = "$FOOTBALL3_CANONICAL_CANDIDATE_SHA"',
            'test "$(git rev-parse FETCH_HEAD)" = "$FOOTBALL3_INTEGRATION_RECEIVER_SHA"',
            'test "$(git rev-parse HEAD)" = "$CANDIDATE_EXACT_HEAD"',
            "python3 -m py_compile governance/football3/test_cross_checkout_dependency_integrity_v1.py",
        )
        for token in invariant_tokens:
            self.assertEqual(scoped_receiver_mutation.count(token), base.count(token), token)

        with self.assertRaises((AssertionError, ValueError)):
            assert_candidate_dual_object_binding_contract(scoped_receiver_mutation)

        with self.assertRaises(AssertionError):
            _mutate_cross_checkout_job_once(
                base,
                CROSS_CHECKOUT_JOB,
                "target-that-does-not-exist",
                "replacement",
            )
        with self.assertRaises(AssertionError):
            _mutate_cross_checkout_job_once(
                base,
                TRUSTED_CONTRACT_JOB,
                receiver_existence,
                receiver_existence_removed,
            )

        duplicated_target_job = target_before.replace(
            receiver_existence,
            receiver_existence + "\n          " + receiver_existence,
            1,
        )
        duplicated_target_workflow = _replace_job_block(base, CROSS_CHECKOUT_JOB, duplicated_target_job)
        self.assertEqual(_job_block(duplicated_target_workflow, CROSS_CHECKOUT_JOB).count(receiver_existence), 2)
        self.assertEqual(_job_block(duplicated_target_workflow, TRUSTED_CONTRACT_JOB), trusted_before)
        with self.assertRaises(AssertionError):
            _mutate_cross_checkout_job_once(
                duplicated_target_workflow,
                CROSS_CHECKOUT_JOB,
                receiver_existence,
                receiver_existence_removed,
            )

        mutations = (
            base.replace(EXPECTED_CANONICAL_CANDIDATE_SHA, EXPECTED_INTEGRATION_RECEIVER_SHA, 1),
            base.replace(EXPECTED_INTEGRATION_RECEIVER_SHA, EXPECTED_CANONICAL_CANDIDATE_SHA),
            base.replace(
                f"FOOTBALL3_CANONICAL_CANDIDATE_SHA: {EXPECTED_CANONICAL_CANDIDATE_SHA}",
                f"FOOTBALL3_CANONICAL_CANDIDATE_SHA: {EXPECTED_INTEGRATION_RECEIVER_SHA}",
                1,
            ).replace(
                f"FOOTBALL3_INTEGRATION_RECEIVER_SHA: {EXPECTED_INTEGRATION_RECEIVER_SHA}",
                f"FOOTBALL3_INTEGRATION_RECEIVER_SHA: {EXPECTED_CANONICAL_CANDIDATE_SHA}",
            ),
            base.replace(
                'git update-ref "$FOOTBALL3_CANONICAL_OBJECT_REF" "$FOOTBALL3_CANONICAL_CANDIDATE_SHA"',
                'echo "canonical object binding removed"',
                1,
            ),
            base.replace(
                'git update-ref "$FOOTBALL3_INTEGRATION_RECEIVER_OBJECT_REF" "$FOOTBALL3_INTEGRATION_RECEIVER_SHA"',
                'echo "receiver object binding removed"',
                1,
            ),
            base.replace(
                'git cat-file -e "${FOOTBALL3_CANONICAL_CANDIDATE_SHA}^{commit}"',
                'echo "canonical object existence verification removed"',
                1,
            ),
            base.replace(
                'test "$(git rev-parse HEAD)" = "$CANDIDATE_EXACT_HEAD"',
                'echo "candidate HEAD equality verification removed"',
            ),
        )
        for mutated in mutations:
            with self.assertRaises((AssertionError, ValueError)):
                assert_candidate_dual_object_binding_contract(mutated)

    def test_candidate_exact_objects_are_independently_bound_when_required(self) -> None:
        canonical_sha = os.environ.get("FOOTBALL3_CANONICAL_CANDIDATE_SHA")
        if not canonical_sha:
            self.skipTest("candidate exact object proof is required only in candidate acceptance")

        receiver_sha = os.environ.get("FOOTBALL3_INTEGRATION_RECEIVER_SHA")
        canonical_ref = os.environ.get("FOOTBALL3_CANONICAL_OBJECT_REF")
        receiver_ref = os.environ.get("FOOTBALL3_INTEGRATION_RECEIVER_OBJECT_REF")
        candidate_head = os.environ.get("CANDIDATE_EXACT_HEAD")
        expected_ref = os.environ.get("FOOTBALL3_CANONICAL_REF")

        self.assertEqual(canonical_sha, EXPECTED_CANONICAL_CANDIDATE_SHA)
        self.assertEqual(receiver_sha, EXPECTED_INTEGRATION_RECEIVER_SHA)
        self.assertEqual(canonical_ref, EXPECTED_CANONICAL_OBJECT_REF)
        self.assertEqual(receiver_ref, EXPECTED_INTEGRATION_RECEIVER_OBJECT_REF)
        self.assertEqual(expected_ref, EXPECTED_CANONICAL_REF)
        self.assertRegex(canonical_sha, r"^[0-9a-f]{40}$")
        assert receiver_sha is not None
        self.assertRegex(receiver_sha, r"^[0-9a-f]{40}$")
        self.assertNotEqual(canonical_sha, receiver_sha)

        assert canonical_ref is not None
        assert receiver_ref is not None
        self.assertEqual(_git("rev-parse", canonical_ref), canonical_sha)
        self.assertEqual(_git("rev-parse", receiver_ref), receiver_sha)
        self.assertEqual(_git("cat-file", "-t", canonical_sha), "commit")
        self.assertEqual(_git("cat-file", "-t", receiver_sha), "commit")
        self.assertEqual(_git("rev-parse", "FETCH_HEAD"), receiver_sha)
        if candidate_head:
            self.assertEqual(_git("rev-parse", "HEAD"), candidate_head)

        for path in REQUIRED_HELPERS:
            subprocess.run(
                ["git", "cat-file", "-e", f"{canonical_ref}:{path}"],
                cwd=ROOT,
                check=True,
            )

        subprocess.run(
            ["git", "cat-file", "-e", f"{receiver_ref}:{RECEIVER_WORKFLOW_PATH}"],
            cwd=ROOT,
            check=True,
        )
        receiver_text = _git("show", f"{receiver_ref}:{RECEIVER_WORKFLOW_PATH}")
        self.assertTrue(receiver_text.startswith(f"name: {RECEIVER_NAME}\n"))
        self.assertIn("AUTO_DISPATCH_RECEIVER_SIGNAL=READY", receiver_text)
        self.assertIn("auto_dispatch_bridge_v1.py", receiver_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
