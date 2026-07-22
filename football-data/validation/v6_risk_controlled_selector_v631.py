#!/usr/bin/env python3
"""V6.3.1 Wilson risk-controlled selective execution on corrected pooled cache.

Instead of choosing a calibration threshold where raw accuracy merely reaches 65%, require
the 90% Wilson lower bound itself to be >=65%. This intentionally builds a safety margin.
Two pre-registered arms are evaluated:
A) non-draw + formal/V6 agreement, both home and away;
B) same eligibility but home picks only.
Thresholds are selected on older 850 only; newer 850 is evaluation only.
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/"manifests"/"v6_sampled_17domain_pooled_scored_cache_v625_r4.json"
OUT=ROOT/"manifests"/"v6_risk_controlled_selector_v631_status.json"
TARGET_LB=0.65; MIN_SELECTED=50; Z=1.6448536269514722

def wilson(h:int,n:int)->float|None:
    if not n:return None
    p=h/n; d=1+Z*Z/n; c=p+Z*Z/(2*n); r=Z*math.sqrt((p*(1-p)+Z*Z/(4*n))/n); return (c-r)/d

def eligible(r:dict[str,Any],arm:str)->bool:
    base=bool(r.get('eligible_prior_selective')) and r.get('pick') in ('home','away')
    return base and (arm=='A_both' or r.get('pick')=='home')

def metric(rows:list[dict[str,Any]],denom:int)->dict[str,Any]:
    n=len(rows);h=sum(int(bool(r['hit'])) for r in rows)
    return {'count':n,'hits':h,'accuracy':h/n if n else None,'wilson90_lower':wilson(h,n),'coverage':n/denom if denom else 0.0}

def choose(rows:list[dict[str,Any]],arm:str)->dict[str,Any]|None:
    pool=[r for r in rows if eligible(r,arm)]
    best=None
    for t in sorted({float(r['confidence']) for r in pool}):
        ch=[r for r in pool if float(r['confidence'])>=t]
        if len(ch)<MIN_SELECTED:continue
        m=metric(ch,len(rows))
        if (m['wilson90_lower'] or -1)<TARGET_LB:continue
        cand={'threshold':t,**m}
        if best is None or cand['count']>best['count'] or (cand['count']==best['count'] and cand['accuracy']>best['accuracy']):best=cand
    return best

def apply(rows:list[dict[str,Any]],arm:str,rule:dict[str,Any]|None)->dict[str,Any]:
    if not rule:return {'status':'NO_RISK_CONTROLLED_RULE'}
    ch=[r for r in rows if eligible(r,arm) and float(r['confidence'])>=float(rule['threshold'])]
    m=metric(ch,len(rows));return {'status':'PASS' if ch else 'NO_SELECTIONS','threshold':rule['threshold'],**m,'raw_65_met':bool(ch) and float(m['accuracy'])>=0.65}

def main()->int:
    generated=datetime.now(timezone.utc).replace(microsecond=0)
    c=json.loads(CACHE.read_text()); old=[r for r in c['rows'] if r['role']=='older']; new=[r for r in c['rows'] if r['role']=='newer']
    if len(old)!=850 or len(new)!=850:raise SystemExit('cache role mismatch')
    arms={}
    for arm in ('A_both','B_home_only'):
        rule=choose(old,arm);arms[arm]={'calibration_rule':rule,'newer_850_test':apply(new,arm,rule)}
    payload={'schema_version':'V6.3.1-wilson-risk-controlled-r1','generated_at_utc':generated.isoformat(),'status':'PASS','design':{'source':'V6.2.5 r4 exact pooled cache','selection_data':'older 850 only','wilson90_lower_target':TARGET_LB,'min_calibration_selected':MIN_SELECTED,'arms':{'A_both':'non-draw + formal/V6 agreement','B_home_only':'same + home only'}},'arms':arms,'governance':{'newer_850_used_for_selection':False,'fresh_confirmation_required_if_promising':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
