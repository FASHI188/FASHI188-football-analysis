#!/usr/bin/env python3
"""Static integrity guard for the football project's continuity router.

This validator deliberately does NOT mirror or validate dynamic project state.
The single live dynamic state source is Airtable《当前状态》. GitHub only keeps
stable routing rules plus historical tombstones.

The guard fails closed if:
- live routing files stop declaring AIRTABLE_CURRENT_STATE_ONLY;
- formal-task routing stops declaring FORMAL_CURRENT_WHEN_REQUIRED;
- retired GitHub state files lose their HISTORY_ONLY_NO_AUTHORITY marker;
- EXECUTION_LITE stops declaring the same single-state-source boundary; or
- the history-only index stops covering the named legacy planning/audit docs.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

PASS = "CONTINUITY_ROUTER_INTEGRITY_PASS"
FAIL = "BLOCKED_CONTINUITY_ROUTER_INTEGRITY"

AIRTABLE_MARKER = "CONTROL_MARKER: AIRTABLE_CURRENT_STATE_ONLY"
FORMAL_MARKER = "FORMAL_MARKER: FORMAL_CURRENT_WHEN_REQUIRED"
HISTORY_MARKER = "HISTORY_ONLY_NO_AUTHORITY"
CURRENT_STATE_TEXT = "Airtable《当前状态》"

LIVE_ROUTER_FILES = (
    "AGENTS.md",
    "CHATGPT_PROJECT_START_HERE.txt",
)
RETIRED_STATE_FILES = (
    "ACTIVE_CHECKPOINT.md",
    "PROJECT_CURRENT.md",
    "LAST_HANDOFF.md",
)
LEGACY_HISTORY_DOCS = (
    "CORRECTION_PLAN.md",
    "DRAW_AUDIT_HANDOFF.md",
    "REPOSITORY_GOVERNANCE_PLAN.md",
)
EXECUTION_FILE = "EXECUTION_LITE.md"
HISTORY_INDEX = "HISTORY_ONLY_INDEX.md"


@dataclass(frozen=True)
class Decision:
    status: str
    reasons: tuple[str, ...] = ()


def _read_required(root: Path, rel: str) -> tuple[str | None, str | None]:
    path = root / rel
    if not path.is_file():
        return None, f"missing file: {rel}"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return None, f"unreadable file {rel}: {exc}"
    if not text.strip():
        return None, f"empty file: {rel}"
    return text, None


def validate_root(root: Path) -> Decision:
    reasons: list[str] = []

    for rel in LIVE_ROUTER_FILES:
        text, error = _read_required(root, rel)
        if error:
            reasons.append(error)
            continue
        assert text is not None
        if AIRTABLE_MARKER not in text:
            reasons.append(f"{rel}: missing {AIRTABLE_MARKER}")
        if FORMAL_MARKER not in text:
            reasons.append(f"{rel}: missing {FORMAL_MARKER}")
        if CURRENT_STATE_TEXT not in text:
            reasons.append(f"{rel}: missing single live Airtable current-state declaration")

    for rel in RETIRED_STATE_FILES:
        text, error = _read_required(root, rel)
        if error:
            reasons.append(error)
            continue
        assert text is not None
        if HISTORY_MARKER not in text:
            reasons.append(f"{rel}: missing {HISTORY_MARKER}")
        if "RETIRED" not in text:
            reasons.append(f"{rel}: missing RETIRED marker")

    execution, error = _read_required(root, EXECUTION_FILE)
    if error:
        reasons.append(error)
    else:
        assert execution is not None
        if CURRENT_STATE_TEXT not in execution:
            reasons.append(f"{EXECUTION_FILE}: missing Airtable current-state boundary")
        if "旧 checkpoint / handoff / pointer 不参与执行恢复" not in execution:
            reasons.append(f"{EXECUTION_FILE}: legacy recovery chain not explicitly retired")

    index, error = _read_required(root, HISTORY_INDEX)
    if error:
        reasons.append(error)
    else:
        assert index is not None
        if HISTORY_MARKER not in index:
            reasons.append(f"{HISTORY_INDEX}: missing {HISTORY_MARKER}")
        for rel in LEGACY_HISTORY_DOCS:
            if rel not in index:
                reasons.append(f"{HISTORY_INDEX}: missing legacy doc {rel}")

    return Decision(FAIL, tuple(reasons)) if reasons else Decision(PASS)


def _write_fixture(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        f"{AIRTABLE_MARKER}\n{FORMAL_MARKER}\n{CURRENT_STATE_TEXT}\n", encoding="utf-8"
    )
    (root / "CHATGPT_PROJECT_START_HERE.txt").write_text(
        f"{AIRTABLE_MARKER}\n{FORMAL_MARKER}\n{CURRENT_STATE_TEXT}\n", encoding="utf-8"
    )
    for rel in RETIRED_STATE_FILES:
        (root / rel).write_text(
            f"# {rel} — RETIRED\n{HISTORY_MARKER}\n", encoding="utf-8"
        )
    (root / EXECUTION_FILE).write_text(
        f"{CURRENT_STATE_TEXT}\n旧 checkpoint / handoff / pointer 不参与执行恢复\n",
        encoding="utf-8",
    )
    (root / HISTORY_INDEX).write_text(
        HISTORY_MARKER + "\n" + "\n".join(LEGACY_HISTORY_DOCS) + "\n",
        encoding="utf-8",
    )
    for rel in LEGACY_HISTORY_DOCS:
        (root / rel).write_text("historical evidence\n", encoding="utf-8")


def run_self_test() -> int:
    checks: list[tuple[str, bool]] = []

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_fixture(root)
        checks.append(("positive", validate_root(root).status == PASS))

        (root / "PROJECT_CURRENT.md").write_text("old live state\n", encoding="utf-8")
        checks.append(("retired_marker_required", validate_root(root).status == FAIL))
        _write_fixture(root)

        (root / "AGENTS.md").write_text(
            f"{FORMAL_MARKER}\n{CURRENT_STATE_TEXT}\n", encoding="utf-8"
        )
        checks.append(("airtable_marker_required", validate_root(root).status == FAIL))
        _write_fixture(root)

        (root / HISTORY_INDEX).write_text(HISTORY_MARKER + "\n", encoding="utf-8")
        checks.append(("legacy_index_complete", validate_root(root).status == FAIL))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    print(json.dumps({"terminal": "PASS" if not failed else "FAIL", "checks": len(checks), "failed": failed}))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    decision = validate_root(args.root)
    print(json.dumps({"status": decision.status, "reasons": decision.reasons}, ensure_ascii=False))
    return 0 if decision.status == PASS else 2


if __name__ == "__main__":
    sys.exit(main())
