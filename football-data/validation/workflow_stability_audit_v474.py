#!/usr/bin/env python3
"""Repository-wide GitHub Actions stability gate.

This gate blocks newly introduced workflow patterns that create recursive writes,
concurrent-main rebase races, deprecated action runtimes, or unbounded jobs.

A frozen, Git-blob-bound legacy baseline is quarantined rather than silently
approved. Existing legacy findings may only stay unchanged or decrease; any new
unsafe finding fails closed. This engineering gate never changes model weights,
probabilities, CURRENT, or competition outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
OUT = ROOT / "football-data" / "manifests" / "workflow_stability_v474_status.json"

# Frozen Git blob of the last complete pre-quarantine stability receipt.
# It contains the exact legacy findings that existed at the V5.5.30 boundary.
LEGACY_BASELINE_BLOB_SHA = "0ca1019a11f02596692240cfbc0413699dfebda1"

DEPRECATED_ACTION_PATTERNS = {
    "actions/checkout@v4": "actions/checkout@v6",
    "actions/setup-python@v5": "actions/setup-python@v6",
    "actions/upload-artifact@v4": "actions/upload-artifact@v7",
    "actions/download-artifact@v4": "actions/download-artifact@v8",
    "actions/github-script@v7": "actions/github-script@v8",
}

RISK_WEIGHTS = {
    "direct_git_push_workflows": 5,
    "git_pull_rebase_workflows": 5,
    "global_contents_write_workflows": 3,
    "deprecated_action_references": 2,
    "missing_timeout_workflows": 2,
    "direct_contents_api_writers": 3,
    "self_trigger_risks": 3,
    "baseline_integrity_errors": 10,
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
                paths.append(stripped[1:].strip().strip("\"'"))
    return paths


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _load_legacy_baseline() -> tuple[dict[str, list[Any]], list[str]]:
    errors: list[str] = []
    try:
        proc = subprocess.run(
            ["git", "cat-file", "blob", LEGACY_BASELINE_BLOB_SHA],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        raw = proc.stdout
    except Exception as exc:
        return {}, [f"LEGACY_BASELINE_BLOB_UNAVAILABLE:{type(exc).__name__}:{exc}"]

    actual = _git_blob_sha(raw)
    if actual != LEGACY_BASELINE_BLOB_SHA:
        errors.append(
            f"LEGACY_BASELINE_BLOB_HASH_MISMATCH:expected={LEGACY_BASELINE_BLOB_SHA}:actual={actual}"
        )
        return {}, errors

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return {}, [f"LEGACY_BASELINE_JSON_INVALID:{type(exc).__name__}:{exc}"]

    critical = payload.get("critical_findings")
    if not isinstance(critical, dict):
        return {}, ["LEGACY_BASELINE_CRITICAL_FINDINGS_MISSING"]

    normalized: dict[str, list[Any]] = {}
    for category in RISK_WEIGHTS:
        if category == "baseline_integrity_errors":
            continue
        items = critical.get(category, [])
        if not isinstance(items, list):
            errors.append(f"LEGACY_BASELINE_CATEGORY_INVALID:{category}")
            items = []
        normalized[category] = items
    return normalized, errors


def _canonical(item: Any) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _set(items: list[Any]) -> set[str]:
    return {_canonical(item) for item in items}


def _decode(items: set[str]) -> list[Any]:
    return [json.loads(item) for item in sorted(items)]


def _risk_score(findings: dict[str, list[Any]]) -> int:
    return sum(RISK_WEIGHTS.get(category, 1) * len(items) for category, items in findings.items())


def _scan_workflows() -> tuple[dict[str, list[Any]], dict[str, list[Any]], int]:
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
                deprecated_action_refs.append(
                    {"workflow": name, "reference": old, "recommended": recommended}
                )

        writes_repo_directly = bool(re.search(r"(?m)^\s*git\s+push\b", text)) or (
            "api.github.com/repos/" in text and "/contents/" in text
        )
        if writes_repo_directly and push_paths:
            self_trigger_risks.append(
                {
                    "workflow": name,
                    "push_paths": push_paths,
                    "reason": (
                        "workflow contains direct repository-writing logic and push triggers; "
                        "shared persistence helpers should be used instead"
                    ),
                }
            )

    critical = {
        "direct_git_push_workflows": direct_git_push,
        "git_pull_rebase_workflows": git_pull_rebase,
        "global_contents_write_workflows": global_contents_write,
        "deprecated_action_references": deprecated_action_refs,
        "missing_timeout_workflows": missing_timeout,
        "direct_contents_api_writers": contents_api_writers,
        "self_trigger_risks": self_trigger_risks,
    }
    informational = {
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
    }
    return critical, informational, len(workflow_files)


def audit() -> dict[str, Any]:
    current, informational, workflow_count = _scan_workflows()
    baseline, baseline_errors = _load_legacy_baseline()

    novel: dict[str, list[Any]] = {}
    quarantined: dict[str, list[Any]] = {}
    resolved: dict[str, list[Any]] = {}

    categories = sorted(set(current) | set(baseline))
    for category in categories:
        current_set = _set(current.get(category, []))
        baseline_set = _set(baseline.get(category, []))
        novel[category] = _decode(current_set - baseline_set)
        quarantined[category] = _decode(current_set & baseline_set)
        resolved[category] = _decode(baseline_set - current_set)

    novel["baseline_integrity_errors"] = list(baseline_errors)
    quarantined["baseline_integrity_errors"] = []
    resolved["baseline_integrity_errors"] = []

    novel = {k: v for k, v in novel.items() if v}
    quarantined = {k: v for k, v in quarantined.items() if v}
    resolved = {k: v for k, v in resolved.items() if v}

    novel_count = sum(len(items) for items in novel.values())
    quarantined_count = sum(len(items) for items in quarantined.values())
    resolved_count = sum(len(items) for items in resolved.values())
    raw_count = sum(len(items) for items in current.values())

    return {
        "schema_version": "V5.5.31-workflow-stability-legacy-quarantine-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if novel_count == 0 else "FAIL",
        "workflow_count": workflow_count,
        "critical_finding_count": novel_count,
        "risk_score": _risk_score(novel),
        "novel_critical_findings": novel,
        "legacy_quarantine": {
            "baseline_git_blob_sha": LEGACY_BASELINE_BLOB_SHA,
            "baseline_integrity_verified": not baseline_errors,
            "current_quarantined_finding_count": quarantined_count,
            "resolved_since_baseline_count": resolved_count,
            "current_raw_finding_count": raw_count,
            "current_raw_risk_score": _risk_score(current),
            "quarantined_findings": quarantined,
            "resolved_findings": resolved,
            "policy": (
                "Legacy findings are frozen by exact Git blob and are not approved. "
                "They may only remain unchanged or decrease. Any new unsafe finding "
                "or baseline-integrity failure is critical and fails closed."
            ),
        },
        "critical_findings": novel,
        "findings": informational,
        "informational_policy": {
            "cancel_in_progress_true": (
                "allowed and recommended for stale push-triggered work where a newer run "
                "supersedes the older run"
            ),
            "job_scoped_contents_write": (
                "allowed only for jobs that persist validated generated artifacts through "
                "the shared safe persistence helpers"
            ),
            "always_condition": (
                "allowed when used to preserve failure receipts or aggregate matrix-job "
                "outcomes truthfully"
            ),
        },
        "formal_weight_change": False,
        "automatic_promotion": False,
        "current_rule_change": False,
        "policy": (
            "Fail closed on every newly introduced direct git push/rebase, global write "
            "permission, deprecated action runtime, missing timeout, direct Contents API "
            "implementation, self-trigger write risk, or legacy-baseline integrity failure. "
            "The frozen legacy backlog remains visible and must be migrated down over time."
        ),
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
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "workflow_count": report["workflow_count"],
                    "critical_finding_count": report["critical_finding_count"],
                    "risk_score": report["risk_score"],
                    "legacy_quarantined_finding_count": report["legacy_quarantine"][
                        "current_quarantined_finding_count"
                    ],
                    "resolved_since_baseline_count": report["legacy_quarantine"][
                        "resolved_since_baseline_count"
                    ],
                    "novel_critical_findings": report["novel_critical_findings"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
