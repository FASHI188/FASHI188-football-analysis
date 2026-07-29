#!/usr/bin/env python3
"""Validate Phase-2B batch-03 and its preserved unique scripts."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE_COMMIT = "22f6de4615ba38a317f7f3952c9cd3745b5efc1d"
PREFLIGHT_COMMIT = "6812d436478c988d1d7d089f7c3842e794f5f2b9"
FROZEN_MAIN = "c9311d3c33d37f0ff52774ecfc4a7816209e3a2a"
OBSERVED_MAIN = "6beea539280293c8e414a3c2eaa2f0f8dd558942"
BACKUP_BRANCH = "backup/pre-governance-20260729"
BRANCH = "codex/repository-governance-phase2b-batch03"
EXPECTED_POOL = 49
EXPECTED_ARCHIVED = 25
EXPECTED_REMAINING = 24
EXPECTED_SCRIPTS = 31
EXPECTED_ACTIVE_BEFORE = 371
EXPECTED_ACTIVE_AFTER = 346

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "governance" / "legacy_workflow_migration_plan.json"
ARCHIVE_ROOT = ROOT / "governance" / "archive" / "workflows" / "phase2b-batch03"
MANIFEST = ARCHIVE_ROOT / "ARCHIVE_MANIFEST.json"
MANIFEST_REL = "governance/archive/workflows/phase2b-batch03/ARCHIVE_MANIFEST.json"
VALIDATOR_REL = "scripts/governance/validate_phase2b_batch03.py"
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
    content = run_git_bytes("show", f"{revision}:{path}")
    return mode, blob_sha, content


def load_pool() -> list[dict[str, Any]]:
    data = json.loads(PLAN.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for record in data["migrations"]:
        if (
            record.get("proposed_batch") == "UNSCHEDULED"
            and record.get("disposition") == "ARCHIVE"
            and record.get("unique_script_dependencies")
            and not record.get("unique_secrets")
            and not record.get("result_or_receipt_dependencies")
            and record.get("archive_blocker")
            == ["contains uniquely referenced script dependencies"]
        ):
            records.append(record)
    records.sort(key=lambda record: record["source_path"])
    if len(records) != EXPECTED_POOL:
        raise ValidationError(
            f"expected {EXPECTED_POOL} script-only candidates, found {len(records)}"
        )
    for record in records:
        for script in record["unique_script_dependencies"]:
            if not object_exists(BASE_COMMIT, script):
                raise ValidationError(
                    f"candidate script missing at base: "
                    f"{record['source_path']} -> {script}"
                )
    return records


def build_manifest() -> dict[str, Any]:
    pool = load_pool()
    selected = pool[:EXPECTED_ARCHIVED]
    remaining = pool[EXPECTED_ARCHIVED:]
    if len(selected) != EXPECTED_ARCHIVED or len(remaining) != EXPECTED_REMAINING:
        raise ValidationError("stable batch split count mismatch")

    script_paths: set[str] = set()
    entries: list[dict[str, Any]] = []
    for record in selected:
        source = record["source_path"]
        archived_path = (
            "governance/archive/workflows/phase2b-batch03/"
            f"{Path(source).name}"
        )
        mode, blob_sha, content = blob_at(BASE_COMMIT, source)
        scripts = sorted(record["unique_script_dependencies"])
        script_paths.update(scripts)
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
                "preserved_scripts": scripts,
                "blocker_resolution": (
                    "Unique scripts remain unchanged in place and the complete "
                    "workflow invocation is preserved in the archived YAML."
                ),
            }
        )
    if len(script_paths) != EXPECTED_SCRIPTS:
        raise ValidationError(
            f"expected {EXPECTED_SCRIPTS} scripts, found {len(script_paths)}"
        )

    scripts: list[dict[str, Any]] = []
    for path in sorted(script_paths):
        mode, blob_sha, content = blob_at(BASE_COMMIT, path)
        scripts.append(
            {
                "path": path,
                "git_mode": mode,
                "blob_sha1": blob_sha,
                "sha256": hashlib.sha256(content).hexdigest(),
                "preservation": "UNCHANGED_IN_PLACE",
            }
        )

    return {
        "schema_version": "phase2b-batch03-archive-manifest-v1",
        "base_commit": BASE_COMMIT,
        "preflight_commit": PREFLIGHT_COMMIT,
        "frozen_main": FROZEN_MAIN,
        "observed_main_before_batch": OBSERVED_MAIN,
        "backup_branch": BACKUP_BRANCH,
        "governance_branch": BRANCH,
        "actions_state": "DISABLED_BY_USER_UNCHANGED",
        "archive_semantics": "LOSSLESS_GIT_RENAME_OUT_OF_ACTIONS_DISCOVERY_PATH",
        "selection_rule": (
            "First 25 lexicographically sorted ARCHIVE workflows whose sole "
            "blocker is one or more existing unique script dependencies."
        ),
        "candidate_pool_count": EXPECTED_POOL,
        "workflow_files_before": EXPECTED_ACTIVE_BEFORE,
        "workflow_files_after": EXPECTED_ACTIVE_AFTER,
        "archived_workflow_count": EXPECTED_ARCHIVED,
        "remaining_candidate_count": EXPECTED_REMAINING,
        "preserved_script_count": EXPECTED_SCRIPTS,
        "main_modified": False,
        "pr_closed_or_merged": False,
        "actions_reenabled": False,
        "entries": entries,
        "preserved_scripts": scripts,
        "remaining_candidates": [
            {
                "source_path": record["source_path"],
                "status": "REMAINS_ACTIVE_FOR_NEXT_BATCH",
            }
            for record in remaining
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
        if (ROOT / entry["source_path"]).exists():
            raise ValidationError(f"source remains active: {entry['source_path']}")
        if not (ROOT / entry["archived_path"]).is_file():
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

    for entry in expected["preserved_scripts"]:
        path = entry["path"]
        if not (ROOT / path).is_file():
            raise ValidationError(f"preserved script missing: {path}")
        content = staged_content(path)
        _, blob_sha, original = blob_at(BASE_COMMIT, path)
        if content != original:
            raise ValidationError(f"preserved script changed: {path}")
        if blob_sha != entry["blob_sha1"]:
            raise ValidationError(f"script blob mismatch: {path}")
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValidationError(f"script SHA256 mismatch: {path}")

    for entry in expected["remaining_candidates"]:
        if not (ROOT / entry["source_path"]).is_file():
            raise ValidationError(
                f"next-batch candidate did not remain active: {entry['source_path']}"
            )
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
    print("PASS phase2b_batch03")
    print(f"base_commit={BASE_COMMIT}")
    print(f"archived_workflow_count={EXPECTED_ARCHIVED}")
    print(f"preserved_script_count={EXPECTED_SCRIPTS}")
    print(f"active_workflow_count={EXPECTED_ACTIVE_AFTER}")
    print(f"remaining_candidate_count={EXPECTED_REMAINING}")
    print("lossless_renames=25")
    print("safe_skeletons_present=5")
    print("unexpected_changes=0")
    print("unstaged_tracked_changes=0")
    print("untracked_files=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
