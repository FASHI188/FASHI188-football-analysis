#!/usr/bin/env python3
"""Repository-wide GitHub Actions stability gate.

This engineering gate blocks workflow patterns that previously created noisy red
runs, recursive writes, concurrent-main rebase races, or deprecated Node runtimes.
It never changes model weights, probabilities, CURRENT, or competition outputs.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
OUT = ROOT / "football-data" / "manifests" / "workflow_stability_v474_status.json"

DEPRECATED_ACTION_PATTERNS = {
    "actions/checkout@v4": "actions/checkout@v6",
    "actions/setup-python@v5": "actions/setup-python@v6",
    "actions/upload-artifact@v4": "actions/upload-artifact@v7",
    "actions/download-artifact@v4": "actions/download-artifact@v8",
    "actions/github-script@v7": "actions/github-script@v8",
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _top_level_contents_write(text: str) -> bool:
    lines = text.splitlines()
    in_permissions = False
    permissions_indent = -1
    for raw in lines:
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if not stripped or stripped.startswith("#"):
            continue
        if indent == 0 and stripped == "permissions:":
            in_permissions = True
            permissions_indent = indent
            continue
        if in_permissions and indent <= permissions_indent:
            in_permissions = False
        if in_permissions and stripped == "contents: write":
            return True
    return False


def _extract_push_paths(text: str) -> list[str]:
    lines = text.splitlines()
    paths: list[str] = []
    in_push = False
    in_paths = False
    push_indent = paths_indent = -1
    for raw in lines:
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if stripped == "push:":
            in_push = True
            in_paths = False
            push_indent = indent
            continue
        if in_push and indent <= push_indent and stripped and not stripped.startswith("#"):
            in_push = False
            in_paths = False
        if in_push and stripped == "paths:":
            in_paths = True
            paths_indent = indent
            continue
        if in_paths:
            if indent <= paths_indent and stripped and not stripped.startswith("-"):
                in_paths = False
                continue
            if stripped.startswith("-"):
                paths.append(stripped[1:].strip().strip('"\''))
    return paths


def audit() -> dict[str, Any]:
    workflow_files = sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))
    direct_git_push: list[str] = []
    git_pull_rebase: list[str] = []
    contents_write: list[str] = []
    global_contents_write: list[str] = []
    cancel_in_progress_true: list[str] = []
    always_jobs: list[str] = []
    missing_timeout: list[str] = []
    deprecated_action_refs: list[dict[str, str]] = []
    generated_manifest_writers: list[str] = []
    contents_api_writers: list[str] = []
    self_trigger_risks: list[dict[str, Any]] = []

    for path in workflow_files:
        text = path.read_text(encoding="utf-8")
        name = rel(path)
        push_paths = _extract_push_paths(text)

        if re.search(r"(?m)^\s*git\s+push\b", text):
            direct_git_push.append(name)
        if re.search(r"(?m)^\s*git\s+pull\s+--rebase\b", text):
            git_pull_rebase.append(name)
        if "contents: write" in text:
            contents_write.append(name)
        if _top_level_contents_write(text):
            global_contents_write.append(name)
        if re.search(r"cancel-in-progress:\s*true", text):
            cancel_in_progress_true.append(name)
        if re.search(r"\bif:\s*always\(\)", text):
            always_jobs.append(name)
        if "timeout-minutes:" not in text:
            missing_timeout.append(name)
        if "football-data/manifests/" in text and ("git add" in text or "contents/" in text):
            generated_manifest_writers.append(name)
        if "api.github.com/repos/" in text and "/contents/" in text:
            contents_api_writers.append(name)

        for old, recommended in DEPRECATED_ACTION_PATTERNS.items():
            if old in text:
                deprecated_action_refs.append({"workflow": name, "reference": old, "recommended": recommended})

        writes_repo_directly = bool(re.search(r"(?m)^\s*git\s+push\b", text)) or (
            "api.github.com/repos/" in text and "/contents/" in text
        )
        if writes_repo_directly and push_paths:
            self_trigger_risks.append({
                "workflow": name,
                "push_paths": push_paths,
                "reason": "workflow contains direct repository-writing logic and push triggers; shared persistence helpers should be used instead",
            })

    critical_findings = {
        "direct_git_push_workflows": direct_git_push,
        "git_pull_rebase_workflows": git_pull_rebase,
        "global_contents_write_workflows": global_contents_write,
        "deprecated_action_references": deprecated_action_refs,
        "missing_timeout_workflows": missing_timeout,
        "direct_contents_api_writers": contents_api_writers,
        "self_trigger_risks": self_trigger_risks,
    }
    critical_count = sum(len(items) for items in critical_findings.values())
    risk_score = (
        5 * len(direct_git_push)
        + 5 * len(git_pull_rebase)
        + 3 * len(global_contents_write)
        + 2 * len(deprecated_action_refs)
        + 2 * len(missing_timeout)
        + 3 * len(contents_api_writers)
        + 3 * len(self_trigger_risks)
    )

    return {
        "schema_version": "V4.7.4-workflow-stability-gate-r3",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if critical_count == 0 else "FAIL",
        "workflow_count": len(workflow_files),
        "critical_finding_count": critical_count,
        "risk_score": risk_score,
        "critical_findings": critical_findings,
        "findings": {
            "direct_git_push_workflows": direct_git_push,
            "git_pull_rebase_workflows": git_pull_rebase,
            "contents_write_workflows": contents_write,
            "global_contents_write_workflows": global_contents_write,
            "cancel_in_progress_true_workflows": cancel_in_progress_true,
            "always_condition_workflows": always_jobs,
            "missing_timeout_workflows": missing_timeout,
            "deprecated_action_references": deprecated_action_refs,
            "generated_manifest_writers": generated_manifest_writers,
            "contents_api_writers": contents_api_writers,
            "self_trigger_risks": self_trigger_risks,
        },
        "informational_policy": {
            "cancel_in_progress_true": "allowed and recommended for stale push-triggered work where a newer run supersedes the older run",
            "job_scoped_contents_write": "allowed only for jobs that persist validated generated artifacts through the shared safe persistence helpers",
            "always_condition": "allowed when used to preserve failure receipts or aggregate matrix-job outcomes truthfully",
        },
        "formal_weight_change": False,
        "automatic_promotion": False,
        "current_rule_change": False,
        "policy": "Fail closed on direct git push/rebase, global write permissions, deprecated action runtimes, missing timeouts, direct Contents API implementations, or self-trigger write risks. Shared persistence helpers and job-scoped least privilege are required.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    args = parser.parse_args()
    report = audit()
    if args.write_receipt:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["status"],
            "workflow_count": report["workflow_count"],
            "critical_finding_count": report["critical_finding_count"],
            "risk_score": report["risk_score"],
            "critical_findings": report["critical_findings"],
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
