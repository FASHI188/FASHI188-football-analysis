#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,warnings
from collections import defaultdict
from pathlib import Path
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression

SRC_COMMIT="41a6eb96ba816757ff892387da5cb111390a7f9c"
FEATURE_SHA="c9b14435062f59f1caaf4ffdbf83373d7aa50b858a48057f66b89cc0605ecea4"
FEATURE_N=30531
PREREG_SHA="3566044d6144bab6beacf7b68e4b5f303cc7d66353ff6a3c2ad14fd96da6026e"
CARRIER_HEAD="2dd8be947b5922703bde7174ef6f6347295bfde8"
CARRIER_RECEIPT_SHA="e474a65ef1a5f370e6e4990faa0f030bdf2f65b024b670a445512bfb0ce8a6fc"
CARRIER_BIND_SHA="21e7c40566af4761f7b1b882c45ff0a18eb55132b503bd6b12d7d8efb8604262"
EXCLUDED="2023-24/en.1.json"
SEASONS=[f"{y}-{str(y+1)[-2:]}" for y in range(2010,2025)]
TEST=["2020-21","2021-22","2022-23","2023-24","2024-25"]
TRAINMAX=["2019-20","2020-21","2021-22","2022-23","2023-24"]
LEAGUES={"at.1.json":15,"de.1.json":15,"en.1.json":14,"es.1.json":13,"fr.1.json":11,"it.1.json":12,"nl.1.json":7,"pt.1.json":7}
NUM=["home_league_rest_days","away_league_rest_days","home_league_congestion_7d","away_league_congestion_7d","home_league_congestion_14d","away_league_congestion_14d","home_league_congestion_21d","away_league_congestion_21d","home_consecutive_away_before","away_consecutive_away_before"]
REST=set(NUM[:2]); CLS={"H":0,"D":1,"A":2}; INV=["H","D","A"]; EPS=1e-15
KEYS={"source_path","row_index","date","round","round_stage","round_number","team1","team2",*NUM}
class Stop(RuntimeError):pass
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1<<20),b""):h.update(b)
 return h.hexdigest()
def jload(p):return json.loads(Path(p).read_text(encoding="utf-8"))
def season(r):return r["source_path"].split("/",1)[0]
def comp(r):return r["source_path"].rsplit("/",1)[1][:-5]

def guard_inputs(features,contract,carrier):
 if sha(contract)!=PREREG_SHA:raise Stop("STOP_PREREG_SHA")
 if sha(Path(carrier)/"independent-prereg-acceptance-receipt.json")!=CARRIER_RECEIPT_SHA:raise Stop("STOP_CARRIER_RECEIPT_SHA")
 if sha(Path(carrier)/"head-binding.json")!=CARRIER_BIND_SHA:raise Stop("STOP_CARRIER_BIND_SHA")
 cr=jload(Path(carrier)/"independent-prereg-acceptance-receipt.json")
 if cr.get("independent_acceptance") is not True or cr.get("carrier_head")!=CARRIER_HEAD:raise Stop("STOP_CARRIER_ACCEPTANCE")
 if cr.get("feature_sha256")!=FEATURE_SHA or cr.get("feature_rows")!=FEATURE_N:raise Stop("STOP_CARRIER_FEATURE_BINDING")
 if sha(features)!=FEATURE_SHA:raise Stop("STOP_FEATURE_SHA")

def load_features(p):
 rows=[];seen=set()
 for line in Path(p).read_text(encoding="utf-8").splitlines():
  r=json.loads(line); k=(r["source_path"],int(r["row_index"]))
  if set(r)!=KEYS:raise Stop("STOP_FEATURE_KEYSET")
  if k in seen:raise Stop("STOP_FEATURE_DUPLICATE")
  if season(r) not in SEASONS or r["source_path"]==EXCLUDED:raise Stop("STOP_FEATURE_SCOPE")
  seen.add(k);rows.append(r)
 if len(rows)!=FEATURE_N:raise Stop("STOP_FEATURE_N")
 paths={r["source_path"] for r in rows}; counts=defaultdict(int)
 for pth in paths:counts[pth.rsplit("/",1)[1]]+=1
 if len(paths)!=94 or dict(counts)!=LEAGUES:raise Stop("STOP_FEATURE_INVENTORY")
 return rows

def labels(rows,root):
 g=defaultdict(list)
 for r in rows:g[r["source_path"]].append(r)
 out={};cnt=defaultdict(int)
 for pth in sorted(g):
  ms=jload(Path(root)/pth).get("matches")
  if not isinstance(ms,list):raise Stop("STOP_LABEL_SCHEMA")
  for r in g[pth]:
   i=int(r["row_index"])
   if i>=len(ms):raise Stop("STOP_LABEL_INDEX")
   m=ms[i]
   if any(m.get(k)!=r.get(k) for k in ("date","round","team1","team2")):raise Stop("STOP_LABEL_IDENTITY")
   ft=(m.get("score") or {}).get("ft")
   if not(isinstance(ft,list) and len(ft)==2 and all(type(x) is int for x in ft)):raise Stop("STOP_LABEL_FT")
   y="H" if ft[0]>ft[1] else "A" if ft[1]>ft[0] else "D"
   k=(pth,i)
   if k in out:raise Stop("STOP_LABEL_DUP")
   out[k]=y;cnt[y]+=1
 if len(out)!=len(rows):raise Stop("STOP_LABEL_JOIN")
 return out,dict(cnt)

def basefeat(r):return {f"c={comp(r)}":1.,f"h={r['team1']}":1.,f"a={r['team2']}":1.}
def prep(train):
 s={}
 for k in NUM:
  vals=[r[k] for r in train]
  med=float(np.median([float(x) for x in vals if x is not None])) if k in REST else 0.
  x=np.array([med if v is None else float(v) for v in vals])
  if not np.isfinite(x).all():raise Stop("STOP_NUMERIC")
  s[k]=(med,float(x.mean()),float(x.std()))
 return s
def candfeat(r,s):
 d=basefeat(r); non=r["round_stage"]!="REGULAR"
 if non:d[f"stage={r['round_stage']}"]=1.
 for k in NUM:
  med,mu,sd=s[k];v=r[k];miss=v is None
  if k in REST:d[f"miss={k}"]=float(miss)
  x=med if miss else float(v);z=0. if sd==0 else (x-mu)/sd
  d[f"n={k}"]=z
  if non:d[f"ix={k}"]=z
 return d

def predict(train,test,y,candidate):
 s=prep(train) if candidate else None; F=(lambda r:candfeat(r,s)) if candidate else basefeat
 v=DictVectorizer(sparse=True,sort=True); X=v.fit_transform([F(r) for r in train]);T=v.transform([F(r) for r in test])
 yy=np.array([CLS[y[(r["source_path"],int(r["row_index"]))]] for r in train])
 if sorted(set(yy))!=[0,1,2]:raise Stop("STOP_CLASSES")
 m=LogisticRegression(C=1.,penalty="l2",solver="lbfgs",max_iter=5000,tol=1e-9)
 with warnings.catch_warnings():
  warnings.filterwarnings("error",category=ConvergenceWarning)
  try:m.fit(X,yy)
  except ConvergenceWarning as e:raise Stop("STOP_CONVERGENCE") from e
 p=m.predict_proba(T)
 if m.classes_.tolist()!=[0,1,2] or not np.isfinite(p).all():raise Stop("STOP_PRED")
 return p

def metr(p,y):
 n=len(y);o=np.zeros((n,3));o[np.arange(n),y]=1
 return {"ll":-np.log(np.clip(p[np.arange(n),y],EPS,1)),"br":((p-o)**2).sum(1),"rps":((np.cumsum(p,1)[:,:2]-np.cumsum(o,1)[:,:2])**2).sum(1)/2,"hit":(p.argmax(1)==y).astype(int)}
def summary(b,c):
 z={}
 for k,n in [("ll","logloss"),("br","brier"),("rps","rps")]:
  x=float(b[k].mean());y=float(c[k].mean());z[n]={"baseline":x,"candidate":y,"delta":y-x}
 z["top1"]={"baseline_hits":int(b["hit"].sum()),"candidate_hits":int(c["hit"].sum()),"count":len(b["hit"])}
 return z
def bootstrap(paths,d):
 bs=sorted(set(paths));sm=np.array([d[[x==b for x in paths]].sum() for b in bs]);nn=np.array([paths.count(b) for b in bs]);rng=np.random.default_rng(20260914);v=[]
 for _ in range(5000):
  ix=rng.integers(0,len(bs),len(bs));v.append(float(sm[ix].sum()/nn[ix].sum()))
 lo,hi=np.quantile(v,[.05,.95]);return {"lower":float(lo),"upper":float(hi),"blocks":len(bs),"replicates":5000,"seed":20260914}
def gate(pool,folds,boot,quals):
 g={}
 g["G1_primary_pooled_logloss"]=pool["logloss"]["delta"]<0
 g["G2_primary_bootstrap"]=boot["upper"]<0
 g["G3_fold_direction"]=sum(x<0 for x in folds)>=4
 g["G4_fold_worst_case"]=max(folds)<=.01
 g["G5_brier"]=pool["brier"]["delta"]<=0
 g["G6_rps"]=pool["rps"]["delta"]<=0
 g["G7_top1_non_degradation"]=pool["top1"]["candidate_hits"]>=pool["top1"]["baseline_hits"]
 g["G8_competition_stability"]=bool(quals) and sum(x["delta"]<0 for x in quals)>len(quals)/2 and max(x["delta"] for x in quals)<=.01
 return g,"DEVELOPMENT_SIGNAL_PASS_RESEARCH_ONLY_NO_PROMOTION" if all(g.values()) else "SCIENTIFIC_EVALUATION_FAIL_CLOSE_NO_RETUNE"

def evaluate(rows,y):
 si={s:i for i,s in enumerate(SEASONS)}; rec=[];fold=[]
 for no,(ts,tm) in enumerate(zip(TEST,TRAINMAX),1):
  tr=[r for r in rows if si[season(r)]<=si[tm]];te=[r for r in rows if season(r)==ts]
  pb=predict(tr,te,y,False);pc=predict(tr,te,y,True);yy=np.array([CLS[y[(r["source_path"],int(r["row_index"]))]] for r in te]);mb=metr(pb,yy);mc=metr(pc,yy);ss=summary(mb,mc)
  fold.append({"fold":no,"test_season":ts,"train_rows":len(tr),"test_rows":len(te),**ss})
  for i,r in enumerate(te):rec.append({"source_path":r["source_path"],"row_index":int(r["row_index"]),"season":ts,"competition":comp(r),"target":INV[yy[i]],"b":[float(x) for x in pb[i]],"c":[float(x) for x in pc[i]],"bll":float(mb["ll"][i]),"cll":float(mc["ll"][i]),"bbr":float(mb["br"][i]),"cbr":float(mc["br"][i]),"brps":float(mb["rps"][i]),"crps":float(mc["rps"][i]),"bh":int(mb["hit"][i]),"ch":int(mc["hit"][i])})
 b={"ll":np.array([r["bll"] for r in rec]),"br":np.array([r["bbr"] for r in rec]),"rps":np.array([r["brps"] for r in rec]),"hit":np.array([r["bh"] for r in rec])}
 c={"ll":np.array([r["cll"] for r in rec]),"br":np.array([r["cbr"] for r in rec]),"rps":np.array([r["crps"] for r in rec]),"hit":np.array([r["ch"] for r in rec])}
 pool=summary(b,c);boot=bootstrap([r["source_path"] for r in rec],c["ll"]-b["ll"]);cs=[]
 for q in sorted({r["competition"] for r in rec}):
  rr=[r for r in rec if r["competition"]==q];ss={r["season"] for r in rr};cs.append({"competition":q,"seasons":len(ss),"matches":len(rr),"delta":float(np.mean([r["cll"]-r["bll"] for r in rr]))})
 quals=[x for x in cs if x["seasons"]>=3 and x["matches"]>=500];g,dec=gate(pool,[x["logloss"]["delta"] for x in fold],boot,quals)
 return {"decision":dec,"pooled":pool,"folds":fold,"bootstrap":boot,"competitions":cs,"qualifying_competitions":quals,"gates":g,"oos_rows":len(rec),"records":rec}

def main():
 a=argparse.ArgumentParser();a.add_argument("--features",required=True);a.add_argument("--source-root",required=True);a.add_argument("--prereg-contract",required=True);a.add_argument("--carrier-artifact",required=True);a.add_argument("--output-dir",required=True);x=a.parse_args();out=Path(x.output_dir);out.mkdir(parents=True,exist_ok=True)
 guard_inputs(x.features,x.prereg_contract,x.carrier_artifact);rows=load_features(x.features);y,cc=labels(rows,x.source_root);res=evaluate(rows,y)
 lab=out/"labels-1x2-minimal.jsonl";lab.write_text("".join(json.dumps({"source_path":r["source_path"],"row_index":r["row_index"],"target_1x2":y[(r["source_path"],r["row_index"])]},sort_keys=True,separators=(",",":"))+"\n" for r in rows))
 recs=res.pop("records");pred=out/"oos-paired-predictions.jsonl";pred.write_text("".join(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n" for r in recs))
 res.update({"schema_version":"football3-v3-schedule-importance-scientific-evaluation-result-v1","feature_sha256":FEATURE_SHA,"feature_rows":FEATURE_N,"label_sha256":sha(lab),"oos_predictions_sha256":sha(pred),"class_counts":cc})
 met=out/"scientific-evaluation-metrics.json";met.write_text(json.dumps(res,sort_keys=True,indent=2)+"\n")
 receipt={"schema_version":"football3-v3-schedule-importance-scientific-evaluation-receipt-v1","decision":res["decision"],"feature_rows":FEATURE_N,"feature_sha256":FEATURE_SHA,"source_commit":SRC_COMMIT,"source_files_read":94,"target_labels_opened":FEATURE_N,"target_label_values_retained":"H_D_A_ONLY","exact_score_values_retained":0,"goal_totals_retained":0,"future_matches_used":False,"stage6_1335_used":False,"standings_pressure_used":False,"rotation_pressure_used":False,"travel_pressure_used":False,"training_executed":True,"hyperparameter_search":False,"feature_selection":False,"threshold_search":False,"tuning":False,"formal_weight":0,"matrix_delta":0,"oos_rows":res["oos_rows"],"all_acceptance_gates_passed":all(res["gates"].values()),"metrics_sha256":sha(met),"minimal_labels_sha256":sha(lab),"oos_predictions_sha256":sha(pred),"next_step":"STOP_NO_RETUNE" if res["decision"].startswith("SCIENTIFIC_") else "RESEARCH_SIGNAL_ONLY_SEPARATE_PROMOTION_DECISION_REQUIRED"}
 (out/"scientific-evaluation-receipt.json").write_text(json.dumps(receipt,sort_keys=True,indent=2)+"\n");print(json.dumps(receipt,sort_keys=True))
if __name__=="__main__":main()
