#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

BASE_URL = "https://www.football-data.co.uk/mmz4281/2526"
DIVS = {
    "E0": "England Premier League",
    "SP1": "Spain La Liga",
    "I1": "Italy Serie A",
    "D1": "Germany Bundesliga",
    "F1": "France Ligue 1",
    "N1": "Netherlands Eredivisie",
    "B1": "Belgium First Division A",
}
ALLOWED = ["Div", "Date", "Time", "HomeTeam", "AwayTeam", "Avg>2.5", "Avg<2.5", "AvgC>2.5", "AvgC<2.5"]
REQUIRED = ["Div", "Date", "HomeTeam", "AwayTeam", "Avg>2.5", "Avg<2.5", "AvgC>2.5", "AvgC<2.5"]
PRICE_COLS = ["Avg>2.5", "Avg<2.5", "AvgC>2.5", "AvgC<2.5"]
START = pd.Timestamp("2025-07-01")
END = pd.Timestamp("2026-06-30 23:59:59")


def fetch_csv(div: str) -> tuple[pd.DataFrame, dict]:
    url = f"{BASE_URL}/{div}.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 C074F research audit"})
    err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                status = getattr(resp, "status", 200)
            # Critical: only the zero-label columns are materialized.
            hdr = pd.read_csv(io.BytesIO(raw), nrows=0).columns.tolist()
            use = [c for c in ALLOWED if c in hdr]
            df = pd.read_csv(io.BytesIO(raw), usecols=use, low_memory=False)
            return df, {"url": url, "http_status": int(status), "bytes": len(raw), "header_columns": hdr, "materialized_columns": use}
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"download failed {div}: {err}")


def devig_over(o, u):
    io_ = 1.0 / np.asarray(o, dtype=float)
    iu_ = 1.0 / np.asarray(u, dtype=float)
    return io_ / (io_ + iu_)


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/c074f_source_gate")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    audits = []
    valid_frames = []
    download_failures = []
    for div, name in DIVS.items():
        try:
            d, meta = fetch_csv(div)
        except Exception as exc:
            download_failures.append({"div": div, "league": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        missing = [c for c in REQUIRED if c not in d.columns]
        if missing:
            audits.append({"div": div, "league": name, "rows": int(len(d)), "missing_required": missing, **meta})
            continue
        for c in PRICE_COLS:
            d[c] = pd.to_numeric(d[c], errors="coerce")
        dt = pd.to_datetime(d["Date"], errors="coerce", dayfirst=True)
        identity_ok = d[["HomeTeam", "AwayTeam"]].notna().all(axis=1)
        date_ok = dt.notna()
        price_ok = d[PRICE_COLS].notna().all(axis=1) & (d[PRICE_COLS] > 1.0).all(axis=1)
        valid = identity_ok & date_ok & price_ok
        season_ok = dt.between(START, END)
        dv_open = devig_over(d.loc[valid, "Avg>2.5"], d.loc[valid, "Avg<2.5"])
        dv_close = devig_over(d.loc[valid, "AvgC>2.5"], d.loc[valid, "AvgC<2.5"])
        movement = logit(dv_close) - logit(dv_open)
        audits.append({
            "div": div,
            "league": name,
            "rows": int(len(d)),
            "identity_rows": int(identity_ok.sum()),
            "valid_date_rows": int(date_ok.sum()),
            "valid_four_price_rows": int(price_ok.sum()),
            "complete_valid_rows": int(valid.sum()),
            "complete_valid_rate": float(valid.mean()) if len(d) else 0.0,
            "valid_date_rate": float(date_ok.mean()) if len(d) else 0.0,
            "all_dates_in_2025_26_window": bool(season_ok.loc[date_ok].all()) if date_ok.any() else False,
            "date_min": None if not date_ok.any() else str(dt.loc[date_ok].min()),
            "date_max": None if not date_ok.any() else str(dt.loc[date_ok].max()),
            "nonzero_movement_rate": float((np.abs(movement) > 1e-12).mean()) if len(movement) else 0.0,
            "mean_abs_movement_logit": float(np.abs(movement).mean()) if len(movement) else 0.0,
            "missing_required": [],
            **meta,
        })
        q = d.loc[valid, ["Div", "Date", "HomeTeam", "AwayTeam"] + PRICE_COLS].copy()
        q["parsed_date"] = dt.loc[valid].values
        q["source_div"] = div
        valid_frames.append(q)

    parsed_leagues = sum(1 for a in audits if not a.get("missing_required"))
    total_rows = int(sum(a.get("rows", 0) for a in audits))
    total_valid = int(sum(a.get("complete_valid_rows", 0) for a in audits))
    total_dates = int(sum(a.get("valid_date_rows", 0) for a in audits))
    overall_valid_rate = total_valid / total_rows if total_rows else 0.0
    overall_date_rate = total_dates / total_rows if total_rows else 0.0
    if valid_frames:
        allv = pd.concat(valid_frames, ignore_index=True)
        po = devig_over(allv["Avg>2.5"], allv["Avg<2.5"])
        pc = devig_over(allv["AvgC>2.5"], allv["AvgC<2.5"])
        mv = logit(pc) - logit(po)
        movement_rate = float((np.abs(mv) > 1e-12).mean())
        mean_abs_mv = float(np.abs(mv).mean())
        all_dates_window = bool(pd.to_datetime(allv["parsed_date"]).between(START, END).all())
        duplicate_identity = int(allv.duplicated(["source_div", "parsed_date", "HomeTeam", "AwayTeam"]).sum())
    else:
        movement_rate = 0.0; mean_abs_mv = 0.0; all_dates_window = False; duplicate_identity = 0

    gate = {
        "parsed_leagues_ge_6": parsed_leagues >= 6,
        "complete_valid_rows_ge_2000": total_valid >= 2000,
        "complete_valid_rate_ge_0_85": overall_valid_rate >= 0.85,
        "valid_dates_ge_0_995": overall_date_rate >= 0.995,
        "nonzero_movement_rate_ge_0_05": movement_rate >= 0.05,
        "all_valid_dates_in_2025_26_window": all_dates_window,
        "target_label_columns_materialized_eq_0": True,
        "model_fit_eq_0": True,
    }
    passed = all(gate.values())
    summary = {
        "experiment": "C074-F",
        "audit_only": True,
        "formal_weight": 0,
        "source": "Football-Data.co.uk",
        "base_url": BASE_URL,
        "season": "2025-2026",
        "frozen_divisions": DIVS,
        "allowed_materialized_columns": ALLOWED,
        "target_label_columns_materialized": 0,
        "model_fit": 0,
        "parsed_leagues": parsed_leagues,
        "download_failures": download_failures,
        "total_rows": total_rows,
        "complete_valid_rows": total_valid,
        "complete_valid_rate": overall_valid_rate,
        "valid_date_rate": overall_date_rate,
        "nonzero_movement_rate": movement_rate,
        "mean_abs_movement_logit": mean_abs_mv,
        "duplicate_identity_rows": duplicate_identity,
        "all_valid_dates_in_2025_26_window": all_dates_window,
        "gate_checks": gate,
        "terminal": "FOOTBALLDATA_2526_ZERO_LABEL_SOURCE_PASS" if passed else "FOOTBALLDATA_2526_ZERO_LABEL_SOURCE_FAIL",
        "leagues": audits,
        "scientific_boundary": "No score/result label was materialized. PASS authorizes freezing C074-G before any 2025/26 result labels are opened; it is not itself confirmation.",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(audits).to_csv(out / "league_coverage.csv", index=False)
    print(json.dumps({k: summary[k] for k in ["terminal","parsed_leagues","total_rows","complete_valid_rows","complete_valid_rate","valid_date_rate","nonzero_movement_rate","mean_abs_movement_logit","target_label_columns_materialized","model_fit"]}, indent=2))
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
