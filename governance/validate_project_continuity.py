#!/usr/bin/env python3
"""Static guard for the football project's simplified governance topology.

The repository must not contain a second live dynamic project-state system.
Airtable《当前状态》 is the only live dynamic state source. GitHub keeps only
stable policy plus factual code/evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

PASS = "GOVERNANCE_TOPOLOGY_INTEGRITY_PASS"
FAIL = "BLOCKED_GOVERNANCE_TOPOLOGY_INTEGRITY"

AIRTABLE_MARKER = "CONTROL_MARKER: AIRTABLE_CURRENT_STATE_ONLY"
FORMAL_MARKER = "FORMAL_MARKER: FORMAL_CURRENT_WHEN_REQUIRED"
AUTH_MARKER = "AUTH_MARKER: CURRENT_USER_COMMAND_REQUIRED"
MIRROR_MARKER = "MIRROR_MARKER: NO_DYNAMIC_STATE_MIRRORS"
CURRENT_STATE_TEXT = "Airtable《当前状态》"

FORBIDDEN_ROOT_FILES = (
    "ACTIVE_CHECKPOINT.md",
    "PROJECT_CURRENT.md",
    "LAST_HANDOFF.md",
    "CHATGPT_PROJECT_START_HERE.txt",
    "HISTORY_ONLY_INDEX.md",
    "CORRECTION_PLAN.md",
    "DRAW_AUDIT_HANDOFF.md",
    "REPOSITORY_GOVERNANCE_PLAN.md",
)


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

    agents, error = _read_required(root, "AGENTS.md")
    if error:
        reasons.append(error)
    else:
        assert agents is not None
        for marker in (AIRTABLE_MARKER, FORMAL_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT):
            if marker not in agents:
                reasons.append(f"AGENTS.md: missing {marker}")

    execution, error = _read_required(root, "EXECUTION_LITE.md")
    if error:
        reasons.append(error)
    else:
        assert execution is not None
        for marker in (AIRTABLE_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT):
            if marker not in execution:
                reasons.append(f"EXECUTION_LITE.md: missing {marker}")
        if "讨论不等于执行" not in execution:
            reasons.append("EXECUTION_LITE.md: missing explicit discussion/execution boundary")

    for rel in FORBIDDEN_ROOT_FILES:
        if (root / rel).exists():
            reasons.append(f"forbidden legacy root governance file present: {rel}")

    return Decision(FAIL, tuple(reasons)) if reasons else Decision(PASS)


def _write_fixture(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        "\n".join((AIRTABLE_MARKER, FORMAL_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT)) + "\n",
        encoding="utf-8",
    )
    (root / "EXECUTION_LITE.md").write_text(
        "\n".join((AIRTABLE_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT, "讨论不等于执行")) + "\n",
        encoding="utf-8",
    )


def run_self_test() -> int:
    checks: list[tuple[str, bool]] = []
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_fixture(root)
        checks.append(("positive", validate_root(root).status == PASS))

        (root / "PROJECT_CURRENT.md").write_text("legacy mirror\n", encoding="utf-8")
        checks.append(("legacy_mirror_rejected", validate_root(root).status == FAIL))
        (root / "PROJECT_CURRENT.md").unlink()

        (root / "AGENTS.md").write_text(
            "\n".join((FORMAL_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT)) + "\n",
            encoding="utf-8",
        )
        checks.append(("airtable_marker_required", validate_root(root).status == FAIL))
        _write_fixture(root)

        (root / "EXECUTION_LITE.md").write_text(
            "\n".join((AIRTABLE_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT)) + "\n",
            encoding="utf-8",
        )
        checks.append(("discussion_execution_boundary_required", validate_root(root).status == FAIL))

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
