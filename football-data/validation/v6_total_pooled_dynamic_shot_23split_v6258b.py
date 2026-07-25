#!/usr/bin/env python3
"""Execution-equivalent cached wrapper for V6.25.8.

The statistical definition is unchanged. This wrapper memoizes each
(competition, season) strict-PIT dynamic-shot row set so nested folds do not
rebuild identical historical seasons repeatedly. If execution fails before the
base manifest is produced, a failure receipt is written to the same manifest
path so the cause is auditable.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
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


def main() -> int:
    try:
        return int(base.main())
    except Exception as exc:
        payload = {
            "schema_version": "V6.25.8b-pooled-dynamic-shot-2v3-execution-receipt-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "FAIL",
            "classification": "RESEARCH_CHALLENGER_EXECUTION_RECEIPT_FORMAL_WEIGHT_0",
            "statistical_definition": "identical_to_V6.25.8",
            "season_row_cache_enabled": True,
            "cache_entries_built": len(_cache),
            "error": f"{type(exc).__name__}: {exc}",
            "formal_weight": 0,
            "current_rule_change": False,
        }
        base.OUT.parent.mkdir(parents=True, exist_ok=True)
        base.OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
