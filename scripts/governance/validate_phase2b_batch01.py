#!/usr/bin/env python3
"""Validate the recoverable Phase-2B batch-01 workflow archive.

The archived workflow bytes must match their source blobs at the accepted
Phase-2B preflight commit. The validator also enforces the exact Git diff scope:
21 lossless renames plus this validator and the generated archive manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE_COMMIT = "6812d436478c988d1d7d089f7c3842e794f5f2b9"
FROZEN_MAIN = "c9311d3c33d37f0ff52774ecfc4a7816209e3a2a"
OBSERVED_MAIN = "6beea539280293c8e414a3c2eaa2f0f8dd558942"
BACKUP_BRANCH = "backup/pre-governance-20260729"
BATCH = "SAFE_ARCHIVE_BATCH_01"
BRANCH = "codex/repository-governance-phase2b-batch01"
EXPECTED_COUNT = 21
EXPECTED_WORKFLOW_COUNT_BEFORE = 416
EXPECTED_WORKFLOW_COUNT_AFTER = 395

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "governance" / "legacy_workflow_migration_plan.json"
ARCHIVE_ROOT = ROOT / "governance" / "archive" / "workflows" / "phase2b-batch01"
MANIFEST = ARCHIVE_ROOT / "ARCHIVE_MANIFEST.json"
VALIDATOR_REL = "scripts/governance/validate_phase2b_batch01.py"
MANIFEST_REL = "governance/archive/workflows/phase2b-batch01/ARCHIVE_MANIFEST.json"
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


def load_records() -> list[dict[str, Any]]:
    data = json.loads(PLAN.read_text(encoding="utf-8"))
    records = [
        record
        for record in data["migrations"]
        if record.get("proposed_batch") == BATCH
    ]
    records.sort(key=lambda record: record["source_path"])
    if len(records) != EXPECTED_COUNT:
        raise ValidationError(
            f"expected {EXPECTED_COUNT} migration records, found {len(records)}"
        )
    for record in records:
        if record.get("disposition") != "ARCHIVE":
            raise ValidationError(f"not ARCHIVE: {record['source_path']}")
        if record.get("safe_to_archive") is not True:
            raise ValidationError(f"not safe_to_archive: {record['source_path']}")
        for field in (
            "unique_script_dependencies",
            "unique_secrets",
            "result_or_receipt_dependencies",
            "archive_blocker",
        ):
            if record.get(field):
                raise ValidationError(
                    f"{record['source_path']} has non-empty {field}"
                )
    return records


def git_blob(source_path: str) -> tuple[str, str, bytes]:
    tree_line = run_git("ls-tree", BASE_COMMIT, "--", source_path).strip()
    fields = tree_line.split(None, 3)
    if len(fields) != 4:
        raise ValidationError(f"missing source blob at base: {source_path}")
    mode, object_type, blob_sha, _ = fields
    if object_type != "blob":
        raise ValidationError(f"source is not a blob: {source_path}")
    content = run_git_bytes("show", f"{BASE_COMMIT}:{source_path}")
    return mode, blob_sha, content


def build_manifest() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for record in load_records():
        source_path = record["source_path"]
        filename = Path(source_path).name
        archived_path = (
            f"governance/archive/workflows/phase2b-batch01/{filename}"
        )
        mode, blob_sha, content = git_blob(source_path)
        entries.append(
            {
                "source_path": source_path,
                "archived_path": archived_path,
                "git_mode": mode,
                "original_blob_sha1": blob_sha,
                "sha256": hashlib.sha256(content).hexdigest(),
                "disposition": record["disposition"],
                "target_workflow": record["target_workflow"],
                "proposed_batch": record["proposed_batch"],
                "safe_to_archive": record["safe_to_archive"],
                "archive_reason": (
                    "No unique script, secret, receipt, or archive blocker; "
                    "preserved outside .github/workflows for recovery."
                ),
            }
        )
    return {
        "schema_version": "phase2b-batch01-archive-manifest-v1",
        "base_commit": BASE_COMMIT,
        "frozen_main": FROZEN_MAIN,
        "observed_main_before_batch": OBSERVED_MAIN,
        "backup_branch": BACKUP_BRANCH,
        "governance_branch": BRANCH,
        "actions_state": "DISABLED_BY_USER_UNCHANGED",
        "archive_semantics": "LOSSLESS_GIT_RENAME_OUT_OF_ACTIONS_DISCOVERY_PATH",
        "phase2b_started": True,
        "main_modified": False,
        "pr_closed_or_merged": False,
        "actions_reenabled": False,
        "workflow_files_before": EXPECTED_WORKFLOW_COUNT_BEFORE,
        "workflow_files_after": EXPECTED_WORKFLOW_COUNT_AFTER,
        "archived_workflow_count": EXPECTED_COUNT,
        "entries": entries,
    }


def write_manifest(expected: dict[str, Any]) -> None:
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def validate_files(expected: dict[str, Any]) -> None:
    for entry in expected["entries"]:
        source = ROOT / entry["source_path"]
        archived = ROOT / entry["archived_path"]
        if source.exists():
            raise ValidationError(f"source remains active: {entry['source_path']}")
        if not archived.is_file():
            raise ValidationError(f"archive missing: {entry['archived_path']}")
        content = run_git_bytes("show", f":{entry['archived_path']}")
        _, blob_sha, original = git_blob(entry["source_path"])
        if content != original:
            raise ValidationError(
                f"staged archive blob differs: {entry['archived_path']}"
            )
        if entry["original_blob_sha1"] != blob_sha:
            raise ValidationError(f"blob SHA mismatch: {entry['source_path']}")
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValidationError(f"SHA256 mismatch: {entry['archived_path']}")

    archived_workflows = sorted(ARCHIVE_ROOT.glob("*.yml"))
    if len(archived_workflows) != EXPECTED_COUNT:
        raise ValidationError(
            f"expected {EXPECTED_COUNT} archived yml files, "
            f"found {len(archived_workflows)}"
        )

    active_workflows = [
        path for path in (ROOT / ".github" / "workflows").iterdir() if path.is_file()
    ]
    if len(active_workflows) != EXPECTED_WORKFLOW_COUNT_AFTER:
        raise ValidationError(
            f"expected {EXPECTED_WORKFLOW_COUNT_AFTER} active workflow files, "
            f"found {len(active_workflows)}"
        )
    for filename in SAFE_SKELETONS:
        path = ROOT / ".github" / "workflows" / filename
        if not path.is_file():
            raise ValidationError(f"safe skeleton missing: {filename}")


def validate_manifest(expected: dict[str, Any]) -> None:
    if not MANIFEST.is_file():
        raise ValidationError(f"manifest missing: {MANIFEST_REL}")
    actual = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if actual != expected:
        raise ValidationError("archive manifest is not reproducible")


def validate_diff(expected: dict[str, Any]) -> None:
    cached = run_git(
        "diff",
        "--cached",
        "--name-status",
        "--find-renames=100%",
        BASE_COMMIT,
        "--",
    )
    raw = cached or run_git(
        "diff",
        "--name-status",
        "--find-renames=100%",
        f"{BASE_COMMIT}..HEAD",
        "--",
    )
    renames: set[tuple[str, str]] = set()
    additions: set[str] = set()
    unexpected: list[str] = []
    for line in raw.splitlines():
        fields = line.split("\t")
        status = fields[0]
        if status == "R100" and len(fields) == 3:
            renames.add((fields[1], fields[2]))
        elif status == "A" and len(fields) == 2:
            additions.add(fields[1])
        else:
            unexpected.append(line)

    expected_renames = {
        (entry["source_path"], entry["archived_path"])
        for entry in expected["entries"]
    }
    expected_additions = {MANIFEST_REL, VALIDATOR_REL}
    if renames != expected_renames:
        raise ValidationError("rename set differs from the 21-entry manifest")
    if additions != expected_additions:
        raise ValidationError(
            f"unexpected additions: {sorted(additions ^ expected_additions)}"
        )
    if unexpected:
        raise ValidationError(f"unexpected changes: {unexpected}")

    unstaged = run_git("diff", "--name-only", "--").strip()
    if unstaged:
        raise ValidationError(f"unstaged tracked changes remain: {unstaged}")
    untracked = run_git("ls-files", "--others", "--exclude-standard").strip()
    if untracked:
        raise ValidationError(f"untracked files remain: {untracked}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="rewrite the deterministic archive manifest before validation",
    )
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

    print("PASS phase2b_batch01")
    print(f"base_commit={BASE_COMMIT}")
    print(f"archived_workflow_count={EXPECTED_COUNT}")
    print(f"active_workflow_count={EXPECTED_WORKFLOW_COUNT_AFTER}")
    print("lossless_renames=21")
    print("safe_skeletons_present=5")
    print("unexpected_changes=0")
    print("unstaged_tracked_changes=0")
    print("untracked_files=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
