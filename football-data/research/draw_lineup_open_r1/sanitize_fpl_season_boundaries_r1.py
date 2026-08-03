#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

FIELDS = [
    "starter_continuity", "starter_changes",
    "gk_changes", "gk_changed", "def_changes", "def_changed",
    "mid_changes", "mid_changed", "fwd_changes", "fwd_changed",
]


def hf(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with args.input.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0]) if rows else []
    rows.sort(key=lambda r: (r["season"], r["date"], int(r["fixture_id"])))
    seen = set()
    cleared = 0
    for row in rows:
        for side in ("home", "away"):
            key = (row["season"], row[f"{side}_team"])
            if key not in seen:
                for suffix in FIELDS:
                    field = f"{side}_{suffix}"
                    if field in row:
                        row[field] = ""
                seen.add(key)
                cleared += 1

    output = args.out / "FPL_STATIC_LINEUP_R1_matches_sanitized.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    receipt = {
        "schema_version": "FPL-SEASON-BOUNDARY-SANITIZER-R1",
        "rows": len(rows),
        "team_season_first_matches_cleared": cleared,
        "reason": "FPL element identifiers reset between seasons; no cross-season continuity comparison",
        "output_sha256": hf(output),
    }
    (args.out / "FPL_STATIC_LINEUP_R1_sanitizer_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
