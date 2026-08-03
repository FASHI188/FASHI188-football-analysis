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
        source_rows = list(csv.DictReader(handle))
        fields = list(source_rows[0]) if source_rows else []

    malformed = [
        row for row in source_rows
        if int(row["home_starter_count"]) != 11 or int(row["away_starter_count"]) != 11
    ]
    rows = [row for row in source_rows if row not in malformed]
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

    malformed_path = args.out / "FPL_STATIC_LINEUP_R1_malformed_start_counts.csv"
    with malformed_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(malformed)

    receipt = {
        "schema_version": "FPL-SEASON-BOUNDARY-SANITIZER-R1",
        "source_rows": len(source_rows),
        "usable_rows": len(rows),
        "malformed_matches_excluded": len(malformed),
        "malformed_home_sides": sum(int(r["home_starter_count"]) != 11 for r in malformed),
        "malformed_away_sides": sum(int(r["away_starter_count"]) != 11 for r in malformed),
        "team_season_first_matches_cleared": cleared,
        "reason": "FPL element identifiers reset between seasons; malformed starting-XI counts are retained in evidence but excluded from modeling",
        "output_sha256": hf(output),
        "malformed_evidence_sha256": hf(malformed_path),
    }
    (args.out / "FPL_STATIC_LINEUP_R1_sanitizer_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
