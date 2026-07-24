#!/usr/bin/env python3
"""Research-only market class-bias calibration for 1X2 Top-1 accuracy.

Tests whether outcome-specific calibration of de-vigged market probabilities can improve
ranking decisions (especially close draw/home/away calls) without any team model input.
Chronological 60/20/20 split is applied within every competition. Latest 20% is untouched.
Legacy prices remain retrospective-reference-only.
"""
from __future__ import annotations
import json,math,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/"validation";E=ROOT/"engine"
for p in(V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
from diagnose_1x2_market_anchor_v697 import _load_model_rows,_match_market,_market_probs,_pick_probs
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import log_loss
OUT=ROOT/"manifests"/"v6_1x2_market_calibration_v6106_status.json"
D=("home","draw","away"); C2I={"home":0,"draw":1,"away":2};I2C={0:"home",1:"draw",2:"away"};SEED=20260724+6106

def _split(rows):
 tr=[];va=[];te=[]
 for cid in sorted({r["competition_id"] for r in rows}):
  s=sorted([r for r in rows if r["competition_id"]==cid],key=lambda r:(r["date"],r["home_team"],r["away_team"]));n=len(s);i1=int(.6*n);i2=int(.8*n);tr+=s[:i1];va+=s[i1:i2];te+=s[i2:]
 return tr,va,te

def _features(r,comps,mode):
 p=_market_probs(r);eps=1e-9; base=[math.log((p["home"]+eps)/(p["draw"]+eps)),math.log((p["away"]+eps)/(p["draw"]+eps))]
 if mode in("shape","shape_comp"):
  vals=sorted(p.values(),reverse=True);base += [p["home"],p["draw"],p["away"],vals[0],vals[0]-vals[1],-sum(p[k]*math.log(p[k]+eps) for k in D)]
 if mode in("log_comp","shape_comp"):base += [1.0 if r["competition_id"]==c else 0.0 for c in comps]
 return base

def _X(rows,comps,mode):return[_features(r,comps,mode) for r in rows]
def _y(rows):return[C2I[r["actual"]] for r in rows]
def _acc(rows,picks):
 h=sum(1 for r,p in zip(rows,picks) if r["actual"]==p);return{"count":len(rows),"hits":h,"accuracy":h/len(rows)}
def _base(rows):return[_pick_probs(_market_probs(r)) for r in rows]
def _pred(model,X):return[I2C[int(x)] for x in model.predict(X)]
def _brier(proba,y):return sum(sum((float(p[j])-(1 if yi==j else 0))**2 for j in range(3)) for p,yi in zip(proba,y))/len(y)
def _model(c):return Pipeline([("s",StandardScaler()),("m",LogisticRegression(C=c,max_iter=4000,solver="lbfgs",random_state=SEED))])

def main():
 rows,providers=_match_market(_load_model_rows());tr,va,te=_split(rows);comps=sorted({r["competition_id"] for r in rows});leader=[]
 for mode in("log","log_comp","shape","shape_comp"):
  Xtr=_X(tr,comps,mode);Xv=_X(va,comps,mode);ytr=_y(tr);yv=_y(va)
  for c in(.01,.03,.1,.3,1.0,3.0,10.0):
   m=_model(c);m.fit(Xtr,ytr);pr=_pred(m,Xv);a=_acc(va,pr);pro=m.predict_proba(Xv);leader.append({"mode":mode,"C":c,**a,"brier":_brier(pro,yv),"log_loss":float(log_loss(yv,pro,labels=[0,1,2]))})
 leader.sort(key=lambda z:(z["accuracy"],-z["brier"],-z["log_loss"]),reverse=True);best=leader[0]
 mode=best["mode"];c=best["C"];m=_model(c);m.fit(_X(tr+va,comps,mode),_y(tr+va));cal=_pred(m,_X(te,comps,mode));base=_base(te);ca=_acc(te,cal);ba=_acc(te,base)
 pair=Counter()
 for r,p,q in zip(te,cal,base):
  x=p==r["actual"];y=q==r["actual"];pair["both_correct" if x and y else "calibration_only" if x else "market_only" if y else "both_wrong"]+=1
 # direction counts and per-comp
 by={}
 for cid in comps:
  ids=[i for i,r in enumerate(te) if r["competition_id"]==cid]
  if not ids:continue
  ch=sum(1 for i in ids if cal[i]==te[i]["actual"]);bh=sum(1 for i in ids if base[i]==te[i]["actual"]);n=len(ids);by[cid]={"count":n,"market_accuracy":bh/n,"calibrated_accuracy":ch/n,"uplift_pp":(ch-bh)/n*100}
 payload={"schema_version":"V6.10.6-market-class-bias-calibration-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP","split":{"train":len(tr),"validation":len(va),"test":len(te),"chronological":True},"validation_leaderboard":leader,"selected":{"mode":mode,"C":c},"latest_test":{"market":ba,"calibrated":ca,"uplift_pp":(ca["accuracy"]-ba["accuracy"])*100,"paired":dict(pair),"market_pick_counts":dict(Counter(base)),"calibrated_pick_counts":dict(Counter(cal)),"actual_counts":dict(Counter(r["actual"] for r in te))},"by_competition":by,"provider_counts":providers,"governance":{"research_only":True,"market_only_no_team_model":True,"test_not_used_for_selection":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps({"selected":payload["selected"],"latest_test":payload["latest_test"],"by_competition":by},ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
