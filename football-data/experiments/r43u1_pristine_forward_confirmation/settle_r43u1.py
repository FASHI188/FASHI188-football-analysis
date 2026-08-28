#!/usr/bin/env python3
from __future__ import annotations

import hashlib, json, math, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
PRED_LEDGER=ROOT/'forward'/'r43u1_pristine_forward_events.json'
MARKET_LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json'
RESULT_LEDGER=ROOT/'forward'/'r43u1_pristine_forward_results.json'
OUT=ROOT/'experiments'/'r43u1_pristine_forward_confirmation'/'results'/'summary_r43u1_pristine_forward_evaluation.json'
SCHEMA='football3-r43u1-pristine-forward-result-ledger-v1'
CLASSES=('home','draw','away')

def load(p:Path):return json.loads(p.read_text(encoding='utf-8'))
def canon(x)->bytes:return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def sha(x)->str:return hashlib.sha256(canon(x)).hexdigest()
def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def top1(p):return max(CLASSES,key=lambda k:(float(p[k]),-CLASSES.index(k)))

def load_results():
    if not RESULT_LEDGER.exists():return {'schema_version':SCHEMA,'events':[]}
    x=load(RESULT_LEDGER)
    if x.get('schema_version')!=SCHEMA or not isinstance(x.get('events'),list):raise RuntimeError('invalid R43U1 result ledger')
    return x

def append_result(ledger,mid,payload):
    prev=ledger['events'][-1]['event_hash'] if ledger['events'] else None
    body={'schema_version':'football3-r43u1-forward-result-event-v1','event_type':'RESULT_SETTLED','match_id':mid,'event_timestamp_utc':now(),'prev_event_hash':prev,'payload':payload}
    body['event_hash']=sha(body);ledger['events'].append(body);return body

def audit_results(ledger,preds):
    prev=None;seen=set();errors=[]
    for e in ledger.get('events',[]):
        body={k:v for k,v in e.items() if k!='event_hash'}
        if sha(body)!=e.get('event_hash'):errors.append(f"event_hash:{e.get('match_id')}")
        if e.get('prev_event_hash')!=prev:errors.append(f"prev_hash:{e.get('match_id')}")
        mid=str(e.get('match_id') or '')
        if mid in seen:errors.append(f'duplicate:{mid}')
        if mid not in preds:errors.append(f'no_prediction:{mid}')
        if mid in preds and e.get('payload',{}).get('prediction_event_hash')!=preds[mid].get('event_hash'):errors.append(f'prediction_hash:{mid}')
        seen.add(mid);prev=e.get('event_hash')
    return {'status':'PASS' if not errors else 'FAIL','event_count':len(ledger.get('events',[])),'head_hash':prev,'errors':errors}

def draw_cal(rows):
    if not rows:return {'n':0}
    ps=[float(r['p']['draw']) for r in rows];ys=[1.0 if r['y']=='draw' else 0.0 for r in rows]
    ll=sum(-(y*math.log(max(p,1e-15))+(1-y)*math.log(max(1-p,1e-15))) for p,y in zip(ps,ys))/len(rows)
    br=sum((p-y)**2 for p,y in zip(ps,ys))/len(rows)
    return {'n':len(rows),'mean_pred':sum(ps)/len(ps),'actual_rate':sum(ys)/len(ys),'logloss':ll,'brier':br}

def metrics(rows):
    n=len(rows)
    if not n:return {'count':0,'hits':0,'top1_accuracy':None,'logloss':None,'brier':None,'rps':None,'top1_picks':{'home':0,'draw':0,'away':0},'top1_hits':{'home':0,'draw':0,'away':0},'actuals':{'home':0,'draw':0,'away':0},'draw_calibration':{'n':0}}
    hits=0;ll=br=rps=0.;picks={k:0 for k in CLASSES};hitby={k:0 for k in CLASSES};acts={k:0 for k in CLASSES}
    for r in rows:
        p=r['p'];y=r['y'];t=top1(p);hits+=int(t==y);picks[t]+=1;hitby[t]+=int(t==y);acts[y]+=1
        ll-=math.log(max(float(p[y]),1e-15));br+=sum((float(p[k])-(1.0 if y==k else 0.0))**2 for k in CLASSES)
        ph=float(p['home']);pd=float(p['draw']);rps+=((ph-(1.0 if y=='home' else 0.0))**2+((ph+pd)-(1.0 if y in {'home','draw'} else 0.0))**2)/2
    return {'count':n,'hits':hits,'top1_accuracy':hits/n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_picks':picks,'top1_hits':hitby,'actuals':acts,'draw_calibration':draw_cal(rows)}

def run():
    pred_doc=load(PRED_LEDGER);preds={str(e['match_id']):e for e in pred_doc.get('events',[]) if e.get('event_type')=='PREDICTION_FROZEN'}
    market=load(MARKET_LEDGER);market_settled={str(e['match_id']):e for e in market.get('events',[]) if e.get('event_type')=='RESULT_SETTLED'}
    results=load_results();done={str(e['match_id']) for e in results.get('events',[])};new=[];rejected=[]
    for mid,pred in sorted(preds.items()):
        if mid in done:continue
        se=market_settled.get(mid)
        if se is None:continue
        expected_market_hash=pred.get('payload',{}).get('source_market_prediction_event_hash')
        got_market_hash=se.get('payload',{}).get('prediction_event_hash')
        if expected_market_hash!=got_market_hash:
            rejected.append({'match_id':mid,'reason':'market_prediction_hash_mismatch'});continue
        result=se.get('payload',{}).get('result') or {};y=str(result.get('actual_result') or '')
        if y not in CLASSES:rejected.append({'match_id':mid,'reason':'invalid_market_settlement'});continue
        payload={'prediction_event_hash':pred['event_hash'],'source_market_prediction_event_hash':expected_market_hash,'source_market_settlement_event_hash':se.get('event_hash'),'result':{'home_goals_90':int(result['home_goals_90']),'away_goals_90':int(result['away_goals_90']),'actual_result':y,'settlement_scope':result.get('settlement_scope') or '90_minutes_including_stoppage'},'prediction_recomputed':False}
        new.append(append_result(results,mid,payload));done.add(mid)
    audit=audit_results(results,preds)
    if audit['status']!='PASS':raise RuntimeError(audit)
    RESULT_LEDGER.parent.mkdir(parents=True,exist_ok=True);RESULT_LEDGER.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    rows=[]
    bymid={str(e['match_id']):e for e in results['events']}
    for mid,re in bymid.items():
        pred=preds[mid];rows.append({'match_id':mid,'p':pred['payload']['r43u0_probabilities'],'y':re['payload']['result']['actual_result'],'kickoff_at':pred['payload']['fixture_identity']['kickoff_at'],'competition_id':pred['payload']['fixture_identity']['competition_id']})
    rows.sort(key=lambda r:(r['kickoff_at'],r['match_id']));m=metrics(rows)
    summary={'schema_version':'football3-r43u1-pristine-forward-evaluation-v1','status':'COMPLETE','classification':'PRISTINE_FORWARD_SETTLEMENT_ONLY','formal_weight':'FORWARD_EVIDENCE','generated_at_utc':now(),'governance':{'prediction_ledger_read_only':True,'prediction_recomputed':False,'market_settlement_reference_required':True,'market_prediction_hash_match_required':True,'settlement_scope':'90_minutes_including_stoppage','main_merge':False,'publication':False},'coverage':{'locked_predictions':len(preds),'settled_predictions':len(results['events']),'open_predictions':len(preds)-len(results['events']),'new_results_appended':len(new),'rejected':rejected},'metrics':m,'result_ledger_audit':audit,'settled_rows':rows,'action':'CONTINUE_FORWARD_ACCUMULATION_NO_RETUNING' if len(results['events'])<30 else 'REVIEW_PREREGISTERED_FORWARD_GATE_WITHOUT_RETUNING'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2));return summary

def verify():
    s=load(OUT);preds={str(e['match_id']):e for e in load(PRED_LEDGER).get('events',[]) if e.get('event_type')=='PREDICTION_FROZEN'};r=load_results();a=audit_results(r,preds);g=s['governance']
    assert s['status']=='COMPLETE' and a['status']=='PASS' and g['prediction_ledger_read_only'] and g['prediction_recomputed'] is False and g['market_settlement_reference_required'] and g['market_prediction_hash_match_required']
    for e in r['events']:assert e['payload']['prediction_recomputed'] is False
    print('R43U1 settlement-only contract verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
