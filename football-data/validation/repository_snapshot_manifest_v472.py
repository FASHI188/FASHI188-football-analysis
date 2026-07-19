#!/usr/bin/env python3
"""Build a deterministic SHA-256 manifest of active football runtime assets.

Scope:
- all football-data files except one-time migration inventory and this generated manifest;
- all football-prefixed GitHub workflow YAML files.

The manifest is for cross-repository migration reconciliation only. It does not
modify CURRENT, formal weights, or model outputs.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
WORKFLOWS = ROOT / ".github" / "workflows"
OUT = FOOTBALL / "manifests" / "repository_snapshot_v472.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_files() -> Iterable[Path]:
    for path in FOOTBALL.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel == OUT.relative_to(ROOT).as_posix():
            continue
        if rel.startswith("football-data/migration/"):
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        yield path
    if WORKFLOWS.exists():
        for pattern in ("football*.yml", "football*.yaml"):
            yield from (p for p in WORKFLOWS.glob(pattern) if p.is_file())


def build() -> dict:
    entries = []
    for path in sorted(set(iter_files()), key=lambda p: p.relative_to(ROOT).as_posix()):
        rel = path.relative_to(ROOT).as_posix()
        entries.append({
            "path": rel,
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        })

    digest = hashlib.sha256()
    for item in entries:
        digest.update(f"{item['path']}\t{item['size_bytes']}\t{item['sha256']}\n".encode("utf-8"))

    return {
        "schema_version": "V4.7.2-repository-snapshot",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": os.environ.get("GITHUB_REPOSITORY", "local"),
        "ref": os.environ.get("GITHUB_REF_NAME", "unknown"),
        "scope": "football-data excluding football-data/migration plus football-prefixed workflows",
        "file_count": len(entries),
        "total_size_bytes": sum(item["size_bytes"] for item in entries),
        "snapshot_sha256": digest.hexdigest(),
        "entries": entries,
    }


def main() -> int:
    report = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "repository": report["repository"],
        "file_count": report["file_count"],
        "total_size_bytes": report["total_size_bytes"],
        "snapshot_sha256": report["snapshot_sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
