#!/usr/bin/env python3
"""V6.19.2 prospective evaluator for the frozen independent 1X2 primary tiers.

Read-only with respect to prediction/result ledgers. Only MARKET_PREDICTION_FROZEN events
whose event_timestamp_utc is on/after the immutable V6.19.2 freeze are eligible. Earlier
predictions are never backfilled into this cohort.
"""
from __future__ import annotations
import json,math
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FREEZE=ROOT/'manifests'/'v6_1x2_independent_primary_prospective_freeze_v6192_status.json'
LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json'
OUT=ROOT/'manifests'/'v6_1x2_independent_primary_forward_eval_v6192_status.json'
DIRS=('home','draw','away')

def dt(x):return datetime.fromisoformat(str(x).replace('Z','+00:00'))
def wilson(k,n,z=1.96):
    if not n:return {'lower':None,'upper':None}
    p=k/n;den=1+z*z/n;c=(p+z*z/(2*n))/den;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return {'lower':c-h,'upper':c+h}
def metrics(rows):
    n=len(rows);settled=[r for r in rows if r.get('actual') in DIRS];sn=len(settled);hits=sum(r['pick']==r['actual'] for r in settled)
    brier=logloss=None
    if sn:
        brier=sum(sum((r['probabilities'][d]-(1.0 if d==r['actual'] else 0.0))**2 for d in DIRS) for r in settled)/sn
        logloss=sum(-math.log(max(1e-15,r['probabilities'][r['actual']])) for r in settled)/sn
    return {'prediction_count':n,'settled_count':sn,'hits':hits,'accuracy':hits/sn if sn else None,'wilson95':wilson(hits,sn),'brier':brier,'logloss':logloss,'pick_counts':dict(Counter(r['pick'] for r in rows)),'actual_counts':dict(Counter(r['actual'] for r in settled))}
def main():
    f=json.loads(FREEZE.read_text(encoding='utf-8'));l=json.loads(LEDGER.read_text(encoding='utf-8'))
    if f.get('status')!='PASS_FROZEN_NO_BACKFILL':raise SystemExit('invalid V6.19.2 freeze')
    if l.get('schema_version')!='V6.5.1-market-first-forward-ledger-r1':raise SystemExit('invalid market ledger')
    boundary=dt(f['prospective_epoch']['freeze_at_utc']);preds={};results={}
    for e in l.get('events') or []:
        if not isinstance(e,dict):continue
        mid=str(e.get('match_id') or '');typ=e.get('event_type')
        if typ=='MARKET_PREDICTION_FROZEN' and mid and dt(e.get('event_timestamp_utc'))>=boundary:
            if mid in preds:raise SystemExit(f'duplicate post-freeze prediction {mid}')
            p=(e.get('payload') or {}).get('prediction') or {};pr=p.get('probabilities') or {};vals={d:float(pr[d]) for d in DIRS};s=sum(vals.values())
            if abs(s-1)>1e-8 or any(v<0 or v>1 for v in vals.values()):raise SystemExit(f'invalid probabilities {mid}')
            pick=max(DIRS,key=lambda d:vals[d]);top=vals[pick]
            if str(p.get('pick'))!=pick:raise SystemExit(f'pick mismatch {mid}')
            identity=(e.get('payload') or {}).get('fixture_identity') or {}
            preds[mid]={'match_id':mid,'competition_id':identity.get('competition_id'),'event_timestamp_utc':e.get('event_timestamp_utc'),'kickoff_at':identity.get('kickoff_at'),'probabilities':vals,'pick':pick,'max_probability':top,'actual':None}
        elif typ=='RESULT_SETTLED' and mid:
            result=((e.get('payload') or {}).get('result') or {});actual=str(result.get('actual_result') or '')
            if actual in DIRS:results[mid]=actual
    rows=[]
    for mid,r in preds.items():
        q=dict(r);q['actual']=results.get(mid);rows.append(q)
    rows.sort(key=lambda r:(str(r.get('kickoff_at') or ''),str(r.get('competition_id') or ''),r['match_id']))
    p60=[r for r in rows if r['max_probability']>=0.60];p58band=[r for r in rows if 0.58<=r['max_probability']<0.60];p58plus=[r for r in rows if r['max_probability']>=0.58]
    bycomp={}
    for cid in sorted({str(r.get('competition_id') or '') for r in rows if r.get('competition_id')}):
        sub=[r for r in rows if r.get('competition_id')==cid];bycomp[cid]={'all':metrics(sub),'p60':metrics([r for r in sub if r['max_probability']>=0.60]),'p58_plus':metrics([r for r in sub if r['max_probability']>=0.58])}
    primary=metrics(p60);secondary=metrics(p58band);combined=metrics(p58plus);allm=metrics(rows)
    if primary['settled_count']>=300:
        review='PASS_PROMOTION_REVIEW_METRICS' if primary['accuracy'] is not None and primary['accuracy']>=0.70 and primary['wilson95']['lower'] is not None and primary['wilson95']['lower']>=0.65 and sum(1 for x in bycomp.values() if x['p60']['settled_count']>0)>=5 else 'FAIL_PROMOTION_REVIEW_METRICS'
    elif primary['settled_count']>=100:review='INTERIM_100_AVAILABLE_NO_PROMOTION'
    else:review='PENDING_PRIMARY_100'
    payload={'schema_version':'V6.19.2-independent-1x2-primary-forward-eval-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'PROSPECTIVE_RESEARCH_EVALUATION','freeze_at_utc':f['prospective_epoch']['freeze_at_utc'],'no_backfill':True,'review_state':review,'all_post_freeze_predictions':allm,'primary_p60':primary,'secondary_p58_to_p60':secondary,'combined_p58_plus':combined,'by_competition':bycomp,'next_unsettled_primary':[r for r in p60 if r['actual'] is None][:20],'governance':{'read_only':True,'prediction_mutation':False,'settlement_mutation':False,'threshold_mutation':False,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
