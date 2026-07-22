#!/usr/bin/env python3
"""V6.4.6 multi-market risk veto.

Uses synchronized AH+OU information as a veto, not as a probability replacement.
The base direction remains the de-vigged closing 1X2 top-1. The multi-market model estimates
latent draw risk and decisive-side agreement; validation selects the maximum-coverage
execution rule subject to >=65% raw accuracy. Holdout is untouched during selection.

This tests whether extra market surfaces are more useful for selective risk control than
for rewriting calibrated 1X2 probabilities.
"""
from __future__ import annotations
import json, math, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];VALIDATION=ROOT/'validation'
if str(VALIDATION) not in sys.path:sys.path.insert(0,str(VALIDATION))
import v6_multimarket_draw_side_v643 as mm
import v6_market_residual_fusion_v620 as mkt
import v6_direct_outcome_mvp_v600 as base
from platform_core import PlatformError
OUT=ROOT/'manifests'/'v6_multimarket_risk_veto_v646_status.json'
CONF_GRID=(0.00,0.05,0.10,0.15,0.20,0.25,0.30,0.35,0.40)
DRAW_MAX_GRID=(0.18,0.20,0.22,0.24,0.26,0.28,0.30,0.32,0.35,0.40)
SIDE_AGREE_GRID=(False,True)
TARGET=.65;MIN_VALID=120
Z90=1.6448536269514722

def wilson(h,n):
    if not n:return None
    p=h/n;z2=Z90*Z90;den=1+z2/n;ctr=p+z2/(2*n);sp=Z90*math.sqrt((p*(1-p)+z2/(4*n))/n);return (ctr-sp)/den

def build():
    by={s:[] for s in mm.SEASONS}
    for cid,code in mm.LEAGUES.items():
        b=mkt._build_domain_rows_with_identity(cid,mm.SEASONS)
        for s in mm.SEASONS:
            raw,_=mkt._download_csv(code,mm.SEASON_CODES[s]);matched,_=mm.match_rows(cid,b[s],raw);by[s]+=[mm.features(r) for r in matched]
    return by

def enrich(rows,model):
    out=[]
    for r in rows:
        market=r['surface']['one'];vals=sorted(((float(market[k]),k) for k in base.CLASSES),reverse=True);pick=vals[0][1];confidence=vals[0][0]-vals[1][0]
        qmm=mm.prob(r,model);side_market='home' if market['home']>=market['away'] else 'away';side_mm='home' if qmm['home']>=qmm['away'] else 'away'
        out.append({'truth':r['actual_result'],'pick':pick,'hit':int(pick==r['actual_result']),'confidence':confidence,'draw_risk':qmm['draw'],'side_agree':side_market==side_mm,'competition_id':r['competition_id']})
    return out

def select(rows,c,d,a):return [r for r in rows if r['pick']!='draw' and r['confidence']>=c and r['draw_risk']<=d and (not a or r['side_agree'])]
def summary(rows,total):
    h=sum(r['hit'] for r in rows);return {'count':len(rows),'hits':h,'accuracy':h/len(rows) if rows else None,'wilson90_lower':wilson(h,len(rows)),'coverage':len(rows)/total if total else 0.0,'by_direction':{p:{'count':sum(r['pick']==p for r in rows),'hits':sum(r['hit'] for r in rows if r['pick']==p)} for p in ('home','away')}}
def main():
    by=build();fitrows=by['2022/23']+by['2023/24'];valid=by['2024/25'];hold=by['2025/26']
    # Freeze multi-market estimator only on pre-holdout data. L2 chosen from V6.4.3 validation; this script does not reselect it on holdout.
    model=mm.fit(fitrows,100.0);v=enrich(valid,model);h=enrich(hold,mm.fit(fitrows+valid,100.0))
    candidates=[]
    for c in CONF_GRID:
      for d in DRAW_MAX_GRID:
       for a in SIDE_AGREE_GRID:
        s=summary(select(v,c,d,a),len(v))
        if s['count']>=MIN_VALID and s['accuracy'] is not None and s['accuracy']>=TARGET:candidates.append({'confidence_min':c,'draw_risk_max':d,'side_agreement_required':a,'validation':s})
    if not candidates:
        out={'schema_version':'V6.4.6-multimarket-risk-veto-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'NO_65_VALIDATION_RULE','validation_count':len(v),'holdout_count':len(h),'governance':{'holdout_used_for_selection':False,'current_rule_change':False}};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out));return 0
    candidates.sort(key=lambda x:(-x['validation']['coverage'],-x['validation']['accuracy'],x['confidence_min'],x['draw_risk_max']));sel=candidates[0];test=summary(select(h,sel['confidence_min'],sel['draw_risk_max'],sel['side_agreement_required']),len(h))
    # benchmark confidence-only rule selected independently on validation with same target/min count
    bench=[]
    for c in CONF_GRID:
        s=summary([r for r in v if r['pick']!='draw' and r['confidence']>=c],len(v))
        if s['count']>=MIN_VALID and s['accuracy']>=TARGET:bench.append({'confidence_min':c,'validation':s})
    bsel=sorted(bench,key=lambda x:(-x['validation']['coverage'],-x['validation']['accuracy']))[0] if bench else None
    btest=summary([r for r in h if r['pick']!='draw' and r['confidence']>=bsel['confidence_min']],len(h)) if bsel else None
    out={'schema_version':'V6.4.6-multimarket-risk-veto-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','design':{'fit_count':len(fitrows),'validation_count':len(v),'holdout_count':len(h),'target_accuracy':TARGET,'minimum_validation_selection':MIN_VALID,'holdout_used_for_selection':False,'probability_replacement':False},'selected_veto_rule':sel,'holdout_veto_result':test,'confidence_only_benchmark_rule':bsel,'confidence_only_holdout':btest,'holdout_accuracy_delta_pp_vs_confidence_only':100*(test['accuracy']-btest['accuracy']) if btest and test['accuracy'] is not None and btest['accuracy'] is not None else None,'holdout_coverage_delta_pp_vs_confidence_only':100*(test['coverage']-btest['coverage']) if btest else None,'raw_65_holdout_met':bool(test['accuracy'] is not None and test['accuracy']>=TARGET),'governance':{'selective_execution_research_only':True,'holdout_used_for_selection':False,'probabilities_unchanged':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
