#!/usr/bin/env python3
"""V6.3.2 pristine forward epoch for the V6.3.1 Wilson risk-controlled selectors.

This starts a NEW research epoch and never changes V6.1.0/V6.1.2/V6.1.3 artifacts.
It reuses only immutable ledger-native pre-kickoff predictions already produced by V6.1.2.
Only predictions frozen on/after the V6.3.2 freeze timestamp may enter evaluation.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/"engine"; VALIDATION=ROOT/"validation"
for p in (ENGINE,VALIDATION):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import v6_pristine_forward_evaluate_v611_r2 as eval611
from platform_core import PlatformError, atomic_write_json, load_json, parse_iso_datetime

RULE=ROOT/"manifests"/"v6_risk_controlled_selector_v631_status.json"
V610=ROOT/"manifests"/"v6_pristine_forward_freeze_v610_status.json"
LEDGER=ROOT/"forward"/"v6_pristine_forward_events_v612.json"
AUDIT=ROOT/"manifests"/"v6_pristine_forward_audit_v613_status.json"
FREEZE=ROOT/"manifests"/"v6_risk_controlled_forward_freeze_v632.json"
OUT=ROOT/"manifests"/"v6_risk_controlled_forward_evaluation_v632_status.json"
SCHEMA="V6.3.2-risk-controlled-pristine-forward-r1"
Z90=1.6448536269514722


def _sha(path:Path)->str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _wilson(h:int,n:int)->float|None:
    if not n:return None
    p=h/n; z=Z90; d=1+z*z/n; c=p+z*z/(2*n); r=z*math.sqrt((p*(1-p)+z*z/(4*n))/n); return (c-r)/d

def _selected(row:dict[str,Any], rule:dict[str,Any])->bool:
    if not bool(row.get("agreement")): return False
    pick=str(row.get("pick"))
    if pick=="draw": return False
    if rule["direction"]=="home" and pick!="home": return False
    return float(row.get("confidence",0.0))>=float(rule["threshold"])

def _summary(rows:list[dict[str,Any]],rule:dict[str,Any],total:int)->dict[str,Any]:
    sel=[r for r in rows if _selected(r,rule)]
    h=sum(int(r["hit"]) for r in sel)
    comps=Counter(str(r["competition_id"]) for r in sel)
    by={}
    for d in ("home","away"):
        p=[r for r in sel if r["pick"]==d]; ph=sum(int(r["hit"]) for r in p)
        by[d]={"count":len(p),"hits":ph,"accuracy":ph/len(p) if p else None,"wilson90_lower":_wilson(ph,len(p))}
    return {"count":len(sel),"hits":h,"accuracy":h/len(sel) if sel else None,"wilson90_lower":_wilson(h,len(sel)),"coverage":len(sel)/total if total else 0.0,"competitions_represented":len(comps),"by_direction":by,"by_competition":{c:{"count":n,"hits":sum(int(r['hit']) for r in sel if r['competition_id']==c)} for c,n in sorted(comps.items())}}

def _create_or_load_freeze(generated:datetime)->dict[str,Any]:
    if FREEZE.exists():
        f=load_json(FREEZE)
        if f.get("schema_version")!="V6.3.2-risk-controlled-forward-freeze-r1": raise PlatformError("unexpected V632 freeze schema")
        return f
    r=load_json(RULE)
    if r.get("status")!="PASS": raise PlatformError("V6.3.1 rule receipt must PASS")
    a=((r.get("arms") or {}).get("A_both") or {}).get("calibration_rule") or {}
    b=((r.get("arms") or {}).get("B_home_only") or {}).get("calibration_rule") or {}
    if not a or not b: raise PlatformError("V6.3.1 frozen arms missing")
    f={
      "schema_version":"V6.3.2-risk-controlled-forward-freeze-r1",
      "status":"FROZEN",
      "freeze_timestamp_utc":generated.isoformat(),
      "source_rule_path":"manifests/v6_risk_controlled_selector_v631_status.json",
      "source_rule_sha256":_sha(RULE),
      "rules":{
        "arm_a_both":{"direction":"both","threshold":float(a["threshold"]),"agreement_required":True,"draws_excluded":True},
        "arm_b_home_only":{"direction":"home","threshold":float(b["threshold"]),"agreement_required":True,"draws_excluded":True},
      },
      "forward_gates":{"minimum_valid_settled":500,"minimum_arm_a":50,"minimum_arm_b":40,"minimum_competitions":8,"wilson90_lower_minimum":0.65},
      "governance":{"historical_backfill":False,"automatic_promotion":False,"current_rule_change":False,"formal_weight_change":False,"runtime_probability_change":False,"v610_v613_unchanged":True}
    }
    atomic_write_json(FREEZE,f); return f

def main()->int:
    generated=datetime.now(timezone.utc).replace(microsecond=0)
    freeze=_create_or_load_freeze(generated)
    frozen_at=parse_iso_datetime(freeze["freeze_timestamp_utc"],"v632_freeze")
    v610=load_json(V610)
    audit=load_json(AUDIT)
    if str(audit.get("status") or "").startswith("FAIL_") or audit.get("evaluation_blocked") is True:
        payload={"schema_version":SCHEMA,"generated_at_utc":generated.isoformat(),"status":"FAIL_V613_AUDIT_GATE","evaluation_status":"BLOCKED","governance":{"automatic_promotion":False}}
        atomic_write_json(OUT,payload); print(json.dumps(payload)); return 1
    ledger=load_json(LEDGER) if LEDGER.exists() else {"schema_version":eval611.ledgerlib.LEDGER_SCHEMA,"events":[]}
    chain=eval611.ledgerlib._audit_chain(ledger)
    rows_all,errors,_=eval611._materialize(v610,ledger)
    if chain.get("status")!="PASS" or errors:
        payload={"schema_version":SCHEMA,"generated_at_utc":generated.isoformat(),"status":"FAIL_LEDGER_INTEGRITY","evaluation_status":"BLOCKED","chain":chain,"semantic_errors":errors}
        atomic_write_json(OUT,payload); print(json.dumps(payload)); return 1
    pred_time={}
    pred_ids=set(); settled_ids=set()
    for e in ledger.get("events",[]):
        mid=str(e.get("match_id") or "")
        if e.get("event_type")=="PREDICTION_FROZEN":
            t=parse_iso_datetime(str(e.get("event_timestamp_utc")),"prediction_event_timestamp")
            if t>=frozen_at: pred_ids.add(mid); pred_time[mid]=t
        elif e.get("event_type")=="RESULT_SETTLED": settled_ids.add(mid)
    invalid={str(x) for x in (audit.get("invalidated_match_ids") or [])}
    rows=[r for r in rows_all if r["match_id"] in pred_ids and r["match_id"] not in invalid]
    excluded=[r for r in rows_all if r["match_id"] in pred_ids and r["match_id"] in invalid]
    summaries={name:_summary(rows,rule,len(rows)) for name,rule in freeze["rules"].items()}
    gates=freeze["forward_gates"]; a=summaries["arm_a_both"]; b=summaries["arm_b_home_only"]
    minimum=(len(rows)>=int(gates["minimum_valid_settled"]) and a["count"]>=int(gates["minimum_arm_a"]) and b["count"]>=int(gates["minimum_arm_b"]) and a["competitions_represented"]>=int(gates["minimum_competitions"]))
    passgate=minimum and a["wilson90_lower"] is not None and float(a["wilson90_lower"])>=float(gates["wilson90_lower_minimum"])
    if not rows: status="PENDING_NO_VALID_SETTLED_FORWARD_PREDICTIONS"
    elif not minimum: status="PENDING_MINIMUM_SAMPLE"
    elif passgate: status="FORWARD_GATE_PASS_REQUIRES_MANUAL_REVIEW"
    else: status="FORWARD_GATE_FAIL"
    payload={
      "schema_version":SCHEMA,"generated_at_utc":generated.isoformat(),"status":"PASS","evaluation_status":status,
      "freeze_timestamp_utc":freeze["freeze_timestamp_utc"],"source_rule_sha256":freeze["source_rule_sha256"],
      "valid_settled_since_freeze":len(rows),"open_predictions_since_freeze":len(pred_ids-settled_ids),"excluded_invalidated_settled":len(excluded),"v613_audit_status":audit.get("status"),"ledger_chain_status":chain.get("status"),
      "arms":summaries,"minimum_sample_gate_met":minimum,"promotion_gate_passed":passgate,
      "governance":{"ledger_native_pre_match_only":True,"predictions_before_v632_freeze_excluded":True,"v613_invalidated_excluded":True,"automatic_promotion":False,"manual_review_required":True,"formal_weight_change":False,"runtime_probability_change":False,"current_rule_change":False}
    }
    atomic_write_json(OUT,payload); print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
