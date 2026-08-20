#!/usr/bin/env python3
"""Repository-wide fail-closed governance topology guard.

Airtable《当前状态》 is the only live dynamic project-state source. GitHub may
contain stable policy/code/data and explicitly de-authorized historical evidence,
but no active Markdown/JSON/TXT object may become a second live task-state,
next-step, or authorization authority.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

PASS = "GOVERNANCE_TOPOLOGY_INTEGRITY_PASS"
FAIL = "BLOCKED_GOVERNANCE_TOPOLOGY_INTEGRITY"
AIRTABLE_MARKER = "CONTROL_MARKER: AIRTABLE_CURRENT_STATE_ONLY"
FORMAL_MARKER = "FORMAL_MARKER: FORMAL_CURRENT_WHEN_REQUIRED"
AUTH_MARKER = "AUTH_MARKER: CURRENT_USER_COMMAND_REQUIRED"
MIRROR_MARKER = "MIRROR_MARKER: NO_DYNAMIC_STATE_MIRRORS"
CURRENT_STATE_TEXT = "Airtable《当前状态》"
FORBIDDEN_ROOT_FILES = {
    "ACTIVE_CHECKPOINT.md",
    "PROJECT_CURRENT.md",
    "LAST_HANDOFF.md",
    "CHATGPT_PROJECT_START_HERE.txt",
    "HISTORY_ONLY_INDEX.md",
    "CORRECTION_PLAN.md",
    "DRAW_AUDIT_HANDOFF.md",
    "REPOSITORY_GOVERNANCE_PLAN.md",
}
FORBIDDEN_ACTIVE_PATHS = {
    "football-data/config/v6_current_research_scope_v6494.json",
    "football-data/validation/audit_current_research_scope_v6494.py",
    "football-data/validation/v6_sync_42_runtime_truth_v6495.py",
    "football-data/validation/v6_sync_f06_challenge_truth_v6505.py",
    ".github/workflows/football-v6494-active-research-scope-guard.yml",
    ".github/workflows/football-v6494-current-state-audit.yml",
}
REQUIRED_BOUNDARY_FILES = (
    "football-data/runtime/README.md",
    "football-data/config/v6_engineering_research_profile_v6494.json",
    "football-data/validation/validate_engineering_research_profile_v6494.py",
    "football-data/docs/ASSET_LIFECYCLE.md",
)
TEXT_SUFFIXES = {".md", ".txt", ".json"}
HISTORICAL_PREFIXES = ("governance/archive/", "evidence/manifests/")
MANIFEST_PREFIX = "football-data/manifests/"
DATA_CONTENT_PREFIXES = (
    "football-data/data/",
    "football-data/raw/",
    "football-data/processed/",
    "football-data/features/",
    "football-data/results/",
    "football-data/cache/",
)
FORBIDDEN_NAME_PATTERNS = (
    re.compile(r"(^|/)PROJECT_CURRENT\.[^/]+$", re.I),
    re.compile(r"(^|/)[^/]*_START_HERE\.[^/]+$", re.I),
    re.compile(r"(^|/)[^/]*_HANDOFF\.[^/]+$", re.I),
    re.compile(r"(^|/)[^/]*_CHECKPOINT\.[^/]+$", re.I),
    re.compile(r"(^|/)FOOTBALL3_INDEPENDENT_CURRENT\.[^/]+$", re.I),
)
LIVE_AUTHORITY_PATTERNS = (
    re.compile(
        r"(?:唯一|\bonly\b|\bauthoritative\b).{0,50}"
        r"(?:实时|\blive\b|\bcurrent\b).{0,50}"
        r"(?:状态源|\bstate source\b|\bstate authority\b)",
        re.I | re.S,
    ),
    re.compile(r"(?:唯一下一步|\bunique next step\b|\bnext_step_authority\b)\s*[:=]", re.I),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:authorization_authority|授权源|authorization source)"
        r"\s*[:=]\s*(?:true|yes|1|authoritative)\b",
        re.I,
    ),
    re.compile(r"(?<![A-Za-z0-9_])project_state_authority\s*[:=]\s*(?:true|yes|1)\b", re.I),
    re.compile(r"(?<![A-Za-z0-9_])task_selection_authority\s*[:=]\s*(?:true|yes|1)\b", re.I),
)
LIVE_POINTER_TEXT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:current_pr|current_head|current_run|current_job|current_artifact|unique_next_step|next_step)"
    r"\s*[:=]\s*(?!null\b|none\b|false\b|0\b|\[\s*\]|\{\s*\}|[\"']\s*[\"'])"
    r"[^\r\n,}]+",
    re.I,
)
HISTORICAL_REQUIRED_MARKERS = (
    "HISTORICAL_EVIDENCE_ONLY",
    "project_state_authority=false",
    "task_selection_authority=false",
    "authorization_authority=false",
)
JSON_AUTHORITY_KEYS = {
    "project_state_authority",
    "task_selection_authority",
    "authorization_authority",
    "next_step_authority",
}
JSON_CURRENT_POINTER_KEYS = {
    "current_pr",
    "current_head",
    "current_run",
    "current_job",
    "current_artifact",
}
JSON_NEXT_STEP_KEYS = {"unique_next_step", "next_step"}
SCIENTIFIC_MANIFEST_MARKERS = {
    "formal_weight",
    "formal_weight_change",
    "probability_change",
    "research_only",
    "research_or_diagnostic_only",
    "runtime_enabled",
    "automatic_promotion",
}


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


def _is_historical(rel: str) -> bool:
    return rel.startswith(HISTORICAL_PREFIXES)


def _nonempty(value: Any) -> bool:
    return value not in (None, "", False, 0, [], {})


def _truthy_authority(value: Any) -> bool:
    return value in (True, 1, "true", "yes", "1", "authoritative")


def _walk_json(payload: Any):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield str(key).strip().lower(), value
            yield from _walk_json(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk_json(item)


def _json_claims_authority(payload: Any) -> bool:
    return any(key in JSON_AUTHORITY_KEYS and _truthy_authority(value) for key, value in _walk_json(payload))


def _json_current_pointer_keys(payload: Any) -> set[str]:
    return {
        key
        for key, value in _walk_json(payload)
        if key in JSON_CURRENT_POINTER_KEYS and _nonempty(value)
    }


def _json_next_step_keys(payload: Any) -> set[str]:
    return {
        key
        for key, value in _walk_json(payload)
        if key in JSON_NEXT_STEP_KEYS and _nonempty(value)
    }


def _json_contains_scientific_marker(payload: Any) -> bool:
    return any(key in SCIENTIFIC_MANIFEST_MARKERS for key, _ in _walk_json(payload))


def _has_historical_deauthority(text: str, suffix: str) -> bool:
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        historical = payload.get("HISTORICAL_EVIDENCE_ONLY")
        return (
            historical in (True, 1, "true", "yes", "HISTORICAL_EVIDENCE_ONLY")
            and payload.get("project_state_authority") is False
            and payload.get("task_selection_authority") is False
            and payload.get("authorization_authority") is False
        )
    return all(marker in text for marker in HISTORICAL_REQUIRED_MARKERS)


def _text_claims_live_authority(text: str, suffix: str) -> bool:
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except Exception:
            return bool(LIVE_POINTER_TEXT_PATTERN.search(text)) or any(
                pattern.search(text) for pattern in LIVE_AUTHORITY_PATTERNS
            )
        return (
            _json_claims_authority(payload)
            or bool(_json_current_pointer_keys(payload))
            or bool(_json_next_step_keys(payload))
        )
    return bool(LIVE_POINTER_TEXT_PATTERN.search(text)) or any(
        pattern.search(text) for pattern in LIVE_AUTHORITY_PATTERNS
    )


def _manifest_live_state_violation(text: str) -> str | None:
    """Reject project-state pointers in football-data/manifests without schema shortcuts.

    Scientific status receipts may contain a historical/research `next_step` note only
    when the JSON is structurally a scientific receipt and contains no current PR/HEAD/
    Run/Job/Artifact pointer and no project/task/authorization authority claim.
    `schema_version` alone never creates an exemption.
    """
    try:
        payload = json.loads(text)
    except Exception:
        if LIVE_POINTER_TEXT_PATTERN.search(text) or any(p.search(text) for p in LIVE_AUTHORITY_PATTERNS):
            return "manifest text contains live project/task pointer or authority language"
        return None
    if _json_claims_authority(payload):
        return "manifest JSON claims project/task/authorization authority"
    current_keys = _json_current_pointer_keys(payload)
    if current_keys:
        return "manifest JSON contains non-empty live current pointer(s): " + ",".join(sorted(current_keys))
    next_keys = _json_next_step_keys(payload)
    if next_keys and not _json_contains_scientific_marker(payload):
        return "manifest JSON contains non-empty next-step pointer without scientific-receipt structure"
    return None


def _scan_dynamic_mirrors(root: Path) -> list[str]:
    reasons: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue

        # Filename bans are unconditional and evaluated before every whitelist.
        if any(pattern.search(rel) for pattern in FORBIDDEN_NAME_PATTERNS):
            reasons.append(f"forbidden nested dynamic-state mirror path: {rel}")
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            reasons.append(f"unable to scan governance text {rel}: {exc}")
            continue
        suffix = path.suffix.lower()

        if rel.startswith(MANIFEST_PREFIX) and suffix == ".json":
            violation = _manifest_live_state_violation(text)
            if violation:
                reasons.append(f"{violation}: {rel}")
            continue

        if _is_historical(rel):
            if _text_claims_live_authority(text, suffix) and not _has_historical_deauthority(text, suffix):
                reasons.append(
                    f"historical evidence with live-authority language lacks de-authorizing markers: {rel}"
                )
            continue

        # Data semantics are allowed only in explicit data directories and only
        # after filename and authority/pointer checks above. There is deliberately
        # no current_season/current_roster path-substring bypass.
        if rel.startswith(DATA_CONTENT_PREFIXES):
            if _text_claims_live_authority(text, suffix):
                reasons.append(f"data-content file contains live project/task/authorization pointer: {rel}")
            continue

        if _text_claims_live_authority(text, suffix):
            reasons.append(f"active text claims live project/task/authorization authority: {rel}")
    return reasons


def validate_root(root: Path) -> Decision:
    reasons: list[str] = []
    agents, error = _read_required(root, "AGENTS.md")
    if error:
        reasons.append(error)
    else:
        for marker in (AIRTABLE_MARKER, FORMAL_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT):
            if marker not in agents:
                reasons.append(f"AGENTS.md: missing {marker}")
    execution, error = _read_required(root, "EXECUTION_LITE.md")
    if error:
        reasons.append(error)
    else:
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
    reasons.extend(_scan_dynamic_mirrors(root))
    validation_root = root / "football-data" / "validation"
    if validation_root.is_dir():
        for path in validation_root.glob("activate_*.py"):
            reasons.append(
                f"activation entrypoint must not live in validation: {path.relative_to(root).as_posix()}"
            )
    runtime_root = root / "football-data/runtime/activation"
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
    return Decision(FAIL, tuple(sorted(set(reasons)))) if reasons else Decision(PASS)


def _write_fixture(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        "\n".join((AIRTABLE_MARKER, FORMAL_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT)) + "\n",
        encoding="utf-8",
    )
    (root / "EXECUTION_LITE.md").write_text(
        "\n".join((AIRTABLE_MARKER, AUTH_MARKER, MIRROR_MARKER, CURRENT_STATE_TEXT, "讨论不等于执行")) + "\n",
        encoding="utf-8",
    )
    for rel in REQUIRED_BOUNDARY_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith("v6_engineering_research_profile_v6494.json"):
            path.write_text(
                json.dumps(
                    {
                        "project_state_authority": False,
                        "execution_authority": "EXPLICIT_DISPATCH_ONLY",
                        "hard_guards": {"no_project_state_authority": True},
                    }
                ),
                encoding="utf-8",
            )
        else:
            path.write_text("boundary\n", encoding="utf-8")
    runtime = root / "football-data/runtime/activation"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in (
        "activate_mls_d_conditional_runtime_v470.py",
        "activate_selective_direction_runtime_v501.py",
    ):
        (runtime / name).write_text("# runtime\n", encoding="utf-8")
    platform = root / "football-data/engine/validate_platform.py"
    platform.parent.mkdir(parents=True, exist_ok=True)
    platform.write_text(
        'def run(write: bool = False): pass\nparser.add_argument("--write-receipt")\n# 行为PASS只认CI测试\n',
        encoding="utf-8",
    )


def _expect_path(root: Path, rel: str, content: str, expected_status: str) -> bool:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        return validate_root(root).status == expected_status
    finally:
        path.unlink()


def run_self_test() -> int:
    checks: list[tuple[str, bool]] = []
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_fixture(root)
        checks.append(("positive", validate_root(root).status == PASS))

        for rel in (
            "football-data/research/FOOTBALL3_INDEPENDENT_CURRENT.md",
            "nested/a/PROJECT_CURRENT.md",
            "nested/a/TEAM_START_HERE.txt",
            "nested/a/TEAM_HANDOFF.json",
            "nested/a/TEAM_CHECKPOINT.md",
        ):
            checks.append((f"reject:{rel}", _expect_path(root, rel, "legacy mirror\n", FAIL)))

        # Production bypass regressions required by the final independent review.
        checks.append((
            "reject_current_season_forbidden_name",
            _expect_path(root, "notes/current_season/FOOTBALL3_INDEPENDENT_CURRENT.md", "legacy mirror\n", FAIL),
        ))
        checks.append((
            "reject_current_roster_forbidden_name",
            _expect_path(root, "notes/current_roster/PROJECT_CURRENT.md", "legacy mirror\n", FAIL),
        ))
        manifest_payload = json.dumps(
            {"schema_version": "r1", "current_pr": 332, "current_head": "abc", "next_step": "run"}
        )
        checks.append((
            "reject_manifest_live_checkpoint_schema_bypass",
            _expect_path(root, "football-data/manifests/LIVE_CHECKPOINT.json", manifest_payload, FAIL),
        ))
        checks.append((
            "reject_manifest_football3_current_schema_bypass",
            _expect_path(
                root,
                "football-data/manifests/FOOTBALL3_INDEPENDENT_CURRENT.json",
                manifest_payload,
                FAIL,
            ),
        ))

        checks.append((
            "reject_generic_live_authority",
            _expect_path(root, "notes/live.json", '{"project_state_authority": true}', FAIL),
        ))
        checks.append((
            "reject_generic_next_step",
            _expect_path(root, "notes/task.json", '{"next_step": "run experiment"}', FAIL),
        ))
        checks.append((
            "allow_explicit_deauthority_json",
            _expect_path(
                root,
                "notes/stable_profile.json",
                '{"no_project_state_authority": true, "project_state_authority": false}',
                PASS,
            ),
        ))
        checks.append((
            "allow_scientific_manifest_note_without_live_pointer",
            _expect_path(
                root,
                "football-data/manifests/V624_STATUS.json",
                '{"schema_version":"r1","formal_weight":0,"next_step":"historical scientific note"}',
                PASS,
            ),
        ))
        checks.append((
            "reject_scientific_manifest_with_current_head",
            _expect_path(
                root,
                "football-data/manifests/V624_STATUS.json",
                '{"schema_version":"r1","formal_weight":0,"current_head":"abc","next_step":"run"}',
                FAIL,
            ),
        ))
        checks.append((
            "allow_data_semantic_in_explicit_data_dir",
            _expect_path(
                root,
                "football-data/data/snapshots/current_season.json",
                '{"season":"2026","teams":[]}',
                PASS,
            ),
        ))
        checks.append((
            "reject_data_semantic_live_pointer",
            _expect_path(
                root,
                "football-data/data/snapshots/current_roster.json",
                '{"current_head":"abc","teams":[]}',
                FAIL,
            ),
        ))
        historical = "\n".join(HISTORICAL_REQUIRED_MARKERS) + "\nproject_state_authority=true # quoted historical text\n"
        checks.append((
            "allow_renamed_deauthorized_archive_evidence",
            _expect_path(root, "governance/archive/HISTORICAL_PROJECT_STATE_EVIDENCE.md", historical, PASS),
        ))
        checks.append((
            "reject_forbidden_name_even_in_archive",
            _expect_path(root, "governance/archive/PROJECT_CURRENT.md", historical, FAIL),
        ))

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
