#!/usr/bin/env python3
"""V6.3.0 two-level correctness selector on the corrected pooled sample cache.

Goal: improve selective hit-rate without changing the underlying 1X2 probability model.
The selector predicts whether the frozen V6.0.1 Top-1 pick will be correct.

Leakage controls:
- only V6.2.5 r4 pooled pre-match probabilities/features are used;
- older 850 is deterministically split outcome-blind into fit/calibration subsets;
- meta-model coefficients are fit only on the fit subset;
- l2 + execution threshold are selected only on the calibration subset;
- newer 850 is evaluated once, unchanged;
- no competition target encoding and no post-match feature enters X.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/"engine"; VALIDATION=ROOT/"validation"
for p in (ENGINE,VALIDATION):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import v6_direct_outcome_mvp_v600 as base
from platform_core import atomic_write_json

CACHE=ROOT/"manifests"/"v6_sampled_17domain_pooled_scored_cache_v625_r4.json"
OUT=ROOT/"manifests"/"v6_pooled_correctness_selector_v630_status.json"
SPLIT_SEED="V6.3.0-correctness-selector-fit-cal-v1"
L2_GRID=(0.1,1.0,10.0,100.0,1000.0)
TARGET=0.65
MIN_CAL_SELECTED=50
Z90=1.6448536269514722


def _entropy(p: dict[str,float])->float:
    return -sum(float(v)*math.log(max(1e-12,float(v))) for v in p.values())

def _margin(p: dict[str,float])->float:
    s=sorted(float(v) for v in p.values()); return s[-1]-s[-2]

def _features(r: dict[str,Any])->list[float]:
    q={k:float(r["q"][k]) for k in base.CLASSES}; f={k:float(r["formal"][k]) for k in base.CLASSES}
    pick=str(r["pick"]); agree=1.0 if pick==str(r["formal_pick"]) else 0.0
    return [
      1.0,
      q["home"],q["draw"],q["away"],max(q.values()),_margin(q),_entropy(q),
      f["home"],f["draw"],f["away"],max(f.values()),_margin(f),_entropy(f),
      agree,
      1.0 if pick=="home" else 0.0,1.0 if pick=="draw" else 0.0,1.0 if pick=="away" else 0.0,
      abs(q["home"]-f["home"]),abs(q["draw"]-f["draw"]),abs(q["away"]-f["away"]),
    ]

def _meta_row(r:dict[str,Any])->dict[str,Any]:
    return {**r,"meta_x":_features(r),"meta_y":1 if bool(r["hit"]) else 0}

def _split_key(r:dict[str,Any])->str:
    return hashlib.sha256((SPLIT_SEED+"|"+str(r["identity"])).encode()).hexdigest()

def _wilson(h:int,n:int)->float|None:
    if not n:return None
    p=h/n; z=Z90; d=1+z*z/n; c=p+z*z/(2*n); rad=z*math.sqrt((p*(1-p)+z*z/(4*n))/n); return (c-rad)/d

def _scored(rows:list[dict[str,Any]],model:dict[str,Any])->list[dict[str,Any]]:
    out=[]
    for r in rows:
        p=float(base._predict_binary(model,r["meta_x"]))
        out.append({**r,"p_correct":p})
    return out

def _prob_scores(rows:list[dict[str,Any]])->dict[str,Any]:
    if not rows:return {"count":0}
    brier=sum((float(r["p_correct"])-float(r["meta_y"]))**2 for r in rows)/len(rows)
    ll=-sum(float(r["meta_y"])*math.log(max(1e-12,float(r["p_correct"])))+(1-float(r["meta_y"]))*math.log(max(1e-12,1-float(r["p_correct"]))) for r in rows)/len(rows)
    return {"count":len(rows),"brier":brier,"log_loss":ll}

def _select(scored:list[dict[str,Any]])->dict[str,Any]|None:
    best=None
    for t in sorted({float(r["p_correct"]) for r in scored}):
        ch=[r for r in scored if float(r["p_correct"])>=t]
        if len(ch)<MIN_CAL_SELECTED: continue
        h=sum(int(bool(r["hit"])) for r in ch); a=h/len(ch)
        if a<TARGET: continue
        cand={"threshold":t,"count":len(ch),"hits":h,"accuracy":a,"wilson90_lower":_wilson(h,len(ch)),"coverage":len(ch)/len(scored)}
        rank=(cand["count"],cand["wilson90_lower"] or -1,cand["accuracy"])
        br=(-1,-1,-1) if best is None else (best["count"],best["wilson90_lower"] or -1,best["accuracy"])
        if rank>br:best=cand
    return best

def _evaluate(scored:list[dict[str,Any]],sel:dict[str,Any]|None,denom:int)->dict[str,Any]:
    if not sel:return {"status":"NO_SELECTION_RULE"}
    ch=[r for r in scored if float(r["p_correct"])>=float(sel["threshold"])]
    h=sum(int(bool(r["hit"])) for r in ch); a=h/len(ch) if ch else None
    by={}
    for d in ("home","draw","away"):
        p=[r for r in ch if r["pick"]==d]; ph=sum(int(bool(r["hit"])) for r in p)
        by[d]={"count":len(p),"hits":ph,"accuracy":ph/len(p) if p else None}
    return {"status":"PASS" if ch else "NO_SELECTIONS","count":len(ch),"hits":h,"accuracy":a,"wilson90_lower":_wilson(h,len(ch)),"coverage":len(ch)/denom,"by_direction":by,"target_65_met":bool(ch) and a>=TARGET}

def main()->int:
    generated=datetime.now(timezone.utc).replace(microsecond=0)
    cache=json.loads(CACHE.read_text())
    if cache.get("schema_version")!="V6.2.5-fixed-sampled-pooled-scored-cache-r4" or cache.get("count")!=1700: raise SystemExit("bad pooled cache")
    old=[_meta_row(r) for r in cache["rows"] if r["role"]=="older"]
    new=[_meta_row(r) for r in cache["rows"] if r["role"]=="newer"]
    order=sorted(old,key=_split_key); nfit=595
    fit=order[:nfit]; cal=order[nfit:]
    if len(fit)!=595 or len(cal)!=255 or len(new)!=850:raise SystemExit("split count failure")
    candidates=[]
    for l2 in L2_GRID:
        model=base._fit_binary(fit,"meta_x","meta_y",l2)
        cal_sc=_scored(cal,model); ps=_prob_scores(cal_sc); sel=_select(cal_sc)
        candidates.append({"l2":l2,"model":model,"calibration_probability_scores":ps,"selection":sel})
    eligible=[c for c in candidates if c["selection"]]
    if not eligible:
        payload={"schema_version":"V6.3.0-correctness-selector-r1","generated_at_utc":generated.isoformat(),"status":"NO_65_CALIBRATION_SELECTOR","candidates":[{"l2":c["l2"],"scores":c["calibration_probability_scores"]} for c in candidates]}
        atomic_write_json(OUT,payload);print(json.dumps(payload));return 0
    eligible.sort(key=lambda c:(-c["selection"]["count"],c["calibration_probability_scores"]["log_loss"],c["l2"]))
    chosen=eligible[0]
    test_sc=_scored(new,chosen["model"]); result=_evaluate(test_sc,chosen["selection"],850)
    payload={
      "schema_version":"V6.3.0-correctness-selector-r1","generated_at_utc":generated.isoformat(),"status":"PASS",
      "design":{"source":"V6.2.5 r4 exact pooled scored cache","older_850_fit":595,"older_850_calibration":255,"newer_850_test":850,"split_seed":SPLIT_SEED,"target":TARGET,"features":"probability shape + formal probability shape + agreement/disagreement only"},
      "selected":{"l2":chosen["l2"],"calibration_probability_scores":chosen["calibration_probability_scores"],"calibration_execution_rule":chosen["selection"],"fit_audit":chosen["model"]},
      "newer_850_test":result,
      "test_probability_scores":_prob_scores(test_sc),
      "governance":{"newer_850_used_for_fit":False,"newer_850_used_for_threshold_selection":False,"post_match_features_in_X":False,"fresh_disjoint_confirmation_required_if_promising":True,"formal_weight_change":False,"runtime_probability_change":False,"current_rule_change":False}
    }
    atomic_write_json(OUT,payload);print(json.dumps(payload,ensure_ascii=False,indent=2));return 0

if __name__=="__main__":raise SystemExit(main())
