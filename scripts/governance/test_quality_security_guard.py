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

    def test_c072d2_temporary_zero_label_execution_hook(self) -> None:
        """Temporary engineering hook for PR #284; remove immediately after evidence capture."""
        script = Path("football-data/research/audit_c072d2_free_ou25_zero_label.py")
        if not script.exists():
            self.skipTest("C072-D2 auditor not present on this branch")
        subprocess.run([sys.executable, str(script)], check=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
