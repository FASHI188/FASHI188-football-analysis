#!/usr/bin/env python3
"""C074-C zero-label source gate for free O/U2.5 open->close history.

Research-only / formal_weight=0.
This audit intentionally loads only non-target columns from the external CSVs.
It does not fit a model and does not expose FTHG/FTAG or any result label.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

USECOLS = [
    "Date",
    "country",
    "league",
    "Season",
    "over_2.5_open",
    "under_2.5_open",
    "over_2.5_close",
    "under_2.5_close",
]
ODD_COLS = USECOLS[4:]


def devig_over(over_odds: pd.Series, under_odds: pd.Series) -> pd.Series:
    io = 1.0 / over_odds
    iu = 1.0 / under_odds
    return io / (io + iu)


def q(series: pd.Series, p: float) -> float | None:
    s = series.dropna()
    if s.empty:
        return None
    return float(s.quantile(p))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--external-root", required=True)
    ap.add_argument("--out", default="artifacts/c074c_source_gate")
    args = ap.parse_args()

    root = Path(args.external_root)
    files = sorted(root.glob("data/**/*.csv"))
    if not files:
        raise SystemExit(f"no csv files under {root}/data")

    frames = []
    file_reports = []
    for path in files:
        try:
            # Hard label isolation: only these eight columns are materialized.
            df = pd.read_csv(path, usecols=USECOLS, low_memory=False)
        except ValueError as exc:
            file_reports.append({"file": str(path.relative_to(root)), "status": "missing_required_columns", "error": str(exc)})
            continue
        df["source_file"] = str(path.relative_to(root))
        frames.append(df)
        file_reports.append({"file": str(path.relative_to(root)), "status": "loaded_non_target_only", "rows": int(len(df))})

    if not frames:
        raise SystemExit("no source file contained all required non-target columns")

    x = pd.concat(frames, ignore_index=True)
    rows_total = int(len(x))
    for c in ODD_COLS:
        x[c] = pd.to_numeric(x[c], errors="coerce")

    dates = pd.to_datetime(x["Date"], errors="coerce", utc=True)
    date_valid = dates.notna()
    x["year"] = dates.dt.year

    pair_complete = x[ODD_COLS].notna().all(axis=1)
    odds_gt_one = (x[ODD_COLS] > 1.0).all(axis=1)
    finite = x[ODD_COLS].apply(lambda s: s.map(lambda v: bool(math.isfinite(v)) if pd.notna(v) else False)).all(axis=1)
    valid = pair_complete & odds_gt_one & finite & date_valid

    v = x.loc[valid].copy()
    v["p_over_open"] = devig_over(v["over_2.5_open"], v["under_2.5_open"])
    v["p_over_close"] = devig_over(v["over_2.5_close"], v["under_2.5_close"])
    v["delta_over"] = v["p_over_close"] - v["p_over_open"]

    # Predeclared source gate thresholds; no target labels are inspected.
    coverage_rate = float(valid.mean()) if rows_total else 0.0
    n_valid = int(valid.sum())
    n_leagues = int(v[["country", "league"]].drop_duplicates().shape[0]) if n_valid else 0
    n_seasons = int(v["Season"].nunique(dropna=True)) if n_valid else 0
    movement_nonzero_rate = float((v["delta_over"].abs() > 1e-9).mean()) if n_valid else 0.0
    date_valid_rate = float(date_valid.mean()) if rows_total else 0.0

    thresholds = {
        "min_valid_rows": 20000,
        "min_distinct_leagues": 6,
        "min_distinct_seasons": 10,
        "min_complete_valid_rate": 0.80,
        "min_date_valid_rate": 0.995,
        "min_nonzero_open_close_movement_rate": 0.05,
    }
    checks = {
        "valid_rows": n_valid >= thresholds["min_valid_rows"],
        "distinct_leagues": n_leagues >= thresholds["min_distinct_leagues"],
        "distinct_seasons": n_seasons >= thresholds["min_distinct_seasons"],
        "complete_valid_rate": coverage_rate >= thresholds["min_complete_valid_rate"],
        "date_valid_rate": date_valid_rate >= thresholds["min_date_valid_rate"],
        "movement_nonzero_rate": movement_nonzero_rate >= thresholds["min_nonzero_open_close_movement_rate"],
    }
    coverage_pass = all(checks.values())

    by_league = (
        v.groupby(["country", "league"], dropna=False)
        .agg(rows=("Date", "size"), first_date=("Date", "min"), last_date=("Date", "max"), seasons=("Season", "nunique"))
        .reset_index()
        .sort_values(["rows", "country", "league"], ascending=[False, True, True])
    )
    by_season = (
        v.groupby("Season", dropna=False)
        .size()
        .rename("rows")
        .reset_index()
        .sort_values("Season")
    )

    # Critical limitation: source has semantic open/close fields but no immutable per-row quote timestamp.
    pit_status = "COARSE_OPEN_CLOSE_SEMANTICS_ONLY_NO_ROW_QUOTE_TIMESTAMP"
    terminal = "COVERAGE_PASS_COARSE_PIT_ONLY" if coverage_pass else "SOURCE_COVERAGE_GATE_FAIL"

    summary = {
        "experiment": "C074-C",
        "research_only": True,
        "formal_weight": 0,
        "source_repo": "nm2890/football-data",
        "source_provenance": "football-data.co.uk mirror/aggregation as described by source README",
        "target_label_columns_materialized": 0,
        "model_fit_count": 0,
        "files_found": len(files),
        "files_loaded": len(frames),
        "rows_total_non_target_view": rows_total,
        "rows_complete_pair": int(pair_complete.sum()),
        "rows_valid": n_valid,
        "complete_valid_rate": coverage_rate,
        "date_valid_rate": date_valid_rate,
        "distinct_leagues": n_leagues,
        "distinct_seasons": n_seasons,
        "first_date": None if not n_valid else str(v["Date"].min()),
        "last_date": None if not n_valid else str(v["Date"].max()),
        "movement": {
            "nonzero_rate": movement_nonzero_rate,
            "delta_over_mean": None if not n_valid else float(v["delta_over"].mean()),
            "delta_over_abs_mean": None if not n_valid else float(v["delta_over"].abs().mean()),
            "delta_over_q05": q(v["delta_over"], 0.05),
            "delta_over_q50": q(v["delta_over"], 0.50),
            "delta_over_q95": q(v["delta_over"], 0.95),
        },
        "thresholds": thresholds,
        "checks": checks,
        "coverage_pass": coverage_pass,
        "pit_status": pit_status,
        "terminal": terminal,
        "scientific_boundary": (
            "May support a research-only open-to-close O/U2.5 movement increment probe. "
            "Must not be represented as timestamped multi-line dynamic OU, and cannot serve as a formal market snapshot "
            "because immutable per-row quote timestamps are absent."
        ),
        "sealed_assets_touched": {
            "C071_reserve_52180": 0,
            "C070F_confirmation_1597": 0,
            "A05": 0,
            "protected": 0,
        },
        "file_reports": file_reports,
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    by_league.to_csv(out / "coverage_by_league.csv", index=False)
    by_season.to_csv(out / "coverage_by_season.csv", index=False)

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
