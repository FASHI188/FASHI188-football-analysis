#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

BASE_URL = "https://www.football-data.co.uk/mmz4281/2526"
DIVS = {
    "E1": "England Championship",
    "E2": "England League One",
    "E3": "England League Two",
    "SC0": "Scotland Premiership",
    "SC1": "Scotland Championship",
    "SC2": "Scotland League One",
    "SC3": "Scotland League Two",
    "D2": "Germany 2. Bundesliga",
    "I2": "Italy Serie B",
    "SP2": "Spain Segunda Division",
    "F2": "France Ligue 2",
    "P1": "Portugal Primeira Liga",
    "G1": "Greece Super League",
    "T1": "Turkey Super Lig",
}
ALLOWED = ["Div", "Date", "Time", "HomeTeam", "AwayTeam"]
REQUIRED = ["Div", "Date", "HomeTeam", "AwayTeam"]
START = pd.Timestamp("2025-07-01")
END = pd.Timestamp("2026-06-30 23:59:59")


def fetch(div: str):
    url = f"{BASE_URL}/{div}.csv"
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0 C074H zero-label audit"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read(); status = int(getattr(resp,"status",200))
            header = pd.read_csv(io.BytesIO(raw), nrows=0).columns.tolist()
            use = [c for c in ALLOWED if c in header]
            d = pd.read_csv(io.BytesIO(raw), usecols=use, low_memory=False)
            return d, {"url":url,"status":status,"bytes":len(raw),"header_columns":header,"materialized_columns":use}
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"; time.sleep(1.5*(attempt+1))
    return None, {"url":url,"error":last}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out",default="artifacts/c074h_tail_zero_label"); args=ap.parse_args()
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    audits=[]; valid_frames=[]; failures=[]
    for div,name in DIVS.items():
        d,meta=fetch(div)
        if d is None:
            failures.append({"div":div,"league":name,**meta}); continue
        missing=[c for c in REQUIRED if c not in d.columns]
        if missing:
            audits.append({"div":div,"league":name,"rows":int(len(d)),"missing_required":missing,**meta}); continue
        dt=pd.to_datetime(d["Date"],errors="coerce",dayfirst=True)
        ident=d[["HomeTeam","AwayTeam"]].notna().all(axis=1)
        date_ok=dt.notna(); valid=ident & date_ok
        in_window=dt.between(START,END)
        q=d.loc[valid,["Div","Date","HomeTeam","AwayTeam"]].copy(); q["parsed_date"]=dt.loc[valid].values; q["source_div"]=div
        valid_frames.append(q)
        audits.append({
            "div":div,"league":name,"rows":int(len(d)),"valid_identity_date_rows":int(valid.sum()),
            "valid_identity_date_rate":float(valid.mean()) if len(d) else 0.0,
            "all_valid_dates_in_window":bool(in_window.loc[valid].all()) if valid.any() else False,
            "date_min":None if not valid.any() else str(dt.loc[valid].min()),
            "date_max":None if not valid.any() else str(dt.loc[valid].max()),
            "missing_required":[],**meta,
        })
    parsed=sum(1 for a in audits if not a.get("missing_required"))
    total_rows=sum(a.get("rows",0) for a in audits)
    total_valid=sum(a.get("valid_identity_date_rows",0) for a in audits)
    rate=total_valid/total_rows if total_rows else 0.0
    if valid_frames:
        allv=pd.concat(valid_frames,ignore_index=True)
        all_window=bool(pd.to_datetime(allv["parsed_date"]).between(START,END).all())
        dup=int(allv.duplicated(["source_div","parsed_date","HomeTeam","AwayTeam"]).sum())
    else:
        all_window=False; dup=0
    gate={
        "parsed_divisions_ge_10":parsed>=10,
        "valid_identity_rows_ge_3500":total_valid>=3500,
        "valid_identity_date_rate_ge_0_995":rate>=0.995,
        "all_valid_dates_in_2025_26_window":all_window,
        "duplicate_identity_rows_eq_0":dup==0,
        "target_result_tail_label_columns_materialized_eq_0":True,
        "model_fit_eq_0":True,
    }
    passed=all(gate.values())
    summary={
        "experiment":"C074-H","audit_only":True,"formal_weight":0,"source":"Football-Data.co.uk","season":"2025-2026",
        "frozen_divisions":DIVS,"excluded_c074g_top_divisions":["E0","SP1","I1","D1","F1","N1","B1"],
        "allowed_materialized_columns":ALLOWED,"target_result_tail_label_columns_materialized":0,"model_fit":0,
        "parsed_divisions":parsed,"download_failures":failures,"total_rows":int(total_rows),"valid_identity_rows":int(total_valid),
        "valid_identity_date_rate":rate,"all_valid_dates_in_window":all_window,"duplicate_identity_rows":dup,
        "gate_checks":gate,"terminal":"UNTOUCHED_2526_TAIL_ZERO_LABEL_SOURCE_PASS" if passed else "UNTOUCHED_2526_TAIL_ZERO_LABEL_SOURCE_FAIL",
        "divisions":audits,
        "boundary":"No score/result/total/tail label materialized. PASS only authorizes freezing C074-I before any score label is opened."
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    pd.DataFrame(audits).to_csv(out/"division_coverage.csv",index=False)
    print(json.dumps({k:summary[k] for k in ["terminal","parsed_divisions","total_rows","valid_identity_rows","valid_identity_date_rate","duplicate_identity_rows","target_result_tail_label_columns_materialized","model_fit"]},indent=2))
    if not passed: raise SystemExit(2)

if __name__=="__main__": main()
