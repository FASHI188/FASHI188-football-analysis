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
POST_FREEZE = ROOT / "governance" / "post_freeze_workflow_adjudication.json"
CAPABILITY = ROOT / "governance" / "consolidated_capability_adjudication.json"
LEDGER = ROOT / "governance" / "final_workflow_ledger.json"
ARCHIVE_LEDGER = ROOT / "governance" / "final_archive_blob_ledger.json"
ARCHIVE_ROOT = ROOT / "governance" / "archive" / "workflows"
V510_MANIFEST = ARCHIVE_ROOT / "v510-current-transition" / "ARCHIVE_MANIFEST.json"

EXPECTED_ACTIVE = {
    ".github/workflows/ci.yml",
    ".github/workflows/forward.yml",
    ".github/workflows/maintenance.yml",
    ".github/workflows/research.yml",
    ".github/workflows/scheduled-data.yml",
    ".github/workflows/football-formal-core-v460.yml",
    ".github/workflows/football-platform-integrity.yml",
    ".github/workflows/football-repository-integrity-v471.yml",
}
EXPECTED_METRICS = {
    "active_workflow_count": 8,
    "contents_write_count": 0,
    "git_commit_push_count": 0,
    "direct_main_push_count": 0,
    "persistence_count": 0,
    "push_trigger_count": 4,
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
EXPECTED_FROZEN_ARCHIVE_COUNT = 402
EXPECTED_V510_RETIREMENT_COUNT = 6
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
    missing_python: set[str] = set()
    per_workflow: list[dict[str, object]] = []
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        if yaml is not None:
            try:
                if not isinstance(yaml.safe_load(text), dict):
                    errors.append(f"YAML root is not mapping: {rel(path)}")
            except Exception as exc:
                errors.append(f"YAML parse failed {rel(path)}: {exc}")
        contents_write = bool(re.search(r"(?m)^\s*contents:\s*write\s*(?:#.*)?$", text))
        persistence = bool(re.search(r"persist_generated_worktree|persist_files_v474\.py|\bgit\s+(?:commit|push)\b|api\.github\.com/repos/.*/contents", text, re.I))
        git_cp = bool(re.search(r"\bgit\s+(?:commit|push)\b|persist_generated_worktree|persist_files_v474\.py", text, re.I))
        direct_main = bool(re.search(r"(?i)\bgit\s+push[^\n]*\bmain\b|persist_generated_worktree[^\n]*--branch\s+main|[\"']branch[\"']\s*:\s*[\"']main[\"']", text))
        push_trigger = bool(re.search(r"(?m)^  push:\s*$", text))
        schedule_trigger = bool(re.search(r"(?m)^  schedule:\s*$", text))
        has_concurrency = bool(re.search(r"(?m)^concurrency:\s*$", text))
        has_timeout = "timeout-minutes:" in text
        missing_here: list[str] = []
        for token in sorted(set(PY_TOKEN.findall(text))):
            if "*" in token or "${{" in token:
                continue
            if not (ROOT / token).exists():
                missing_python.add(token)
                missing_here.append(token)
        metrics["contents_write_count"] += int(contents_write)
        metrics["persistence_count"] += int(persistence)
        metrics["git_commit_push_count"] += int(git_cp)
        metrics["direct_main_push_count"] += int(direct_main)
        metrics["push_trigger_count"] += int(push_trigger)
        metrics["schedule_trigger_count"] += int(schedule_trigger)
        metrics["missing_concurrency_count"] += int(not has_concurrency)
        metrics["missing_timeout_count"] += int(not has_timeout)
        per_workflow.append({"path": rel(path), "missing_python": missing_here})

    metrics["active_workflow_count"] = len(workflows)
    metrics["missing_python_reference_count"] = len(missing_python)
    metrics["unknown_count"] = len(unknown)

    plan = load_json(PLAN)
    migrations = plan.get("migrations", [])
    frozen_sha = plan.get("frozen_sha")
    if len(migrations) != 411:
        errors.append(f"frozen migration count {len(migrations)} != 411")
    if not frozen_sha:
        errors.append("migration plan missing frozen_sha")
        frozen_sha = ""
    dispositions = collections.Counter(row.get("disposition") for row in migrations)
    if dict(dispositions) != {"CONSOLIDATE": 54, "ARCHIVE": 348, "KEEP": 5, "MANUAL_ONLY": 4}:
        errors.append(f"frozen disposition drift: {dict(dispositions)}")
    frozen = {row["source_path"]: row for row in migrations}

    archive_by_name: dict[str, list[str]] = collections.defaultdict(list)
    for path in [*ARCHIVE_ROOT.rglob("*.yml"), *ARCHIVE_ROOT.rglob("*.yaml")]:
        archive_by_name[path.name].append(rel(path))

    frozen_entries: list[dict[str, object]] = []
    frozen_checked = 0
    frozen_exact = 0
    frozen_mismatch = 0
    for source, row in sorted(frozen.items()):
        if row.get("disposition") not in {"ARCHIVE", "CONSOLIDATE"}:
            continue
        copies = archive_by_name.get(Path(source).name, [])
        exact = False
        frozen_blob = None
        archive_blob = None
        archive_path = copies[0] if len(copies) == 1 else None
        try:
            if len(copies) != 1:
                raise RuntimeError(f"archive_copy_count={len(copies)} copies={copies}")
            frozen_blob = blob_at(frozen_sha, source)
            archive_blob = blob_at("HEAD", archive_path)
            exact = frozen_blob == archive_blob
            if not exact:
                raise RuntimeError(f"blob mismatch frozen={frozen_blob} archive={archive_blob}")
            frozen_checked += 1
            frozen_exact += 1
        except Exception as exc:
            frozen_mismatch += 1
            errors.append(f"frozen archive validation failed for {source}: {exc}")
        frozen_entries.append({"source_path": source, "archive_path": archive_path, "frozen_blob_sha": frozen_blob, "archive_blob_sha": archive_blob, "exact_match": exact})

    if frozen_checked != EXPECTED_FROZEN_ARCHIVE_COUNT or frozen_exact != EXPECTED_FROZEN_ARCHIVE_COUNT or frozen_mismatch != 0:
        errors.append(f"frozen archive totals checked={frozen_checked} exact={frozen_exact} mismatch={frozen_mismatch}")

    v510 = load_json(V510_MANIFEST)
    v510_rows = v510.get("entries", [])
    retired_sources = {row.get("source_path") for row in v510_rows}
    if len(v510_rows) != EXPECTED_V510_RETIREMENT_COUNT or len(retired_sources) != EXPECTED_V510_RETIREMENT_COUNT:
        errors.append("V5.1 retirement manifest must contain six unique entries")
    v510_checked = 0
    v510_exact = 0
    v510_mismatch = 0
    for row in v510_rows:
        source = row["source_path"]
        archive_path = row["archive_path"]
        try:
            source_blob = blob_at(v510["base_main_sha"], source)
            archive_blob = blob_at("HEAD", archive_path)
            if source_blob != row.get("source_blob_sha"):
                raise RuntimeError(f"manifest source blob mismatch {source_blob} != {row.get('source_blob_sha')}")
            if source_blob != archive_blob:
                raise RuntimeError(f"archive blob mismatch {source_blob} != {archive_blob}")
            if source in active:
                raise RuntimeError("retired source remains active")
            v510_checked += 1
            v510_exact += 1
        except Exception as exc:
            v510_mismatch += 1
            errors.append(f"V5.1 retirement validation failed for {source}: {exc}")

    retained_frozen = {source for source, row in frozen.items() if row.get("disposition") in {"KEEP", "MANUAL_ONLY"}}
    expected_retired = retained_frozen - active
    if expected_retired != retired_sources:
        errors.append(f"retired frozen source set drift: expected={sorted(expected_retired)} manifest={sorted(retired_sources)}")

    additions = active - set(frozen)
    adjudicated_additions = {row["path"] for row in load_json(POST_FREEZE).get("post_freeze_additions", [])}
    if additions != adjudicated_additions or len(additions) != 5:
        errors.append(f"post-freeze additions drift active={sorted(additions)} adjudicated={sorted(adjudicated_additions)}")

    capability = load_json(CAPABILITY)
    cap_rows = capability.get("entries", [])
    cap_counts = collections.Counter(row.get("adjudication") for row in cap_rows)
    actual_cap_counts = {key: cap_counts.get(key, 0) for key in EXPECTED_CAPABILITY_COUNTS}
    if actual_cap_counts != EXPECTED_CAPABILITY_COUNTS:
        errors.append(f"capability counts drift: {actual_cap_counts}")

    for path in ROOT.joinpath("governance").rglob("*.json"):
        if path == ARCHIVE_LEDGER and args.write_archive_blob_ledger:
            continue
        try:
            load_json(path)
        except Exception as exc:
            errors.append(f"JSON parse failed {rel(path)}: {exc}")

    ledger = load_json(LEDGER)
    for key, expected in EXPECTED_METRICS.items():
        got = int(metrics[key])
        if got != expected:
            errors.append(f"metric {key}: got {got}, expected {expected}")
        if int(ledger["final_static_metrics"][key]) != expected:
            errors.append(f"ledger metric {key} drift")

    archive_payload = {
        "schema_version": "final-archive-blob-ledger-v2",
        "frozen_inventory_sha": frozen_sha,
        "head": git("rev-parse", "HEAD"),
        "archive_blob_checked": frozen_checked,
        "archive_blob_exact_match": frozen_exact,
        "archive_blob_mismatch": frozen_mismatch,
        "v510_retirement_checked": v510_checked,
        "v510_retirement_exact_match": v510_exact,
        "v510_retirement_mismatch": v510_mismatch,
        "entries": frozen_entries,
    }
    if args.write_archive_blob_ledger:
        ARCHIVE_LEDGER.write_text(json.dumps(archive_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "metrics": {key: int(metrics[key]) for key in EXPECTED_METRICS},
        "active_workflows": sorted(active),
        "frozen_archive_checked": frozen_checked,
        "frozen_archive_exact_match": frozen_exact,
        "frozen_archive_mismatch": frozen_mismatch,
        "v510_retirement_checked": v510_checked,
        "v510_retirement_exact_match": v510_exact,
        "v510_retirement_mismatch": v510_mismatch,
        "capability_adjudication_counts": actual_cap_counts,
        "unknown_paths": unknown,
        "missing_python_references": sorted(missing_python),
        "per_workflow": per_workflow,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.strict and errors:
        return 2
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
