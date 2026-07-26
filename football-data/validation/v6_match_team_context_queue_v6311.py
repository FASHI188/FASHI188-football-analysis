#!/usr/bin/env python3
"""V6.31.1 validator wrapper for compact hash-bound roster references."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import v6_match_team_context_queue_v631 as base

SNAPSHOT_ROOT = base.ROOT / "evidence" / "team_configuration_weekly"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
_old_validate_axis = base.validate_axis


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_axis_v2(team: dict[str, Any], axis: str) -> list[str]:
    if axis != "roster":
        return _old_validate_axis(team, axis)
    errors = []
    item = team.get("roster")
    if not isinstance(item, dict):
        return ["missing_roster"]
    status = str(item.get("status") or "")
    if not status:
        errors.append("roster_missing_status")
    sources = item.get("sources") or []
    if not isinstance(sources, list):
        errors.append("roster_sources_not_list")
        sources = []
    if status == "STRICT_CURRENT":
        players = item.get("players")
        inline_ok = isinstance(players, list) and len(players) >= 18
        count = int(item.get("player_count") or 0)
        filename = str(item.get("snapshot_file") or "")
        digest = str(item.get("snapshot_sha256") or "").lower()
        ref_ok = count >= 18 and bool(filename) and bool(HEX64.fullmatch(digest))
        if ref_ok:
            path = SNAPSHOT_ROOT / filename
            if not path.is_file():
                errors.append("strict_roster_snapshot_file_missing")
            elif sha256_file(path) != digest:
                errors.append("strict_roster_snapshot_sha256_mismatch")
        if not inline_ok and not ref_ok:
            errors.append("strict_roster_evidence_below_18_or_unbound")
        if not sources:
            errors.append("strict_roster_without_source")
    return errors


base.validate_axis = validate_axis_v2

if __name__ == "__main__":
    raise SystemExit(base.main())
