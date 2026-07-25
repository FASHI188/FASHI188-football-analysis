#!/usr/bin/env python3
"""Execution-equivalent cached wrapper for V6.25.8.

The statistical definition is unchanged. This wrapper memoizes each
(competition, season) strict-PIT dynamic-shot row set so nested folds do not
rebuild identical historical seasons repeatedly.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_pooled_dynamic_shot_23split_v6258 as base

_original_load = base._load
_cache: dict[tuple[str, str], list[dict]] = {}


def _cached_load(ctx, season):
    key = (str(ctx["cid"]), str(season))
    if key not in _cache:
        _cache[key] = _original_load(ctx, season)
    return _cache[key]


base._load = _cached_load

if __name__ == "__main__":
    raise SystemExit(base.main())
