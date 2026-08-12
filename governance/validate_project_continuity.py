#!/usr/bin/env python3
"""Fail-closed validator for football-project conversation continuity.

This script is governance-only. It does not call GitHub, Airtable, Provider APIs,
read football labels, fit models, or mutate project assets.

Production use supplies:
  1. PROJECT_CURRENT.md bytes;
  2. a normalized JSON snapshot of the single Airtable current-state record;
  3. optional formal-CURRENT count when model/formal work is requested.

`--self-test` runs deterministic in-memory positive/negative fixtures only. It never
modifies live Airtable or any repository state.
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

REQUIRED_MATCH_FIELDS = (
    "project_id",
    "state_version",
    "current_objective",
    "exact_head",
    "pull_request",
    "allowed_items",
    "prohibited_items",
    "next_step",
)


@dataclass(frozen=True)
class Decision:
    status: str
    reasons: tuple[str, ...] = ()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_scalar(text: str, key: str) -> str | None:
    # PROJECT_CURRENT.md deliberately uses simple `- key: value` identity/state lines.
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
    if not allowed or not prohibited or not next_step:
        raise ValueError("required governance sections missing")

    return {
        "project_id": _extract_scalar(text, "project_id"),
        "state_version": int(state_version_raw),
        "current_objective": _extract_scalar(text, "current_objective"),
        "exact_head": _extract_scalar(text, "exact_head"),
        "pull_request": _extract_scalar(text, "pull_request"),
        "allowed_items": allowed,
        "prohibited_items": prohibited,
        "next_step": next_step,
        "recorded_hash_marker": _extract_scalar(text, "project_current_sha256"),
        "computed_sha256": sha256_bytes(data),
    }


def validate(
    project_current_bytes: bytes | None,
    airtable_snapshot: dict[str, Any] | None,
    *,
    airtable_available: bool = True,
    active_record_count: int = 1,
    model_or_formal_task: bool = False,
    current_rule_count: int | None = None,
) -> Decision:
    if not airtable_available:
        return Decision(BLOCKED_AIRTABLE, ("Airtable unavailable; read-only only",))
    if project_current_bytes is None or airtable_snapshot is None:
        return Decision(BLOCKED_MISMATCH, ("PROJECT_CURRENT.md or Airtable current state missing",))
    if active_record_count != 1:
        return Decision(BLOCKED_MISMATCH, (f"active_record_count={active_record_count}",))

    if model_or_formal_task and current_rule_count != 1:
        return Decision(BLOCKED_CURRENT_COUNT, (f"current_rule_count={current_rule_count}",))

    try:
        local = normalize_project_current(project_current_bytes)
    except Exception as exc:  # fail closed
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


def workflow_success_implies_model_pass(workflow_status: str) -> bool:
    # Explicitly false: execution success is not scientific/model validity.
    return False


def queued_implies_in_progress(run_status: str) -> bool:
    # Explicitly false: preserve GitHub's state exactly.
    return False


def _fixture_bytes(state_version: int = 1, head: str = "abc", pr: str = "#1") -> bytes:
    text = f"""# 足球项目当前状态

## 身份
- project_id: football-project
- state_version: {state_version}
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态
- current_objective: continuity governance
- exact_head: {head}
- pull_request: {pr}

## 允许事项
- governance-only

## 禁止事项
- research

## 唯一下一步
- user acceptance
"""
    return text.encode("utf-8")


def run_self_test() -> int:
    base = _fixture_bytes()
    local = normalize_project_current(base)
    snapshot = {
        "project_id": "football-project",
        "state_version": 1,
        "current_objective": "continuity governance",
        "exact_head": "abc",
        "pull_request": "#1",
        "allowed_items": "- governance-only",
        "prohibited_items": "- research",
        "next_step": "- user acceptance",
        "project_current_sha256": local["computed_sha256"],
    }

    checks: list[tuple[str, bool]] = []
    checks.append(("positive_sync", validate(base, snapshot).status == SYNC_OK))

    tampered = base.replace(b"abc", b"abd")
    checks.append(("tampered_file_hash", validate(tampered, snapshot).status == BLOCKED_MISMATCH))

    version_bad = dict(snapshot, state_version=2)
    checks.append(("airtable_version_mismatch", validate(base, version_bad).status == BLOCKED_MISMATCH))

    checks.append(("two_active_records", validate(base, snapshot, active_record_count=2).status == BLOCKED_MISMATCH))
    checks.append(("airtable_unavailable", validate(base, snapshot, airtable_available=False).status == BLOCKED_AIRTABLE))

    # An old status file is intentionally not an input to validate(); therefore a conflicting
    # old HEAD cannot override PROJECT_CURRENT/Airtable.
    old_file_conflicting_head = "deadbeef"
    checks.append(("old_file_head_cannot_override", old_file_conflicting_head != local["exact_head"] and validate(base, snapshot).status == SYNC_OK))

    checks.append(("current_count_model_gate", validate(base, snapshot, model_or_formal_task=True, current_rule_count=2).status == BLOCKED_CURRENT_COUNT))
    checks.append(("workflow_success_not_model_pass", workflow_success_implies_model_pass("success") is False))
    checks.append(("queued_not_in_progress", queued_implies_in_progress("queued") is False))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    if failed:
        print(json.dumps({"terminal": "FAIL", "failed": failed}, ensure_ascii=False))
        return 1
    print(json.dumps({"terminal": "PASS", "checks": len(checks)}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--project-current", type=Path)
    parser.add_argument("--airtable-snapshot", type=Path)
    parser.add_argument("--active-record-count", type=int, default=1)
    parser.add_argument("--model-or-formal-task", action="store_true")
    parser.add_argument("--current-rule-count", type=int)
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.project_current or not args.airtable_snapshot:
        parser.error("--project-current and --airtable-snapshot are required unless --self-test is used")

    project_bytes = args.project_current.read_bytes()
    snapshot = json.loads(args.airtable_snapshot.read_text(encoding="utf-8"))
    decision = validate(
        project_bytes,
        snapshot,
        active_record_count=args.active_record_count,
        model_or_formal_task=args.model_or_formal_task,
        current_rule_count=args.current_rule_count,
    )
    print(json.dumps({"status": decision.status, "reasons": decision.reasons}, ensure_ascii=False))
    return 0 if decision.status == SYNC_OK else 2


if __name__ == "__main__":
    sys.exit(main())
