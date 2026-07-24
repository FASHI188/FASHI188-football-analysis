#!/usr/bin/env python3
"""V6.11.7b: rerun unchanged PIT-lineup experiment with normalized join keys.

The base v6117 experiment is unchanged. This wrapper fixes only joins:
- market match date: YYYY-MM-DDT00:00:00+00:00 -> YYYY-MM-DD
- team names: arbitrary source labels (e.g. Arsenal / Arsenal FC) -> the shared
  normalize_team_token representation.
"""
from __future__ import annotations

from pathlib import Path
import validate_1x2_pit_lineup_increment_v6117 as base
from platform_core import normalize_team_token

_original_load_matches = base._load_matches
_original_load_lineups = base._load_lineups


def _load_matches_join_fixed():
    rows = _original_load_matches()
    for r in rows:
        r["date"] = str(r["date"])[:10]
        r["home"] = normalize_team_token(r["home"])
        r["away"] = normalize_team_token(r["away"])
    return rows


def _load_lineups_join_fixed(cid: str):
    raw = _original_load_lineups(cid)
    fixed = {}
    for (season, date, team), item in raw.items():
        fixed[(season, date, normalize_team_token(team))] = item
    return fixed


base._load_matches = _load_matches_join_fixed
base._load_lineups = _load_lineups_join_fixed
base.OUT = Path(__file__).resolve().parents[1] / "manifests" / "v6_1x2_pit_lineup_increment_v6117b_status.json"

if __name__ == "__main__":
    raise SystemExit(base.main())
