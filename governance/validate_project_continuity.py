#!/usr/bin/env python3
"""Static guard for the football project's simplified governance topology.

Airtable《当前状态》 is the only live dynamic project-state source. GitHub keeps
stable policy, code, data and factual evidence. This guard also prevents deeper
engineering folders from recreating a second `current`/checkpoint/activation
control surface.
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
FORBIDDEN_ACTIVE_PATHS = (
    "football-data/config/v6_current_research_scope_v6494.json",
    "football-data/validation/audit_current_research_scope_v6494.py",
    "football-data/validation/v6_sync_42_runtime_truth_v6495.py",
    "football-data/validation/v6_sync_f06_challenge_truth_v6505.py",
    ".github/workflows/football-v6494-active-research-scope-guard.yml",
    ".github/workflows/football-v6494-current-state-audit.yml",
)
REQUIRED_BOUNDARY_FILES = (
    "football-data/runtime/README.md",
    "football-data/config/v6_engineering_research_profile_v6494.json",
    "football-data/validation/validate_engineering_research_profile_v6494.py",
    "football-data/docs/ASSET_LIFECYCLE.md",
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
    for rel in FORBIDDEN_ACTIVE_PATHS:
        if (root / rel).exists():
            reasons.append(f"forbidden legacy active control path present: {rel}")
    for rel in REQUIRED_BOUNDARY_FILES:
        if not (root / rel).is_file():
            reasons.append(f"required governance boundary file missing: {rel}")

    validation_root = root / "football-data" / "validation"
    if validation_root.is_dir():
        for path in validation_root.glob("activate_*.py"):
            reasons.append(f"activation entrypoint must not live in validation: {path.relative_to(root).as_posix()}")

    runtime_root = root / "football-data" / "runtime" / "activation"
    for name in (
        "activate_mls_d_conditional_runtime_v470.py",
        "activate_selective_direction_runtime_v501.py",
    ):
        if not (runtime_root / name).is_file():
            reasons.append(f"required runtime activation entrypoint missing: football-data/runtime/activation/{name}")

    platform, error = _read_required(root, "football-data/engine/validate_platform.py")
    if error:
        reasons.append(error)
    else:
        assert platform is not None
        if "def run(write: bool = False" not in platform:
            reasons.append("validate_platform.py must be read-only by default")
        if '"--write-receipt"' not in platform:
            reasons.append("validate_platform.py missing explicit write-receipt gate")
        if "行为PASS只认CI测试" not in platform:
            reasons.append("validate_platform.py still lacks static-vs-behavioral PASS boundary")

    profile, error = _read_required(root, "football-data/config/v6_engineering_research_profile_v6494.json")
    if error:
        reasons.append(error)
    else:
        assert profile is not None
        try:
            payload = json.loads(profile)
        except Exception as exc:
            reasons.append(f"engineering research profile invalid JSON: {exc}")
        else:
            if payload.get("project_state_authority") is not False:
                reasons.append("engineering research profile claims project-state authority")
            if payload.get("execution_authority") != "EXPLICIT_DISPATCH_ONLY":
                reasons.append("engineering research profile execution authority is not explicit-dispatch-only")
            if "current_runtime_truth_registry" in json.dumps(payload, ensure_ascii=False):
                reasons.append("engineering research profile reintroduces current_runtime_truth_registry")

    return Decision(FAIL, tuple(reasons)) if reasons else Decision(PASS)


def _write_fixture(root: Path) -> None:
    (root / "AGENTS.md").write_text("\n".join((AIRTABLE_MARKER, FORMAL_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT)) + "\n", encoding="utf-8")
    (root / "EXECUTION_LITE.md").write_text("\n".join((AIRTABLE_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT, "讨论不等于执行")) + "\n", encoding="utf-8")
    for rel in REQUIRED_BOUNDARY_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith("v6_engineering_research_profile_v6494.json"):
            path.write_text(json.dumps({"project_state_authority": False, "execution_authority": "EXPLICIT_DISPATCH_ONLY"}), encoding="utf-8")
        else:
            path.write_text("boundary\n", encoding="utf-8")
    runtime = root / "football-data" / "runtime" / "activation"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("activate_mls_d_conditional_runtime_v470.py", "activate_selective_direction_runtime_v501.py"):
        (runtime / name).write_text("# runtime\n", encoding="utf-8")
    platform = root / "football-data" / "engine" / "validate_platform.py"
    platform.parent.mkdir(parents=True, exist_ok=True)
    platform.write_text('def run(write: bool = False): pass\nparser.add_argument("--write-receipt")\n# 行为PASS只认CI测试\n', encoding="utf-8")


def run_self_test() -> int:
    checks: list[tuple[str, bool]] = []
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_fixture(root)
        checks.append(("positive", validate_root(root).status == PASS))

        (root / "PROJECT_CURRENT.md").write_text("legacy mirror\n", encoding="utf-8")
        checks.append(("legacy_root_mirror_rejected", validate_root(root).status == FAIL))
        (root / "PROJECT_CURRENT.md").unlink()

        bad = root / "football-data" / "validation" / "activate_bad.py"
        bad.write_text("bad\n", encoding="utf-8")
        checks.append(("activation_in_validation_rejected", validate_root(root).status == FAIL))
        bad.unlink()

        legacy = root / "football-data" / "config" / "v6_current_research_scope_v6494.json"
        legacy.write_text("{}\n", encoding="utf-8")
        checks.append(("legacy_current_scope_rejected", validate_root(root).status == FAIL))
        legacy.unlink()

        profile = root / "football-data" / "config" / "v6_engineering_research_profile_v6494.json"
        profile.write_text(json.dumps({"project_state_authority": True, "execution_authority": "AUTO"}), encoding="utf-8")
        checks.append(("engineering_profile_authority_rejected", validate_root(root).status == FAIL))

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
