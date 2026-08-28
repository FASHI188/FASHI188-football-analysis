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
REVIEW_MIN=30
CONFIRM_MIN=100
FULL_VOLUME_ACCURACY_FLOOR=0.53
WILSON90_FLOOR=0.50
TIME_FOLDS=3
Z90=1.6448536269514722

def load(p:Path):return json.loads(p.read_text(encoding='utf-8'))
def canon(x)->bytes:return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def sha(x)->str:return hashlib.sha256(canon(x)).hexdigest()
def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def top1(p):return max(CLASSES,key=lambda k:(float(p[k]),-CLASSES.index(k)))

def wilson90(hits:int,n:int):
    if n<=0:return None
    p=hits/n;z2=Z90*Z90;den=1+z2/n;ctr=p+z2/(2*n);spr=Z90*math.sqrt((p*(1-p)+z2/(4*n))/n)
    return (ctr-spr)/den

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

def draw_cal(rows,key):
    if not rows:return {'n':0}
    ps=[float(r[key]['draw']) for r in rows];ys=[1.0 if r['y']=='draw' else 0.0 for r in rows]
    ll=sum(-(y*math.log(max(p,1e-15))+(1-y)*math.log(max(1-p,1e-15))) for p,y in zip(ps,ys))/len(rows)
    br=sum((p-y)**2 for p,y in zip(ps,ys))/len(rows)
    return {'n':len(rows),'mean_pred':sum(ps)/len(ps),'actual_rate':sum(ys)/len(ys),'logloss':ll,'brier':br}

def metrics(rows,key):
    n=len(rows)
    if not n:return {'count':0,'hits':0,'top1_accuracy':None,'wilson90_lower':None,'logloss':None,'brier':None,'rps':None,'top1_picks':{'home':0,'draw':0,'away':0},'top1_hits':{'home':0,'draw':0,'away':0},'actuals':{'home':0,'draw':0,'away':0},'draw_calibration':{'n':0}}
    hits=0;ll=br=rps=0.;picks={k:0 for k in CLASSES};hitby={k:0 for k in CLASSES};acts={k:0 for k in CLASSES}
    for r in rows:
        p=r[key];y=r['y'];t=top1(p);hits+=int(t==y);picks[t]+=1;hitby[t]+=int(t==y);acts[y]+=1
        ll-=math.log(max(float(p[y]),1e-15));br+=sum((float(p[k])-(1.0 if y==k else 0.0))**2 for k in CLASSES)
        ph=float(p['home']);pd=float(p['draw']);rps+=((ph-(1.0 if y=='home' else 0.0))**2+((ph+pd)-(1.0 if y in {'home','draw'} else 0.0))**2)/2
    return {'count':n,'hits':hits,'top1_accuracy':hits/n,'wilson90_lower':wilson90(hits,n),'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_picks':picks,'top1_hits':hitby,'actuals':acts,'draw_calibration':draw_cal(rows,key)}

def delta(base,cand):
    if not base['count'] or not cand['count']:return {'hits':0,'accuracy_pp':None,'logloss':None,'brier':None,'rps':None,'draw_logloss':None,'draw_brier':None}
    return {'hits':cand['hits']-base['hits'],'accuracy_pp':100*(cand['top1_accuracy']-base['top1_accuracy']),'logloss':cand['logloss']-base['logloss'],'brier':cand['brier']-base['brier'],'rps':cand['rps']-base['rps'],'draw_logloss':cand['draw_calibration']['logloss']-base['draw_calibration']['logloss'],'draw_brier':cand['draw_calibration']['brier']-base['draw_calibration']['brier']}

def time_folds(rows,k=TIME_FOLDS):
    n=len(rows)
    if n<k:return []
    q,r=divmod(n,k);out=[];start=0
    for i in range(k):
        size=q+(1 if i<r else 0);out.append(rows[start:start+size]);start+=size
    return out

def run():
    pred_doc=load(PRED_LEDGER);preds={str(e['match_id']):e for e in pred_doc.get('events',[]) if e.get('event_type')=='PREDICTION_FROZEN'}
    market=load(MARKET_LEDGER)
    market_preds={str(e['match_id']):e for e in market.get('events',[]) if e.get('event_type')=='MARKET_PREDICTION_FROZEN'}
    market_settled={str(e['match_id']):e for e in market.get('events',[]) if e.get('event_type')=='RESULT_SETTLED'}
    results=load_results();done={str(e['match_id']) for e in results.get('events',[])};new=[];rejected=[]
    for mid,pred in sorted(preds.items()):
        if mid in done:continue
        se=market_settled.get(mid)
        if se is None:continue
        expected_market_hash=pred.get('payload',{}).get('source_market_prediction_event_hash')
        got_market_hash=se.get('payload',{}).get('prediction_event_hash')
        mp=market_preds.get(mid)
        if mp is None or mp.get('event_hash')!=expected_market_hash:
            rejected.append({'match_id':mid,'reason':'source_market_prediction_missing_or_hash_mismatch'});continue
        if expected_market_hash!=got_market_hash:
            rejected.append({'match_id':mid,'reason':'market_prediction_hash_mismatch'});continue
        result=se.get('payload',{}).get('result') or {};y=str(result.get('actual_result') or '')
        if y not in CLASSES:rejected.append({'match_id':mid,'reason':'invalid_market_settlement'});continue
        payload={'prediction_event_hash':pred['event_hash'],'source_market_prediction_event_hash':expected_market_hash,'source_market_settlement_event_hash':se.get('event_hash'),'result':{'home_goals_90':int(result['home_goals_90']),'away_goals_90':int(result['away_goals_90']),'actual_result':y,'settlement_scope':result.get('settlement_scope') or '90_minutes_including_stoppage'},'prediction_recomputed':False}
        new.append(append_result(results,mid,payload));done.add(mid)
    audit=audit_results(results,preds)
    if audit['status']!='PASS':raise RuntimeError(audit)
    RESULT_LEDGER.parent.mkdir(parents=True,exist_ok=True);RESULT_LEDGER.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    rows=[];bymid={str(e['match_id']):e for e in results['events']}
    for mid,re in bymid.items():
        pred=preds[mid];mp=market_preds.get(mid);expected=pred['payload']['source_market_prediction_event_hash']
        if mp is None or mp.get('event_hash')!=expected:raise RuntimeError(f'paired market source drift:{mid}')
        rows.append({'match_id':mid,'u0':pred['payload']['r43u0_probabilities'],'market':mp['payload']['prediction']['probabilities'],'y':re['payload']['result']['actual_result'],'kickoff_at':pred['payload']['fixture_identity']['kickoff_at'],'competition_id':pred['payload']['fixture_identity']['competition_id']})
    rows.sort(key=lambda r:(r['kickoff_at'],r['match_id']))
    u0=metrics(rows,'u0');mk=metrics(rows,'market');dm=delta(mk,u0)
    fold_receipts=[]
    for i,f in enumerate(time_folds(rows),1):
        fm=metrics(f,'market');fu=metrics(f,'u0');fd=delta(fm,fu)
        fold_receipts.append({'fold':i,'n':len(f),'dates':[f[0]['kickoff_at'],f[-1]['kickoff_at']],'market':fm,'r43u0':fu,'r43u0_minus_market':fd})
    nonnegative_top1_folds=sum(1 for f in fold_receipts if f['r43u0_minus_market']['accuracy_pp'] is not None and f['r43u0_minus_market']['accuracy_pp']>=-1e-12)
    positive_logloss_folds=sum(1 for f in fold_receipts if f['r43u0_minus_market']['logloss'] is not None and f['r43u0_minus_market']['logloss']<0)
    review_sample_met=len(rows)>=REVIEW_MIN
    confirm_sample_met=len(rows)>=CONFIRM_MIN
    signal=bool(review_sample_met and u0['top1_accuracy']>=FULL_VOLUME_ACCURACY_FLOOR and dm['accuracy_pp']>=0 and dm['logloss']<0 and dm['brier']<0 and dm['rps']<0 and dm['draw_logloss']<=0 and dm['draw_brier']<=0 and nonnegative_top1_folds>=2 and positive_logloss_folds>=2)
    confirmed=bool(confirm_sample_met and signal and u0['wilson90_lower']>=WILSON90_FLOOR)
    action='FORWARD_CONFIRMATION_GATE_PASSED_MANUAL_REVIEW_ONLY_NO_AUTOPROMOTION' if confirmed else ('FORWARD_SIGNAL_AT_REVIEW_SAMPLE_CONTINUE_TO_100_WITHOUT_RETUNING' if signal else ('REVIEW_GATE_FAILED_CONTINUE_PRISTINE_FORWARD_WITHOUT_RETUNING' if review_sample_met else 'CONTINUE_FORWARD_ACCUMULATION_NO_RETUNING'))
    summary={'schema_version':'football3-r43u1-pristine-forward-evaluation-v2','status':'COMPLETE','classification':'PRISTINE_FORWARD_SETTLEMENT_ONLY_PAIRED_MARKET','formal_weight':'FORWARD_EVIDENCE','generated_at_utc':now(),'governance':{'prediction_ledger_read_only':True,'prediction_recomputed':False,'market_settlement_reference_required':True,'market_prediction_hash_match_required':True,'paired_market_prediction_hash_required':True,'gate_preregistered_before_first_r43u1_settlement':True,'parameter_search':False,'threshold_search':False,'draw_override':False,'settlement_scope':'90_minutes_including_stoppage','main_merge':False,'publication':False,'automatic_promotion':False},'gate_preregistration':{'review_min_settled':REVIEW_MIN,'confirmation_min_settled':CONFIRM_MIN,'full_volume_accuracy_floor':FULL_VOLUME_ACCURACY_FLOOR,'wilson90_lower_floor_at_confirmation':WILSON90_FLOOR,'paired_requirements':['R43U0 Top1 accuracy >= direct market','R43U0 LogLoss < direct market','R43U0 Brier < direct market','R43U0 RPS < direct market','R43U0 draw LogLoss <= direct market','R43U0 draw Brier <= direct market','>=2/3 chronological folds nonnegative Top1 delta','>=2/3 chronological folds positive LogLoss delta'],'no_retuning_on_forward_outcomes':True},'coverage':{'locked_predictions':len(preds),'settled_predictions':len(results['events']),'open_predictions':len(preds)-len(results['events']),'new_results_appended':len(new),'rejected':rejected},'metrics':u0,'paired':{'direct_market':mk,'r43u0':u0,'r43u0_minus_market':dm,'folds':fold_receipts,'nonnegative_top1_folds':nonnegative_top1_folds,'positive_logloss_folds':positive_logloss_folds},'gate':{'review_sample_met':review_sample_met,'confirmation_sample_met':confirm_sample_met,'forward_signal_passed':signal,'forward_confirmation_passed':confirmed,'action':action},'result_ledger_audit':audit,'settled_rows':rows,'action':action}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2));return summary

def verify():
    s=load(OUT);preds={str(e['match_id']):e for e in load(PRED_LEDGER).get('events',[]) if e.get('event_type')=='PREDICTION_FROZEN'};r=load_results();a=audit_results(r,preds);g=s['governance'];pr=s['gate_preregistration']
    assert s['status']=='COMPLETE' and s['schema_version']=='football3-r43u1-pristine-forward-evaluation-v2' and a['status']=='PASS'
    assert g['prediction_ledger_read_only'] and g['prediction_recomputed'] is False and g['market_settlement_reference_required'] and g['market_prediction_hash_match_required'] and g['paired_market_prediction_hash_required']
    assert g['gate_preregistered_before_first_r43u1_settlement'] and g['parameter_search'] is False and g['threshold_search'] is False and g['draw_override'] is False and g['automatic_promotion'] is False
    assert pr['review_min_settled']==REVIEW_MIN and pr['confirmation_min_settled']==CONFIRM_MIN and pr['full_volume_accuracy_floor']==FULL_VOLUME_ACCURACY_FLOOR and pr['wilson90_lower_floor_at_confirmation']==WILSON90_FLOOR and pr['no_retuning_on_forward_outcomes'] is True
    for e in r['events']:assert e['payload']['prediction_recomputed'] is False
    print('R43U1 paired settlement-only preregistered contract verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
