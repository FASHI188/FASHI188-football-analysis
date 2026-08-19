#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

CODES = ["E1","E2","E3","SC0","SC1","SC2","SC3","D2","I2","SP2","F2","P1"]
ID_COLS = ["Date","HomeTeam","AwayTeam"]
MARKET_COLS = ["Avg>2.5","Avg<2.5","AvgC>2.5","AvgC<2.5"]
OPTIONAL_COLS = ["Div","Time"]
EXPECTED_IDENTITY_COUNT = 4567
EXPECTED_IDENTITY_SHA = "fea3360a19094337579f1348858c7298e0b1bce1a177174cafc8e31dfd12c710"
SPLIT_DATE = pd.Timestamp("2026-01-01")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def ids_sha(keys: list[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(keys)) + "\n").encode()).hexdigest()


def header_only(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return next(csv.reader(f))


def devig_over(over, under):
    io = 1.0 / over
    iu = 1.0 / under
    return io / (io + iu)


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-9, 1 - 1e-9)
    return np.log(p / (1 - p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    root = Path(args.source_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    all_keys: list[str] = []
    reports = {}
    total_market_valid = 0
    total_identity = 0
    total_date_valid = 0
    total_nonzero_move = 0
    early_valid = 0
    late_valid = 0
    files_ge70 = 0

    for code in CODES:
        p = root / f"{code}.csv"
        if not p.is_file() or p.stat().st_size == 0:
            raise RuntimeError(f"missing/empty frozen file {code}")
        header = header_only(p)
        missing = [c for c in ID_COLS + MARKET_COLS if c not in header]
        if missing:
            reports[code] = {
                "missing_required_columns": missing,
                "raw_sha256": sha256(p),
                "raw_bytes": p.stat().st_size,
            }
            continue

        # Critical boundary: outcome/result columns are not selected into pandas at all.
        usecols = ID_COLS + MARKET_COLS + [c for c in OPTIONAL_COLS if c in header]
        d = pd.read_csv(p, usecols=usecols, dtype=str, low_memory=False)
        if any(c in d.columns for c in ["FTHG","FTAG","FTR","HTHG","HTAG","HTR"]):
            raise RuntimeError("outcome column materialized")

        idmask = d[ID_COLS].notna().all(axis=1)
        for c in ID_COLS:
            idmask &= d[c].astype(str).str.strip().ne("")
        di = d.loc[idmask].copy()
        keys = [f"{code}|{r.Date.strip()}|{r.HomeTeam.strip()}|{r.AwayTeam.strip()}" for r in di[ID_COLS].itertuples(index=False)]
        all_keys.extend(keys)
        n = len(di)
        total_identity += n

        dates = pd.to_datetime(di["Date"], errors="coerce", dayfirst=True)
        date_valid = dates.notna()
        total_date_valid += int(date_valid.sum())

        numeric = pd.DataFrame({c: pd.to_numeric(di[c], errors="coerce") for c in MARKET_COLS}, index=di.index)
        market_valid = numeric.notna().all(axis=1) & (numeric > 1.0).all(axis=1) & date_valid
        mv = int(market_valid.sum())
        total_market_valid += mv
        coverage = mv / n if n else 0.0
        files_ge70 += int(coverage >= 0.70)

        if mv:
            ix = market_valid[market_valid].index
            po = devig_over(numeric.loc[ix, "Avg>2.5"].to_numpy(float), numeric.loc[ix, "Avg<2.5"].to_numpy(float))
            pc = devig_over(numeric.loc[ix, "AvgC>2.5"].to_numpy(float), numeric.loc[ix, "AvgC<2.5"].to_numpy(float))
            movement = logit(pc) - logit(po)
            nonzero = int(np.sum(np.abs(movement) > 1e-12))
            total_nonzero_move += nonzero
            dvalid = dates.loc[ix]
            early = int((dvalid < SPLIT_DATE).sum())
            late = int((dvalid >= SPLIT_DATE).sum())
            early_valid += early
            late_valid += late
            mean_abs = float(np.mean(np.abs(movement)))
        else:
            nonzero = early = late = 0
            mean_abs = 0.0

        reports[code] = {
            "identity_count": int(n),
            "identity_sha256": ids_sha(keys),
            "valid_date_count": int(date_valid.sum()),
            "market_valid_count": int(mv),
            "market_valid_fraction": float(coverage),
            "nonzero_movement_count": int(nonzero),
            "mean_abs_movement_logit": mean_abs,
            "early_market_valid_count": int(early),
            "late_market_valid_count": int(late),
            "raw_sha256": sha256(p),
            "raw_bytes": p.stat().st_size,
            "selected_columns": usecols,
            "target_result_columns_materialized": 0,
        }

    duplicate = len(all_keys) - len(set(all_keys))
    identity_sha = ids_sha(all_keys)
    market_fraction = total_market_valid / total_identity if total_identity else 0.0
    date_fraction = total_date_valid / total_identity if total_identity else 0.0
    nonzero_rate = total_nonzero_move / total_market_valid if total_market_valid else 0.0
    all_files_have_market_cols = len(reports) == 12 and all("missing_required_columns" not in v for v in reports.values())

    gate = {
        "fixed_file_count_12": len(reports) == 12,
        "identity_count_exact_4567": total_identity == EXPECTED_IDENTITY_COUNT,
        "identity_sha_exact_C076D": identity_sha == EXPECTED_IDENTITY_SHA,
        "duplicate_identity_count_zero": duplicate == 0,
        "all_four_market_columns_every_file": all_files_have_market_cols,
        "valid_identity_dates_ge_0_995": date_fraction >= 0.995,
        "market_valid_count_ge_3500": total_market_valid >= 3500,
        "market_valid_fraction_ge_0_75": market_fraction >= 0.75,
        "files_with_market_coverage_ge_0_70_ge_8": files_ge70 >= 8,
        "nonzero_movement_rate_ge_0_05": nonzero_rate >= 0.05,
        "early_market_valid_count_ge_1400": early_valid >= 1400,
        "late_market_valid_count_ge_1400": late_valid >= 1400,
        "target_result_columns_materialized_zero": all(v.get("target_result_columns_materialized", 0) == 0 for v in reports.values()),
    }
    passed = all(gate.values())
    summary = {
        "schema_version": "C078C_FRESH_MARKET_ZERO_LABEL_GATE_V1",
        "status": "PASS_FRESH_MARKET_ZERO_LABEL_GATE" if passed else "STOP_SOURCE_MARKET_COVERAGE",
        "fixed_codes": CODES,
        "identity_count": int(total_identity),
        "identity_sha256": identity_sha,
        "duplicate_identity_count": int(duplicate),
        "valid_date_fraction": float(date_fraction),
        "market_valid_count": int(total_market_valid),
        "market_valid_fraction": float(market_fraction),
        "files_market_coverage_ge_0_70": int(files_ge70),
        "nonzero_movement_rate": float(nonzero_rate),
        "early_market_valid_count": int(early_valid),
        "late_market_valid_count": int(late_valid),
        "split_date": "2026-01-01",
        "files": reports,
        "gate": gate,
        "label_boundary": {
            "target_result_columns_materialized": 0,
            "FTHG_FTAG_numeric_conversion": False,
            "FTHG_FTAG_values_stored": False,
            "FTHG_FTAG_values_hashed": False,
            "goal_totals_computed": False,
            "goal_difference_computed": False,
            "tail_membership_computed": False,
            "model_fit": False,
            "only_identity_date_and_market_price_values_used": True,
        },
        "hard_boundaries": {
            "C076D_score_values_opened": False,
            "C077B_labels_read": False,
            "C071_reserve52180_opened": False,
            "C070F1597_opened": False,
            "A05_or_protected_opened": False,
            "formal_weight": 0,
            "CURRENT_change": False,
            "unified_matrix_generated": False,
        },
        "next_if_pass": "freeze a separate calibration-to-confirmation scientific contract before any C076-D numeric score value is opened",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
