from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

INTERVALS=((0,30),(31,60),(61,75),(76,90),(91,130))
EVENTS=("substitution","red_card","var","injury","cooling_break","stoppage")


class ProcessHazardError(RuntimeError): pass

def _dt(v:str)->datetime:
    d=datetime.fromisoformat(v.replace("Z","+00:00"))
    if d.tzinfo is None: raise ProcessHazardError("timezone required")
    return d.astimezone(timezone.utc)

def _sha(v:Any)->str:
    return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()

def _bucket(minute:int)->str:
    for lo,hi in INTERVALS:
        if lo<=minute<=hi: return f"{lo}-{hi}"
    return "91-130"

def fit_hazards(rows:list[dict[str,Any]], *, cutoff:str)->dict[str,Any]:
    co=_dt(cutoff); counts=defaultdict(float); exposure=defaultdict(float); shas=[]
    for r in rows:
        if _dt(r["known_at"])>=co: raise ProcessHazardError("target/future process event reached estimator")
        event=str(r["event_type"])
        if event not in EVENTS: raise ProcessHazardError("unknown process event")
        b=_bucket(int(r["minute"])); counts[(event,b)]+=1.0; shas.append(str(r.get("source_sha256","")))
        exposure[b]+=float(r.get("match_exposure",1.0))
    if not rows:
        return {"status":"BLOCKED_DATA","hazards":{},"log_mu_home_delta":0.0,"log_mu_away_delta":0.0,"uncertainty":0.40,"evidence_sha256":None}
    buckets={f"{lo}-{hi}" for lo,hi in INTERVALS}
    hazards={f"{e}:{b}": (counts[(e,b)]+0.5)/(exposure[b]+2.0) for e in EVENTS for b in buckets}
    return {"status":"IMPLEMENTED","hazards":hazards,"log_mu_home_delta":0.0,"log_mu_away_delta":0.0,"uncertainty":1/(1+len(rows))**0.5,"evidence_sha256":_sha(shas)}
