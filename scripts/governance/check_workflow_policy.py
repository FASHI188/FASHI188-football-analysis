#!/usr/bin/env python3
"""Evaluate static workflow inventory against repository governance policy."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


class GithubActionsLoader(yaml.SafeLoader):
    pass


for first_char, resolvers in list(GithubActionsLoader.yaml_implicit_resolvers.items()):
    GithubActionsLoader.yaml_implicit_resolvers[first_char] = [
        item for item in resolvers if item[0] != "tag:yaml.org,2002:bool"
    ]
GithubActionsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def violations_for(record: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    whitelist = set(policy.get("contents_write_whitelist", []))
    path = record["path"]
    if record["push_trigger"] and record["git_commit_or_push"]:
        violations.append("PUSH_TRIGGER_WITH_REPOSITORY_PERSISTENCE")
    if record["direct_main_push"]:
        violations.append("DIRECT_PUSH_TO_MAIN")
    if record["contents_write"] and path not in whitelist:
        violations.append("CONTENTS_WRITE_NOT_WHITELISTED")
    if not record["concurrency_defined"]:
        violations.append("MISSING_CONCURRENCY")
    if record["long_running_likely"] and not record["cancel_in_progress"]:
        violations.append("LONG_RUNNING_WITHOUT_CANCEL_IN_PROGRESS")
    if record["modifies_or_generates_workflow"]:
        violations.append("WORKFLOW_MUTATES_WORKFLOW")
    if record["modifies_formal_current"]:
        violations.append("WORKFLOW_MUTATES_FORMAL_CURRENT")
    if record["promotes_formal_weight"]:
        violations.append("RESEARCH_SELF_PROMOTES_FORMAL_WEIGHT")
    if record["push_trigger"] and record["git_commit_or_push"]:
        violations.append("BOT_COMMIT_USED_AS_EVENT_BUS")
    return violations


def skeleton_record(path: str) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    document = yaml.load(raw, Loader=GithubActionsLoader) or {}
    on_value = document.get("on")
    triggers = (
        list(on_value.keys())
        if isinstance(on_value, dict)
        else [on_value]
        if isinstance(on_value, str)
        else list(on_value)
        if isinstance(on_value, list)
        else []
    )
    permissions = document.get("permissions") or {}
    contents_write = (
        permissions == "write-all"
        or (
            isinstance(permissions, dict)
            and str(permissions.get("contents", "")).lower() == "write"
        )
    )
    concurrency = document.get("concurrency")
    cancel_in_progress = (
        isinstance(concurrency, dict)
        and (
            concurrency.get("cancel-in-progress") is True
            or str(concurrency.get("cancel-in-progress", "")).lower() == "true"
        )
    )
    command_text = "\n".join(
        str(step.get("run", ""))
        for job in (document.get("jobs") or {}).values()
        if isinstance(job, dict)
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    )
    return {
        "path": path.replace("\\", "/"),
        "push_trigger": "push" in triggers,
        "git_commit_or_push": bool(
            re.search(r"\bgit\s+(?:commit|push)\b", command_text, re.IGNORECASE)
        ),
        "direct_main_push": bool(
            re.search(r"\bgit\s+push[^\n]*\bmain\b", command_text, re.IGNORECASE)
        ),
        "contents_write": contents_write,
        "concurrency_defined": concurrency is not None,
        "long_running_likely": False,
        "cancel_in_progress": cancel_in_progress,
        "modifies_or_generates_workflow": False,
        "modifies_formal_current": False,
        "promotes_formal_weight": False,
        "_skeleton_contract_violations": [
            message
            for condition, message in (
                (set(triggers) != {"workflow_dispatch"}, "SKELETON_TRIGGER_NOT_MANUAL_ONLY"),
                ("PAUSED_FOR_REPOSITORY_GOVERNANCE" not in raw, "SKELETON_PAUSE_MARKER_MISSING"),
                (
                    not isinstance(permissions, dict)
                    or str(permissions.get("contents", "")).lower() != "read",
                    "SKELETON_CONTENTS_PERMISSION_NOT_READ",
                ),
            )
            if condition
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default="governance/workflow_inventory.json")
    parser.add_argument("--policy", default="governance/workflow_policy.json")
    parser.add_argument("--path-prefix", action="append", default=[])
    parser.add_argument("--skeleton", action="append", default=[])
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    policy = load_json(args.policy)
    if args.skeleton:
        records = [skeleton_record(path) for path in args.skeleton]
    else:
        inventory = load_json(args.inventory)
        records = inventory["workflows"]
        if args.path_prefix:
            records = [
                record
                for record in records
                if any(record["path"].startswith(prefix) for prefix in args.path_prefix)
            ]

    report_records = []
    for record in records:
        violations = violations_for(record, policy)
        violations.extend(record.get("_skeleton_contract_violations", []))
        if violations:
            report_records.append(
                {"path": record["path"], "violations": violations}
            )
    report = {
        "schema_version": "workflow-policy-report-v1",
        "checked_workflow_count": len(records),
        "violating_workflow_count": len(report_records),
        "violation_count": sum(len(item["violations"]) for item in report_records),
        "report_only": args.report_only,
        "records": report_records,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0 if args.report_only or not report_records else 1


if __name__ == "__main__":
    sys.exit(main())
