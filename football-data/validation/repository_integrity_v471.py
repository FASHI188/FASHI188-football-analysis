#!/usr/bin/env python3
"""Repository-wide fail-closed integrity audit for the football runtime repository.

V4.7.1 exists to catch engineering errors before long-running model workflows start.
It does not modify CURRENT, formal probabilities, model weights, or competition outputs.

Hard failures include:
- workflow Python/local-action references that do not exist;
- Python syntax errors in engine/validation code;
- invalid JSON in config/manifests;
- runtime bootstrap pointing away from the dedicated football repository;
- formal engine SHA drift;
- competition-registry/formal-core count mismatch;
- active legacy-repository references outside explicit migration provenance;
- CURRENT rule files stored in GitHub;
- finance/stock legacy paths inside the football runtime repository.

Warnings include workflow hygiene issues that do not prove execution failure, such as
write workflows that push without a pull --rebase conflict guard.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
WORKFLOWS = ROOT / ".github" / "workflows"
OUT = FOOTBALL / "manifests" / "repository_integrity_v471_status.json"

EXPECTED_REPO = "FASHI188/FASHI188-football-analysis"
LEGACY_REPO = "FASHI188/" + "HHH1"
EXPECTED_REF = "main"
EXPECTED_COMPETITIONS = 17
BANNED_PATH_TOKENS = ("stock", "investment", "quote_bus", "quote-bus")

PY_REF_RE = re.compile(r"\bpython(?:3)?\s+(?:-m\s+py_compile\s+)?(football-data/[A-Za-z0-9_./-]+\.py)\b")
LOCAL_ACTION_RE = re.compile(r"uses:\s*[\"']?\./([^\s\"'#]+)")
STATIC_PATH_RE = re.compile(r"^\s*-\s*[\"']?((?:football-data/(?:engine|validation|tests|config)/|\.github/workflows/)[^\"']+?)[\"']?\s*$")
WORKFLOW_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
CONCURRENCY_GROUP_RE = re.compile(r"^\s*group:\s*(.+?)\s*$", re.MULTILINE)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def audit() -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    details: dict[str, Any] = {}

    def error(code: str, message: str, **extra: Any) -> None:
        errors.append({"code": code, "message": message, **extra})

    def warn(code: str, message: str, **extra: Any) -> None:
        warnings.append({"code": code, "message": message, **extra})

    # 1) Workflow reference and hygiene audit.
    workflow_files = sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))
    workflow_names: dict[str, list[str]] = {}
    concurrency_groups: dict[str, list[str]] = {}
    referenced_python: set[str] = set()
    missing_static_refs: set[str] = set()

    for wf in workflow_files:
        text = wf.read_text(encoding="utf-8")
        wf_rel = rel(wf)

        if wf.name.startswith("migration-"):
            error("one_time_migration_workflow_present", "one-time migration workflow remains in the active runtime repository", workflow=wf_rel)
        if "/home/runner/work/" in text:
            error("workflow_absolute_runner_path", "workflow contains a brittle absolute GitHub runner path", workflow=wf_rel)
        if LEGACY_REPO in text:
            error("workflow_legacy_repo_reference", "workflow still references legacy repository authority", workflow=wf_rel)

        name_match = WORKFLOW_NAME_RE.search(text)
        if not name_match:
            error("workflow_missing_name", "workflow has no top-level name", workflow=wf_rel)
        else:
            workflow_names.setdefault(name_match.group(1).strip(), []).append(wf_rel)

        group_match = CONCURRENCY_GROUP_RE.search(text)
        if group_match:
            concurrency_groups.setdefault(group_match.group(1).strip(), []).append(wf_rel)

        if "git push" in text and "contents: write" not in text:
            error("workflow_push_without_write_permission", "workflow pushes commits without contents: write", workflow=wf_rel)
        if "git push" in text and "git pull --rebase" not in text:
            warn("workflow_push_without_rebase_guard", "workflow pushes directly without a pull --rebase conflict guard", workflow=wf_rel)
        if "timeout-minutes:" not in text:
            warn("workflow_missing_timeout", "workflow has no explicit timeout-minutes", workflow=wf_rel)

        for match in PY_REF_RE.finditer(text):
            path_text = match.group(1)
            referenced_python.add(path_text)
            if not (ROOT / path_text).is_file():
                error("workflow_missing_python_reference", "workflow references a Python file that does not exist", workflow=wf_rel, path=path_text)

        for match in LOCAL_ACTION_RE.finditer(text):
            action_path = ROOT / match.group(1)
            if not action_path.exists():
                error("workflow_missing_local_action", "workflow references a local action path that does not exist", workflow=wf_rel, path=rel(action_path) if action_path.is_relative_to(ROOT) else str(action_path))

        for line in text.splitlines():
            match = STATIC_PATH_RE.match(line)
            if not match:
                continue
            path_text = match.group(1).strip()
            if any(char in path_text for char in "*?[{"):
                continue
            if not (ROOT / path_text).exists():
                missing_static_refs.add(f"{wf_rel} -> {path_text}")

    for name, files in workflow_names.items():
        if len(files) > 1:
            warn("duplicate_workflow_name", "multiple workflows share the same display name", name=name, workflows=files)
    for group, files in concurrency_groups.items():
        if len(files) > 1:
            warn("duplicate_concurrency_group", "multiple workflows share the same concurrency group", group=group, workflows=files)
    for item in sorted(missing_static_refs):
        error("workflow_missing_static_path_filter_reference", "workflow path filter references a missing static repository path", reference=item)

    details["workflows"] = {
        "count": len(workflow_files),
        "referenced_python_count": len(referenced_python),
        "missing_static_reference_count": len(missing_static_refs),
    }

    # 2) Compile all active Python runtime/validation code.
    python_files = sorted((FOOTBALL / "engine").rglob("*.py")) + sorted((FOOTBALL / "validation").rglob("*.py"))
    compile_failures = []
    for path in python_files:
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:  # pragma: no cover - exercised in CI on syntax failure
            compile_failures.append({"path": rel(path), "error": str(exc)})
            error("python_compile_failure", "Python source failed syntax compilation", path=rel(path), detail=str(exc))
    details["python_compile"] = {"files_checked": len(python_files), "failures": compile_failures}

    # 3) Parse JSON configs/manifests so malformed generated artifacts cannot silently persist.
    json_files = sorted((FOOTBALL / "config").rglob("*.json")) + sorted((FOOTBALL / "manifests").glob("*.json"))
    json_failures = []
    for path in json_files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            json_failures.append({"path": rel(path), "error": str(exc)})
            error("json_parse_failure", "JSON config/manifest is malformed", path=rel(path), detail=str(exc))
    details["json_parse"] = {"files_checked": len(json_files), "failures": json_failures}

    # 4) Runtime authority, engine hash, registry and formal-core invariants.
    bootstrap_path = FOOTBALL / "manifests" / "runtime_bootstrap.json"
    core_path = FOOTBALL / "manifests" / "formal_core_v460_status.json"
    registry_path = FOOTBALL / "config" / "platform_registry.json"

    try:
        bootstrap = load_json(bootstrap_path)
        runtime = bootstrap.get("runtime_authority") or {}
        core_cfg = bootstrap.get("formal_core") or {}
        if runtime.get("repository") != EXPECTED_REPO:
            error("runtime_authority_repo_mismatch", "runtime bootstrap points to the wrong repository", actual=runtime.get("repository"), expected=EXPECTED_REPO)
        if runtime.get("ref") != EXPECTED_REF:
            error("runtime_authority_ref_mismatch", "runtime bootstrap points to the wrong ref", actual=runtime.get("ref"), expected=EXPECTED_REF)
        if int(core_cfg.get("expected_competition_count", -1)) != EXPECTED_COMPETITIONS:
            error("bootstrap_competition_count_mismatch", "bootstrap expected competition count changed", actual=core_cfg.get("expected_competition_count"), expected=EXPECTED_COMPETITIONS)

        engine_path = ROOT / str(core_cfg.get("engine_path") or "")
        expected_engine_sha = str(core_cfg.get("expected_engine_sha256") or "")
        if not engine_path.is_file():
            error("formal_engine_missing", "formal engine path is missing", path=str(core_cfg.get("engine_path")))
            actual_engine_sha = None
        else:
            actual_engine_sha = sha256(engine_path)
            if actual_engine_sha != expected_engine_sha:
                error("formal_engine_sha_mismatch", "formal engine SHA differs from frozen bootstrap expectation", expected=expected_engine_sha, actual=actual_engine_sha)
    except Exception as exc:
        bootstrap = {}
        actual_engine_sha = None
        error("runtime_bootstrap_audit_failure", "runtime bootstrap could not be fully audited", detail=str(exc))

    try:
        registry = load_json(registry_path)
        competitions = registry.get("competitions") or []
        ids = [item.get("competition_id") for item in competitions if isinstance(item, dict)]
        if registry.get("formal_current_stored_in_github") is not False:
            error("formal_current_storage_flag_invalid", "platform registry must declare formal CURRENT is not stored in GitHub")
        if int(registry.get("competition_count", -1)) != EXPECTED_COMPETITIONS:
            error("registry_competition_count_mismatch", "registry competition_count is not 17", actual=registry.get("competition_count"))
        if len(ids) != EXPECTED_COMPETITIONS or len(set(ids)) != EXPECTED_COMPETITIONS:
            error("registry_competition_id_integrity", "registry competition IDs are missing or duplicated", count=len(ids), unique_count=len(set(ids)))
    except Exception as exc:
        registry = {}
        ids = []
        error("registry_audit_failure", "platform registry could not be fully audited", detail=str(exc))

    try:
        core = load_json(core_path)
        if int(core.get("competition_count_requested", -1)) != EXPECTED_COMPETITIONS:
            error("formal_core_requested_count_mismatch", "formal core requested competition count is not 17", actual=core.get("competition_count_requested"))
        if int(core.get("competition_count_built", -1)) != EXPECTED_COMPETITIONS:
            error("formal_core_built_count_mismatch", "formal core built competition count is not 17", actual=core.get("competition_count_built"))
        if int(core.get("competition_count_failed", -1)) != 0:
            error("formal_core_failure_count_nonzero", "formal core manifest reports failed competitions", actual=core.get("competition_count_failed"))
        if actual_engine_sha and core.get("engine_sha256") != actual_engine_sha:
            error("formal_core_manifest_engine_sha_mismatch", "formal core manifest engine SHA does not match actual engine", manifest=core.get("engine_sha256"), actual=actual_engine_sha)
        reports = core.get("reports") or {}
        if len(reports) != EXPECTED_COMPETITIONS:
            error("formal_core_report_count_mismatch", "formal core report count is not 17", actual=len(reports))
    except Exception as exc:
        core = {}
        error("formal_core_audit_failure", "formal core manifest could not be fully audited", detail=str(exc))

    active_batch_repo_checks = {}
    for manifest_name in ("league_batch_001.json", "league_batch_002.json"):
        path = FOOTBALL / "manifests" / manifest_name
        try:
            data = load_json(path)
            active_batch_repo_checks[manifest_name] = data.get("repository")
            if data.get("repository") != EXPECTED_REPO:
                error("active_batch_manifest_repo_mismatch", "active batch manifest points to a legacy or unexpected repository", path=rel(path), actual=data.get("repository"), expected=EXPECTED_REPO)
        except Exception as exc:
            error("active_batch_manifest_audit_failure", "active batch manifest could not be audited", path=rel(path), detail=str(exc))

    details["runtime_invariants"] = {
        "expected_repository": EXPECTED_REPO,
        "actual_repository": (bootstrap.get("runtime_authority") or {}).get("repository") if bootstrap else None,
        "actual_engine_sha256": actual_engine_sha,
        "registry_competition_count": len(ids),
        "formal_core_report_count": len((core.get("reports") or {})) if core else 0,
        "active_batch_repositories": active_batch_repo_checks,
    }

    # 5) Governance separation: no CURRENT in GitHub, no active legacy refs, no finance assets.
    current_named_files = [rel(path) for path in ROOT.rglob("*") if path.is_file() and "CURRENT_唯一正式规则" in path.name]
    if current_named_files:
        error("current_rule_file_in_github", "formal CURRENT rule files must remain in the project File Library, not GitHub", paths=current_named_files)

    banned_paths = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        lower = rel(path).lower()
        if any(token in lower for token in BANNED_PATH_TOKENS):
            banned_paths.append(rel(path))
    if banned_paths:
        error("legacy_finance_paths_present", "stock/investment/quote-bus paths remain in the football repository", paths=sorted(banned_paths))

    legacy_refs = []
    allowed_legacy_provenance = {
        bootstrap_path,
        FOOTBALL / "manifests" / "repository_migration_acceptance.json",
    }
    scan_roots = [ROOT / ".github", FOOTBALL / "engine", FOOTBALL / "validation", FOOTBALL / "config", FOOTBALL / "manifests"]
    for base in scan_roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path in allowed_legacy_provenance:
                continue
            if path.suffix.lower() not in {".py", ".json", ".yml", ".yaml", ".md", ".txt"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if LEGACY_REPO in text:
                legacy_refs.append(rel(path))
    if legacy_refs:
        error("active_legacy_repo_reference", "active runtime/config/workflow files still reference legacy repository authority", paths=sorted(legacy_refs))

    # 6) Research/governance artifacts must remain non-promoting inside GitHub.
    research_patterns = (
        "total_goals_", "current_season_", "cross_year_", "stage_gate_",
        "active_league_", "jpn_j1_promotion_review", "remaining_work_", "a_grade_batch_",
    )
    nonzero_research_weights = []
    for path in (FOOTBALL / "manifests").glob("*.json"):
        if not any(token in path.name for token in research_patterns):
            continue
        try:
            data = load_json(path)
        except Exception:
            continue
        weight = data.get("formal_weight")
        if isinstance(weight, (int, float)) and float(weight) != 0.0:
            nonzero_research_weights.append({"path": rel(path), "formal_weight": weight})
    if nonzero_research_weights:
        error("research_formal_weight_nonzero", "research/governance manifests must not self-promote in GitHub", items=nonzero_research_weights)

    status = "PASS" if not errors else "FAIL"
    return {
        "schema_version": "V4.7.1-repository-integrity",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "hard_error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "details": details,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "policy": "Engineering integrity only. This audit cannot modify CURRENT or formal model weights. Hard failures must be repaired before long-running football workflows are trusted.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    report = audit()
    if args.write_receipt:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["status"],
            "hard_error_count": report["hard_error_count"],
            "warning_count": report["warning_count"],
            "errors": report["errors"],
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
