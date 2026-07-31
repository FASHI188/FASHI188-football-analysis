#!/usr/bin/env python3
"""Compatibility CLI using the hardened canonical-digest Artifact core."""
from __future__ import annotations
import e3g0d_archive_core as core
import e3g0d_api_football_archive_helper as legacy

def _legacy_hex_digest(meta, raw):
    """Legacy helper adds the sha256 prefix itself; core remains canonical."""
    return core.artifact_digest(meta, raw).split(':', 1)[1]

legacy.artifact_digest = _legacy_hex_digest

if __name__ == '__main__':
    raise SystemExit(legacy.main())
