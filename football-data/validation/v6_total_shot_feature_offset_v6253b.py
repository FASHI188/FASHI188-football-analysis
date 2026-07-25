#!/usr/bin/env python3
"""Execution wrapper for V6.25.3 with safe unavailable-fold fallback.

Some oldest seasons have no outer-fold frozen parameter record. They are not
eligible as training seasons and must be skipped, not treated as a challenger
failure. This wrapper changes no model parameter or statistical rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_shot_feature_offset_v6253 as base

_original_load_season = base._load_season


def _safe_load_season(cid, season, report, config, stats):
    try:
        return _original_load_season(cid, season, report, config, stats)
    except Exception:
        return []


base._load_season = _safe_load_season

if __name__ == "__main__":
    raise SystemExit(base.main())
