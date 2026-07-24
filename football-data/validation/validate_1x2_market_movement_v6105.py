#!/usr/bin/env python3
"""Research-only opening-to-closing 1X2 market-movement validation.

Matches same-provider opening and closing price triplets, de-vigs each, and tests whether
closing confidence plus movement/consistency can improve selective 1X2 hit rate. Rules
are selected on chronological validation data and evaluated once on latest holdout.
Legacy prices have no original quote timestamps, so results are retrospective only.
"""
from __future__ import annotations
import csv,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; V=ROOT/"validation"; E=ROOT/"engine"
for p in (V,E):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
from platform_core import canonical_team_name,load_aliases,parse_match_date
from diagnose_1x2_market_anchor_v697 import _load_model_rows,_devig,_pick_probs
OUT=ROOT/"manifests"/"v6_1x2_market_movement_v6105_status.json"
DIRECTIONS=("home","draw","away")
PAIRS=(
    (("PSH","PSD","PSA"),("PSCH","PSCD","PSCA"),"Pinnacle"),
    (("B365H","B365D","B365A"),("B365CH","B365CD","B365CA"),"Bet365"),
    (("AvgH","AvgD","AvgA"),("AvgCH","AvgCD","AvgCA"),"Average"),
)
def _f(v):
    try:x=float(str(v).strip())
    except:return None
    return x if x>1 else None
def _probs(raw,cols):
    x=[_f(raw.get(c)) for c in cols]
    return _devig(*x) if all(v is not None for v in x) else None
def _key(cid,season,date,home,away):return(cid,season,date,home,away)
def _rows():
    base=_load_model_rows(); aliases=load_aliases(); lookup={_key(r["competition_id"],r["season"],r["date"],r["home_team"],r["away_team"]):r for r in base}; out={}
    for cid in sorted({r["competition_id"] for r in base}):
        d=ROOT/"processed"/cid
        if not d.exists():continue
        for path in sorted(d.glob("*.csv")):
            with path.open("r",encoding="utf-8-sig",newline="") as f:
                for rr in csv.DictReader(f):
                    raw={str(k):"" if v is None else str(v) for k,v in rr.items() if k}; season=str(raw.get("season") or raw.get("Season") or "").strip()
                    if not raw.get("Date") or not raw.get("HomeTeam") or not raw.get("AwayTeam"):continue
                    try:date=parse_match_date(raw["Date"],season).isoformat()
                    except:continue
                    h=canonical_team_name(cid,raw["HomeTeam"],aliases);a=canonical_team_name(cid,raw["AwayTeam"],aliases);key=_key(cid,season,date,h,a); b=lookup.get(key)
                    if b is None or key in out:continue
                    for oc,cc,label in PAIRS:
                        op=_probs(raw,oc);cp=_probs(raw,cc)
                        if op and cp:
                            pick=_pick_probs(cp); opick=_pick_probs(op); item=dict(b); item.update({"open_p":op,"close_p":cp,"provider":label,"close_pick":pick,"open_pick":opick,"agreement":pick==opick,"delta_pick":cp[pick]-op[pick],"close_max":cp[pick]}); out[key]=item;break
    return list(out.values())
def _split(rows):
    val=[];test=[]
    for cid in sorted({r["competition_id"] for r in rows}):
        s=sorted([r for r in rows if r["competition_id"]==cid],key=lambda r:(r["date"],r["home_team"],r["away_team"])); n=len(s);i1=int(.6*n);i2=int(.8*n);val+=s[i1:i2];test+=s[i2:]
    return val,test
def _eval(rows,pmin,agreement=None,dmin=-9):
    s=[]
    for r in rows:
        if r["close_max"]<pmin or r["delta_pick"]<dmin:continue
        if agreement is not None and r["agreement"]!=agreement:continue
        s.append(r)
    h=sum(1 for r in s if r["close_pick"]==r["actual"]);return{"count":len(s),"hits":h,"coverage":len(s)/len(rows) if rows else 0,"accuracy":h/len(s) if s else None,"pmin":pmin,"agreement":agreement,"dmin":dmin}
def _fit(val,target):
    cand=[]
    for p in (0.44,0.48,0.50,0.52,0.54,0.56,0.58,0.60,0.62,0.65):
        for ag in (None,True):
            for d in (-0.05,-0.02,0.0,0.01,0.02,0.03,0.05):
                e=_eval(val,p,ag,d)
                if e["count"]>=75 and e["accuracy"] is not None and e["accuracy"]>=target:cand.append(e)
    if not cand:return None
    cand.sort(key=lambda e:(e["coverage"],e["count"],e["accuracy"]),reverse=True);return cand[0]
def main():
    rows=_rows();val,test=_split(rows);targets={}
    for t in (.60,.65,.70):
        g=_fit(val,t);targets[str(int(t*100))]={"validation_gate":g,"latest_test":_eval(test,g["pmin"],g["agreement"],g["dmin"]) if g else None}
    diagnostics={
        "all_closing":{"validation":_eval(val,0,None,-9),"test":_eval(test,0,None,-9)},
        "agree_only":{"validation":_eval(val,0,True,-9),"test":_eval(test,0,True,-9)},
        "steam_positive":{"validation":_eval(val,0,None,0),"test":_eval(test,0,None,0)},
        "agree_and_positive":{"validation":_eval(val,0,True,0),"test":_eval(test,0,True,0)},
    }
    payload={"schema_version":"V6.10.5-market-movement-1x2-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP","matched_open_close_count":len(rows),"validation_count":len(val),"test_count":len(test),"provider_counts":dict(__import__('collections').Counter(r["provider"] for r in rows)),"targets":targets,"diagnostics":diagnostics,"governance":{"research_only":True,"test_not_used_for_gate_selection":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps({"count":len(rows),"targets":targets,"diagnostics":diagnostics},ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
