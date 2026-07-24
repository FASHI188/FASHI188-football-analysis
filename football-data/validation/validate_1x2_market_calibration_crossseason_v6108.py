#!/usr/bin/env python3
"""Cross-season validation of market class-bias calibration for 1X2.

Hyperparameters are selected using ONLY the older completed season (80/20 chronological
train/validation within competition), then the chosen calibrator is refit on the entire
older season and evaluated once on the entire newer completed season. This is the hard
replication test for the +0.55pp V6.10.6 finding.
"""
from __future__ import annotations
import json,math,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/"validation";E=ROOT/"engine"
for p in(V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
from platform_core import load_json
from validate_1x2_market_selective_multiseason_v6107 import _extract,_seasons
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
OUT=ROOT/"manifests"/"v6_1x2_market_calibration_crossseason_v6108_status.json"
FORMAL=ROOT/"manifests"/"formal_core_v460_status.json";D=("home","draw","away");C2I={"home":0,"draw":1,"away":2};I2C={0:"home",1:"draw",2:"away"};SEED=20260724+6108

def _pick(p):return max(D,key=lambda k:p[k])
def _features(r,comps,mode):
 p=r["p"];eps=1e-9;base=[math.log((p["home"]+eps)/(p["draw"]+eps)),math.log((p["away"]+eps)/(p["draw"]+eps))]
 if mode in("shape","shape_comp"):
  vals=sorted(p.values(),reverse=True);base += [p["home"],p["draw"],p["away"],vals[0],vals[0]-vals[1],-sum(p[k]*math.log(p[k]+eps) for k in D)]
 if mode in("log_comp","shape_comp"):base += [1.0 if r["competition_id"]==c else 0.0 for c in comps]
 return base
def _X(rows,comps,mode):return[_features(r,comps,mode) for r in rows]
def _y(rows):return[C2I[r["actual"]] for r in rows]
def _model(c):return Pipeline([("s",StandardScaler()),("m",LogisticRegression(C=c,max_iter=4000,solver="lbfgs",random_state=SEED))])
def _labels(m,X):return[I2C[int(x)] for x in m.predict(X)]
def _acc(rows,picks):
 h=sum(1 for r,p in zip(rows,picks) if r["actual"]==p);return{"count":len(rows),"hits":h,"accuracy":h/len(rows) if rows else None}
def main():
 comps=sorted((load_json(FORMAL).get("reports") or {}).keys());older=[];newer=[]
 for cid in comps:
  rows,_=_extract(cid);s0,s1=_seasons(cid);older += [r for r in rows if r["season"]==s0];newer += [r for r in rows if r["season"]==s1]
 available=sorted({r["competition_id"] for r in older}&{r["competition_id"] for r in newer});older=[r for r in older if r["competition_id"] in available];newer=[r for r in newer if r["competition_id"] in available]
 tr=[];va=[]
 for cid in available:
  s=sorted([r for r in older if r["competition_id"]==cid],key=lambda r:(r["date"],r["home"],r["away"]));cut=int(.8*len(s));tr+=s[:cut];va+=s[cut:]
 leader=[]
 for mode in("log","log_comp","shape","shape_comp"):
  for c in(.01,.03,.1,.3,1,3,10):
   m=_model(c);m.fit(_X(tr,available,mode),_y(tr));pr=_labels(m,_X(va,available,mode));a=_acc(va,pr);leader.append({"mode":mode,"C":c,**a})
 leader.sort(key=lambda z:z["accuracy"],reverse=True);best=leader[0];m=_model(best["C"]);m.fit(_X(older,available,best["mode"]),_y(older));cal=_labels(m,_X(newer,available,best["mode"]));market=[_pick(r["p"]) for r in newer];ca=_acc(newer,cal);ma=_acc(newer,market)
 pair=Counter();by={}
 for r,p,q in zip(newer,cal,market):
  x=p==r["actual"];y=q==r["actual"];pair["both_correct" if x and y else "cal_only" if x else "market_only" if y else "both_wrong"]+=1
 for cid in available:
  ids=[i for i,r in enumerate(newer) if r["competition_id"]==cid];n=len(ids);ch=sum(1 for i in ids if cal[i]==newer[i]["actual"]);mh=sum(1 for i in ids if market[i]==newer[i]["actual"]);by[cid]={"count":n,"market_accuracy":mh/n,"calibrated_accuracy":ch/n,"uplift_pp":(ch-mh)/n*100}
 payload={"schema_version":"V6.10.8-market-calibration-crossseason-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP","older_season_rows":len(older),"newer_season_rows":len(newer),"available_competitions":available,"older_split":{"train":len(tr),"validation":len(va)},"validation_leaderboard":leader,"selected_on_older_only":{"mode":best["mode"],"C":best["C"],"validation_accuracy":best["accuracy"]},"newer_season_test":{"market":ma,"calibrated":ca,"uplift_pp":(ca["accuracy"]-ma["accuracy"])*100,"paired":dict(pair),"market_pick_counts":dict(Counter(market)),"calibrated_pick_counts":dict(Counter(cal)),"actual_counts":dict(Counter(r["actual"] for r in newer))},"by_competition":by,"governance":{"research_only":True,"newer_season_never_used_for_selection":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps({"selected":payload["selected_on_older_only"],"test":payload["newer_season_test"],"by":by},ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
