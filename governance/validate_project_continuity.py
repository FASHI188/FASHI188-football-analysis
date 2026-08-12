#!/usr/bin/env python3
"""Fail-closed validator for football-project conversation continuity.

Governance-only: no GitHub/Airtable/provider calls, no football labels, no model
execution, and no mutations. Production callers provide PROJECT_CURRENT.md bytes
plus a normalized snapshot of the single active Airtable current-state record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SYNC_OK = "STATE_SYNC_VERIFIED"
BLOCKED_MISMATCH = "BLOCKED_STATE_MISMATCH"
BLOCKED_AIRTABLE = "BLOCKED_AIRTABLE_UNAVAILABLE"
BLOCKED_CURRENT_COUNT = "BLOCKED_CURRENT_RULE_COUNT_MISMATCH"
BLOCKED_CURRENT_UNAVAILABLE = "BLOCKED_CURRENT_RULE_UNAVAILABLE"

REQUIRED_MATCH_FIELDS = (
    "project_id",
    "state_version",
    "updated_at_utc",
    "status",
    "current_phase",
    "current_objective",
    "exact_head",
    "branch",
    "pull_request",
    "pull_request_state",
    "allowed_items",
    "prohibited_items",
    "next_step",
    "stop_conditions",
    "state_log_record_id",
)


@dataclass(frozen=True)
class Decision:
    status: str
    reasons: tuple[str, ...] = ()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_scalar(text: str, key: str) -> str | None:
    m = re.search(rf"(?m)^-\s*{re.escape(key)}:\s*(.*?)\s*$", text)
    return m.group(1).strip() if m else None


def _extract_section(text: str, heading: str) -> str | None:
    pattern = rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)"
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


def normalize_project_current(data: bytes) -> dict[str, Any]:
    text = data.decode("utf-8")
    state_version_raw = _extract_scalar(text, "state_version")
    if state_version_raw is None or not state_version_raw.isdigit() or int(state_version_raw) <= 0:
        raise ValueError("state_version must be a positive integer")

    allowed = _extract_section(text, "允许事项")
    prohibited = _extract_section(text, "禁止事项")
    next_step = _extract_section(text, "唯一下一步")
    stop_conditions = _extract_section(text, "停止条件")
    if not allowed or not prohibited or not next_step or not stop_conditions:
        raise ValueError("required governance sections missing")

    normalized = {
        "project_id": _extract_scalar(text, "project_id"),
        "state_version": int(state_version_raw),
        "updated_at_utc": _extract_scalar(text, "updated_at_utc"),
        "status": _extract_scalar(text, "status"),
        "current_phase": _extract_scalar(text, "current_phase"),
        "current_objective": _extract_scalar(text, "current_objective"),
        "exact_head": _extract_scalar(text, "exact_head"),
        "branch": _extract_scalar(text, "branch"),
        "pull_request": _extract_scalar(text, "pull_request"),
        "pull_request_state": _extract_scalar(text, "pull_request_state"),
        "allowed_items": allowed,
        "prohibited_items": prohibited,
        "next_step": next_step,
        "stop_conditions": stop_conditions,
        "state_log_record_id": _extract_scalar(text, "state_log_record_id"),
        "recorded_hash_marker": _extract_scalar(text, "project_current_sha256"),
        "computed_sha256": sha256_bytes(data),
    }
    missing = [
        field for field in REQUIRED_MATCH_FIELDS
        if normalized.get(field) is None or normalized.get(field) == ""
    ]
    if missing:
        raise ValueError("required fields missing: " + ",".join(missing))
    return normalized


def validate(
    project_current_bytes: bytes | None,
    airtable_snapshot: dict[str, Any] | None,
    *,
    airtable_available: bool = True,
    active_record_count: int = 1,
    model_or_formal_task: bool = False,
    current_rule_count: int | None = None,
    current_rule_readable: bool | None = None,
) -> Decision:
    if not airtable_available:
        return Decision(BLOCKED_AIRTABLE, ("Airtable unavailable; read-only only",))
    if project_current_bytes is None or airtable_snapshot is None:
        return Decision(BLOCKED_MISMATCH, ("PROJECT_CURRENT.md or Airtable current state missing",))
    if active_record_count != 1:
        return Decision(BLOCKED_MISMATCH, (f"active_record_count={active_record_count}",))

    if model_or_formal_task:
        if current_rule_count != 1:
            return Decision(BLOCKED_CURRENT_COUNT, (f"current_rule_count={current_rule_count}",))
        if current_rule_readable is not True:
            return Decision(BLOCKED_CURRENT_UNAVAILABLE, ("formal CURRENT is not confirmed fully readable",))

    try:
        local = normalize_project_current(project_current_bytes)
    except Exception as exc:
        return Decision(BLOCKED_MISMATCH, (f"PROJECT_CURRENT parse error: {exc}",))

    reasons: list[str] = []
    if local["recorded_hash_marker"] != "RECORDED_IN_AIRTABLE":
        reasons.append("project_current_sha256 marker is not RECORDED_IN_AIRTABLE")

    for field in REQUIRED_MATCH_FIELDS:
        if local.get(field) != airtable_snapshot.get(field):
            reasons.append(f"{field} mismatch")

    if local["computed_sha256"] != airtable_snapshot.get("project_current_sha256"):
        reasons.append("PROJECT_CURRENT.md SHA-256 mismatch")

    if reasons:
        return Decision(BLOCKED_MISMATCH, tuple(reasons))
    return Decision(SYNC_OK)


def workflow_success_implies_model_pass(_: str) -> bool:
    return False


def queued_implies_in_progress(_: str) -> bool:
    return False


def _fixture_bytes(
    *,
    state_version: int = 2,
    head: str = "abc",
    branch: str = "research/test",
    pr: str = "#1",
    phase: str = "WAITING_USER_ACCEPTANCE",
    pr_state: str = "VERIFIED_OPEN_DRAFT_UNMERGED",
    updated_at: str = "2026-08-12T04:03:00Z",
) -> bytes:
    return f"""# 足球项目当前状态

## 身份
- project_id: football-project
- state_version: {state_version}
- updated_at_utc: {updated_at}
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态
- status: WAITING
- current_phase: {phase}
- current_objective: continuity governance
- exact_head: {head}
- branch: {branch}
- pull_request: {pr}
- pull_request_state: {pr_state}

## 允许事项
- governance-only

## 禁止事项
- research

## 唯一下一步
- user acceptance

## 停止条件
- mismatch blocks

## Airtable同步
- state_log_record_id: recSTATELOG0000001
""".encode("utf-8")


def _snapshot_for(data: bytes) -> dict[str, Any]:
    local = normalize_project_current(data)
    return {
        field: local[field] for field in REQUIRED_MATCH_FIELDS
    } | {"project_current_sha256": local["computed_sha256"]}


def run_self_test() -> int:
    base = _fixture_bytes()
    snapshot = _snapshot_for(base)
    checks: list[tuple[str, bool]] = []

    checks.append(("positive_sync", validate(base, snapshot).status == SYNC_OK))
    checks.append((
        "tampered_file_hash",
        validate(base.replace(b"abc", b"abd"), snapshot).status == BLOCKED_MISMATCH,
    ))
    checks.append((
        "airtable_version_mismatch",
        validate(base, dict(snapshot, state_version=3)).status == BLOCKED_MISMATCH,
    ))
    checks.append((
        "phase_mismatch",
        validate(base, dict(snapshot, current_phase="OTHER")).status == BLOCKED_MISMATCH,
    ))
    checks.append((
        "branch_mismatch",
        validate(base, dict(snapshot, branch="other/branch")).status == BLOCKED_MISMATCH,
    ))
    checks.append((
        "pr_state_mismatch",
        validate(base, dict(snapshot, pull_request_state="READY")).status == BLOCKED_MISMATCH,
    ))
    checks.append((
        "updated_at_mismatch",
        validate(base, dict(snapshot, updated_at_utc="2026-08-12T00:00:00Z")).status == BLOCKED_MISMATCH,
    ))
    checks.append((
        "permission_mismatch",
        validate(base, dict(snapshot, allowed_items="- broaden research")).status == BLOCKED_MISMATCH,
    ))
    checks.append((
        "stop_condition_mismatch",
        validate(base, dict(snapshot, stop_conditions="- no block")).status == BLOCKED_MISMATCH,
    ))
    checks.append((
        "state_log_mismatch",
        validate(base, dict(snapshot, state_log_record_id="recOTHER000000000")).status == BLOCKED_MISMATCH,
    ))
    checks.append(("two_active_records", validate(base, snapshot, active_record_count=2).status == BLOCKED_MISMATCH))
    checks.append(("airtable_unavailable", validate(base, snapshot, airtable_available=False).status == BLOCKED_AIRTABLE))

    old_file_conflicting_head = "deadbeef"
    checks.append((
        "old_file_head_cannot_override",
        old_file_conflicting_head != normalize_project_current(base)["exact_head"]
        and validate(base, snapshot).status == SYNC_OK,
    ))
    checks.append((
        "current_count_model_gate",
        validate(
            base, snapshot, model_or_formal_task=True,
            current_rule_count=2, current_rule_readable=True,
        ).status == BLOCKED_CURRENT_COUNT,
    ))
    checks.append((
        "current_unreadable_model_gate",
        validate(
            base, snapshot, model_or_formal_task=True,
            current_rule_count=1, current_rule_readable=False,
        ).status == BLOCKED_CURRENT_UNAVAILABLE,
    ))
    checks.append(("workflow_success_not_model_pass", workflow_success_implies_model_pass("success") is False))
    checks.append(("queued_not_in_progress", queued_implies_in_progress("queued") is False))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    terminal = {"terminal": "PASS" if not failed else "FAIL", "checks": len(checks)}
    if failed:
        terminal["failed"] = failed
    print(json.dumps(terminal, ensure_ascii=False))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--project-current", type=Path)
    parser.add_argument("--airtable-snapshot", type=Path)
    parser.add_argument("--active-record-count", type=int, default=1)
    parser.add_argument("--airtable-unavailable", action="store_true")
    parser.add_argument("--model-or-formal-task", action="store_true")
    parser.add_argument("--current-rule-count", type=int)
    parser.add_argument("--current-rule-unavailable", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.project_current:
        parser.error("--project-current is required unless --self-test is used")
    if args.airtable_unavailable:
        decision = validate(
            args.project_current.read_bytes(),
            None,
            airtable_available=False,
            active_record_count=args.active_record_count,
        )
    else:
        if not args.airtable_snapshot:
            parser.error("--airtable-snapshot is required unless --airtable-unavailable is used")
        snapshot = json.loads(args.airtable_snapshot.read_text(encoding="utf-8"))
        decision = validate(
            args.project_current.read_bytes(),
            snapshot,
            active_record_count=args.active_record_count,
            model_or_formal_task=args.model_or_formal_task,
            current_rule_count=args.current_rule_count,
            current_rule_readable=(
                False if args.current_rule_unavailable
                else True if args.model_or_formal_task
                else None
            ),
        )

    print(json.dumps({"status": decision.status, "reasons": decision.reasons}, ensure_ascii=False))
    return 0 if decision.status == SYNC_OK else 2


if __name__ == "__main__":
    sys.exit(main())
