#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

IDENTITY_COLS = ["Season", "Div", "Date", "Time", "HomeTeam", "AwayTeam"]


def htxt(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def valid_odd(v: str) -> bool:
    try:
        x = float(str(v).strip())
    except Exception:
        return False
    return math.isfinite(x) and x > 1.0


def valid_line(v: str) -> bool:
    try:
        x = float(str(v).strip())
    except Exception:
        return False
    return math.isfinite(x) and -5.0 <= x <= 5.0


def row_complete(row: dict[str, str], cols: list[str]) -> bool:
    for c in cols:
        if c in {"AHh", "AHCh"}:
            if not valid_line(row.get(c, "")):
                return False
        elif not valid_odd(row.get(c, "")):
            return False
    return True


def identity(row: dict[str, str]) -> str:
    return "|".join(str(row.get(c, "")).strip() for c in IDENTITY_COLS)


def set_sha(ids: list[str]) -> str:
    return htxt("\n".join(sorted(ids)) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registration", type=Path, required=True)
    ap.add_argument("--sanitized-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    reg = json.loads(args.registration.read_text(encoding="utf-8"))
    lanes = {x["id"]: x["required_columns"] for x in reg["frozen_lanes"]}
    counts = {k: {"preholdout": 0, "holdout": 0, "total": 0} for k in lanes}
    file_schema = {}
    eligible_by_lane: dict[str, list[dict[str, str]]] = defaultdict(list)
    rows_scanned = 0
    duplicate_ids = 0
    seen: set[str] = set()

    for p in sorted(args.sanitized_dir.glob("*.csv")):
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            forbidden = {"FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR"}
            if forbidden.intersection(header):
                raise RuntimeError(f"result columns present in sanitized input {p.name}: {sorted(forbidden.intersection(header))}")
            file_schema[p.name] = {
                "columns": header,
                "lane_schema_available": {k: all(c in header for c in cols) for k, cols in lanes.items()},
            }
            for row in reader:
                rows_scanned += 1
                ident = identity(row)
                if not all(row.get(c, "").strip() for c in IDENTITY_COLS):
                    continue
                if ident in seen:
                    duplicate_ids += 1
                    continue
                seen.add(ident)
                season = row["Season"].strip()
                for lane, cols in lanes.items():
                    if not file_schema[p.name]["lane_schema_available"][lane]:
                        continue
                    if row_complete(row, cols):
                        counts[lane]["total"] += 1
                        bucket = "holdout" if season == reg["identity_lock"]["holdout_season"] else "preholdout"
                        counts[lane][bucket] += 1
                        eligible_by_lane[lane].append({"identity": ident, "season": season})

    sel = reg["lane_selection"]
    chosen = None
    for lane in sel["preferred_order"]:
        c = counts[lane]
        if c["preholdout"] >= int(sel["minimum_preholdout_complete_rows"]) and c["holdout"] >= int(sel["minimum_holdout_pool_rows_2425"]):
            chosen = lane
            break

    locked = []
    locked_sha = None
    if chosen:
        pool = [x for x in eligible_by_lane[chosen] if x["season"] == reg["identity_lock"]["holdout_season"]]
        seed = int(reg["identity_lock"]["seed"])
        n = int(reg["identity_lock"]["rows"])
        locked = sorted(pool, key=lambda x: htxt(f"{seed}|{x['identity']}"))[:n]
        if len(locked) != n:
            raise RuntimeError(f"only {len(locked)} lockable rows")
        locked_sha = set_sha([x["identity"] for x in locked])
        status = "PASS_R39G_CROSSMARKET_SOURCE_AND_FIXED100_LOCK_NO_LABELS"
    else:
        status = "STOP_R39G_CROSSMARKET_SOURCE_COVERAGE_INSUFFICIENT"

    out = {
        "schema_version": reg["schema_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "rows_scanned": rows_scanned,
        "duplicate_identity_rows": duplicate_ids,
        "lane_counts": counts,
        "selected_lane": chosen,
        "locked_fixed100_rows": len(locked),
        "locked_fixed100_identity_sha256": locked_sha,
        "locked_holdout_season": reg["identity_lock"]["holdout_season"],
        "file_schema": file_schema,
        "prior_beat_the_bookie_blind_set": reg["prior_blind_set"],
        "no_label_audit": {
            "result_columns_present_in_audit_input": False,
            "FTHG_accessed": 0,
            "FTAG_accessed": 0,
            "FTR_accessed": 0,
            "model_fit": 0,
            "prediction_metrics": 0,
            "threshold_selection": 0,
            "holdout_labels_accessed": 0,
        },
        "hard_limits": reg["hard_limits"],
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "source_audit_and_identity_lock_r39g.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
