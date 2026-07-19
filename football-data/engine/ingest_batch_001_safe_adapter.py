#!/usr/bin/env python3
"""Non-destructive execution wrapper for batch-001 ingestion.

The legacy batch-001 core atomically replaced the *entire* raw, processed and
league_profiles roots after rebuilding only batch-001 competitions. In a shared
17-domain repository that deletes batch-002 and Norway assets. This wrapper
preserves the validated parser/profile logic while changing root publication to
competition-by-competition atomic replacement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import ingest_batch_001_alias_adapter as ALIAS

ORIGINAL_ATOMIC_REPLACE = ALIAS.CORE.atomic_replace_dir
MANAGED_ROOT_NAMES = {"raw", "processed", "league_profiles"}


def merge_competition_tree(staged_root: Path, destination_root: Path) -> None:
    """Publish only competition directories present in this batch.

    Existing competition directories not present in staged_root are preserved.
    Each generated competition directory is still replaced atomically using the
    original audited helper.
    """
    destination_root.mkdir(parents=True, exist_ok=True)
    if not staged_root.exists():
        return
    for competition_dir in sorted(list(staged_root.iterdir()), key=lambda p: p.name):
        if not competition_dir.is_dir():
            continue
        ORIGINAL_ATOMIC_REPLACE(competition_dir, destination_root / competition_dir.name)


def safe_atomic_replace(staged: Path, destination: Path) -> None:
    if destination.parent == ALIAS.CORE.ROOT and destination.name in MANAGED_ROOT_NAMES:
        merge_competition_tree(staged, destination)
        return
    ORIGINAL_ATOMIC_REPLACE(staged, destination)


ALIAS.CORE.atomic_replace_dir = safe_atomic_replace


def main() -> int:
    return ALIAS.main()


if __name__ == "__main__":
    raise SystemExit(main())
