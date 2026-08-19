#!/usr/bin/env python3
from __future__ import annotations

import argparse, io, json, time, urllib.error, urllib.request
from pathlib import Path
import pandas as pd

FILES={"ARG":"Argentina","AUT":"Austria","BRA":"Brazil","CHN":"China","DNK":"Denmark","FIN":"Finland","IRL":"Ireland","JPN":"Japan","MEX":"Mexico","NOR":"Norway","POL":"Poland","ROU":"Romania","RUS":"Russia","SWE":"Sweden","SWZ":"Switzerland","USA":"USA"}
START=pd.Timestamp("2025-01-01"); END=pd.Timestamp("2025-12-31 23:59:59")


def fetch(code):
    url=f"https://www.football-data.co.uk/new/{code}.csv"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 C074J zero-label audit"})
    last=None
    for a in range(3):
        try:
            with urllib.request.urlopen(req,timeout=30) as r: raw=r.read(); status=int(getattr(r,"status",200))
            header=pd.read_csv(io.BytesIO(raw),nrows=0).columns.tolist()
            date="Date" if "Date" in header else None
            home="Home" if "Home" in header else ("HomeTeam" if "HomeTeam" in header else None)
            away="Away" if "Away" in header else ("AwayTeam" if "AwayTeam" in header else None)
            if not all([date,home,away]): return None,{"url":url,"status":status,"header_columns":header,"reason":"missing_identity_alias"}
            use=[date,home,away]+[c for c in ["Country","League","Season"] if c in header]
            d=pd.read_csv(io.BytesIO(raw),usecols=use,low_memory=False)
            return d,{"url":url,"status":status,"bytes":len(raw),"header_columns":header,"materialized_columns":use,"date_col":date,"home_col":home,"away_col":away}
        except urllib.error.HTTPError as e:
            if e.code==404:return None,{"url":url,"status":404,"reason":"not_available"}
            last=f"HTTPError:{e.code}"
        except Exception as e:last=f"{type(e).__name__}:{e}"
        time.sleep(1.5*(a+1))
    return None,{"url":url,"status":0,"reason":last}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out",default="artifacts/c074j_extra16_zero_label"); a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    audits=[]; frames=[]; failures=[]
    for code,name in FILES.items():
        d,m=fetch(code)
        if d is None: failures.append({"code":code,"name":name,**m}); continue
        dt=pd.to_datetime(d[m["date_col"]],errors="coerce",dayfirst=True)
        cand=dt.between(START,END)
        ident=d[[m["home_col"],m["away_col"]]].notna().all(axis=1); valid=cand & dt.notna() & ident
        q=d.loc[valid,[m["date_col"],m["home_col"],m["away_col"]]].copy(); q.columns=["Date","Home","Away"]; q["parsed_date"]=dt.loc[valid].values; q["source_code"]=code; frames.append(q)
        audits.append({"code":code,"name":name,"all_rows":int(len(d)),"date_min":None if not dt.notna().any() else str(dt.min()),"date_max":None if not dt.notna().any() else str(dt.max()),"candidate_2025_rows":int(cand.sum()),"valid_2025_identity_rows":int(valid.sum()),"valid_2025_rate":float(valid.sum()/cand.sum()) if cand.sum() else 0.0,**m})
    parsed=len(audits)
    if frames:
        allv=pd.concat(frames,ignore_index=True); total_valid=len(allv); dup=int(allv.duplicated(["source_code","parsed_date","Home","Away"]).sum()); all_window=bool(pd.to_datetime(allv.parsed_date).between(START,END).all())
    else: total_valid=0; dup=0; all_window=False
    total_candidate=sum(x["candidate_2025_rows"] for x in audits); rate=total_valid/total_candidate if total_candidate else 0.0
    gate={"parsed_files_ge_12":parsed>=12,"valid_2025_identity_rows_ge_3500":total_valid>=3500,"valid_2025_rate_ge_0_995":rate>=.995,"duplicates_eq_0":dup==0,"all_test_dates_in_2025":all_window,"labels_materialized_eq_0":True,"model_fit_eq_0":True}
    s={"experiment":"C074-J","audit_only":True,"formal_weight":0,"source":"Football-Data.co.uk /new extra16","frozen_files":FILES,"test_window":"2025 calendar year","parsed_files":parsed,"download_failures":failures,"candidate_2025_rows":int(total_candidate),"valid_2025_identity_rows":int(total_valid),"valid_2025_rate":rate,"duplicate_identity_rows":dup,"all_test_dates_in_2025":all_window,"target_result_tail_label_columns_materialized":0,"model_fit":0,"gate_checks":gate,"terminal":"EXTRA16_2025_TAIL_ZERO_LABEL_PASS" if all(gate.values()) else "EXTRA16_2025_TAIL_ZERO_LABEL_FAIL","files":audits}
    (out/"summary.json").write_text(json.dumps(s,indent=2,ensure_ascii=False),encoding="utf-8"); pd.DataFrame(audits).to_csv(out/"file_coverage.csv",index=False)
    print(json.dumps({k:s[k] for k in ["terminal","parsed_files","candidate_2025_rows","valid_2025_identity_rows","valid_2025_rate","duplicate_identity_rows","target_result_tail_label_columns_materialized","model_fit"]},indent=2))
    if not all(gate.values()): raise SystemExit(2)
if __name__=="__main__":main()
