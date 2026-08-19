#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

EXPECTED_CSV_SHA = "e9538997e1ec46582e240add8eb37372341a0c75b51e024c8bef0139aa29c082"
EXPECTED_IDENTITY_SHA = "95ff10827e5097158c2bf20838e317c106d0b53c8ad6088a50fecae99b6ad0f4"
EXPECTED_ROWS = 18768
EXPECTED_RUN = 32244931845
DATE_FORMATS = (
    "%d-%m-%y %H:%M",
    "%d-%m-%Y %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%Y-%m-%d %H:%M:%S",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_date(value: str) -> bool:
    s = str(value).strip()
    if not s:
        return False
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            pass
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def identity_sha(rows: list[dict[str, str]]) -> str:
    lines = [
        "|".join([
            r.get("sourceCode", ""), r.get("id", ""), r.get("matchDate", ""),
            r.get("League", ""), r.get("Season", ""), r.get("homeTeam", ""), r.get("awayTeam", "")
        ])
        for r in rows
    ]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    csv_path, summary_path, out_path = map(Path, (args.csv, args.summary, args.out))

    csv_sha = sha256(csv_path)
    if csv_sha != EXPECTED_CSV_SHA:
        raise RuntimeError(f"frozen CSV SHA mismatch: {csv_sha}")

    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"frozen row-count mismatch: {len(rows)}")
    id_sha = identity_sha(rows)
    if id_sha != EXPECTED_IDENTITY_SHA:
        raise RuntimeError(f"frozen identity SHA mismatch: {id_sha}")

    old = json.loads(summary_path.read_text(encoding="utf-8"))
    if old.get("retained_rows") != EXPECTED_ROWS:
        raise RuntimeError("summary row-count mismatch")
    if old.get("dataset_sha256") != EXPECTED_CSV_SHA:
        raise RuntimeError("summary CSV SHA mismatch")
    if old.get("ordered_identity_sha256") != EXPECTED_IDENTITY_SHA:
        raise RuntimeError("summary identity SHA mismatch")

    # Guard every non-date scientific/source-quality value from the frozen run.
    expected_audit = {
        "complete_identity_fraction": 1.0,
        "duplicate_fraction": 0.0,
        "duplicate_rows": 0,
        "season_count": 11,
        "ou25_coverage": 0.9999467178175618,
        "joint_ou15_25_35_coverage": 0.9988810741687979,
        "all_five_line_coverage": 0.954923273657289,
        "all_five_monotone_fraction": 0.9999442026559536,
    }
    for k, v in expected_audit.items():
        if old.get("audit", {}).get(k) != v:
            raise RuntimeError(f"frozen non-date audit mismatch for {k}: {old.get('audit', {}).get(k)}")
    if old.get("football_table_data_requests_made") != 41:
        raise RuntimeError("frozen request-count mismatch")
    if old.get("target_result_columns_requested_or_materialized") != 0 or old.get("model_fit") != 0 or old.get("model_score") != 0:
        raise RuntimeError("frozen boundary mismatch")

    valid_n = sum(valid_date(r.get("matchDate", "")) for r in rows)
    valid_fraction = valid_n / len(rows) if rows else 0.0

    corrected = json.loads(json.dumps(old))
    corrected["schema"] = "C072N8_MULTILINE_ODDS_ZERO_LABEL_V1R1_DATE_PARSER_CORRECTED"
    corrected["implementation_correction"] = "C072N8_IMPLEMENTATION_CORRECTION_01"
    corrected["adjudicated_from_run_id"] = EXPECTED_RUN
    corrected["offline_reaudit_new_footiqo_requests"] = 0
    corrected["date_parser_formats"] = list(DATE_FORMATS)
    corrected["audit"]["valid_date_rows"] = valid_n
    corrected["audit"]["valid_date_fraction"] = valid_fraction
    corrected["gates"]["valid_date_ge_995pct"] = valid_fraction >= 0.995
    corrected["terminal"] = (
        "C072N8_MULTILINE_ODDS_ZERO_LABEL_PASS"
        if all(corrected["gates"].values())
        else "C072N8_ZERO_LABEL_DATA_QUALITY_FAIL"
    )
    corrected["correction_integrity"] = {
        "csv_sha256_verified": csv_sha,
        "ordered_identity_sha256_verified": id_sha,
        "row_count_verified": len(rows),
        "all_non_date_audit_values_frozen": True,
        "new_source_requests": 0,
        "target_result_values_materialized": 0,
        "model_fit": 0,
    }

    rendered = json.dumps(corrected, ensure_ascii=False, indent=2, sort_keys=True)
    out_path.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
