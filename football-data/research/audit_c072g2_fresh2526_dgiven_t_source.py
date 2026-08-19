#!/usr/bin/env python3
from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

DIVS = ["E1","E2","E3","SC0","SC1","SC2","SC3","D2","I2","SP2","F2","P1"]
BASE = "https://www.football-data.co.uk/mmz4281/2526/{}.csv"
REQ = ["Date","HomeTeam","AwayTeam","AvgH","AvgD","AvgA","AvgCH","AvgCD","AvgCA"]


def fetch(div):
    req = Request(BASE.format(div), headers={"User-Agent":"Mozilla/5.0 football3-research-source-audit"})
    return urlopen(req, timeout=90).read()


def devig3(a,b,c):
    x=np.column_stack([1/a,1/b,1/c]); return x/x.sum(axis=1,keepdims=True)


def main():
    frames=[]; per=[]
    for div in DIVS:
        raw=fetch(div)
        hdr=pd.read_csv(io.BytesIO(raw), nrows=0).columns.tolist()
        missing=[x for x in REQ if x not in hdr]
        if missing: raise RuntimeError(f"{div} missing required zero-label fields {missing}")
        df=pd.read_csv(io.BytesIO(raw), usecols=REQ)
        df["division"]=div
        dt=pd.to_datetime(df.Date, errors="coerce", dayfirst=True)
        op=df[["AvgH","AvgD","AvgA"]].apply(pd.to_numeric,errors="coerce")
        cl=df[["AvgCH","AvgCD","AvgCA"]].apply(pd.to_numeric,errors="coerce")
        ok_o=np.isfinite(op).all(1)&(op>1).all(1); ok_c=np.isfinite(cl).all(1)&(cl>1).all(1); both=ok_o&ok_c
        move=np.zeros(len(df),dtype=bool)
        if both.any():
            po=devig3(*[op.loc[both,c].to_numpy(float) for c in op.columns]); pc=devig3(*[cl.loc[both,c].to_numpy(float) for c in cl.columns]); move[np.flatnonzero(both)]=(np.abs(pc-po).sum(1)>1e-9)
        per.append({"division":div,"rows":int(len(df)),"valid_date_rate":float(dt.notna().mean()),"opening_coverage":float(ok_o.mean()),"closing_coverage":float(ok_c.mean()),"nonzero_movement_rate_on_both":float(move[both].mean()) if both.any() else 0.0})
        df["_valid_date"]=dt.notna(); df["_open"]=ok_o; df["_close"]=ok_c; df["_both"]=both; df["_move"]=move
        frames.append(df)
    x=pd.concat(frames,ignore_index=True)
    dup=int(x[["division","Date","HomeTeam","AwayTeam"]].astype(str).duplicated().sum())
    both=x._both.to_numpy(bool)
    good_close=sum(r["closing_coverage"]>=.85 for r in per)
    gates={
      "files_12":len(per)==12,
      "rows_ge_4000":len(x)>=4000,
      "duplicates_zero":dup==0,
      "valid_dates_ge_0_995":float(x._valid_date.mean())>=.995,
      "opening_coverage_ge_0_95":float(x._open.mean())>=.95,
      "closing_coverage_ge_0_90":float(x._close.mean())>=.90,
      "nonzero_movement_ge_0_80":float(x.loc[both,"_move"].mean())>=.80 if both.any() else False,
      "every_file_ge_100":all(r["rows"]>=100 for r in per),
      "files_closing_ge_0_85_at_least_10":good_close>=10,
      "target_result_values_materialized_zero":True,
      "model_fit_zero":True,
    }
    out={
      "schema":"C072G2_FRESH2526_DGIVENT_ZERO_LABEL_SOURCE_V1",
      "project_line":"football3",
      "divisions":DIVS,
      "files":per,
      "rows":int(len(x)),"duplicates":dup,
      "valid_date_rate":float(x._valid_date.mean()),"opening_coverage":float(x._open.mean()),"closing_coverage":float(x._close.mean()),
      "nonzero_movement_rate_on_complete":float(x.loc[both,"_move"].mean()) if both.any() else 0.0,
      "files_closing_ge_0_85":int(good_close),
      "target_result_values_materialized":0,"model_fit":0,
      "C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"formal_weight":0,
      "gates":gates,"terminal":"FRESH2526_DGIVENT_SOURCE_PASS" if all(gates.values()) else "STOP_SOURCE_COVERAGE"
    }
    p=Path("football-data/research/c072g2_source_summary.json"); p.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
