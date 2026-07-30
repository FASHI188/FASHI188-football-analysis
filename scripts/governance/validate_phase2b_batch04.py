#!/usr/bin/env python3
"""Validate Phase-2B batch-04 lossless workflow archival.

Batch-04 is defined by the exact 24 `remaining_candidates` emitted by the
phase2b-batch03 manifest. It is intentionally Git-history authoritative.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE_COMMIT = "46d5cc953d88a003e3b63afc44ca984e5c071198"
EXPECTED_ACTIVE_BEFORE = 344
EXPECTED_ACTIVE_AFTER = 320
EXPECTED_ARCHIVED = 24
EXPECTED_SCRIPT_COUNT = 27
ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "governance" / "legacy_workflow_migration_plan.json"
PREVIOUS = ROOT / "governance" / "archive" / "workflows" / "phase2b-batch03" / "ARCHIVE_MANIFEST.json"
ARCHIVE_ROOT = ROOT / "governance" / "archive" / "workflows" / "phase2b-batch04"
MANIFEST = ARCHIVE_ROOT / "ARCHIVE_MANIFEST.json"
MANIFEST_REL = "governance/archive/workflows/phase2b-batch04/ARCHIVE_MANIFEST.json"
VALIDATOR_REL = "scripts/governance/validate_phase2b_batch04.py"
SAFE_SKELETONS = ("ci.yml", "research.yml", "scheduled-data.yml", "forward.yml", "maintenance.yml")


class ValidationError(RuntimeError):
    pass


def run_git_bytes(*args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(ROOT), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise ValidationError(f"git {' '.join(args)} failed rc={proc.returncode}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def run_git(*args: str) -> str:
    return run_git_bytes(*args).decode("utf-8", errors="strict")


def object_exists(revision: str, path: str) -> bool:
    proc = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{revision}:{path}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return proc.returncode == 0


def blob_sha(revision: str, path: str) -> str:
    line = run_git("ls-tree", revision, "--", path).strip()
    fields = line.split(None, 3)
    if len(fields) != 4 or fields[1] != "blob":
        raise ValidationError(f"missing blob at {revision}: {path}")
    return fields[2]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValidationError(f"invalid JSON {path}: {exc}") from exc


def expected_sources() -> list[str]:
    previous = load_json(PREVIOUS)
    sources = [row["source_path"] for row in previous["remaining_candidates"]]
    if len(sources) != EXPECTED_ARCHIVED or len(set(sources)) != EXPECTED_ARCHIVED:
        raise ValidationError("phase2b-batch03 remaining-candidate set is not exactly 24 unique paths")
    return sources


def plan_records() -> dict[str, dict[str, Any]]:
    plan = load_json(PLAN)
    return {row["source_path"]: row for row in plan["migrations"]}


def validate_manifest_and_plan() -> dict[str, Any]:
    manifest = load_json(MANIFEST)
    if manifest.get("base_commit") != BASE_COMMIT:
        raise ValidationError("manifest base_commit mismatch")
    if manifest.get("workflow_files_before") != EXPECTED_ACTIVE_BEFORE:
        raise ValidationError("manifest workflow_files_before mismatch")
    if manifest.get("workflow_files_after") != EXPECTED_ACTIVE_AFTER:
        raise ValidationError("manifest workflow_files_after mismatch")
    if manifest.get("archived_workflow_count") != EXPECTED_ARCHIVED:
        raise ValidationError("manifest archived_workflow_count mismatch")
    if manifest.get("preserved_script_count") != EXPECTED_SCRIPT_COUNT:
        raise ValidationError("manifest preserved_script_count mismatch")
    if any(manifest.get(k) != 0 for k in ("model_changes", "data_changes", "config_changes", "current_changes")):
        raise ValidationError("forbidden model/data/config/CURRENT change count")
    if manifest.get("actions_reenabled") is not False:
        raise ValidationError("Actions state changed")
    if manifest.get("main_modified") is not False:
        raise ValidationError("main_modified must remain false")

    sources = expected_sources()
    records = plan_records()
    entries = manifest.get("entries", [])
    if [row["source_path"] for row in entries] != sources:
        raise ValidationError("manifest source order/set differs from batch03 remaining candidates")

    scripts: set[str] = set()
    for entry in entries:
        source = entry["source_path"]
        rec = records.get(source)
        if not rec:
            raise ValidationError(f"source missing from migration plan: {source}")
        if rec.get("disposition") != "ARCHIVE":
            raise ValidationError(f"not ARCHIVE in plan: {source}")
        if rec.get("target_workflow") != "research.yml":
            raise ValidationError(f"unexpected target workflow: {source}")
        if rec.get("unique_secrets"):
            raise ValidationError(f"secret-bearing workflow cannot enter batch04: {source}")
        if rec.get("result_or_receipt_dependencies"):
            raise ValidationError(f"receipt-bearing workflow cannot enter script-only batch04: {source}")
        if rec.get("archive_blocker") != ["contains uniquely referenced script dependencies"]:
            raise ValidationError(f"not script-only blocker: {source}")
        expected_scripts = sorted(rec.get("unique_script_dependencies") or [])
        if not expected_scripts:
            raise ValidationError(f"no unique script dependency: {source}")
        if sorted(entry.get("preserved_scripts") or []) != expected_scripts:
            raise ValidationError(f"preserved script list mismatch: {source}")
        scripts.update(expected_scripts)
        expected_archive = f"governance/archive/workflows/phase2b-batch04/{Path(source).name}"
        if entry.get("archived_path") != expected_archive:
            raise ValidationError(f"archive path mismatch: {source}")
        sha = blob_sha(BASE_COMMIT, source)
        if entry.get("original_blob_sha1") != sha:
            raise ValidationError(f"source blob mismatch: {source}")
        for script in expected_scripts:
            if not object_exists(BASE_COMMIT, script):
                raise ValidationError(f"preserved script missing at base: {script}")
            if blob_sha("HEAD", script) != blob_sha(BASE_COMMIT, script):
                raise ValidationError(f"preserved script changed: {script}")
    if len(scripts) != EXPECTED_SCRIPT_COUNT:
        raise ValidationError(f"expected {EXPECTED_SCRIPT_COUNT} unique scripts, found {len(scripts)}")
    return manifest


def validate_archive_files(manifest: dict[str, Any]) -> None:
    for entry in manifest["entries"]:
        source = entry["source_path"]
        archive = entry["archived_path"]
        if object_exists("HEAD", source):
            raise ValidationError(f"source remains active: {source}")
        if not object_exists("HEAD", archive):
            raise ValidationError(f"archive missing: {archive}")
        if blob_sha("HEAD", archive) != entry["original_blob_sha1"]:
            raise ValidationError(f"archive blob is not lossless: {archive}")

    active = run_git("ls-tree", "-r", "--name-only", "HEAD", "--", ".github/workflows").splitlines()
    active = [p for p in active if p.startswith(".github/workflows/")]
    if len(active) != EXPECTED_ACTIVE_AFTER:
        raise ValidationError(f"expected {EXPECTED_ACTIVE_AFTER} active workflows, found {len(active)}")
    for name in SAFE_SKELETONS:
        if f".github/workflows/{name}" not in active:
            raise ValidationError(f"safe skeleton missing: {name}")


def validate_diff(manifest: dict[str, Any]) -> None:
    raw = run_git("diff", "--name-status", "--find-renames=100%", f"{BASE_COMMIT}..HEAD", "--")
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
    expected_renames = {(row["source_path"], row["archived_path"]) for row in manifest["entries"]}
    if renames != expected_renames:
        raise ValidationError("rename set differs from manifest")
    if additions != {MANIFEST_REL, VALIDATOR_REL}:
        raise ValidationError("addition set differs from manifest and validator")
    if unexpected:
        raise ValidationError(f"unexpected changes: {unexpected}")


def main() -> int:
    try:
        manifest = validate_manifest_and_plan()
        validate_archive_files(manifest)
        validate_diff(manifest)
    except (OSError, KeyError, TypeError, ValueError, ValidationError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    print("PASS phase2b_batch04")
    print(f"base_commit={BASE_COMMIT}")
    print(f"archived_workflow_count={EXPECTED_ARCHIVED}")
    print(f"preserved_script_count={EXPECTED_SCRIPT_COUNT}")
    print(f"active_workflow_count={EXPECTED_ACTIVE_AFTER}")
    print("remaining_script_only_candidate_count=0")
    print("lossless_renames=24")
    print("safe_skeletons_present=5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
