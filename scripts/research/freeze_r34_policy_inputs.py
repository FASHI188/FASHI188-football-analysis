#!/usr/bin/env python3
"""Freeze the external byte inputs for the R34 2024/25 policy run.

Research-only. This script downloads fixed historical sources, verifies the known
2023/24 hashes, audits both market files, and emits a receipt. It performs no
model fitting or scoring.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import urllib.request
from pathlib import Path

GOALS_2324_URL = (
    "https://raw.githubusercontent.com/schochastics/football-data/"
    "6ba5e7e8f8657b6ccdeb0e89778765423f8d5aaf/"
    "data/goals_time/eng-premier-league.csv"
)
GOALS_2324_EXPECTED_GIT_BLOB_SHA1 = "9b7d9c4428ab16b509c7de55eaf4c5f9720ff42a"
MARKET_2324_URL = "https://www.football-data.co.uk/mmz4281/2324/E0.csv"
MARKET_2324_EXPECTED_SHA256 = "760f6881175fba2ebccfb89c4a07acbd4172262daee6d07f3baf5dc379242333"
MARKET_2425_URL = "https://www.football-data.co.uk/mmz4281/2425/E0.csv"
REQUIRED_MARKET_COLUMNS = {
    "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
    "AvgCH", "AvgCD", "AvgCA", "AvgC>2.5", "AvgC<2.5",
}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "football-research-audit/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def git_blob_sha1(payload: bytes) -> str:
    return hashlib.sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()


def decode_csv(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("unable to decode CSV")


def audit_market(payload: bytes, season: str) -> dict[str, object]:
    text = decode_csv(payload)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"{season}: empty market file")
    columns = set(rows[0])
    missing = sorted(REQUIRED_MARKET_COLUMNS - columns)
    if missing:
        raise ValueError(f"{season}: missing market columns {missing}")
    if len(rows) != 380:
        raise ValueError(f"{season}: expected 380 rows, found {len(rows)}")
    fixtures = {(row["HomeTeam"], row["AwayTeam"]) for row in rows}
    if len(fixtures) != 380:
        raise ValueError(f"{season}: directed fixtures are not unique")
    for i, row in enumerate(rows, start=1):
        for col in REQUIRED_MARKET_COLUMNS:
            if row.get(col, "") == "":
                raise ValueError(f"{season}: row {i} empty required column {col}")
        inv = [1.0 / float(row[c]) for c in ("AvgCH", "AvgCD", "AvgCA")]
        if not all(value > 0 for value in inv):
            raise ValueError(f"{season}: row {i} invalid 1X2 prices")
    return {
        "rows": len(rows),
        "unique_directed_fixtures": len(fixtures),
        "first_date": rows[0]["Date"],
        "last_date": rows[-1]["Date"],
        "columns": sorted(columns),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    goals = fetch(GOALS_2324_URL)
    market_2324 = fetch(MARKET_2324_URL)
    market_2425 = fetch(MARKET_2425_URL)

    actual_goals_blob = git_blob_sha1(goals)
    actual_market_2324_sha = sha256(market_2324)
    if actual_goals_blob != GOALS_2324_EXPECTED_GIT_BLOB_SHA1:
        raise ValueError(
            f"2023/24 goals blob mismatch: expected {GOALS_2324_EXPECTED_GIT_BLOB_SHA1}, "
            f"actual {actual_goals_blob}"
        )
    if actual_market_2324_sha != MARKET_2324_EXPECTED_SHA256:
        raise ValueError(
            f"2023/24 market hash mismatch: expected {MARKET_2324_EXPECTED_SHA256}, "
            f"actual {actual_market_2324_sha}"
        )

    audit_2324 = audit_market(market_2324, "2023/24")
    audit_2425 = audit_market(market_2425, "2024/25")

    files = {
        "goals_2324_raw.csv": goals,
        "market_2324_E0.csv": market_2324,
        "market_2425_E0.csv": market_2425,
    }
    for name, payload in files.items():
        (args.output_dir / name).write_bytes(payload)

    receipt = {
        "receipt_id": "R34_POLICY_2425_INPUT_FREEZE_V1",
        "status": "PASS",
        "research_only": True,
        "formal_weight": 0,
        "model_scoring_performed": False,
        "sources": {
            "goals_2324": {
                "url": GOALS_2324_URL,
                "expected_git_blob_sha1": GOALS_2324_EXPECTED_GIT_BLOB_SHA1,
                "actual_git_blob_sha1": actual_goals_blob,
                "sha256": sha256(goals),
                "bytes": len(goals),
            },
            "market_2324": {
                "url": MARKET_2324_URL,
                "expected_sha256": MARKET_2324_EXPECTED_SHA256,
                "actual_sha256": actual_market_2324_sha,
                "bytes": len(market_2324),
                "audit": audit_2324,
            },
            "market_2425": {
                "url": MARKET_2425_URL,
                "actual_sha256": sha256(market_2425),
                "bytes": len(market_2425),
                "audit": audit_2425,
            },
        },
        "formal_changes": {"model": 0, "formal_data": 0, "config": 0, "CURRENT": 0},
    }
    (args.output_dir / "input_freeze_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
