#!/usr/bin/env python3
"""Dependency-light changed-file quality and security guard for CI.

The guard is deliberately scoped to files changed between two Git commits so legacy
repository debt does not make every pull request red. It does not call external
services, read GitHub Secrets, or mutate repository state.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

TEXT_SUFFIXES = {
    ".py", ".sh", ".ps1", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".md", ".txt"
}
WORKFLOW_PREFIX = ".github/workflows/"
DEDICATED_WORKFLOW = ".github/workflows/football-engineering-quality-security.yml"

SECRET_PATTERNS = (
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{32,}\b")),
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)
DANGEROUS_SHELL = (
    ("REMOTE_PIPE_TO_SHELL", re.compile(r"(?:curl|wget)[^\n|]*\|\s*(?:sudo\s+)?(?:bash|sh)\b", re.I)),
    ("CHMOD_777", re.compile(r"\bchmod\s+(?:-R\s+)?777\b", re.I)),
)
CONFLICT_MARKERS = (("<" * 7) + " ", (">" * 7) + " ")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()


def changed_files(base: str, head: str) -> list[str]:
    output = git("diff", "--name-only", "--diff-filter=ACMR", base, head)
    return [line for line in output.splitlines() if line and Path(line).is_file()]


def added_files(base: str, head: str) -> set[str]:
    output = git("diff", "--name-only", "--diff-filter=A", base, head)
    return {line for line in output.splitlines() if line}


def scan_text(path: str, text: str, *, is_added: bool) -> list[str]:
    findings: list[str] = []
    for marker in CONFLICT_MARKERS:
        if marker in text:
            findings.append(f"{path}: MERGE_CONFLICT_MARKER")
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(f"{path}: POSSIBLE_{name}")
    for name, pattern in DANGEROUS_SHELL:
        if pattern.search(text):
            findings.append(f"{path}: {name}")

    if path.startswith(WORKFLOW_PREFIX) and path.endswith((".yml", ".yaml")):
        lowered = text.lower()
        if re.search(r"(?m)^\s*pull_request_target\s*:", text):
            findings.append(f"{path}: PULL_REQUEST_TARGET_FORBIDDEN")
        if re.search(r"(?m)^\s*permissions\s*:\s*write-all\s*$", text, re.I):
            findings.append(f"{path}: WRITE_ALL_FORBIDDEN")
        if is_added and not re.search(r"(?m)^permissions\s*:", text):
            findings.append(f"{path}: NEW_WORKFLOW_MISSING_TOP_LEVEL_PERMISSIONS")
        if path == DEDICATED_WORKFLOW:
            if "secrets." in lowered:
                findings.append(f"{path}: DEDICATED_WORKFLOW_MUST_NOT_USE_SECRETS")
            if not re.search(r"(?ms)^permissions\s*:\s*\n\s+contents\s*:\s*read\s*$", text):
                findings.append(f"{path}: DEDICATED_WORKFLOW_MUST_BE_CONTENTS_READ_ONLY")
            for use in re.findall(r"(?m)^\s*-?\s*uses\s*:\s*([^\s#]+)", text):
                if not (use.startswith("actions/checkout@") or use.startswith("actions/setup-python@")):
                    findings.append(f"{path}: NON_BUILTIN_ACTION:{use}")
    return findings


def check_python(path: str) -> list[str]:
    try:
        source = Path(path).read_text(encoding="utf-8")
        ast.parse(source, filename=path)
    except (UnicodeDecodeError, SyntaxError) as exc:
        return [f"{path}: PYTHON_SYNTAX_ERROR:{exc}"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()

    files = changed_files(args.base, args.head)
    added = added_files(args.base, args.head)
    findings: list[str] = []

    for path in files:
        p = Path(path)
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(path, text, is_added=path in added))
        if p.suffix.lower() == ".py":
            findings.extend(check_python(path))

    print(f"QUALITY_SECURITY_CHANGED_FILES={len(files)}")
    if findings:
        print("QUALITY_SECURITY_GUARD=FAIL")
        for finding in findings:
            print(f"::error::{finding}")
        return 1
    print("QUALITY_SECURITY_GUARD=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
