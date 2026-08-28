#!/usr/bin/env python3
from __future__ import annotations

import hashlib, json, math, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
for p in (ROOT/'experiments'/'r43t0_dynamic_bivariate_residual_state',ROOT/'experiments'/'r43u0_fixed_diagonal_inflation',ROOT/'experiments'/'r43q0_sharp_market_score_base'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import run_r43t0 as t0
import run_r43u0 as u0
import run_r43q0 as q0

MARKET_LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json'
LOCK_LEDGER=ROOT/'forward'/'r43u1_pristine_forward_events.json'
OUT=HERE/'results'/'summary_r43u1_pristine_forward_lock.json'
SCHEMA='football3-r43u1-pristine-forward-ledger-v1'
MIN_LEAD=timedelta(minutes=30)
CLASSES=('home','draw','away')

def load(p:Path):return json.loads(p.read_text(encoding='utf-8'))
def iso(x:str)->datetime:
    d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
    if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)
def canon(x)->bytes:return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def sha(x)->str:return hashlib.sha256(canon(x)).hexdigest()
def top1(p):return max(CLASSES,key=lambda k:(float(p[k]),-CLASSES.index(k)))
def margin(p):
    v=sorted((float(p[k]) for k in CLASSES),reverse=True);return v[0]-v[1]

def current_state():
    rows=t0.build_rows();groups=t0.group_rows(rows);x=np.zeros(2);P=np.eye(2)*t0.INITIAL_VAR
    for g in groups:
        xp=t0.STATE_AR*x;Pp=(t0.STATE_AR**2)*P+np.eye(2)*t0.PROCESS_VAR
        x,P=t0.simultaneous_update(xp,Pp,g)
    return x,P,len(rows),groups[-1][0]['kickoff_utc'] if groups else None

def candidates(now:datetime):
    led=load(MARKET_LEDGER);preds={};settled=set()
    for e in led.get('events',[]):
        mid=str(e.get('match_id') or '')
        if e.get('event_type')=='MARKET_PREDICTION_FROZEN':preds[mid]=e
        elif e.get('event_type')=='RESULT_SETTLED':settled.add(mid)
    out=[]
    for mid,e in preds.items():
        if mid in settled:continue
        p=e.get('payload') or {};fx=p.get('fixture_identity') or {};surf=p.get('frozen_surfaces') or {}
        try:k=iso(fx['kickoff_at']);f=iso(e['event_timestamp_utc'])
        except Exception:continue
        if not f<k or not now<k-MIN_LEAD:continue
        if not all(key in surf for key in ('one_x_two_odds','asian_handicap','over_under')):continue
        if p.get('retrospective_backfill') is True:continue
        out.append((k,mid,e))
    out.sort(key=lambda z:(z[0],z[1]));return out

def load_lock_ledger():
    if not LOCK_LEDGER.exists():return {'schema_version':SCHEMA,'events':[]}
    x=load(LOCK_LEDGER)
    if x.get('schema_version')!=SCHEMA or not isinstance(x.get('events'),list):raise RuntimeError('invalid R43U1 lock ledger')
    return x

def append_event(ledger,mid,ts,payload):
    prev=ledger['events'][-1]['event_hash'] if ledger['events'] else None
    body={'schema_version':'football3-r43u1-forward-event-v1','event_type':'PREDICTION_FROZEN','match_id':mid,'event_timestamp_utc':ts,'prev_event_hash':prev,'payload':payload}
    body['event_hash']=sha(body);ledger['events'].append(body);return body

def audit(ledger):
    prev=None;seen=set()
    for e in ledger.get('events',[]):
        h=e.get('event_hash');body={k:v for k,v in e.items() if k!='event_hash'}
        if sha(body)!=h:return {'status':'FAIL','error':'event_hash_mismatch','match_id':e.get('match_id')}
        if e.get('prev_event_hash')!=prev:return {'status':'FAIL','error':'chain_mismatch','match_id':e.get('match_id')}
        mid=str(e.get('match_id') or '')
        if mid in seen:return {'status':'FAIL','error':'duplicate_match_id','match_id':mid}
        seen.add(mid);prev=h
        p=e.get('payload') or {};fx=p.get('fixture_identity') or {}
        if not iso(e['event_timestamp_utc'])<iso(fx['kickoff_at']):return {'status':'FAIL','error':'lock_not_prematch','match_id':mid}
        if 'actual_result' in json.dumps(p,sort_keys=True):return {'status':'FAIL','error':'outcome_field_present','match_id':mid}
    return {'status':'PASS','event_count':len(ledger.get('events',[])),'head_hash':prev}

def run():
    now=datetime.now(timezone.utc).replace(microsecond=0);x,P,settled_n,last_settled_kickoff=current_state();cand=candidates(now)
    ledger=load_lock_ledger();existing={str(e.get('match_id')) for e in ledger.get('events',[])};new=[]
    xf=np.asarray(x,dtype=float);Pf=np.asarray(P,dtype=float);last_group=None
    groups=[];cur=[];key=None
    for item in cand:
        k=item[0]
        if key is None or k==key:cur.append(item);key=k
        else:groups.append(cur);cur=[item];key=k
    if cur:groups.append(cur)
    for g in groups:
        xf=t0.STATE_AR*xf;Pf=(t0.STATE_AR**2)*Pf+np.eye(2)*t0.PROCESS_VAR
        for k,mid,e in g:
            if mid in existing:continue
            p=e['payload'];fx=p['fixture_identity'];surf=p['frozen_surfaces'];market=q0.devig_1x2(surf['one_x_two_odds'])
            lh,la,obj=q0.infer_lambdas(surf['asian_handicap'],surf['over_under'],market)
            dh,da=t0.project_lambdas(lh,la,xf);m=u0.inflate(q0.score_matrix(dh,da));u=q0.matrix_1x2(m)
            payload={'fixture_identity':fx,'source_market_prediction_event_hash':e.get('event_hash'),'source_market_frozen_at_utc':e.get('event_timestamp_utc'),'same_snapshot_surfaces':True,'model':'R43U0_DYNAMIC_BIVARIATE_PLUS_FIXED_DIAGONAL_1P25','model_code_head':os.environ.get('GITHUB_SHA'),'model_parameters':{'transition_ar':t0.STATE_AR,'process_var':t0.PROCESS_VAR,'initial_var':t0.INITIAL_VAR,'observation_noise_floor':t0.OBS_NOISE_FLOOR,'state_apply_shrink':t0.STATE_APPLY_SHRINK,'state_clip_abs':t0.MAX_STATE_ABS,'diagonal_factor':u0.DIAGONAL_FACTOR},'market_probabilities':market,'market_implied_lambdas':{'home':lh,'away':la,'fit_objective':obj},'state_forecast':{'total_residual':float(xf[0]),'goal_difference_residual':float(xf[1])},'dynamic_lambdas':{'home':dh,'away':da},'r43u0_probabilities':u,'top1':top1(u),'confidence_margin':margin(u),'outcome_access_at_lock':False,'draw_forced':False}
            ev=append_event(ledger,mid,now.isoformat(),payload);new.append(ev);existing.add(mid)
        last_group=g[0][0].isoformat()
    a=audit(ledger)
    if a.get('status')!='PASS':raise RuntimeError(a)
    LOCK_LEDGER.parent.mkdir(parents=True,exist_ok=True);LOCK_LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary={'schema_version':'football3-r43u1-pristine-forward-lock-summary-v1','status':'COMPLETE','classification':'PRISTINE_FORWARD_LOCK_NO_OUTCOME_ACCESS','formal_weight':'PENDING_SETTLEMENT','generated_at_utc':now.isoformat(),'governance':{'r43u0_parameters_frozen':True,'outcome_access_at_prediction_lock':False,'only_unsettled_future_market_predictions':True,'minimum_lock_lead_minutes':30,'same_snapshot_1x2_ah_ou_required':True,'retrospective_backfill_allowed':False,'main_merge':False,'publication':False},'state_basis':{'settled_rows_used_before_lock':settled_n,'last_settled_kickoff_utc':last_settled_kickoff},'coverage':{'eligible_open_future_market_events':len(cand),'new_predictions_locked':len(new),'total_locked_predictions':len(ledger['events'])},'new_locks':[{'match_id':e['match_id'],'kickoff_at':e['payload']['fixture_identity']['kickoff_at'],'competition_id':e['payload']['fixture_identity']['competition_id'],'home_team':e['payload']['fixture_identity']['home_team'],'away_team':e['payload']['fixture_identity']['away_team'],'top1':e['payload']['top1'],'probabilities':e['payload']['r43u0_probabilities'],'confidence_margin':e['payload']['confidence_margin'],'event_hash':e['event_hash']} for e in new],'ledger_audit':a,'action':'WAIT_FOR_90_MINUTE_SETTLEMENT_THEN_SCORE_WITHOUT_RECOMPUTING_PREDICTIONS' if ledger['events'] else 'NO_ELIGIBLE_FUTURE_MARKET_EVENTS'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2));return summary

def verify():
    s=load(OUT);l=load_lock_ledger();a=audit(l);g=s['governance']
    assert s['status']=='COMPLETE' and a['status']=='PASS' and g['r43u0_parameters_frozen'] and not g['outcome_access_at_prediction_lock'] and g['same_snapshot_1x2_ah_ou_required'] and not g['retrospective_backfill_allowed']
    for e in l['events']:
        assert e['payload']['model_parameters']['diagonal_factor']==1.25 and not e['payload']['draw_forced']
    print('R43U1 pristine forward lock verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
