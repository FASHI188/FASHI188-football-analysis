#!/usr/bin/env python3
"""Compare frozen HHH1 and dedicated-football repository SHA-256 snapshots.

The comparison is path- and byte-hash-based. It classifies identical files,
changed common files, source-only files, and target-only files. Source-only
runtime/data/model assets are fail-closed unless explicitly classified as known
one-time migration/repair controls.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KNOWN_RETIRED_SOURCE_ONLY = {
    ".github/workflows/football-actions-write-probe.yml",
    ".github/workflows/football-clean-migration-inventory.yml",
    "football-data/manifests/v462_migration_status.txt",
    "football-data/tools/apply_v462_bottom_fixes.py",
    "football-data/tools/apply_v462_bottom_fixes_r2.py",
    "football-data/tools/apply_v462_bottom_fixes_r3.py",
    "football-data/tools/apply_v462_bottom_fixes_r4.py",
}

CRITICAL_PREFIXES = (
    "football-data/engine/",
    "football-data/calibration/",
    "football-data/config/",
    "football-data/schemas/",
    "football-data/models/",
    "football-data/raw/",
    "football-data/processed/",
    "football-data/league_profiles/",
    "football-data/team_strengths/",
    "football-data/training_datasets/",
    "football-data/validation/",
    "football-data/tests/",
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["path"]: item for item in manifest.get("entries", [])}


def compact(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": item["path"],
        "size_bytes": item["size_bytes"],
        "sha256": item["sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    source = load(Path(args.source))
    target = load(Path(args.target))
    s = index(source)
    t = index(target)

    source_paths = set(s)
    target_paths = set(t)
    common = sorted(source_paths & target_paths)
    source_only = sorted(source_paths - target_paths)
    target_only = sorted(target_paths - source_paths)

    identical = []
    changed = []
    for path in common:
        si = s[path]
        ti = t[path]
        if si["sha256"] == ti["sha256"] and si["size_bytes"] == ti["size_bytes"]:
            identical.append(path)
        else:
            changed.append({
                "path": path,
                "source_size_bytes": si["size_bytes"],
                "target_size_bytes": ti["size_bytes"],
                "source_sha256": si["sha256"],
                "target_sha256": ti["sha256"],
            })

    retired = [p for p in source_only if p in KNOWN_RETIRED_SOURCE_ONLY]
    unclassified = [p for p in source_only if p not in KNOWN_RETIRED_SOURCE_ONLY]
    critical_missing = [p for p in unclassified if p.startswith(CRITICAL_PREFIXES)]
    workflow_missing_review = [p for p in unclassified if p.startswith(".github/workflows/football")]
    other_missing_review = [p for p in unclassified if p not in critical_missing and p not in workflow_missing_review]

    formal_engine = "football-data/engine/football_v460_engine.py"
    engine_equal = (
        formal_engine in s
        and formal_engine in t
        and s[formal_engine]["sha256"] == t[formal_engine]["sha256"]
    )

    critical_source_paths = [p for p in source_paths if p.startswith(CRITICAL_PREFIXES)]
    critical_target_presence = all(p in target_paths for p in critical_source_paths if p not in KNOWN_RETIRED_SOURCE_ONLY)

    if critical_missing:
        status = "FAIL_CRITICAL_SOURCE_ASSETS_MISSING"
    elif workflow_missing_review or other_missing_review:
        status = "REVIEW_REQUIRED_SOURCE_ONLY_NONCRITICAL_ASSETS"
    elif not engine_equal or not critical_target_presence:
        status = "FAIL_CORE_INVARIANT"
    else:
        status = "PASS_NO_UNEXPLAINED_SOURCE_ONLY_ASSETS"

    report = {
        "schema_version": "V4.7.2-cross-repository-reconciliation",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_repository": source.get("repository"),
        "target_repository": target.get("repository"),
        "source_snapshot_sha256": source.get("snapshot_sha256"),
        "target_snapshot_sha256": target.get("snapshot_sha256"),
        "source_file_count": len(source_paths),
        "target_file_count": len(target_paths),
        "common_file_count": len(common),
        "identical_file_count": len(identical),
        "changed_common_file_count": len(changed),
        "source_only_file_count": len(source_only),
        "target_only_file_count": len(target_only),
        "formal_engine_sha_equal": engine_equal,
        "all_source_critical_paths_present_in_target": critical_target_presence,
        "known_retired_source_only": retired,
        "critical_source_only": critical_missing,
        "workflow_source_only_review": workflow_missing_review,
        "other_source_only_review": other_missing_review,
        "source_only": [compact(s[p]) for p in source_only],
        "target_only": [compact(t[p]) for p in target_only],
        "changed_common": changed,
        "status": status,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "policy": "Byte-level migration reconciliation only. CURRENT remains external and no model weight or formal probability is modified.",
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "source_file_count": report["source_file_count"],
        "target_file_count": report["target_file_count"],
        "identical_file_count": report["identical_file_count"],
        "changed_common_file_count": report["changed_common_file_count"],
        "source_only_file_count": report["source_only_file_count"],
        "target_only_file_count": report["target_only_file_count"],
        "critical_source_only": critical_missing,
        "workflow_source_only_review": workflow_missing_review,
        "other_source_only_review": other_missing_review,
    }, ensure_ascii=False, indent=2))

    if args.strict and status.startswith("FAIL_"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
