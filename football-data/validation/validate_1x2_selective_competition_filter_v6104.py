#!/usr/bin/env python3
"""Research-only competition-filtered selective market 1X2 validation.

A global confidence threshold is fixed first. Competition eligibility is then decided
ONLY from each competition's middle chronological 20% validation segment. The latest
20% is untouched until final scoring. This tests whether removing domains where the
same confidence threshold historically underperforms can improve selective accuracy
without using test outcomes to choose leagues.
"""
from __future__ import annotations
import json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; V=ROOT/"validation"; E=ROOT/"engine"
for p in (V,E):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
from diagnose_1x2_market_anchor_v697 import _load_model_rows,_match_market,_market_probs,_pick_probs
OUT=ROOT/"manifests"/"v6_1x2_selective_competition_filter_v6104_status.json"
THRESHOLDS=(0.50,0.54,0.56,0.58,0.60,0.62)

def _split(rows):
    val=[]; test=[]
    for cid in sorted({r["competition_id"] for r in rows}):
        s=sorted([r for r in rows if r["competition_id"]==cid],key=lambda r:(r["date"],r["home_team"],r["away_team"]))
        n=len(s); i1=int(.6*n); i2=int(.8*n); val+=s[i1:i2]; test+=s[i2:]
    return val,test

def _eval(rows,pmin,allowed=None):
    selected=[]
    for r in rows:
        if allowed is not None and r["competition_id"] not in allowed: continue
        p=_market_probs(r); pick=_pick_probs(p)
        if p[pick]>=pmin: selected.append((r,pick))
    hits=sum(1 for r,p in selected if p==r["actual"])
    return {"count":len(selected),"hits":hits,"coverage":len(selected)/len(rows) if rows else 0.0,"accuracy":hits/len(selected) if selected else None}

def _by_comp(rows,pmin):
    out={}
    for cid in sorted({r["competition_id"] for r in rows}):
        sub=[r for r in rows if r["competition_id"]==cid]; e=_eval(sub,pmin); out[cid]=e
    return out

def main():
    rows,providers=_match_market(_load_model_rows()); val,test=_split(rows)
    results={}
    for pmin in THRESHOLDS:
        vcomp=_by_comp(val,pmin)
        # Predeclared eligibility rules, all based on validation only.
        variants={}
        for label,min_n,min_acc in (("loose",8,.60),("balanced",10,.65),("strict",12,.68)):
            allowed=sorted(cid for cid,e in vcomp.items() if e["count"]>=min_n and e["accuracy"] is not None and e["accuracy"]>=min_acc)
            variants[label]={"rule":{"min_validation_selected":min_n,"min_validation_accuracy":min_acc},"allowed_competitions":allowed,"validation":_eval(val,pmin,set(allowed)),"latest_test":_eval(test,pmin,set(allowed))}
        results[str(pmin)]={"global_validation":_eval(val,pmin),"global_test":_eval(test,pmin),"validation_by_competition":vcomp,"filters":variants}
    # Choose ONE policy using validation aggregate only: maximize coverage subject >=65% validation and >=3 comps.
    candidates=[]
    for ps,item in results.items():
        for label,v in item["filters"].items():
            e=v["validation"]
            if len(v["allowed_competitions"])>=3 and e["count"]>=75 and e["accuracy"] is not None and e["accuracy"]>=.65:
                candidates.append((e["coverage"],e["count"],e["accuracy"],float(ps),label))
    chosen=max(candidates) if candidates else None
    final=None
    if chosen:
        _,_,_,pmin,label=chosen; v=results[str(pmin)]["filters"][label]
        final={"threshold":pmin,"filter":label,**v}
    payload={"schema_version":"V6.10.4-selective-competition-filter-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP","validation_count":len(val),"latest_test_count":len(test),"grid":results,"selected_on_validation_only":final,"governance":{"research_only":True,"test_outcomes_not_used_for_threshold_or_competition_selection":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"selected":final},ensure_ascii=False,indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
