#!/usr/bin/env python3
"""Repository-wide fail-closed governance topology guard.

Airtable《当前状态》 is the only live dynamic project-state source. GitHub may
contain stable policy/code/data and frozen historical evidence, but no active
Markdown/JSON/TXT object may become a second live task-state, next-step, or
authorization authority.
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
FROZEN_RECEIPT_PREFIXES = ("football-data/manifests/",)
DATA_SEMANTIC_PATH_HINTS = ("current_season", "current_roster")
DATA_CONTENT_PREFIXES = (
    "football-data/data/",
    "football-data/raw/",
    "football-data/processed/",
    "football-data/features/",
    "football-data/results/",
    "football-data/cache/",
)
FORBIDDEN_NAME_PATTERNS = (
    re.compile(r"(^|/)PROJECT_CURRENT\.md$", re.I),
    re.compile(r"(^|/)[^/]*_START_HERE\.[^/]+$", re.I),
    re.compile(r"(^|/)[^/]*_HANDOFF\.[^/]+$", re.I),
    re.compile(r"(^|/)[^/]*_CHECKPOINT\.[^/]+$", re.I),
    re.compile(r"(^|/)FOOTBALL3_INDEPENDENT_CURRENT\.md$", re.I),
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
    re.compile(
        r"(?<![A-Za-z0-9_])project_state_authority\s*[:=]\s*(?:true|yes|1)\b",
        re.I,
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])task_selection_authority\s*[:=]\s*(?:true|yes|1)\b",
        re.I,
    ),
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
JSON_LIVE_POINTER_KEYS = {
    "current_pr",
    "current_head",
    "current_run",
    "current_job",
    "current_artifact",
    "unique_next_step",
    "next_step",
    "authorization_source",
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


def _is_frozen_receipt(rel: str) -> bool:
    return rel.startswith(FROZEN_RECEIPT_PREFIXES)


def _is_data_semantic_whitelist(rel: str) -> bool:
    low = rel.lower()
    return any(hint in low for hint in DATA_SEMANTIC_PATH_HINTS)


def _truthy_authority(value: Any) -> bool:
    return value in (True, 1, "true", "yes", "1", "authoritative")


def _json_claims_authority(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_norm = str(key).strip().lower()
            if key_norm in JSON_AUTHORITY_KEYS and _truthy_authority(value):
                return True
            if _json_claims_authority(value):
                return True
    elif isinstance(payload, list):
        return any(_json_claims_authority(item) for item in payload)
    return False


def _json_claims_live_authority(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_norm = str(key).strip().lower()
            if key_norm in JSON_AUTHORITY_KEYS and _truthy_authority(value):
                return True
            if key_norm in JSON_LIVE_POINTER_KEYS and value not in (None, "", False, 0, [], {}):
                return True
            if _json_claims_live_authority(value):
                return True
    elif isinstance(payload, list):
        return any(_json_claims_live_authority(item) for item in payload)
    return False


def _text_claims_live_authority(text: str, suffix: str) -> bool:
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except Exception:
            return any(pattern.search(text) for pattern in LIVE_AUTHORITY_PATTERNS)
        return _json_claims_live_authority(payload)
    return any(pattern.search(text) for pattern in LIVE_AUTHORITY_PATTERNS)


def _frozen_receipt_is_safe(rel: str, text: str) -> bool:
    """Allow versioned receipt JSON to retain historical next-step/status fields.

    A receipt may describe what was current *at that historical checkpoint*; it
    is not a live control plane unless it claims project/task/authorization
    authority. Exact authority keys therefore remain fail-closed while ordinary
    versioned receipt fields such as `next_step` are treated as evidence.
    """
    if not _is_frozen_receipt(rel) or not rel.lower().endswith(".json"):
        return False
    try:
        payload = json.loads(text)
    except Exception:
        return False
    if not isinstance(payload, dict) or not payload.get("schema_version"):
        return False
    return not _json_claims_authority(payload)


def _scan_dynamic_mirrors(root: Path) -> list[str]:
    reasons: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        historical = _is_historical(rel)
        data_semantic = _is_data_semantic_whitelist(rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            reasons.append(f"unable to scan governance text {rel}: {exc}")
            continue
        frozen_receipt = _frozen_receipt_is_safe(rel, text)
        name_hit = any(pattern.search(rel) for pattern in FORBIDDEN_NAME_PATTERNS)
        if name_hit and not (historical or frozen_receipt or data_semantic):
            reasons.append(f"forbidden nested dynamic-state mirror path: {rel}")
            continue
        if data_semantic:
            continue
        if historical:
            if _text_claims_live_authority(text, path.suffix.lower()) and not all(
                marker in text for marker in HISTORICAL_REQUIRED_MARKERS
            ):
                reasons.append(
                    f"historical evidence with live-authority language lacks de-authorizing header: {rel}"
                )
            continue
        if frozen_receipt:
            continue
        if rel.startswith(DATA_CONTENT_PREFIXES):
            continue
        if _text_claims_live_authority(text, path.suffix.lower()):
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
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("legacy mirror\n", encoding="utf-8")
            checks.append((f"reject:{rel}", validate_root(root).status == FAIL))
            p.unlink()
        live = root / "notes/live.json"
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text('{"project_state_authority": true}', encoding="utf-8")
        checks.append(("reject_live_authority_claim", validate_root(root).status == FAIL))
        live.unlink()
        live_pointer = root / "notes/task.json"
        live_pointer.write_text('{"next_step": "run experiment"}', encoding="utf-8")
        checks.append(("reject_live_next_step_pointer", validate_root(root).status == FAIL))
        live_pointer.unlink()
        no_authority = root / "notes/stable_profile.json"
        no_authority.write_text(
            '{"no_project_state_authority": true, "project_state_authority": false}',
            encoding="utf-8",
        )
        checks.append(("allow_explicit_deauthority_json", validate_root(root).status == PASS))
        no_authority.unlink()
        frozen_checkpoint = root / "football-data/manifests/V522_CHECKPOINT.json"
        frozen_checkpoint.parent.mkdir(parents=True, exist_ok=True)
        frozen_checkpoint.write_text(
            '{"schema_version":"V5.2.2-frozen-r1","project_current_version":"V5.0.0","next_step":"historical note","current_rule_change":false}',
            encoding="utf-8",
        )
        checks.append(("allow_versioned_frozen_manifest_checkpoint", validate_root(root).status == PASS))
        frozen_checkpoint.unlink()
        bad_frozen_checkpoint = root / "football-data/manifests/BAD_CHECKPOINT.json"
        bad_frozen_checkpoint.write_text(
            '{"schema_version":"r1","project_state_authority":true}',
            encoding="utf-8",
        )
        checks.append(("reject_authoritative_manifest_checkpoint", validate_root(root).status == FAIL))
        bad_frozen_checkpoint.unlink()
        for rel in ("data/current_season.csv", "data/current_roster.txt"):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("current data snapshot\n", encoding="utf-8")
            checks.append((f"allow_data_semantic:{rel}", validate_root(root).status == PASS))
            p.unlink()
        hist = root / "governance/archive/PROJECT_CURRENT.md"
        hist.parent.mkdir(parents=True, exist_ok=True)
        hist.write_text(
            "\n".join(HISTORICAL_REQUIRED_MARKERS) + "\nproject_state_authority=true # quoted historical text\n",
            encoding="utf-8",
        )
        checks.append(("allow_deauthorized_historical_evidence", validate_root(root).status == PASS))
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
