#!/usr/bin/env python3
"""Pre-score static fix wrapper for V6.18.1.

The base V6.18.1 shot lookup is intentionally date-keyed (YYYY-MM-DD), while the
formal MatchRow uses UTC-midnight datetime ISO strings. This wrapper adds the
UTC-midnight aliases before the formal join, then records the fix in the receipt.
No scored V6.18.1 receipt existed when this fix was authored.
"""
from __future__ import annotations

import json
from pathlib import Path

import v6_total_shot_residual_v6181 as base

_original_lagged = base.lagged_shot_lookup


def lagged_shot_lookup_fixed(raw_rows):
    lookup, names = _original_lagged(raw_rows)
    expanded = dict(lookup)
    for (cid, season, date_token, home, away), feat in list(lookup.items()):
        if "T" not in date_token:
            expanded[(cid, season, f"{date_token}T00:00:00+00:00", home, away)] = feat
    return expanded, names


def main():
    base.lagged_shot_lookup = lagged_shot_lookup_fixed
    code = base.main()
    path = Path(base.OUT)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "V6.18.1-shot-informed-direct-total-r2"
        payload["pretest_static_audit_revision"] = [
            "align date-only lagged-shot keys with UTC-midnight MatchRow ISO keys before formal join"
        ]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
