#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
PLAN = ROOT / "governance" / "legacy_workflow_migration_plan.json"
ADJ = ROOT / "governance" / "post_freeze_workflow_adjudication.json"
CAP_ADJ = ROOT / "governance" / "consolidated_capability_adjudication.json"
LEDGER = ROOT / "governance" / "final_workflow_ledger.json"
ARCHIVE_BLOB_LEDGER = ROOT / "governance" / "final_archive_blob_ledger.json"
ARCH = ROOT / "governance" / "archive" / "workflows"
EXPECTED_ACTIVE = {
    ".github/workflows/ci.yml",
    ".github/workflows/forward.yml",
    ".github/workflows/maintenance.yml",
    ".github/workflows/research.yml",
    ".github/workflows/scheduled-data.yml",
    ".github/workflows/football-formal-core-v460.yml",
    ".github/workflows/football-platform-integrity.yml",
    ".github/workflows/football-repository-integrity-v471.yml",
    ".github/workflows/football-v6494-active-research-scope-guard.yml",
    ".github/workflows/football-v6494-current-state-audit.yml",
    ".github/workflows/football-v6482-unified-forward-pipeline.yml",
    ".github/workflows/football-v6492-fresh-challengers.yml",
    ".github/workflows/football-v6495-context-conditioned-selector.yml",
    ".github/workflows/football-v6495-context-materialize.yml",
}
EXPECTED = {
    "active_workflow_count": 14,
    "contents_write_count": 0,
    "git_commit_push_count": 0,
    "direct_main_push_count": 0,
    "persistence_count": 0,
    "push_trigger_count": 6,
    "schedule_trigger_count": 3,
    "missing_concurrency_count": 0,
    "missing_timeout_count": 0,
    "missing_python_reference_count": 0,
    "unknown_count": 0,
}
EXPECTED_CAPABILITY_COUNTS = {
    "EXECUTION_RETAINED": 6,
    "STATIC_REFERENCE_ONLY": 33,
    "RETIRED_ARCHIVE_ONLY": 15,
}
EXPECTED_ARCHIVE_BLOB_COUNT = 402
PY_TOKEN = re.compile(r"(?<![A-Za-z0-9_.-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.py)\b")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def blob_at(rev: str, path: str) -> str:
    return git("rev-parse", f"{rev}:{path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--write-archive-blob-ledger", action="store_true")
    args = parser.parse_args()
    errors: list[str] = []

    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None
        errors.append("PyYAML is required for YAML syntax validation")

    workflows = sorted([*WF.glob("*.yml"), *WF.glob("*.yaml")])
    active = {rel(path) for path in workflows}
    unknown = sorted(active - EXPECTED_ACTIVE)
    missing_expected = sorted(EXPECTED_ACTIVE - active)
    if missing_expected:
        errors.append(f"missing expected active workflows: {missing_expected}")

    metrics = collections.Counter()
    missing_py: set[str] = set()
    per_workflow = []
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        if yaml is not None:
            try:
                doc = yaml.safe_load(text)
                if not isinstance(doc, dict):
                    errors.append(f"YAML root is not mapping: {rel(path)}")
            except Exception as exc:
                errors.append(f"YAML parse failed {rel(path)}: {exc}")
        contents_write = bool(re.search(r"(?m)^\s*contents:\s*write\s*(?:#.*)?$", text))
        persistence = bool(
            re.search(
                r"persist_generated_worktree|persist_files_v474\.py|\bgit\s+(?:commit|push)\b|api\.github\.com/repos/.*/contents",
                text,
                re.I,
            )
        )
        git_cp = bool(re.search(r"\bgit\s+(?:commit|push)\b|persist_generated_worktree|persist_files_v474\.py", text, re.I))
        direct_main = bool(
            re.search(
                r"(?i)\bgit\s+push[^\n]*\bmain\b|persist_generated_worktree[^\n]*--branch\s+main|[\"']branch[\"']\s*:\s*[\"']main[\"']",
                text,
            )
        )
        push_trigger = bool(re.search(r"(?m)^  push:\s*$", text))
        schedule_trigger = bool(re.search(r"(?m)^  schedule:\s*$", text))
        has_concurrency = bool(re.search(r"(?m)^concurrency:\s*$", text))
        has_timeout = "timeout-minutes:" in text
        refs = sorted(set(PY_TOKEN.findall(text)))
        missing_here = []
        for token in refs:
            if "*" in token or "${{" in token:
                continue
            candidate = ROOT / token
            if not candidate.exists():
                missing_py.add(token)
                missing_here.append(token)
        metrics["contents_write_count"] += int(contents_write)
        metrics["persistence_count"] += int(persistence)
        metrics["git_commit_push_count"] += int(git_cp)
        metrics["direct_main_push_count"] += int(direct_main)
        metrics["push_trigger_count"] += int(push_trigger)
        metrics["schedule_trigger_count"] += int(schedule_trigger)
        metrics["missing_concurrency_count"] += int(not has_concurrency)
        metrics["missing_timeout_count"] += int(not has_timeout)
        per_workflow.append(
            {
                "path": rel(path),
                "contents_write": contents_write,
                "persistence": persistence,
                "git_commit_push": git_cp,
                "direct_main_push": direct_main,
                "push_trigger": push_trigger,
                "schedule_trigger": schedule_trigger,
                "concurrency": has_concurrency,
                "timeout": has_timeout,
                "missing_python": missing_here,
            }
        )

    metrics["active_workflow_count"] = len(workflows)
    metrics["missing_python_reference_count"] = len(missing_py)
    metrics["unknown_count"] = len(unknown)

    plan = load_json(PLAN)
    migrations = plan["migrations"]
    frozen_sha = plan.get("frozen_sha")
    if not frozen_sha:
        errors.append("migration plan missing frozen_sha")
        frozen_sha = ""
    if len(migrations) != 411:
        errors.append(f"frozen migration count {len(migrations)} != 411")
    disp = collections.Counter(row["disposition"] for row in migrations)
    expected_disp = {"CONSOLIDATE": 54, "ARCHIVE": 348, "KEEP": 5, "MANUAL_ONLY": 4}
    if dict(disp) != expected_disp:
        errors.append(f"disposition mismatch: {dict(disp)} expected={expected_disp}")
    frozen = {row["source_path"]: row for row in migrations}

    archived_by_name: dict[str, list[str]] = collections.defaultdict(list)
    for path in [*ARCH.rglob("*.yml"), *ARCH.rglob("*.yaml")]:
        archived_by_name[path.name].append(rel(path))

    archive_blob_entries: list[dict[str, object]] = []
    archive_blob_checked = 0
    archive_blob_exact_match = 0
    archive_blob_mismatch = 0

    for source, row in sorted(frozen.items()):
        name = Path(source).name
        if row["disposition"] in {"ARCHIVE", "CONSOLIDATE"}:
            if source in active:
                errors.append(f"removed disposition still active: {source}")
            copies = archived_by_name.get(name, [])
            if len(copies) != 1:
                errors.append(f"archive copy count {len(copies)} for {source}: {copies}")
                archive_blob_entries.append(
                    {
                        "source_path": source,
                        "frozen_blob_sha": None,
                        "archive_path": copies[0] if len(copies) == 1 else None,
                        "archive_blob_sha": None,
                        "exact_match": False,
                        "error": f"archive_copy_count={len(copies)}",
                    }
                )
                archive_blob_mismatch += 1
                continue
            archive_path = copies[0]
            try:
                frozen_blob_sha = blob_at(frozen_sha, source)
                archive_blob_sha = blob_at("HEAD", archive_path)
                exact_match = frozen_blob_sha == archive_blob_sha
                archive_blob_checked += 1
                archive_blob_exact_match += int(exact_match)
                archive_blob_mismatch += int(not exact_match)
                if not exact_match:
                    errors.append(
                        f"archive blob mismatch {source}: frozen={frozen_blob_sha} archive={archive_blob_sha} path={archive_path}"
                    )
                archive_blob_entries.append(
                    {
                        "source_path": source,
                        "frozen_blob_sha": frozen_blob_sha,
                        "archive_path": archive_path,
                        "archive_blob_sha": archive_blob_sha,
                        "exact_match": exact_match,
                    }
                )
            except Exception as exc:
                archive_blob_mismatch += 1
                errors.append(f"archive blob verification failed for {source}: {exc}")
                archive_blob_entries.append(
                    {
                        "source_path": source,
                        "frozen_blob_sha": None,
                        "archive_path": archive_path,
                        "archive_blob_sha": None,
                        "exact_match": False,
                        "error": str(exc),
                    }
                )
        elif source not in active:
            errors.append(f"retained disposition not active: {source}")

    if archive_blob_checked != EXPECTED_ARCHIVE_BLOB_COUNT:
        errors.append(f"archive_blob_checked={archive_blob_checked} expected={EXPECTED_ARCHIVE_BLOB_COUNT}")
    if archive_blob_exact_match != EXPECTED_ARCHIVE_BLOB_COUNT:
        errors.append(f"archive_blob_exact_match={archive_blob_exact_match} expected={EXPECTED_ARCHIVE_BLOB_COUNT}")
    if archive_blob_mismatch != 0:
        errors.append(f"archive_blob_mismatch={archive_blob_mismatch} expected=0")

    archive_ledger_payload = {
        "schema_version": "final-archive-blob-ledger-v1",
        "frozen_inventory_sha": frozen_sha,
        "head": git("rev-parse", "HEAD"),
        "archive_blob_checked": archive_blob_checked,
        "archive_blob_exact_match": archive_blob_exact_match,
        "archive_blob_mismatch": archive_blob_mismatch,
        "entries": archive_blob_entries,
    }
    if args.write_archive_blob_ledger:
        ARCHIVE_BLOB_LEDGER.write_text(
            json.dumps(archive_ledger_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    additions = active - set(frozen)
    adjudicated = {row["path"] for row in load_json(ADJ)["post_freeze_additions"]}
    if additions != adjudicated:
        errors.append(f"post-freeze addition mismatch active={sorted(additions)} adjudicated={sorted(adjudicated)}")
    if len(additions) != 5:
        errors.append(f"post-freeze additions {len(additions)} != 5")

    capability = load_json(CAP_ADJ)
    capability_rows = capability.get("entries", [])
    capability_sources = {row.get("source_path") for row in capability_rows}
    frozen_consolidate_sources = {row["source_path"] for row in migrations if row["disposition"] == "CONSOLIDATE"}
    if capability_sources != frozen_consolidate_sources:
        errors.append("54-way capability adjudication source set does not match frozen CONSOLIDATE set")
    capability_counts = collections.Counter(row.get("adjudication") for row in capability_rows)
    actual_capability_counts = {key: capability_counts.get(key, 0) for key in EXPECTED_CAPABILITY_COUNTS}
    if actual_capability_counts != EXPECTED_CAPABILITY_COUNTS:
        errors.append(f"capability adjudication counts drift: {actual_capability_counts}")

    for path in ROOT.joinpath("governance").rglob("*.json"):
        if path == ARCHIVE_BLOB_LEDGER and args.write_archive_blob_ledger:
            continue
        try:
            load_json(path)
        except Exception as exc:
            errors.append(f"JSON parse failed {rel(path)}: {exc}")

    ledger = load_json(LEDGER)
    for key, expected in EXPECTED.items():
        got = int(metrics[key])
        if got != expected:
            errors.append(f"metric {key}: got {got}, expected {expected}")
        if int(ledger["final_static_metrics"][key]) != expected:
            errors.append(f"ledger metric {key} drift")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "metrics": {key: int(metrics[key]) for key in EXPECTED},
        "archive_blob_checked": archive_blob_checked,
        "archive_blob_exact_match": archive_blob_exact_match,
        "archive_blob_mismatch": archive_blob_mismatch,
        "archive_blob_ledger_path": rel(ARCHIVE_BLOB_LEDGER),
        "capability_adjudication_counts": actual_capability_counts,
        "capability_claim": "PARTIAL_EXECUTION_RETENTION_WITH_EXPLICIT_RETIREMENT",
        "unknown_paths": unknown,
        "missing_python_references": sorted(missing_py),
        "frozen_dispositions": dict(disp),
        "frozen_original_removed_count": sum(1 for row in migrations if row["disposition"] in {"ARCHIVE", "CONSOLIDATE"}),
        "post_freeze_addition_count": len(additions),
        "per_workflow": per_workflow,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.strict and errors:
        return 2
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
