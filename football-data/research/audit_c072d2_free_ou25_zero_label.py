#!/usr/bin/env python3
import hashlib
import io
import json
import math
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
REPO = "nm2890/football-data"
FILES = [
    "data/england/premier-league.csv",
    "data/spain/laliga.csv",
    "data/italy/serie-a.csv",
    "data/germany/bundesliga.csv",
    "data/france/ligue-1.csv",
    "data/belgium/jupiler-pro-league.csv",
    "data/netherlands/eredivisie.csv",
    "data/egypt/premier-league.csv",
]
ALLOWED = [
    "Date", "country", "league", "Season", "HomeTeam", "AwayTeam",
    "over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close",
]
PRICE = ["over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close"]
IDENTITY = ["Date", "country", "league", "HomeTeam", "AwayTeam"]
OUT = Path("football-data/research/c072d2_free_ou25_zero_label_audit.json")


def raw_url(path: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{REV}/{path}"


def fetch(path: str):
    req = urllib.request.Request(raw_url(path), headers={"User-Agent": "football3-c072d2-zero-label"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    sha = hashlib.sha256(data).hexdigest()
    # Critical leakage guard: only allowed columns may be materialized.
    df = pd.read_csv(io.BytesIO(data), usecols=ALLOWED)
    return data, sha, df


def logit(p):
    return np.log(p / (1.0 - p))


def main():
    frames = []
    file_stats = []
    for path in FILES:
        raw, sha, df = fetch(path)
        missing_cols = [c for c in ALLOWED if c not in df.columns]
        if missing_cols:
            raise RuntimeError(f"{path}: missing required allowed columns {missing_cols}")
        df = df.copy()
        df["_source_file"] = path
        frames.append(df)
        file_stats.append({
            "path": path,
            "bytes": len(raw),
            "sha256": sha,
            "rows": int(len(df)),
            "materialized_columns": list(df.columns[:-1]),
            "forbidden_score_result_columns_materialized": 0,
        })

    all_df = pd.concat(frames, ignore_index=True)
    date = pd.to_datetime(all_df["Date"], errors="coerce", utc=True)
    valid_date = date.notna()

    price_num = all_df[PRICE].apply(pd.to_numeric, errors="coerce")
    complete_valid_price = price_num.notna().all(axis=1) & (price_num > 1.0).all(axis=1)

    valid = price_num.loc[complete_valid_price]
    inv_oo = 1.0 / valid["over_2.5_open"]
    inv_uo = 1.0 / valid["under_2.5_open"]
    inv_oc = 1.0 / valid["over_2.5_close"]
    inv_uc = 1.0 / valid["under_2.5_close"]
    p_open = inv_oo / (inv_oo + inv_uo)
    p_close = inv_oc / (inv_oc + inv_uc)
    movement = p_close - p_open
    movement_logit = logit(p_close) - logit(p_open)

    ident = all_df[IDENTITY].astype("string").fillna("<NA>")
    dup = ident.duplicated(keep=False)
    unique_leagues = all_df[["country", "league"]].astype("string").drop_duplicates()
    seasons = all_df["Season"].astype("string").dropna().unique()

    n = len(all_df)
    n_price = int(complete_valid_price.sum())
    n_move_nonzero = int((movement.abs() > 1e-12).sum())
    summary = {
        "contract": "C072-D2_FREE_OU25_OPEN_CLOSE_ZERO_LABEL",
        "project": "football3",
        "parent_c072c_head": "e3e73c998020beef585cc459a69ea5b73b44ddb3",
        "quarantine_c073_c077": True,
        "external_repo": REPO,
        "external_revision": REV,
        "files_expected": len(FILES),
        "files_parsed": len(file_stats),
        "file_stats": file_stats,
        "total_identity_rows": int(n),
        "valid_date_rows": int(valid_date.sum()),
        "valid_date_fraction": float(valid_date.mean()) if n else 0.0,
        "complete_valid_four_price_rows": n_price,
        "complete_valid_four_price_fraction": float(n_price / n) if n else 0.0,
        "league_count": int(len(unique_leagues)),
        "season_count": int(len(seasons)),
        "duplicate_identity_rows": int(dup.sum()),
        "duplicate_identity_fraction": float(dup.mean()) if n else 0.0,
        "nonzero_movement_rows": n_move_nonzero,
        "nonzero_movement_fraction_among_complete": float(n_move_nonzero / n_price) if n_price else 0.0,
        "mean_abs_de_vig_probability_movement": float(movement.abs().mean()) if n_price else None,
        "median_abs_de_vig_probability_movement": float(movement.abs().median()) if n_price else None,
        "mean_abs_movement_logit": float(movement_logit.abs().mean()) if n_price else None,
        "target_score_result_columns_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
    }

    gates = {
        "all_8_files": len(file_stats) == 8,
        "rows_ge_30000": n >= 30000,
        "valid_date_ge_995pct": summary["valid_date_fraction"] >= 0.995,
        "four_price_ge_80pct": summary["complete_valid_four_price_fraction"] >= 0.80,
        "league_ge_8": summary["league_count"] >= 8,
        "season_ge_12": summary["season_count"] >= 12,
        "duplicate_le_01pct": summary["duplicate_identity_fraction"] <= 0.001,
        "movement_nonzero_ge_5pct": summary["nonzero_movement_fraction_among_complete"] >= 0.05,
        "no_target_materialization": summary["target_score_result_columns_materialized"] == 0,
        "no_model": summary["model_fit"] == 0 and summary["model_score"] == 0,
    }
    summary["gates"] = gates
    summary["all_gates_pass"] = bool(all(gates.values()))
    summary["terminal"] = "COARSE_OU25_OPEN_CLOSE_SOURCE_PASS" if summary["all_gates_pass"] else "SOURCE_GATE_FAIL"
    summary["interpretation_boundary"] = (
        "Coarse average O/U2.5 opening/closing research source only; no immutable quote timestamps, "
        "no multi-line market ladder, not Betfair-equivalent, no model evidence."
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["all_gates_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
