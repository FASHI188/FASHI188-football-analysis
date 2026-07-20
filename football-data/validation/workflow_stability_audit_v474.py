#!/usr/bin/env python3
"""Repository-wide GitHub Actions stability audit.

Engineering-only diagnostic. It scans workflow text for patterns that commonly
create noisy red runs, recursive CI, stale generated commits, concurrency races,
or deprecated JavaScript action runtimes. It never changes model weights,
probabilities, CURRENT, or competition outputs.
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

NODE20_PATTERNS = {
    "actions/checkout@v4": "actions/checkout@v6",
    "actions/setup-python@v5": "actions/setup-python@v6",
    "actions/upload-artifact@v4": "actions/upload-artifact@v7",
    "actions/download-artifact@v4": "actions/download-artifact@v8",
    "actions/github-script@v7": "actions/github-script@v8",
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _top_level_contents_write(text: str) -> bool:
    # Conservative text check. Job-level write permission is also reported because
    # it is still a repository writer, but global write permission is the larger risk.
    return bool(re.search(r"(?ms)^permissions:\s*\n(?:^[ \t]+.*\n)*?^[ \t]+contents:\s*write\s*$", text))


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
                value = stripped[1:].strip().strip('"\'')
                paths.append(value)
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
    node20_refs: list[dict[str, str]] = []
    generated_manifest_writers: list[str] = []
    contents_api_writers: list[str] = []
    self_trigger_risks: list[dict[str, Any]] = []

    for path in workflow_files:
        text = path.read_text(encoding="utf-8")
        name = rel(path)
        push_paths = _extract_push_paths(text)

        if "git push" in text:
            direct_git_push.append(name)
        if "git pull --rebase" in text:
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

        for old, recommended in NODE20_PATTERNS.items():
            if old in text:
                node20_refs.append({"workflow": name, "reference": old, "recommended": recommended})

        # Heuristic: a workflow that writes tracked files and has broad push paths can
        # recursively trigger itself or sibling CI even if commit messages attempt skip-ci.
        writes_repo = "git push" in text or ("api.github.com/repos/" in text and "/contents/" in text)
        if writes_repo and push_paths:
            self_trigger_risks.append({
                "workflow": name,
                "push_paths": push_paths,
                "reason": "repository-writing workflow also has push triggers; verify written paths are excluded",
            })

    risk_score = (
        5 * len(direct_git_push)
        + 3 * len(global_contents_write)
        + 2 * len(cancel_in_progress_true)
        + len(node20_refs)
        + len(missing_timeout)
    )

    return {
        "schema_version": "V4.7.4-workflow-stability-audit",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_DIAGNOSTIC",
        "workflow_count": len(workflow_files),
        "risk_score": risk_score,
        "findings": {
            "direct_git_push_workflows": direct_git_push,
            "git_pull_rebase_workflows": git_pull_rebase,
            "contents_write_workflows": contents_write,
            "global_contents_write_workflows": global_contents_write,
            "cancel_in_progress_true_workflows": cancel_in_progress_true,
            "always_condition_workflows": always_jobs,
            "missing_timeout_workflows": missing_timeout,
            "node20_action_references": node20_refs,
            "generated_manifest_writers": generated_manifest_writers,
            "contents_api_writers": contents_api_writers,
            "self_trigger_risks": self_trigger_risks,
        },
        "formal_weight_change": False,
        "automatic_promotion": False,
        "current_rule_change": False,
        "policy": "Diagnostic only during stabilization. Findings do not fail CI until repository writers are migrated and a clean baseline is established.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    report = audit()
    if args.write_receipt:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        findings = report["findings"]
        print(json.dumps({
            "status": report["status"],
            "workflow_count": report["workflow_count"],
            "risk_score": report["risk_score"],
            "direct_git_push_count": len(findings["direct_git_push_workflows"]),
            "global_contents_write_count": len(findings["global_contents_write_workflows"]),
            "node20_reference_count": len(findings["node20_action_references"]),
            "cancel_in_progress_true_count": len(findings["cancel_in_progress_true_workflows"]),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
