#!/usr/bin/env python3
from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

DIVS=["EC","T1","G1"]
BASE="https://www.football-data.co.uk/mmz4281/2526/{}.csv"
REQ=["Date","HomeTeam","AwayTeam","AvgH","AvgD","AvgA","AvgCH","AvgCD","AvgCA","Avg>2.5","Avg<2.5","AvgC>2.5","AvgC<2.5"]


def fetch(div):
    req=Request(BASE.format(div),headers={"User-Agent":"Mozilla/5.0 football3-zero-label-source"})
    return urlopen(req,timeout=90).read()


def devig_over(o,u):
    po=1.0/o; pu=1.0/u; return po/(po+pu)


def main():
    frames=[]; per=[]
    for div in DIVS:
        raw=fetch(div)
        hdr=pd.read_csv(io.BytesIO(raw),nrows=0).columns.tolist()
        missing=[x for x in REQ if x not in hdr]
        if missing: raise RuntimeError(f"{div} missing zero-label fields {missing}")
        x=pd.read_csv(io.BytesIO(raw),usecols=REQ)
        x["division"]=div
        dt=pd.to_datetime(x.Date,errors="coerce",dayfirst=True,utc=True)
        open1=x[["AvgH","AvgD","AvgA"]].apply(pd.to_numeric,errors="coerce")
        close1=x[["AvgCH","AvgCD","AvgCA"]].apply(pd.to_numeric,errors="coerce")
        ou=x[["Avg>2.5","Avg<2.5","AvgC>2.5","AvgC<2.5"]].apply(pd.to_numeric,errors="coerce")
        ok_o1=np.isfinite(open1).all(1)&(open1>1).all(1)
        ok_c1=np.isfinite(close1).all(1)&(close1>1).all(1)
        ok_ou=np.isfinite(ou).all(1)&(ou>1).all(1)
        move=np.zeros(len(x),dtype=bool)
        if ok_ou.any():
            g=ou.loc[ok_ou]
            po=devig_over(g["Avg>2.5"].to_numpy(float),g["Avg<2.5"].to_numpy(float))
            pc=devig_over(g["AvgC>2.5"].to_numpy(float),g["AvgC<2.5"].to_numpy(float))
            move[np.flatnonzero(ok_ou)]=np.abs(pc-po)>1e-9
        per.append({"division":div,"rows":int(len(x)),"valid_date_rate":float(dt.notna().mean()),"opening_1x2_coverage":float(ok_o1.mean()),"closing_1x2_coverage":float(ok_c1.mean()),"ou25_four_price_coverage":float(ok_ou.mean()),"ou25_nonzero_movement_rate":float(move[ok_ou].mean()) if ok_ou.any() else 0.0})
        x["_valid_date"]=dt.notna(); x["_open1"]=ok_o1; x["_close1"]=ok_c1; x["_ou"]=ok_ou; x["_move"]=move
        frames.append(x)
    z=pd.concat(frames,ignore_index=True)
    dup=int(z[["division","Date","HomeTeam","AwayTeam"]].astype(str).duplicated().sum())
    ou=z._ou.to_numpy(bool)
    gates={
      "divisions_3":len(per)==3,
      "rows_ge_800":len(z)>=800,
      "each_division_ge_200":all(r["rows"]>=200 for r in per),
      "duplicates_zero":dup==0,
      "valid_dates_ge_0_995":float(z._valid_date.mean())>=.995,
      "opening_1x2_ge_0_95":float(z._open1.mean())>=.95,
      "closing_1x2_ge_0_90":float(z._close1.mean())>=.90,
      "ou25_four_price_ge_0_90":float(z._ou.mean())>=.90,
      "ou25_movement_ge_0_80":float(z.loc[ou,"_move"].mean())>=.80 if ou.any() else False,
      "each_division_ou25_ge_0_85":all(r["ou25_four_price_coverage"]>=.85 for r in per),
      "target_result_values_materialized_zero":True,
      "model_fit_zero":True,
    }
    out={"schema":"C072J2_FRESH_JOINT_ZERO_LABEL_SOURCE_V1","project_line":"football3","divisions":DIVS,"files":per,"rows":int(len(z)),"duplicates":dup,"valid_date_rate":float(z._valid_date.mean()),"opening_1x2_coverage":float(z._open1.mean()),"closing_1x2_coverage":float(z._close1.mean()),"ou25_four_price_coverage":float(z._ou.mean()),"ou25_nonzero_movement_rate":float(z.loc[ou,"_move"].mean()) if ou.any() else 0.0,"target_result_values_materialized":0,"model_fit":0,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"formal_weight":0,"gates":gates,"terminal":"C072J2_FRESH_JOINT_SOURCE_PASS" if all(gates.values()) else "STOP_JOINT_SOURCE_COVERAGE"}
    p=Path("football-data/research/c072j2_source_summary.json"); p.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n"); print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
