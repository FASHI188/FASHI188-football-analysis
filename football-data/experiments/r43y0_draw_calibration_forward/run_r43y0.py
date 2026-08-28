#!/usr/bin/env python3
from __future__ import annotations

import hashlib, json, math, sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'forward'/'r43u1_pristine_forward_events.json'
LEDGER=ROOT/'forward'/'r43y0_draw_calibration_forward_events.json'
OUT=ROOT/'experiments'/'r43y0_draw_calibration_forward'/'results'/'summary_r43y0_draw_calibration_lock.json'
SCHEMA='football3-r43y0-draw-calibration-forward-ledger-v1'
EVENT_SCHEMA='football3-r43y0-draw-calibration-forward-event-v1'
CLASSES=('home','draw','away')
MIN_LEAD=timedelta(minutes=30)
SOURCE_RUN_ID=33178193071
SOURCE_BRANCH='football3/r43x0-high-confidence-coverage'
SOURCE_U0_N=53
SOURCE_DRAW_MEAN=0.29263991077316237
SOURCE_DRAW_RATE=0.32075471698113206

def logit(p):
    p=min(max(float(p),1e-12),1-1e-12);return math.log(p/(1-p))
def sigmoid(x):return 1/(1+math.exp(-x))
DRAW_LOGIT_INTERCEPT=logit(SOURCE_DRAW_RATE)-logit(SOURCE_DRAW_MEAN)

def load(p:Path):return json.loads(p.read_text(encoding='utf-8'))
def canon(x)->bytes:return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def sha(x)->str:return hashlib.sha256(canon(x)).hexdigest()
def utcnow():return datetime.now(timezone.utc).replace(microsecond=0)
def iso(x):
    d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timezone missing')
    return d.astimezone(timezone.utc)
def top1(p):return max(CLASSES,key=lambda k:(float(p[k]),-CLASSES.index(k)))

def calibrate(p):
    ph,pd,pa=float(p['home']),float(p['draw']),float(p['away'])
    qd=sigmoid(logit(pd)+DRAW_LOGIT_INTERCEPT)
    side=ph+pa
    if side<=0:raise ValueError('invalid home/away mass')
    rest=1-qd
    q={'home':rest*ph/side,'draw':qd,'away':rest*pa/side}
    s=sum(q.values());q={k:v/s for k,v in q.items()}
    return q

def load_ledger():
    if not LEDGER.exists():return {'schema_version':SCHEMA,'events':[]}
    x=load(LEDGER)
    if x.get('schema_version')!=SCHEMA or not isinstance(x.get('events'),list):raise RuntimeError('invalid R43Y0 ledger')
    return x

def append_event(doc,mid,payload,ts):
    prev=doc['events'][-1]['event_hash'] if doc['events'] else None
    body={'schema_version':EVENT_SCHEMA,'event_type':'PREDICTION_FROZEN','match_id':mid,'event_timestamp_utc':ts,'prev_event_hash':prev,'payload':payload}
    body['event_hash']=sha(body);doc['events'].append(body);return body

def audit(doc):
    prev=None;seen=set();errors=[]
    for e in doc.get('events',[]):
        body={k:v for k,v in e.items() if k!='event_hash'}
        mid=str(e.get('match_id') or '')
        if sha(body)!=e.get('event_hash'):errors.append(f'hash:{mid}')
        if e.get('prev_event_hash')!=prev:errors.append(f'prev:{mid}')
        if mid in seen:errors.append(f'duplicate:{mid}')
        p=e.get('payload',{}).get('r43y0_probabilities') or {}
        if set(p)!=set(CLASSES) or abs(sum(float(p[k]) for k in CLASSES)-1)>1e-9:errors.append(f'probabilities:{mid}')
        if e.get('payload',{}).get('top1')!=top1(p):errors.append(f'top1:{mid}')
        if e.get('payload',{}).get('draw_forced') is not False:errors.append(f'draw_forced:{mid}')
        seen.add(mid);prev=e.get('event_hash')
    return {'status':'PASS' if not errors else 'FAIL','event_count':len(doc.get('events',[])),'head_hash':prev,'errors':errors}

def run():
    source=load(SOURCE);base={str(e['match_id']):e for e in source.get('events',[]) if e.get('event_type')=='PREDICTION_FROZEN'}
    doc=load_ledger();existing={str(e['match_id']) for e in doc['events']};locked=[];skipped=[];t=utcnow()
    for mid,e in sorted(base.items(),key=lambda kv:(kv[1]['payload']['fixture_identity']['kickoff_at'],kv[0])):
        if mid in existing:continue
        kickoff=iso(e['payload']['fixture_identity']['kickoff_at'])
        if kickoff-t<MIN_LEAD:
            skipped.append({'match_id':mid,'reason':'lead_under_30m_or_started','kickoff_at':kickoff.isoformat()});continue
        p=e['payload']['r43u0_probabilities'];q=calibrate(p);bt=top1(p);ct=top1(q)
        payload={'fixture_identity':e['payload']['fixture_identity'],'source_r43u1_prediction_event_hash':e['event_hash'],'source_r43u0_probabilities':p,'calibration':{'method':'draw_calibration_in_the_large_logit_intercept','source_run_id':SOURCE_RUN_ID,'source_branch':SOURCE_BRANCH,'development_source_n':SOURCE_U0_N,'development_mean_pred_draw':SOURCE_DRAW_MEAN,'development_actual_draw_rate':SOURCE_DRAW_RATE,'fixed_draw_logit_intercept':DRAW_LOGIT_INTERCEPT,'home_away_remaining_mass_ratio_preserved':True,'parameter_search':False,'threshold_search':False},'r43y0_probabilities':q,'base_top1':bt,'top1':ct,'top1_changed':ct!=bt,'changed_to_draw':ct=='draw' and bt!='draw','outcome_access_at_lock':False,'draw_forced':False}
        ne=append_event(doc,mid,payload,t.isoformat());existing.add(mid)
        locked.append({'match_id':mid,'kickoff_at':kickoff.isoformat(),'competition_id':payload['fixture_identity']['competition_id'],'home_team':payload['fixture_identity']['home_team'],'away_team':payload['fixture_identity']['away_team'],'base_top1':bt,'top1':ct,'base_probabilities':p,'probabilities':q,'event_hash':ne['event_hash']})
    a=audit(doc)
    if a['status']!='PASS':raise RuntimeError(a)
    LEDGER.parent.mkdir(parents=True,exist_ok=True);LEDGER.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    base_counts=Counter();cand_counts=Counter();changed=0;to_draw=0
    for e in doc['events']:
        pl=e['payload'];base_counts[pl['base_top1']]+=1;cand_counts[pl['top1']]+=1;changed+=int(pl['top1_changed']);to_draw+=int(pl['changed_to_draw'])
    summary={'schema_version':'football3-r43y0-draw-calibration-forward-lock-summary-v1','status':'COMPLETE','classification':'PRISTINE_PARALLEL_FORWARD_LOCK_NO_OUTCOME_ACCESS','formal_weight':'PENDING_SETTLEMENT','generated_at_utc':t.isoformat(),'governance':{'source_r43u1_predictions_read_only':True,'outcome_access_at_prediction_lock':False,'parameter_search':False,'threshold_search':False,'draw_override':False,'home_away_remaining_mass_ratio_preserved':True,'retrospective_backfill_allowed':False,'main_merge':False,'publication':False},'development_calibration_source':{'source_run_id':SOURCE_RUN_ID,'source_branch':SOURCE_BRANCH,'n':SOURCE_U0_N,'mean_pred_draw':SOURCE_DRAW_MEAN,'actual_draw_rate':SOURCE_DRAW_RATE,'fixed_draw_logit_intercept':DRAW_LOGIT_INTERCEPT},'coverage':{'source_r43u1_predictions':len(base),'new_predictions_locked':len(locked),'skipped':skipped,'total_locked_predictions':len(doc['events'])},'structural_activation':{'base_top1_picks':dict(base_counts),'candidate_top1_picks':dict(cand_counts),'top1_changed_count':changed,'changed_to_draw_count':to_draw,'natural_draw_top1_count':cand_counts.get('draw',0)},'new_locks':locked,'ledger_audit':a,'action':'WAIT_FOR_PRISTINE_SETTLEMENT_PAIRED_Y0_VS_U0_NO_RETUNING'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2));return summary

def verify():
    s=load(OUT);a=audit(load_ledger());g=s['governance'];d=s['development_calibration_source']
    assert s['status']=='COMPLETE' and a['status']=='PASS' and g['source_r43u1_predictions_read_only'] and g['outcome_access_at_prediction_lock'] is False
    assert g['parameter_search'] is False and g['threshold_search'] is False and g['draw_override'] is False and g['home_away_remaining_mass_ratio_preserved']
    assert abs(float(d['fixed_draw_logit_intercept'])-DRAW_LOGIT_INTERCEPT)<1e-15
    print('R43Y0 pristine draw-calibration lock verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
