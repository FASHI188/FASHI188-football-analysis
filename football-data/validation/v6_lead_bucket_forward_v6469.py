#!/usr/bin/env python3
"""V6.46.9 prospective lead-time bucket sidecar for market 1X2.

Problem repaired
----------------
V6.5.1 freezes the earliest eligible snapshot anywhere inside 1-72h. The first 61 settled
matches therefore averaged ~58h lead, while retrospective closing-like odds were used for
historical threshold research. One mixed lead window cannot tell whether a confidence
rule is stable at the user's actual query/purchase time.

Design
------
Start a new no-backfill prospective epoch and freeze one immutable prediction per fixture
per predeclared lead bucket: H1_6, H6_24, H24_48, H48_72. Each bucket chooses the first
prospectively observed snapshot after the fixture enters that bucket. No bucket is chosen
as champion from prior outcomes. Results reuse independently frozen official result
receipts from the V6.5.1 resolver when fixture identity matches exactly.

Research only, formal_weight=0, no CURRENT/runtime probability change.
"""
from __future__ import annotations

import json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/'validation';ENGINE=ROOT/'engine'
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path:sys.path.insert(0,str(p))

import v6_market_first_forward_v651 as market
from platform_core import PlatformError, atomic_write_json, load_json, normalize_team_token, parse_iso_datetime, sha256_json

EVIDENCE=ROOT/'evidence'/'markets_prospective'
FREEZE=ROOT/'manifests'/'v6_lead_bucket_forward_v6469_freeze.json'
LEDGER=ROOT/'forward'/'v6_lead_bucket_events_v6469.json'
RESULTS=ROOT/'forward'/'inbox'/'market_first_results_v651.json'
STATUS=ROOT/'manifests'/'v6_lead_bucket_forward_v6469_status.json'
FREEZE_SCHEMA='V6.46.9-lead-bucket-freeze-r1';LEDGER_SCHEMA='V6.46.9-lead-bucket-ledger-r1';EVENT_SCHEMA='V6.46.9-lead-bucket-event-r1'
BUCKETS={'H1_6':(1.0,6.0),'H6_24':(6.0,24.0),'H24_48':(24.0,48.0),'H48_72':(48.0,72.0)}
MIN_RESULT_AGE=timedelta(hours=2)
D=('home','draw','away')


def utc_now()->datetime:return datetime.now(timezone.utc).replace(microsecond=0)
def event_hash(x:dict[str,Any])->str:return sha256_json(x)
def identity(raw:dict[str,Any])->tuple[str,str,str,str]:return (str(raw.get('competition_id') or ''),str(raw.get('kickoff_utc') or ''),normalize_team_token(str(raw.get('home_team') or '')),normalize_team_token(str(raw.get('away_team') or '')))
def base_match_id(key:tuple[str,str,str,str])->str:return 'lead_'+sha256_json({'cid':key[0],'ko':key[1],'h':key[2],'a':key[3]})[:24]
def bucket_match_id(key:tuple[str,str,str,str],bucket:str)->str:return base_match_id(key)+'_'+bucket.lower()

def ensure_freeze(now:datetime)->dict[str,Any]:
    if FREEZE.exists():
        x=load_json(FREEZE)
        if x.get('schema_version')!=FREEZE_SCHEMA or x.get('status')!='FROZEN':raise PlatformError('invalid V6.46.9 freeze')
        return x
    x={'schema_version':FREEZE_SCHEMA,'status':'FROZEN','freeze_timestamp_utc':now.isoformat(),'buckets':{k:{'min_lead_hours':v[0],'max_lead_hours':v[1]} for k,v in BUCKETS.items()},'selection_rule':'first prospectively observed snapshot after entering each bucket','probability_source':'multiplicatively de-vigged active Kambi 1X2','historical_backfill':False,'formal_weight':0,'governance':{'automatic_promotion':False,'runtime_probability_change':False,'current_rule_change':False}}
    atomic_write_json(FREEZE,x);return x

def load_ledger()->dict[str,Any]:
    if not LEDGER.exists():return {'schema_version':LEDGER_SCHEMA,'events':[]}
    x=load_json(LEDGER)
    if x.get('schema_version')!=LEDGER_SCHEMA or not isinstance(x.get('events'),list):raise PlatformError('invalid V6.46.9 ledger')
    return x

def append(ledger:dict[str,Any],typ:str,mid:str,ts:str,payload:dict[str,Any])->dict[str,Any]:
    events=ledger['events'];e={'schema_version':EVENT_SCHEMA,'sequence':len(events)+1,'event_type':typ,'event_timestamp_utc':ts,'match_id':mid,'previous_event_hash':events[-1]['event_hash'] if events else 'GENESIS','payload':payload};e['event_hash']=event_hash(e);events.append(e);return e

def audit(ledger:dict[str,Any])->dict[str,Any]:
    prev='GENESIS';errors=[]
    for i,e in enumerate(ledger.get('events',[]),1):
        if e.get('sequence')!=i:errors.append(f'seq:{i}')
        if e.get('previous_event_hash')!=prev:errors.append(f'prev:{i}')
        c=dict(e);rec=c.pop('event_hash',None);exp=event_hash(c)
        if rec!=exp:errors.append(f'hash:{i}')
        prev=str(rec or '')
    return {'status':'PASS' if not errors else 'FAIL','event_count':len(ledger.get('events',[])),'tip_hash':prev,'errors':errors}

def pred_events(ledger):return {str(e['match_id']):e for e in ledger['events'] if e.get('event_type')=='BUCKET_PREDICTION_FROZEN'}
def settle_events(ledger):return {str(e['match_id']):e for e in ledger['events'] if e.get('event_type')=='RESULT_SETTLED'}

def bucket_for_lead(hours:float)->str|None:
    for name,(lo,hi) in BUCKETS.items():
        if lo<=hours<hi or (name=='H48_72' and lo<=hours<=hi):return name
    return None

def scan(now:datetime,freeze:dict[str,Any],ledger:dict[str,Any])->dict[str,int]:
    stats=Counter();epoch=parse_iso_datetime(freeze['freeze_timestamp_utc'],'freeze');existing=pred_events(ledger);candidates={}
    for path in sorted(EVIDENCE.glob('*.json')) if EVIDENCE.exists() else []:
        stats['files_seen']+=1
        try:
            r=load_json(path);obs=parse_iso_datetime(str(r.get('source_observed_at_utc') or r.get('freeze_utc') or ''),'observed');ko=parse_iso_datetime(str(r.get('kickoff_utc') or ''),'kickoff')
            if obs<epoch:stats['before_epoch']+=1;continue
            if obs>=ko or obs>now:stats['invalid_timing']+=1;continue
            # Hard no-backfill: a new prediction may only be created while the match is still in the future.
            if now>=ko:stats['kickoff_already_passed']+=1;continue
            lead=(ko-obs).total_seconds()/3600;b=bucket_for_lead(lead)
            if b is None:stats['outside_buckets']+=1;continue
            if not isinstance(r.get('one_x_two'),dict):stats['missing_1x2']+=1;continue
            key=identity(r);mid=bucket_match_id(key,b)
            if mid in existing:stats['already_frozen']+=1;continue
            ck=(key,b);old=candidates.get(ck)
            if old is None or obs<old[0]:candidates[ck]=(obs,path,r,lead)
        except Exception:stats['files_rejected']+=1
    for (key,b),(obs,path,r,lead) in sorted(candidates.items(),key=lambda x:(x[1][0],x[0][0],x[0][1])):
        q=market.devig(r['one_x_two']);pick,margin=market.top_pick(q);mid=bucket_match_id(key,b)
        payload={'bucket':b,'lead_hours':lead,'fixture_identity':{'competition_id':key[0],'season':str(r.get('season') or ''),'kickoff_at':key[1],'home_team':str(r.get('home_team') or ''),'away_team':str(r.get('away_team') or ''),'settlement_scope':str(r.get('settlement_scope') or '')},'market_source':{'provider_name':r.get('provider_name'),'provider_group':r.get('provider_group'),'source_url':r.get('source_url'),'source_observed_at_utc':obs.isoformat(),'evidence_path':str(path.relative_to(ROOT)),'evidence_sha256':sha256_json(r),'raw_snapshot_sha256':r.get('raw_snapshot_sha256')},'prediction':{'probabilities':q,'pick':pick,'pmax':max(q.values()),'margin':margin}}
        append(ledger,'BUCKET_PREDICTION_FROZEN',mid,obs.isoformat(),payload);existing[mid]=ledger['events'][-1];stats[f'new_{b}']+=1;stats['new_predictions']+=1
    return dict(sorted(stats.items()))

def result_map()->dict[tuple[str,str,str,str],dict[str,Any]]:
    if not RESULTS.exists():return {}
    x=load_json(RESULTS);out={}
    for r in x.get('results',[]):
        if not isinstance(r,dict):continue
        key=(str(r.get('competition_id') or ''),str(r.get('kickoff_at') or ''),normalize_team_token(str(r.get('home_team') or '')),normalize_team_token(str(r.get('away_team') or '')))
        if all(key):out[key]=r
    return out

def settle(now:datetime,ledger:dict[str,Any])->dict[str,int]:
    stats=Counter();pred=pred_events(ledger);settled=settle_events(ledger);receipts=result_map()
    for mid,e in sorted(pred.items()):
        if mid in settled:continue
        fi=e['payload']['fixture_identity'];ko=parse_iso_datetime(fi['kickoff_at'],'kickoff')
        if now<ko+MIN_RESULT_AGE:stats['not_old_enough']+=1;continue
        key=(str(fi['competition_id']),str(fi['kickoff_at']),normalize_team_token(str(fi['home_team'])),normalize_team_token(str(fi['away_team'])))
        rec=receipts.get(key)
        if not rec:stats['official_receipt_not_yet_available']+=1;continue
        actual=str(rec.get('actual_result') or '')
        if actual not in D:stats['invalid_receipt']+=1;continue
        append(ledger,'RESULT_SETTLED',mid,now.isoformat(),{'prediction_event_hash':e['event_hash'],'bucket':e['payload']['bucket'],'result':{'home_goals_90':int(rec['home_goals_90']),'away_goals_90':int(rec['away_goals_90']),'actual_result':actual},'official_result_receipt_sha256':sha256_json(rec),'official_result_source':rec.get('source')})
        stats['new_settlements']+=1
    return dict(sorted(stats.items()))

def metric(rows:list[dict[str,Any]])->dict[str,Any]:
    n=len(rows)
    if not n:return {'count':0}
    h=sum(r['pick']==r['actual'] for r in rows);ll=br=rps=0.0
    for r in rows:
        p=r['p'];y={d:1.0 if r['actual']==d else 0.0 for d in D};ll-=math.log(max(1e-15,p[r['actual']]));br+=sum((p[d]-y[d])**2 for d in D);rps+=((p['home']-y['home'])**2+((p['home']+p['draw'])-(y['home']+y['draw']))**2)/2
    return {'count':n,'hits':h,'accuracy':h/n,'log_loss':ll/n,'brier':br/n,'rps':rps/n}
def evaluate(ledger:dict[str,Any])->dict[str,Any]:
    pred=pred_events(ledger);settled=settle_events(ledger);by=defaultdict(list);paired=defaultdict(dict)
    for mid,se in settled.items():
        pe=pred.get(mid)
        if not pe or se['payload'].get('prediction_event_hash')!=pe.get('event_hash'):continue
        b=pe['payload']['bucket'];p=pe['payload']['prediction'];actual=se['payload']['result']['actual_result'];row={'pick':p['pick'],'p':{d:float(p['probabilities'][d]) for d in D},'actual':actual};by[b].append(row)
        fi=pe['payload']['fixture_identity'];base=(fi['competition_id'],fi['kickoff_at'],normalize_team_token(fi['home_team']),normalize_team_token(fi['away_team']));paired[base][b]=row
    common_all=[v for v in paired.values() if all(b in v for b in BUCKETS)]
    return {'by_bucket':{b:metric(by.get(b,[])) for b in BUCKETS},'fixtures_with_any_settled_bucket':len(paired),'fixtures_with_all_four_buckets_settled':len(common_all),'promotion_ready':False,'minimum_per_bucket_for_timing_comparison':100,'minimum_gate_met':all(len(by.get(b,[]))>=100 for b in BUCKETS)}
def main()->int:
    now=utc_now();freeze=ensure_freeze(now);ledger=load_ledger();before=audit(ledger)
    if before['status']!='PASS':raise PlatformError(str(before))
    scan_stats=scan(now,freeze,ledger);settle_stats=settle(now,ledger);after=audit(ledger)
    if after['status']!='PASS':raise PlatformError(str(after))
    atomic_write_json(LEDGER,ledger);ev=evaluate(ledger);payload={'schema_version':'V6.46.9-lead-bucket-forward-status-r1','generated_at_utc':now.isoformat(),'status':'PASS','freeze':freeze,'ledger_audit':after,'prediction_scan':scan_stats,'settlement_scan':settle_stats,'evaluation':ev,'governance':{'new_postfreeze_epoch':True,'historical_backfill':False,'one_prediction_per_fixture_per_bucket':True,'bucket_champion_not_preselected':True,'official_result_receipts_only':True,'formal_weight':0,'automatic_promotion':False,'runtime_probability_change':False,'current_rule_change':False}}
    atomic_write_json(STATUS,payload);print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
