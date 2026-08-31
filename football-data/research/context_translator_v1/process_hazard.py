from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

INTERVALS=((0,30),(31,60),(61,75),(76,90),(91,130))
EVENTS=("substitution","red_card","var","injury","cooling_break","stoppage")
FEATURES=("early_red","late_red","early_sub","late_sub","interruption","stoppage")


class ProcessHazardError(RuntimeError):pass


def _dt(v:str)->datetime:
    d=datetime.fromisoformat(v.replace("Z","+00:00"))
    if d.tzinfo is None:raise ProcessHazardError("timezone required")
    return d.astimezone(timezone.utc)


def _sha(v:Any)->str:
    return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()


def _bucket(minute:int)->str:
    for lo,hi in INTERVALS:
        if lo<=minute<=hi:return f"{lo}-{hi}"
    return "91-130"


def fit_hazards(rows:list[dict[str,Any]],*,cutoff:str)->dict[str,Any]:
    co=_dt(cutoff); counts=defaultdict(float); matches=set(); shas=[]
    for r in rows:
        if _dt(r["known_at"])>=co:raise ProcessHazardError("target/future process event reached estimator")
        event=str(r["event_type"])
        if event not in EVENTS:raise ProcessHazardError("unknown process event")
        counts[(event,_bucket(int(r["minute"])))]+=1.0; matches.add(str(r.get("match_id",r["known_at"]))); shas.append(str(r.get("source_sha256","")))
    if not rows:
        return {"status":"BLOCKED_DATA","hazards":{},"log_mu_home_delta":0.0,"log_mu_away_delta":0.0,"uncertainty":0.40,"evidence_sha256":None}
    n=max(len(matches),1); buckets={f"{lo}-{hi}" for lo,hi in INTERVALS}
    hazards={f"{e}:{b}":(counts[(e,b)]+0.5)/(n+2.0) for e in EVENTS for b in buckets}
    return {"status":"IMPLEMENTED","hazards":hazards,"log_mu_home_delta":0.0,"log_mu_away_delta":0.0,"uncertainty":min(1.0,1/math.sqrt(1+n)+0.10),"evidence_sha256":_sha(sorted(shas))}


def process_feature_vector(rows:list[dict[str,Any]],team_id:str,*,cutoff:str)->tuple[dict[str,float],float,str|None,int]:
    co=_dt(cutoff); usable=[]; matches=set(); shas=[]
    for r in rows:
        if str(r.get("team_id"))!=str(team_id):continue
        if _dt(str(r["known_at"]))>=co:raise ProcessHazardError("future process history")
        if str(r["event_type"]) not in EVENTS:raise ProcessHazardError("unknown process event")
        usable.append(r); matches.add(str(r.get("match_id",r["known_at"]))); shas.append(str(r.get("source_sha256","")))
    n=len(matches)
    if n==0:return {k:0.0 for k in FEATURES},1.0,None,0
    cnt=defaultdict(float)
    for r in usable:
        typ=str(r["event_type"]); minute=int(r["minute"])
        if typ=="red_card":cnt["early_red" if minute<=60 else "late_red"]+=1
        elif typ=="substitution":cnt["early_sub" if minute<=60 else "late_sub"]+=1
        elif typ in {"var","injury","cooling_break"}:cnt["interruption"]+=1
        elif typ=="stoppage":cnt["stoppage"]+=1
    x={k:(cnt[k]+0.25)/(n+1.0) for k in FEATURES}
    return x,min(1.0,1/math.sqrt(1+n)+0.10),_sha(sorted(shas)),n


def fit_hazard_coefficients(rows:list[dict[str,Any]],*,ridge:float=35.0)->dict[str,float]:
    out={}
    for k in FEATURES:
        xx=xy=0.0
        for r in rows:
            x=float(r.get("x",{}).get(k,0.0)); y=float(r.get("target_log_mu_residual",0.0)); w=max(0.0,float(r.get("weight",1.0)))
            xx+=w*x*x;xy+=w*x*y
        out[k]=max(-0.10,min(0.10,xy/(xx+ridge)))
    return out


def process_adjustment(rows:list[dict[str,Any]],home_team_id:str,away_team_id:str,*,cutoff:str,coeffs:dict[str,float])->dict[str,Any]:
    hx,hu,hsha,hn=process_feature_vector(rows,home_team_id,cutoff=cutoff); ax,au,asha,an=process_feature_vector(rows,away_team_id,cutoff=cutoff)
    if hn<3 or an<3:
        return {"status":"BLOCKED_DATA","log_mu_home_delta":0.0,"log_mu_away_delta":0.0,"uncertainty":max(hu,au),"evidence_sha256":None,"support":{"home":hn,"away":an}}
    raw=sum(float(coeffs.get(k,0.0))*(hx[k]-ax[k]) for k in FEATURES); delta=max(-0.18,min(0.18,raw))
    return {"status":"IMPLEMENTED","log_mu_home_delta":delta,"log_mu_away_delta":-delta,"uncertainty":max(hu,au),
            "evidence_sha256":_sha({"home":hsha,"away":asha,"hx":hx,"ax":ax,"coeffs":coeffs}),"support":{"home":hn,"away":an}}
