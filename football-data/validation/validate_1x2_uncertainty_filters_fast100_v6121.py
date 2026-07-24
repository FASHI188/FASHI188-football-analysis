#!/usr/bin/env python3
"""V6.12.1 research-only 100-match rapid screen for selective 1X2 uncertainty filters.

The goal is not to improve full-coverage accuracy. It is to test whether simple market
uncertainty diagnostics can improve the accuracy of the executed subset versus a plain
max-probability threshold.

Chronology: preceding 200 matches select each rule family's threshold; final 100 matches
are untouched test. Historical odds do not preserve original quote timestamps, so this
is retrospective research only and cannot alter formal CURRENT.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import validate_1x2_pit_lineup_increment_v6117 as base
from platform_core import canonical_team_name, load_aliases, parse_match_date

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_1x2_uncertainty_filters_fast100_v6121_status.json"
SEASON = "2025/26"
PROVIDERS = {
    "Pinnacle": ("PSH","PSD","PSA"),
    "Bet365": ("B365H","B365D","B365A"),
    "Average": ("AvgH","AvgD","AvgA"),
}


def triplet(raw, cols):
    vals=[base._f(raw.get(c)) for c in cols]
    if any(v is None for v in vals): return None
    return base._devig(*vals)


def load_rows():
    aliases=load_aliases(); out={}
    for cid in base.COMPETITIONS:
        d=ROOT/"processed"/cid
        if not d.exists(): continue
        for path in sorted(d.glob("*.csv")):
            with path.open("r",encoding="utf-8-sig",newline="") as fh:
                for raw0 in csv.DictReader(fh):
                    raw={str(k):"" if v is None else str(v) for k,v in raw0.items() if k}
                    season=str(raw.get("season") or raw.get("Season") or "").strip()
                    if season!=SEASON or not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"): continue
                    actual=base._actual(raw)
                    if actual is None: continue
                    ps={name:triplet(raw,cols) for name,cols in PROVIDERS.items()}
                    ps={k:v for k,v in ps.items() if v is not None}
                    if not ps: continue
                    primary=ps.get("Pinnacle") or ps.get("Bet365") or ps.get("Average")
                    try: dt=parse_match_date(raw["Date"],season)
                    except Exception: continue
                    home=canonical_team_name(cid,raw["HomeTeam"],aliases); away=canonical_team_name(cid,raw["AwayTeam"],aliases)
                    arr=np.asarray(primary,float); order=np.sort(arr)[::-1]
                    entropy=float(-(arr*np.log(np.clip(arr,1e-15,1))).sum()/math.log(3))
                    picks=[int(np.argmax(v)) for v in ps.values()]
                    agree=len(picks)>=2 and len(set(picks))==1
                    disp=None
                    if len(ps)>=2:
                        mat=np.asarray(list(ps.values()),float)
                        disp=float(np.max(np.ptp(mat,axis=0)))
                    key=(cid,dt.isoformat(),home,away)
                    out[key]={"competition_id":cid,"date":dt.isoformat(),"home":home,"away":away,"actual":actual,
                              "p":tuple(primary),"maxp":float(order[0]),"margin":float(order[0]-order[1]),"entropy":entropy,
                              "agreement":agree,"provider_count":len(ps),"dispersion":disp}
    return sorted(out.values(),key=lambda r:(r["date"],r["competition_id"],r["home"],r["away"]))


def correct(r):
    pred=("home","draw","away")[int(np.argmax(np.asarray(r["p"])))]
    return pred==r["actual"]


def stats(rows, gate):
    sel=[r for r in rows if gate(r)]
    hits=sum(correct(r) for r in sel)
    return {"count":len(sel),"coverage":len(sel)/len(rows) if rows else 0.0,"hits":hits,"accuracy":hits/len(sel) if sel else None}


def select_family(val, candidates, min_count=40):
    board=[]
    for label,gate,params in candidates:
        s=stats(val,gate)
        if s["count"]<min_count: continue
        board.append((s["accuracy"],s["count"],label,gate,params,s))
    if not board: return None
    board.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)
    return board[0]


def main():
    rows=load_rows()
    if len(rows)<300: raise RuntimeError(f"insufficient {SEASON} rows: {len(rows)}")
    val=rows[-300:-100]; test=rows[-100:]
    families={}

    p_candidates=[]
    for p in np.arange(0.52,0.641,0.01):
        pp=float(round(p,2)); p_candidates.append((f"p>={pp:.2f}",lambda r,pp=pp:r["maxp"]>=pp,{"p":pp}))
    families["max_probability"]=p_candidates

    pm=[]
    for p in (0.52,0.54,0.56,0.58,0.60):
        for m in (0.05,0.08,0.10,0.12,0.15,0.18,0.20):
            pm.append((f"p>={p:.2f}&margin>={m:.2f}",lambda r,p=p,m=m:r["maxp"]>=p and r["margin"]>=m,{"p":p,"margin":m}))
    families["probability_plus_margin"]=pm

    pe=[]
    for p in (0.52,0.54,0.56,0.58,0.60):
        for e in (0.95,0.92,0.90,0.88,0.86,0.84):
            pe.append((f"p>={p:.2f}&entropy<={e:.2f}",lambda r,p=p,e=e:r["maxp"]>=p and r["entropy"]<=e,{"p":p,"entropy_max":e}))
    families["probability_plus_entropy"]=pe

    pa=[]
    for p in (0.52,0.54,0.56,0.58,0.60):
        pa.append((f"p>={p:.2f}&unanimous",lambda r,p=p:r["maxp"]>=p and r["agreement"],{"p":p,"unanimous":True}))
    families["probability_plus_provider_agreement"]=pa

    pd=[]
    for p in (0.52,0.54,0.56,0.58,0.60):
        for d in (0.02,0.03,0.04,0.05,0.06):
            pd.append((f"p>={p:.2f}&disp<={d:.2f}",lambda r,p=p,d=d:r["maxp"]>=p and r["dispersion"] is not None and r["dispersion"]<=d,{"p":p,"dispersion_max":d}))
    families["probability_plus_low_dispersion"]=pd

    result={}
    for name,cands in families.items():
        chosen=select_family(val,cands,40)
        if chosen is None:
            result[name]={"status":"NO_VALID_RULE"}; continue
        vacc,vcount,label,gate,params,vstats=chosen
        result[name]={"selected_rule":label,"params":params,"validation":vstats,"test":stats(test,gate)}

    fixed={}
    for p in (0.56,0.58,0.60): fixed[f"p_ge_{p:.2f}"]=stats(test,lambda r,p=p:r["maxp"]>=p)
    all_market=stats(test,lambda r:True)
    payload={
        "schema_version":"V6.12.1-uncertainty-filters-fast100-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS",
        "formal_current_version":"V5.0.1","classification":"RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "governance":{"validation_matches":200,"test_matches":100,"test_untouched_for_rule_selection":True,"minimum_validation_selected":40,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False},
        "sample":{"season":SEASON,"rows":len(rows),"validation_first":val[0]["date"],"validation_last":val[-1]["date"],"test_first":test[0]["date"],"test_last":test[-1]["date"]},
        "test_all_market":all_market,"fixed_probability_gates":fixed,"selected_families":result,
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"all":all_market,"fixed":fixed,"families":result},indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
