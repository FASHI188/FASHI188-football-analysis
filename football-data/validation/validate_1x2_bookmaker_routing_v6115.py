#!/usr/bin/env python3
"""Research-only cross-season bookmaker-disagreement routing for 1X2 Top-1.

Rather than averaging bookmakers, test whether a simple route learned on an older
season can choose the more reliable market source when bookmakers disagree.
No target-season outcome is used for route selection.
"""
from __future__ import annotations
import csv, json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path: sys.path.insert(0, str(ENGINE))
from platform_core import canonical_team_name, load_aliases, load_registry, parse_match_date

OUT = ROOT / "manifests" / "v6_1x2_bookmaker_routing_v6115_status.json"
DIRECTIONS=("home","draw","away")
CAL={"SWE_Allsvenskan","NOR_Eliteserien","JPN_J1","KOR_KLeague1","BRA_SerieA","ARG_Primera","USA_MLS"}
SOURCES={
 "pinnacle": (("PSCH","PSCD","PSCA"),("PSH","PSD","PSA")),
 "bet365": (("B365CH","B365CD","B365CA"),("B365H","B365D","B365A")),
 "average": (("AvgCH","AvgCD","AvgCA"),("AvgH","AvgD","AvgA")),
}

def odds(x:Any):
 try:v=float(str(x).strip())
 except: return None
 return v if math.isfinite(v) and v>1 else None

def probs(vals):
 q=[1/x for x in vals]; s=sum(q); return {k:q[i]/s for i,k in enumerate(DIRECTIONS)}

def extract(raw,source):
 for cols in SOURCES[source]:
  vals=[odds(raw.get(c)) for c in cols]
  if all(v is not None for v in vals): return probs(vals)
 return None

def actual(raw):
 try:h=int(float(raw.get("FTHG",""))); a=int(float(raw.get("FTAG","")))
 except:return None
 return "home" if h>a else "away" if h<a else "draw"

def seasons(cid): return ("2024","2025") if cid in CAL else ("2024/25","2025/26")

def pick(p): return max(DIRECTIONS,key=lambda k:p[k])
def maxp(p): return max(p.values())
def entropy(p): return -sum(v*math.log(max(v,1e-15)) for v in p.values())

def load_rows():
 aliases=load_aliases(); rows=[]; seen=set()
 for item in load_registry()["competitions"]:
  cid=str(item["competition_id"]); old,new=seasons(cid); d=ROOT/"processed"/cid
  if not d.exists(): continue
  for path in sorted(d.glob("*.csv")):
   with path.open("r",encoding="utf-8-sig",newline="") as f:
    for raw0 in csv.DictReader(f):
     raw={str(k):"" if v is None else str(v).strip() for k,v in raw0.items() if k}
     season=str(raw.get("season") or raw.get("Season") or "").strip()
     if season not in {old,new} or not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"): continue
     y=actual(raw)
     if y is None: continue
     ps={s:extract(raw,s) for s in SOURCES}
     if not any(ps.values()): continue
     try:dt=parse_match_date(raw["Date"],season)
     except:continue
     home=canonical_team_name(cid,raw["HomeTeam"],aliases); away=canonical_team_name(cid,raw["AwayTeam"],aliases)
     key=(cid,season,dt.date().isoformat(),home,away)
     if key in seen:continue
     seen.add(key); rows.append({"competition_id":cid,"season":season,"bucket":"older" if season==old else "newer","date":dt.isoformat(),"actual":y,"probs":ps})
 rows.sort(key=lambda r:(r["competition_id"],r["date"])); return rows

def preferred(r):
 for s in ("pinnacle","bet365","average"):
  if r["probs"].get(s): return r["probs"][s]
 raise RuntimeError("no source")

def source_prob(r,s): return r["probs"].get(s) or preferred(r)

def metrics(rows,fn):
 hits=0; pc=Counter()
 for r in rows:
  p=fn(r); z=pick(p); pc[z]+=1; hits+=int(z==r["actual"])
 return {"count":len(rows),"hits":hits,"accuracy":hits/len(rows) if rows else None,"pick_counts":dict(pc)}

def split_old(rows):
 g=defaultdict(list)
 for r in rows:
  if r["bucket"]=="older":g[r["competition_id"]].append(r)
 tr=[]; va=[]
 for cid,it in g.items():
  it.sort(key=lambda r:r["date"])
  if len(it)<20:continue
  c=max(1,min(len(it)-1,int(.8*len(it)))); tr+=it[:c]; va+=it[c:]
 return tr,va

def provider_accuracy(rows,source):
 valid=[r for r in rows if r["probs"].get(source)]
 if not valid:return (0,None)
 h=sum(pick(r["probs"][source])==r["actual"] for r in valid); return len(valid),h/len(valid)

def fit_comp_route(rows):
 route={}
 for cid in sorted({r["competition_id"] for r in rows}):
  sub=[r for r in rows if r["competition_id"]==cid]
  cand=[]
  for s in SOURCES:
   n,a=provider_accuracy(sub,s)
   if n>=20 and a is not None:cand.append((a,n,s))
  if cand: route[cid]=sorted(cand,key=lambda x:(x[0],x[1],x[2]),reverse=True)[0][2]
 return route

def route_fn(route): return lambda r: source_prob(r,route.get(r["competition_id"],"pinnacle"))

def most_confident(r):
 avail=[p for p in r["probs"].values() if p]
 return max(avail,key=maxp) if avail else preferred(r)

def lowest_entropy(r):
 avail=[p for p in r["probs"].values() if p]
 return min(avail,key=entropy) if avail else preferred(r)

def agree_then_confident(r):
 avail=[p for p in r["probs"].values() if p]
 if not avail:return preferred(r)
 counts=Counter(pick(p) for p in avail)
 common,n=counts.most_common(1)[0]
 if n>=2:
  same=[p for p in avail if pick(p)==common]; return max(same,key=maxp)
 return max(avail,key=maxp)

def main():
 rows=load_rows(); old=[r for r in rows if r["bucket"]=="older"]; new=[r for r in rows if r["bucket"]=="newer"]
 if len(old)<1000 or len(new)<1000: raise RuntimeError(f"insufficient rows old={len(old)} new={len(new)}")
 tr,va=split_old(rows)
 train_route=fit_comp_route(tr)
 candidates={
  "preferred":preferred,
  "pinnacle":lambda r:source_prob(r,"pinnacle"),
  "bet365":lambda r:source_prob(r,"bet365"),
  "average":lambda r:source_prob(r,"average"),
  "competition_route":route_fn(train_route),
  "most_confident":most_confident,
  "lowest_entropy":lowest_entropy,
  "agree_then_confident":agree_then_confident,
 }
 val={name:metrics(va,fn) for name,fn in candidates.items()}
 # Select only on older validation; ties prefer simpler/preferred.
 priority={"preferred":0,"pinnacle":1,"bet365":2,"average":3,"most_confident":4,"lowest_entropy":5,"agree_then_confident":6,"competition_route":7}
 selected=max(candidates,key=lambda n:(val[n]["accuracy"],-priority[n]))
 # Refit competition route on full older only after family selection.
 full_route=fit_comp_route(old)
 test_candidates=dict(candidates); test_candidates["competition_route"]=route_fn(full_route)
 test={name:metrics(new,fn) for name,fn in test_candidates.items()}
 base=test["preferred"]; sel=test[selected]
 bycomp={}
 for cid in sorted({r["competition_id"] for r in new}):
  sub=[r for r in new if r["competition_id"]==cid]
  if len(sub)<10:continue
  bm=metrics(sub,preferred); sm=metrics(sub,test_candidates[selected])
  bycomp[cid]={"count":len(sub),"preferred_accuracy":bm["accuracy"],"selected_accuracy":sm["accuracy"],"uplift_pp":(sm["accuracy"]-bm["accuracy"])*100}
 payload={
  "schema_version":"V6.11.5-bookmaker-disagreement-routing-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
  "sample":{"total":len(rows),"older":len(old),"newer":len(new),"older_train":len(tr),"older_validation":len(va)},
  "older_validation":{"competition_route":train_route,"candidates":val,"selected_family":selected},
  "newer_season_test":{"competition_route":full_route,"candidates":test,"selected_family":selected,"selected_vs_preferred_uplift_pp":(sel["accuracy"]-base["accuracy"])*100},
  "by_competition":bycomp,
  "governance":{"research_only":True,"newer_season_never_used_for_route_selection":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False}
 }
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8"); return 0
if __name__=="__main__": raise SystemExit(main())
