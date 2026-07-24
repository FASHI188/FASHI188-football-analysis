#!/usr/bin/env python3
"""V6.14.2 diagnostic-only anatomy of the same final 100 O/U2.5 matches used by V6.14.1."""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import validate_total25_multibook_fast100_v6141 as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_total25_error_anatomy_fast100_v6142_status.json'

def pick(r):return 1 if r['consensus']>=0.5 else 0
def correct(r):return pick(r)==r['actual']
def pack(rows):
    h=sum(correct(r) for r in rows);return {'count':len(rows),'hits':h,'accuracy':h/len(rows) if rows else None}
def group(rows,key):
    d=defaultdict(list)
    for r in rows:d[str(key(r))].append(r)
    return {k:pack(v) for k,v in sorted(d.items())}
def band(r):
    c=max(r['consensus'],1-r['consensus'])
    if c<0.52:return '0.50-0.52'
    if c<0.54:return '0.52-0.54'
    if c<0.56:return '0.54-0.56'
    if c<0.58:return '0.56-0.58'
    if c<0.60:return '0.58-0.60'
    return '>=0.60'
def main():
    rows,_=base.load();test=rows[-100:]
    payload={'schema_version':'V6.14.2-total25-error-anatomy-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'DIAGNOSTIC_RETROSPECTIVE_RESEARCH_ONLY','governance':{'same_fixed_100_as_v6141':True,'no_model_fit':True,'no_rule_selection':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'sample':{'first':test[0]['date'],'last':test[-1]['date']},'overall':pack(test),'by_market_pick':group(test,lambda r:'OVER' if pick(r)==1 else 'UNDER'),'by_actual':group(test,lambda r:'OVER' if r['actual']==1 else 'UNDER'),'by_competition':group(test,lambda r:r['competition_id']),'by_confidence_band':group(test,band),'by_pick_and_confidence':group(test,lambda r:('OVER' if pick(r)==1 else 'UNDER')+'|'+band(r)),'provider_disagreement':{'unanimous':pack([r for r in test if r['unanimous']]),'not_unanimous':pack([r for r in test if not r['unanimous']])}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
