#!/usr/bin/env python3
"""Validate Phase-2B batch-02 and its preserved evidence dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE_COMMIT = "c7c0c412616b88ec573ec16bae60c258ed0e8d47"
PREFLIGHT_COMMIT = "6812d436478c988d1d7d089f7c3842e794f5f2b9"
FROZEN_MAIN = "c9311d3c33d37f0ff52774ecfc4a7816209e3a2a"
OBSERVED_MAIN = "6beea539280293c8e414a3c2eaa2f0f8dd558942"
BACKUP_BRANCH = "backup/pre-governance-20260729"
BRANCH = "codex/repository-governance-phase2b-batch02"
EXPECTED_CANDIDATES = 25
EXPECTED_ARCHIVED = 24
EXPECTED_DEPENDENCIES = 31
EXPECTED_ACTIVE_BEFORE = 395
EXPECTED_ACTIVE_AFTER = 371
EXPECTED_EXCLUDED_SOURCE = (
    ".github/workflows/football-ger-three-surface-v534.yml"
)
EXPECTED_MISSING_DEPENDENCY = (
    "football-data/manifests/"
    "ger_three_surface_market_projection_v534_status.json"
)

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "governance" / "legacy_workflow_migration_plan.json"
ARCHIVE_ROOT = ROOT / "governance" / "archive" / "workflows" / "phase2b-batch02"
MANIFEST = ARCHIVE_ROOT / "ARCHIVE_MANIFEST.json"
MANIFEST_REL = "governance/archive/workflows/phase2b-batch02/ARCHIVE_MANIFEST.json"
VALIDATOR_REL = "scripts/governance/validate_phase2b_batch02.py"
SAFE_SKELETONS = (
    "ci.yml",
    "research.yml",
    "scheduled-data.yml",
    "forward.yml",
    "maintenance.yml",
)


class ValidationError(RuntimeError):
    pass


def run_git_bytes(*args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise ValidationError(
            f"git {' '.join(args)} failed rc={proc.returncode}: "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


def run_git(*args: str) -> str:
    return run_git_bytes(*args).decode("utf-8", errors="strict")


def object_exists(revision: str, path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{revision}:{path}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def blob_at(revision: str, path: str) -> tuple[str, str, bytes]:
    tree_line = run_git("ls-tree", revision, "--", path).strip()
    fields = tree_line.split(None, 3)
    if len(fields) != 4:
        raise ValidationError(f"missing blob at {revision}: {path}")
    mode, object_type, blob_sha, _ = fields
    if object_type != "blob":
        raise ValidationError(f"not a blob at {revision}: {path}")
    return mode, blob_sha, run_git_bytes("show", f"{revision}:{path}")


def load_candidate_records() -> list[dict[str, Any]]:
    data = json.loads(PLAN.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for record in data["migrations"]:
        blockers = record.get("archive_blocker", [])
        if (
            record.get("proposed_batch") == "UNSCHEDULED"
            and record.get("disposition") == "ARCHIVE"
            and not record.get("unique_script_dependencies")
            and not record.get("unique_secrets")
            and record.get("result_or_receipt_dependencies")
            and blockers == [
                "contains uniquely referenced result/receipt dependencies"
            ]
        ):
            records.append(record)
    records.sort(key=lambda record: record["source_path"])
    if len(records) != EXPECTED_CANDIDATES:
        raise ValidationError(
            f"expected {EXPECTED_CANDIDATES} candidates, found {len(records)}"
        )
    return records


def classify_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    archived: list[dict[str, Any]] = []
    excluded: list[tuple[dict[str, Any], list[str]]] = []
    for record in load_candidate_records():
        missing = [
            path
            for path in record["result_or_receipt_dependencies"]
            if not object_exists(BASE_COMMIT, path)
        ]
        if missing:
            excluded.append((record, missing))
        else:
            archived.append(record)
    if len(archived) != EXPECTED_ARCHIVED:
        raise ValidationError(
            f"expected {EXPECTED_ARCHIVED} archive records, found {len(archived)}"
        )
    if len(excluded) != 1:
        raise ValidationError(f"expected one excluded record, found {len(excluded)}")
    excluded_record, missing = excluded[0]
    if excluded_record["source_path"] != EXPECTED_EXCLUDED_SOURCE:
        raise ValidationError("unexpected excluded workflow")
    if missing != [EXPECTED_MISSING_DEPENDENCY]:
        raise ValidationError(f"unexpected missing dependencies: {missing}")
    return archived, excluded_record


def build_manifest() -> dict[str, Any]:
    records, excluded = classify_records()
    entries: list[dict[str, Any]] = []
    dependency_paths: set[str] = set()
    for record in records:
        source = record["source_path"]
        archived_path = (
            "governance/archive/workflows/phase2b-batch02/"
            f"{Path(source).name}"
        )
        mode, blob_sha, content = blob_at(BASE_COMMIT, source)
        dependencies = sorted(record["result_or_receipt_dependencies"])
        dependency_paths.update(dependencies)
        entries.append(
            {
                "source_path": source,
                "archived_path": archived_path,
                "git_mode": mode,
                "original_blob_sha1": blob_sha,
                "sha256": hashlib.sha256(content).hexdigest(),
                "disposition": record["disposition"],
                "target_workflow": record["target_workflow"],
                "original_proposed_batch": record["proposed_batch"],
                "original_safe_to_archive": record["safe_to_archive"],
                "original_archive_blocker": record["archive_blocker"],
                "preserved_dependencies": dependencies,
                "blocker_resolution": (
                    "All referenced result/config evidence remains in place, "
                    "is recorded below, and is excluded from this batch diff."
                ),
            }
        )

    if len(dependency_paths) != EXPECTED_DEPENDENCIES:
        raise ValidationError(
            f"expected {EXPECTED_DEPENDENCIES} dependencies, "
            f"found {len(dependency_paths)}"
        )
    dependency_entries: list[dict[str, Any]] = []
    for path in sorted(dependency_paths):
        mode, blob_sha, content = blob_at(BASE_COMMIT, path)
        dependency_entries.append(
            {
                "path": path,
                "git_mode": mode,
                "blob_sha1": blob_sha,
                "sha256": hashlib.sha256(content).hexdigest(),
                "preservation": "UNCHANGED_IN_PLACE",
            }
        )

    return {
        "schema_version": "phase2b-batch02-archive-manifest-v1",
        "base_commit": BASE_COMMIT,
        "preflight_commit": PREFLIGHT_COMMIT,
        "frozen_main": FROZEN_MAIN,
        "observed_main_before_batch": OBSERVED_MAIN,
        "backup_branch": BACKUP_BRANCH,
        "governance_branch": BRANCH,
        "actions_state": "DISABLED_BY_USER_UNCHANGED",
        "archive_semantics": "LOSSLESS_GIT_RENAME_OUT_OF_ACTIONS_DISCOVERY_PATH",
        "selection_rule": (
            "ARCHIVE plus no unique script/secret dependency; evidence-only "
            "blocker resolved by immutable in-place preservation ledger."
        ),
        "workflow_files_before": EXPECTED_ACTIVE_BEFORE,
        "workflow_files_after": EXPECTED_ACTIVE_AFTER,
        "archived_workflow_count": EXPECTED_ARCHIVED,
        "preserved_dependency_count": EXPECTED_DEPENDENCIES,
        "main_modified": False,
        "pr_closed_or_merged": False,
        "actions_reenabled": False,
        "entries": entries,
        "preserved_dependencies": dependency_entries,
        "excluded_candidates": [
            {
                "source_path": excluded["source_path"],
                "status": "REMAINS_ACTIVE_BLOCKED",
                "missing_dependency": EXPECTED_MISSING_DEPENDENCY,
                "reason": (
                    "Referenced generated receipt is absent at the batch base; "
                    "the workflow cannot enter batch-02."
                ),
            }
        ],
    }


def write_manifest(expected: dict[str, Any]) -> None:
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def staged_content(path: str) -> bytes:
    return run_git_bytes("show", f":{path}")


def validate_files(expected: dict[str, Any]) -> None:
    for entry in expected["entries"]:
        source = ROOT / entry["source_path"]
        archived = ROOT / entry["archived_path"]
        if source.exists():
            raise ValidationError(f"source remains active: {entry['source_path']}")
        if not archived.is_file():
            raise ValidationError(f"archive missing: {entry['archived_path']}")
        content = staged_content(entry["archived_path"])
        _, blob_sha, original = blob_at(BASE_COMMIT, entry["source_path"])
        if content != original:
            raise ValidationError(
                f"staged archive blob differs: {entry['archived_path']}"
            )
        if blob_sha != entry["original_blob_sha1"]:
            raise ValidationError(f"source blob mismatch: {entry['source_path']}")
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValidationError(f"archive SHA256 mismatch: {entry['archived_path']}")

    for entry in expected["preserved_dependencies"]:
        path = entry["path"]
        if not (ROOT / path).is_file():
            raise ValidationError(f"preserved dependency missing: {path}")
        index_content = staged_content(path)
        _, blob_sha, original = blob_at(BASE_COMMIT, path)
        if index_content != original:
            raise ValidationError(f"preserved dependency changed: {path}")
        if blob_sha != entry["blob_sha1"]:
            raise ValidationError(f"dependency blob mismatch: {path}")
        if hashlib.sha256(index_content).hexdigest() != entry["sha256"]:
            raise ValidationError(f"dependency SHA256 mismatch: {path}")

    if not (ROOT / EXPECTED_EXCLUDED_SOURCE).is_file():
        raise ValidationError("excluded workflow did not remain active")
    if object_exists(BASE_COMMIT, EXPECTED_MISSING_DEPENDENCY):
        raise ValidationError("expected missing dependency unexpectedly exists")
    if len(list(ARCHIVE_ROOT.glob("*.yml"))) != EXPECTED_ARCHIVED:
        raise ValidationError("archive yml count mismatch")
    active = [
        path for path in (ROOT / ".github" / "workflows").iterdir() if path.is_file()
    ]
    if len(active) != EXPECTED_ACTIVE_AFTER:
        raise ValidationError(
            f"expected {EXPECTED_ACTIVE_AFTER} active workflows, found {len(active)}"
        )
    for filename in SAFE_SKELETONS:
        if not (ROOT / ".github" / "workflows" / filename).is_file():
            raise ValidationError(f"safe skeleton missing: {filename}")


def validate_manifest(expected: dict[str, Any]) -> None:
    if not MANIFEST.is_file():
        raise ValidationError(f"manifest missing: {MANIFEST_REL}")
    actual = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if actual != expected:
        raise ValidationError("archive manifest is not reproducible")


def validate_diff(expected: dict[str, Any]) -> None:
    cached = run_git(
        "diff", "--cached", "--name-status", "--find-renames=100%",
        BASE_COMMIT, "--",
    )
    raw = cached or run_git(
        "diff", "--name-status", "--find-renames=100%",
        f"{BASE_COMMIT}..HEAD", "--",
    )
    renames: set[tuple[str, str]] = set()
    additions: set[str] = set()
    unexpected: list[str] = []
    for line in raw.splitlines():
        fields = line.split("\t")
        if fields[0] == "R100" and len(fields) == 3:
            renames.add((fields[1], fields[2]))
        elif fields[0] == "A" and len(fields) == 2:
            additions.add(fields[1])
        else:
            unexpected.append(line)
    expected_renames = {
        (entry["source_path"], entry["archived_path"])
        for entry in expected["entries"]
    }
    if renames != expected_renames:
        raise ValidationError("rename set differs from manifest")
    if additions != {MANIFEST_REL, VALIDATOR_REL}:
        raise ValidationError("addition set differs from manifest and validator")
    if unexpected:
        raise ValidationError(f"unexpected changes: {unexpected}")
    if run_git("diff", "--name-only", "--").strip():
        raise ValidationError("unstaged tracked changes remain")
    if run_git("ls-files", "--others", "--exclude-standard").strip():
        raise ValidationError("untracked files remain")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    try:
        expected = build_manifest()
        if args.write_manifest:
            write_manifest(expected)
            print(f"MANIFEST_WRITTEN {MANIFEST_REL}")
            return 0
        validate_files(expected)
        validate_manifest(expected)
        validate_diff(expected)
    except (OSError, KeyError, TypeError, ValueError, ValidationError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    print("PASS phase2b_batch02")
    print(f"base_commit={BASE_COMMIT}")
    print(f"archived_workflow_count={EXPECTED_ARCHIVED}")
    print(f"preserved_dependency_count={EXPECTED_DEPENDENCIES}")
    print(f"active_workflow_count={EXPECTED_ACTIVE_AFTER}")
    print("lossless_renames=24")
    print("excluded_missing_receipt=1")
    print("safe_skeletons_present=5")
    print("unexpected_changes=0")
    print("unstaged_tracked_changes=0")
    print("untracked_files=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
