#!/usr/bin/env python3
"""Strict chronological validation of selective market-first 1X2 hit rate.

This answers a different question from full-coverage accuracy: at what coverage can a
market-first selector sustain 60%, 65% or 70% Top-1 hit rate? Gates are chosen on the
middle 20% of each competition after a 60% chronological development segment, then
frozen and evaluated on the latest 20% only. No test-result tuning is allowed.
"""
from __future__ import annotations
import json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; VALIDATION=ROOT/"validation"; ENGINE=ROOT/"engine"
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
from diagnose_1x2_market_anchor_v697 import _load_model_rows,_match_market,_market_probs,_pick_probs
OUT=ROOT/"manifests"/"v6_1x2_selective_market_v6103_status.json"
DIRECTIONS=("home","draw","away")

def _split(rows):
    dev=[]; val=[]; test=[]
    for cid in sorted({r["competition_id"] for r in rows}):
        s=sorted([r for r in rows if r["competition_id"]==cid],key=lambda r:(r["date"],r["home_team"],r["away_team"]))
        n=len(s); i1=int(.6*n); i2=int(.8*n); dev+=s[:i1]; val+=s[i1:i2]; test+=s[i2:]
    return dev,val,test

def _select(rows,pmin,margin):
    out=[]
    for r in rows:
        p=_market_probs(r); order=sorted(DIRECTIONS,key=lambda k:p[k],reverse=True)
        if p[order[0]]>=pmin and p[order[0]]-p[order[1]]>=margin: out.append((r,order[0],p[order[0]],p[order[0]]-p[order[1]]))
    return out

def _eval(rows,pmin,margin):
    s=_select(rows,pmin,margin); h=sum(1 for r,p,_,__ in s if p==r["actual"])
    return {"count":len(s),"hits":h,"coverage":len(s)/len(rows) if rows else 0.0,"accuracy":h/len(s) if s else None,"pmin":pmin,"margin":margin}

def _fit(val,target):
    cand=[]
    for pi in range(34,81,2):
        for mi in range(0,31,2):
            e=_eval(val,pi/100,mi/100)
            if e["count"]>=75 and e["accuracy"] is not None and e["accuracy"]>=target: cand.append(e)
    if not cand: return None
    cand.sort(key=lambda e:(e["coverage"],e["count"],e["accuracy"]),reverse=True); return cand[0]

def main():
    rows,providers=_match_market(_load_model_rows()); dev,val,test=_split(rows)
    targets={}
    for target in (.60,.65,.70):
        gate=_fit(val,target); targets[str(int(target*100))]={"validation_gate":gate,"latest_test":_eval(test,gate["pmin"],gate["margin"]) if gate else None}
    fixed={}
    for pmin in (.45,.50,.55,.58,.60,.62,.65,.70):
        fixed[str(pmin)]={"validation":_eval(val,pmin,0.0),"latest_test":_eval(test,pmin,0.0)}
    # competition breakdown for the gate chosen for 65%, if available
    gate65=targets["65"]["validation_gate"]; bycomp={}
    if gate65:
        for cid in sorted({r["competition_id"] for r in test}):
            sub=[r for r in test if r["competition_id"]==cid]; bycomp[cid]=_eval(sub,gate65["pmin"],gate65["margin"])
    payload={"schema_version":"V6.10.3-selective-market-1x2-chronological-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP","split":{"development":len(dev),"validation":len(val),"latest_test":len(test),"chronological_within_competition":True},"targets":targets,"fixed_probability_thresholds":fixed,"target65_by_competition_test":bycomp,"provider_counts":providers,"governance":{"research_only":True,"test_not_used_for_gate_selection":True,"coverage_always_reported":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"split":payload["split"],"targets":targets,"fixed":fixed},ensure_ascii=False,indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
