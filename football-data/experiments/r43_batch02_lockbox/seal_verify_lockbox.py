#!/usr/bin/env python3
from __future__ import annotations

import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
U1=ROOT/'forward'/'r43u1_pristine_forward_events.json'
Y0=ROOT/'forward'/'r43y0_draw_calibration_forward_events.json'
B1=ROOT/'forward'/'r43_batch01_pristine_lockbox.json'
U1_RESULTS=ROOT/'forward'/'r43u1_pristine_forward_results.json'
MARKET=ROOT/'forward'/'v6_market_first_events_v651.json'
LOCKBOX=ROOT/'forward'/'r43_batch02_pristine_lockbox.json'
OUT=ROOT/'experiments'/'r43_batch02_lockbox'/'results'/'summary_r43_batch02_lockbox_verify.json'
START=24
END=41

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def canon(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def sha(x):return hashlib.sha256(canon(x)).hexdigest()
def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def event_hash_valid(e):
    body={k:v for k,v in e.items() if k!='event_hash'}
    return sha(body)==e.get('event_hash')

def digest_rows(events,a,b):
    rows=[{'match_id':str(e['match_id']),'event_hash':str(e['event_hash'])} for e in events[a:b]]
    return sha(rows)

def digest_id_set(ids):
    return sha(sorted(str(x) for x in ids))

def audit_chain_prefix(events,n):
    errors=[];prev=None
    if len(events)<n:errors.append(f'event_count_under_seal:{len(events)}<{n}')
    for i,e in enumerate(events[:n]):
        if not event_hash_valid(e):errors.append(f'event_hash:{i+1}:{e.get("match_id")}')
        if e.get('prev_event_hash')!=prev:errors.append(f'prev_hash:{i+1}:{e.get("match_id")}')
        prev=e.get('event_hash')
    return errors

def ensure_batch01_prefix(u,y):
    b=load(B1);errs=[]
    for key,events in [('r43u1',u),('r43y0',y)]:
        spec=b[key];n=int(spec['sealed_event_count'])
        head=events[n-1]['event_hash'] if len(events)>=n else None
        dig=digest_rows(events,0,n) if len(events)>=n else None
        if head!=spec['sealed_head_event_hash']:errs.append(f'batch01_head:{key}')
        if dig!=spec['ordered_match_event_hash_digest_sha256']:errs.append(f'batch01_digest:{key}')
    if errs:raise RuntimeError(errs)

def settled_ids():
    ids=set()
    if U1_RESULTS.exists():
        for e in load(U1_RESULTS).get('events') or []:
            if e.get('event_type')=='RESULT_SETTLED':ids.add(str(e.get('match_id')))
    for e in load(MARKET).get('events') or []:
        if e.get('event_type')=='RESULT_SETTLED':ids.add(str(e.get('match_id')))
    return ids

def make_lockbox():
    u=load(U1).get('events') or [];y=load(Y0).get('events') or []
    if len(u)<END or len(y)<END:raise RuntimeError(f'need >=41 locks u1={len(u)} y0={len(y)}')
    eu=audit_chain_prefix(u,END);ey=audit_chain_prefix(y,END)
    if eu or ey:raise RuntimeError({'u1':eu,'y0':ey})
    ensure_batch01_prefix(u,y)
    uids=[str(e['match_id']) for e in u[START:END]]
    yids=[str(e['match_id']) for e in y[START:END]]
    if len(set(uids))!=(END-START) or len(set(yids))!=(END-START):raise RuntimeError('duplicate match id inside Batch02 segment')
    if set(uids)!=set(yids):
        raise RuntimeError({'error':'Batch02 match-id set mismatch','u1_only':sorted(set(uids)-set(yids)),'y0_only':sorted(set(yids)-set(uids))})
    batch_ids=set(uids);seen=settled_ids() & batch_ids
    if seen:raise RuntimeError(f'Batch02 already has settlement before seal:{sorted(seen)}')
    box={
      'schema_version':'football3-r43-batch02-pristine-lockbox-v2',
      'status':'SEALED_PRE_SETTLEMENT',
      'classification':'PRISTINE_FORWARD_BATCH_LOCKBOX',
      'batch_id':'R43_BATCH02_20260828_EVENTS25_41',
      'sealed_basis_utc':now(),
      'governance':{
        'sealed_before_first_batch_result':True,
        'prediction_recompute_allowed':False,
        'parameter_retuning_on_batch_outcomes_allowed':False,
        'threshold_retuning_on_batch_outcomes_allowed':False,
        'draw_override_allowed':False,
        'cross_ledger_physical_order_required':False,
        'batch_match_id_set_equality_required':True,
        'automatic_promotion':False,
        'main_merge':False,
        'publication':False
      },
      'range':{'one_based_start_event':25,'one_based_end_event':41,'event_count':17,'batch_match_id_set_digest_sha256':digest_id_set(batch_ids)},
      'r43u1':{
        'ledger_path':'forward/r43u1_pristine_forward_events.json',
        'sealed_event_count':41,
        'sealed_head_event_hash':u[40]['event_hash'],
        'ordered_match_event_hash_digest_sha256':digest_rows(u,0,41),
        'batch_segment_match_event_hash_digest_sha256':digest_rows(u,24,41),
        'batch_segment_match_id_set_digest_sha256':digest_id_set(uids),
        'lock_run_id':33185837852,
        'lock_run_head':'a53a2596b960059323629c510c4b2d03fe8f097c',
        'review_min_settled':30,
        'confirmation_min_settled':100,
        'accuracy_floor':0.53,
        'wilson90_lower_floor_at_confirmation':0.50
      },
      'r43y0':{
        'ledger_path':'forward/r43y0_draw_calibration_forward_events.json',
        'sealed_event_count':41,
        'sealed_head_event_hash':y[40]['event_hash'],
        'ordered_match_event_hash_digest_sha256':digest_rows(y,0,41),
        'batch_segment_match_event_hash_digest_sha256':digest_rows(y,24,41),
        'batch_segment_match_id_set_digest_sha256':digest_id_set(yids),
        'lock_run_id':33185971027,
        'lock_run_head':'19d41e1bd69a641f4f8bea9a0906437efd7c946c',
        'fixed_draw_logit_intercept':0.1322913820792354,
        'natural_draw_top1_count_at_seal':sum(1 for e in y[:41] if e.get('payload',{}).get('top1')=='draw'),
        'discovery_min_settled':20,
        'confirmation_min_settled':50,
        'accuracy_floor':0.53,
        'wilson90_lower_floor_at_confirmation':0.50
      },
      'settlement_state_at_seal':{'batch02_settled_count':0,'r43u1_locked':len(u),'r43y0_locked':len(y)},
      'action':'VERIFY_BATCH01_AND_BATCH02_SEALED_PREFIXES_BEFORE_SCORING_AND_NEVER_RETUNE_ON_BATCH02_OUTCOMES'
    }
    LOCKBOX.write_text(json.dumps(box,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return box

def audit_from_box(box):
    out={};segment_sets={}
    for key in ('r43u1','r43y0'):
        spec=box[key];events=load(ROOT/spec['ledger_path']).get('events') or [];n=int(spec['sealed_event_count'])
        errors=audit_chain_prefix(events,n)
        head=events[n-1]['event_hash'] if len(events)>=n else None
        pdig=digest_rows(events,0,n) if len(events)>=n else None
        sdig=digest_rows(events,START,END) if len(events)>=END else None
        ids=[str(e['match_id']) for e in events[START:END]] if len(events)>=END else []
        iddig=digest_id_set(ids) if len(ids)==END-START else None
        segment_sets[key]=set(ids)
        if head!=spec['sealed_head_event_hash']:errors.append('sealed_head_mismatch')
        if pdig!=spec['ordered_match_event_hash_digest_sha256']:errors.append('sealed_prefix_digest_mismatch')
        if sdig!=spec['batch_segment_match_event_hash_digest_sha256']:errors.append('batch_segment_digest_mismatch')
        if iddig!=spec['batch_segment_match_id_set_digest_sha256']:errors.append('batch_match_id_set_digest_mismatch')
        out[key]={'status':'PASS' if not errors else 'FAIL','current_event_count':len(events),'sealed_event_count':n,'sealed_prefix_head':head,'sealed_prefix_digest':pdig,'batch_segment_digest':sdig,'batch_match_id_set_digest':iddig,'errors':errors}
    cross_errors=[]
    if segment_sets.get('r43u1')!=segment_sets.get('r43y0'):cross_errors.append('batch_match_id_set_mismatch')
    expected=(box.get('range') or {}).get('batch_match_id_set_digest_sha256')
    if expected and digest_id_set(segment_sets.get('r43u1') or set())!=expected:cross_errors.append('batch_match_id_set_vs_lockbox_mismatch')
    return out,cross_errors

def verify_and_write():
    box=load(LOCKBOX);ensure_batch01_prefix(load(U1).get('events') or [],load(Y0).get('events') or [])
    audits,cross=audit_from_box(box);passed=all(v['status']=='PASS' for v in audits.values()) and not cross
    summary={'schema_version':'football3-r43-batch02-lockbox-verification-v2','status':'PASS' if passed else 'FAIL','batch_id':box['batch_id'],'lockbox_status':box['status'],'governance':box['governance'],'range':box['range'],'r43u1_prefix_audit':audits['r43u1'],'r43y0_prefix_audit':audits['r43y0'],'cross_ledger_audit':{'status':'PASS' if not cross else 'FAIL','errors':cross},'action':'BATCH02_PREFIX_IMMUTABLE_CONTINUE_FORWARD_SETTLEMENT' if passed else 'STOP_BATCH02_SCORING_LOCKBOX_MISMATCH'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if not passed:raise SystemExit(2)
    return summary

def seal():
    if not LOCKBOX.exists():make_lockbox()
    return verify_and_write()

def verify():
    x=verify_and_write();assert x['status']=='PASS';print('R43 Batch02 lockbox verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'seal'
    if cmd=='seal':seal()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
