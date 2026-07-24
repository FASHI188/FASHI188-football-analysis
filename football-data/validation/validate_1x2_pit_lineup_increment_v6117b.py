#!/usr/bin/env python3
"""V6.11.7b: rerun the unchanged PIT-lineup experiment with normalized date keys.

The original v6117 used parse_match_date(...).isoformat(), producing
YYYY-MM-DDT00:00:00+00:00, while historical lineup keys use YYYY-MM-DD.
This wrapper changes only that join key; model features, splits and gates remain
unchanged.
"""
from __future__ import annotations

from pathlib import Path
import validate_1x2_pit_lineup_increment_v6117 as base

_original_load_matches = base._load_matches


def _load_matches_date_key_fixed():
    rows = _original_load_matches()
    for r in rows:
        r["date"] = str(r["date"])[:10]
    return rows


base._load_matches = _load_matches_date_key_fixed
base.OUT = Path(__file__).resolve().parents[1] / "manifests" / "v6_1x2_pit_lineup_increment_v6117b_status.json"

if __name__ == "__main__":
    raise SystemExit(base.main())
