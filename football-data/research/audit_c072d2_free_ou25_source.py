#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import math
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
BASE = f"https://raw.githubusercontent.com/nm2890/football-data/{REV}/"
FILES = [
    "data/belgium/jupiler-pro-league.csv",
    "data/egypt/premier-league.csv",
    "data/england/premier-league.csv",
    "data/france/ligue-1.csv",
    "data/germany/bundesliga.csv",
    "data/italy/serie-a.csv",
    "data/netherlands/eredivisie.csv",
    "data/spain/laliga.csv",
]
USE = ["Date", "Season", "HomeTeam", "AwayTeam", "over_2.5_open", "over_2.5_close", "under_2.5_open", "under_2.5_close"]


def devig_over(o, u):
    po = 1.0 / o
    pu = 1.0 / u
    return po / (po + pu)


def main():
    frames = []
    per_file = []
    for path in FILES:
        raw = urlopen(BASE + path, timeout=60).read()
        df = pd.read_csv(io.BytesIO(raw), usecols=USE)
        df["source_file"] = path
        date = pd.to_datetime(df["Date"], errors="coerce", utc=True)
        prices = df[["over_2.5_open", "over_2.5_close", "under_2.5_open", "under_2.5_close"]].apply(pd.to_numeric, errors="coerce")
        complete = np.isfinite(prices).all(axis=1) & (prices > 1.0).all(axis=1)
        cov = float(complete.mean()) if len(df) else 0.0
        per_file.append({"file": path, "rows": int(len(df)), "valid_date_rate": float(date.notna().mean()), "four_price_coverage": cov})
        df["_valid_date"] = date.notna()
        df["_complete"] = complete
        for c in prices.columns:
            df[c] = prices[c]
        frames.append(df)

    all_df = pd.concat(frames, ignore_index=True)
    ids = all_df[["source_file", "Date", "HomeTeam", "AwayTeam"]].astype(str)
    duplicates = int(ids.duplicated().sum())
    valid_date_rate = float(all_df["_valid_date"].mean())
    complete = all_df["_complete"].to_numpy(bool)
    complete_rate = float(complete.mean())

    g = all_df.loc[complete]
    p_open = devig_over(g["over_2.5_open"].to_numpy(float), g["under_2.5_open"].to_numpy(float))
    p_close = devig_over(g["over_2.5_close"].to_numpy(float), g["under_2.5_close"].to_numpy(float))
    movement = p_close - p_open
    nonzero_move_rate = float(np.mean(np.abs(movement) > 1e-9)) if len(movement) else 0.0
    file_good = sum(x["four_price_coverage"] >= 0.70 for x in per_file)

    gates = {
        "files_exactly_8": len(per_file) == 8,
        "rows_ge_30000": len(all_df) >= 30000,
        "duplicates_zero": duplicates == 0,
        "valid_dates_ge_0_995": valid_date_rate >= 0.995,
        "four_price_coverage_ge_0_80": complete_rate >= 0.80,
        "nonzero_movement_ge_0_05": nonzero_move_rate >= 0.05,
        "files_with_coverage_ge_0_70_at_least_6": file_good >= 6,
        "target_result_columns_materialized_zero": True,
        "model_fit_zero": True,
    }
    out = {
        "schema": "C072D2_FREE_OU25_ZERO_LABEL_SOURCE_AUDIT_V1",
        "project_line": "football3",
        "parent_head": "e3e73c998020beef585cc459a69ea5b73b44ddb3",
        "quarantined": "C073-C077",
        "source_revision": REV,
        "files": per_file,
        "rows": int(len(all_df)),
        "duplicates": duplicates,
        "valid_date_rate": valid_date_rate,
        "four_price_coverage": complete_rate,
        "nonzero_devig_over_movement_rate": nonzero_move_rate,
        "mean_abs_devig_over_movement": float(np.mean(np.abs(movement))) if len(movement) else None,
        "files_ge_70pct_coverage": int(file_good),
        "target_result_columns_materialized": 0,
        "model_fit": 0,
        "pit_classification": "COARSE_OPEN_CLOSE_SEMANTICS_ONLY_NO_IMMUTABLE_QUOTE_TIMESTAMPS",
        "gates": gates,
        "terminal": "SOURCE_COVERAGE_PASS_COARSE_PIT_ONLY" if all(gates.values()) else "STOP_SOURCE_COVERAGE",
    }
    p = Path("football-data/research/c072d2_source_audit_summary.json")
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
