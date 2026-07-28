#!/usr/bin/env python3
"""V6.50.2 shape audit for prospective V6.50.0 KL-projected matrices.
Read-only: checks score Top-1 diversity, 1-1 concentration, Top1/Top2 gaps, unchanged total
marginals, and exact match of projected result marginals to frozen F05 1X2.
"""
from __future__ import annotations
import json, statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
LEDGER=ROOT/'forward'/'v6_kl_joint_projection_events_v6500.json'
OUT=ROOT/'manifests'/'v6_kl_shape_audit_v6502_status.json'
D=('home','draw','away'); T=('0','1','2','3','4','5','6','7+'); TOL=1e-9

def load(p:Path)->dict[str,Any]:
    x=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(x,dict): raise RuntimeError(str(p))
    return x

def main()->int:
    x=load(LEDGER); preds=[e for e in x.get('events',[]) if isinstance(e,dict) and e.get('event_type')=='KL_MATRIX_PREDICTION_FROZEN']
    errors=[]; rows=[]
    for e in preds:
        p=e.get('payload') or {}; q=p.get('projected') or {}; prior=p.get('source_f06_candidate') or {}; f05=p.get('source_f05_1x2') or {}
        qt=q.get('total') or {}; pt=prior.get('total') or {}; qr=q.get('result') or {}; sm=q.get('score_matrix') or {}; top=q.get('score_top10') or []
        total_delta=max(abs(float(qt[k])-float(pt[k])) for k in T)
        result_delta=max(abs(float(qr[d])-float(f05[d])) for d in D)
        vals=sorted(((str(k),float(v)) for k,v in sm.items()),key=lambda kv:(-kv[1],kv[0]))
        if not vals: errors.append(f"empty:{e.get('match_id')}"); continue
        top1=vals[0]; top2=vals[1] if len(vals)>1 else (None,None)
        supplied=(str(top[0].get('score')),float(top[0].get('probability'))) if top else (None,None)
        if supplied[0]!=top1[0] or abs(float(supplied[1])-top1[1])>TOL: errors.append(f"top_mismatch:{e.get('match_id')}")
        if total_delta>TOL: errors.append(f"total_changed:{e.get('match_id')}:{total_delta}")
        if result_delta>TOL: errors.append(f"result_not_f05:{e.get('match_id')}:{result_delta}")
        rows.append({'match_id':e.get('match_id'),'competition_id':(p.get('fixture_identity') or {}).get('competition_id'),'top1':top1[0],'top1_probability':top1[1],'top2':top2[0],'top2_probability':top2[1],'gap':top1[1]-top2[1] if top2[1] is not None else None,'total_delta_from_f06':total_delta,'result_delta_from_f05':result_delta})
    c=Counter(r['top1'] for r in rows); gaps=[r['gap'] for r in rows if r['gap'] is not None]; probs=[r['top1_probability'] for r in rows]
    checks={'ledger_present':bool(preds),'all_projected_surfaces_consistent':not errors,'no_probability_or_matrix_mutation':True}
    payload={'schema_version':'V6.50.2-kl-score-shape-audit-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'formal_current_version':'V5.0.1','status':'PASS' if all(checks.values()) else 'FAIL','prediction_count':len(rows),'score_shape':{'top1_counts':dict(sorted(c.items())),'unique_top1_score_count':len(c),'one_one_top1_count':int(c.get('1-1',0)),'one_one_top1_rate':c.get('1-1',0)/len(rows) if rows else 0.0,'mean_top1_probability':statistics.fmean(probs) if probs else None,'mean_top1_top2_gap':statistics.fmean(gaps) if gaps else None,'min_top1_top2_gap':min(gaps) if gaps else None,'max_top1_top2_gap':max(gaps) if gaps else None},'constraint_audit':{'max_total_delta_from_f06':max((r['total_delta_from_f06'] for r in rows),default=None),'max_result_delta_from_f05':max((r['result_delta_from_f05'] for r in rows),default=None),'errors':errors},'audit':{'checks':checks},'fixtures':rows,'governance':{'audit_only':True,'probability_mutation':False,'matrix_mutation':False,'formal_weight':0,'current_rule_change':False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps({'status':payload['status'],'prediction_count':len(rows),'score_shape':payload['score_shape'],'constraint_audit':payload['constraint_audit']},ensure_ascii=False,indent=2)); return 0 if payload['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
