#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[2]
Y0=ROOT/'forward'/'r43y0_draw_calibration_forward_events.json'
U1_RESULTS=ROOT/'forward'/'r43u1_pristine_forward_results.json'
LOCK_SUMMARY=ROOT/'experiments'/'r43y0_draw_calibration_forward'/'results'/'summary_r43y0_draw_calibration_lock.json'
OUT=ROOT/'experiments'/'r43y0_draw_calibration_forward'/'results'/'summary_r43y0_draw_calibration_evaluation.json'
CLASSES=('home','draw','away')
DISCOVERY_MIN=20
CONFIRM_MIN=50
ACCURACY_FLOOR=0.53
WILSON90_FLOOR=0.50
FOLDS=3
Z90=1.6448536269514722

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def top1(p):return max(CLASSES,key=lambda k:(float(p[k]),-CLASSES.index(k)))
def wilson(h,n):
    if n<=0:return None
    p=h/n;z2=Z90*Z90;den=1+z2/n;ctr=p+z2/(2*n);spr=Z90*math.sqrt((p*(1-p)+z2/(4*n))/n);return (ctr-spr)/den

def draw_cal(rows,key):
    if not rows:return {'n':0}
    ps=[float(r[key]['draw']) for r in rows];ys=[1.0 if r['y']=='draw' else 0.0 for r in rows]
    ll=sum(-(y*math.log(max(p,1e-15))+(1-y)*math.log(max(1-p,1e-15))) for p,y in zip(ps,ys))/len(rows)
    br=sum((p-y)**2 for p,y in zip(ps,ys))/len(rows)
    return {'n':len(rows),'mean_pred':sum(ps)/len(ps),'actual_rate':sum(ys)/len(ys),'logloss':ll,'brier':br}

def metrics(rows,key):
    n=len(rows);picks={k:0 for k in CLASSES};hitby={k:0 for k in CLASSES};acts={k:0 for k in CLASSES}
    if not n:return {'count':0,'hits':0,'top1_accuracy':None,'wilson90_lower':None,'logloss':None,'brier':None,'rps':None,'top1_picks':picks,'top1_hits':hitby,'actuals':acts,'draw_calibration':{'n':0}}
    hits=0;ll=br=rps=0.0
    for r in rows:
        p=r[key];y=r['y'];t=top1(p);hits+=int(t==y);picks[t]+=1;hitby[t]+=int(t==y);acts[y]+=1
        ll-=math.log(max(float(p[y]),1e-15));br+=sum((float(p[k])-(1.0 if y==k else 0.0))**2 for k in CLASSES)
        ph=float(p['home']);pd=float(p['draw']);rps+=((ph-(1.0 if y=='home' else 0.0))**2+((ph+pd)-(1.0 if y in {'home','draw'} else 0.0))**2)/2
    return {'count':n,'hits':hits,'top1_accuracy':hits/n,'wilson90_lower':wilson(hits,n),'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_picks':picks,'top1_hits':hitby,'actuals':acts,'draw_calibration':draw_cal(rows,key)}

def delta(base,cand):
    if not base['count']:return {'hits':0,'accuracy_pp':None,'logloss':None,'brier':None,'rps':None,'draw_logloss':None,'draw_brier':None}
    return {'hits':cand['hits']-base['hits'],'accuracy_pp':100*(cand['top1_accuracy']-base['top1_accuracy']),'logloss':cand['logloss']-base['logloss'],'brier':cand['brier']-base['brier'],'rps':cand['rps']-base['rps'],'draw_logloss':cand['draw_calibration']['logloss']-base['draw_calibration']['logloss'],'draw_brier':cand['draw_calibration']['brier']-base['draw_calibration']['brier']}
def folds(rows):
    if len(rows)<FOLDS:return []
    q,r=divmod(len(rows),FOLDS);out=[];s=0
    for i in range(FOLDS):
        z=q+(1 if i<r else 0);out.append(rows[s:s+z]);s+=z
    return out

def run():
    ydoc=load(Y0);yp={str(e['match_id']):e for e in ydoc.get('events',[]) if e.get('event_type')=='PREDICTION_FROZEN'}
    rdoc=load(U1_RESULTS) if U1_RESULTS.exists() else {'events':[]};rr={str(e['match_id']):e for e in rdoc.get('events',[]) if e.get('event_type')=='RESULT_SETTLED'}
    rows=[];rejected=[]
    for mid,e in yp.items():
        r=rr.get(mid)
        if r is None:continue
        if r.get('payload',{}).get('prediction_event_hash')!=e.get('payload',{}).get('source_r43u1_prediction_event_hash'):
            rejected.append({'match_id':mid,'reason':'r43u1_result_prediction_hash_mismatch'});continue
        y=str(r['payload']['result']['actual_result'])
        if y not in CLASSES:rejected.append({'match_id':mid,'reason':'invalid_result'});continue
        rows.append({'match_id':mid,'kickoff_at':e['payload']['fixture_identity']['kickoff_at'],'competition_id':e['payload']['fixture_identity']['competition_id'],'u0':e['payload']['source_r43u0_probabilities'],'y0':e['payload']['r43y0_probabilities'],'y':y})
    rows.sort(key=lambda x:(x['kickoff_at'],x['match_id']))
    u=metrics(rows,'u0');y=metrics(rows,'y0');d=delta(u,y);fr=[]
    for i,f in enumerate(folds(rows),1):
        fu=metrics(f,'u0');fy=metrics(f,'y0');fd=delta(fu,fy);fr.append({'fold':i,'n':len(f),'dates':[f[0]['kickoff_at'],f[-1]['kickoff_at']],'u0':fu,'y0':fy,'y0_minus_u0':fd})
    nonneg=sum(1 for f in fr if f['y0_minus_u0']['accuracy_pp'] is not None and f['y0_minus_u0']['accuracy_pp']>=-1e-12)
    drawll=sum(1 for f in fr if f['y0_minus_u0']['draw_logloss'] is not None and f['y0_minus_u0']['draw_logloss']<0)
    structural=load(LOCK_SUMMARY)['structural_activation'];natural=int(structural['natural_draw_top1_count'])
    discovery=len(rows)>=DISCOVERY_MIN
    confirmation=len(rows)>=CONFIRM_MIN
    signal=bool(discovery and natural>0 and d['accuracy_pp']>=0 and d['logloss']<0 and d['brier']<0 and d['rps']<0 and d['draw_logloss']<0 and d['draw_brier']<0 and nonneg>=2 and drawll>=2)
    confirmed=bool(confirmation and signal and y['top1_accuracy']>=ACCURACY_FLOOR and y['wilson90_lower']>=WILSON90_FLOOR)
    action='Y0_FORWARD_CONFIRMATION_PASSED_MANUAL_REVIEW_ONLY' if confirmed else ('Y0_DISCOVERY_SIGNAL_CONTINUE_TO_50_NO_RETUNING' if signal else ('Y0_DISCOVERY_GATE_FAILED_DO_NOT_RETUNE_ON_THESE_MATCHES' if discovery else 'WAIT_FOR_PRISTINE_SETTLEMENT_NO_RETUNING'))
    out={'schema_version':'football3-r43y0-draw-calibration-forward-evaluation-v1','status':'COMPLETE','classification':'PRISTINE_PAIRED_FORWARD_Y0_VS_U0','formal_weight':'FORWARD_EVIDENCE','generated_at_utc':now(),'governance':{'predictions_read_only':True,'outcome_used_only_for_settlement':True,'parameter_search':False,'threshold_search':False,'draw_override':False,'gate_preregistered_before_first_y0_settlement':True,'automatic_promotion':False,'main_merge':False,'publication':False},'gate_preregistration':{'discovery_min_settled':DISCOVERY_MIN,'confirmation_min_settled':CONFIRM_MIN,'confirmation_accuracy_floor':ACCURACY_FLOOR,'confirmation_wilson90_lower_floor':WILSON90_FLOOR,'requirements':['natural draw Top1 activation >0 in locked ledger','Y0 Top1 accuracy >= U0','Y0 LogLoss < U0','Y0 Brier < U0','Y0 RPS < U0','Y0 draw LogLoss < U0','Y0 draw Brier < U0','>=2/3 chronological folds nonnegative Top1 delta','>=2/3 chronological folds improved draw LogLoss'],'no_retuning_on_forward_outcomes':True},'coverage':{'locked_predictions':len(yp),'settled_predictions':len(rows),'open_predictions':len(yp)-len(rows),'rejected':rejected},'structural_activation':structural,'paired':{'u0':u,'y0':y,'y0_minus_u0':d,'folds':fr,'nonnegative_top1_folds':nonneg,'improved_draw_logloss_folds':drawll},'gate':{'discovery_sample_met':discovery,'confirmation_sample_met':confirmation,'forward_signal_passed':signal,'forward_confirmation_passed':confirmed,'action':action},'settled_rows':rows,'action':action}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return out

def verify():
    s=load(OUT);g=s['governance'];p=s['gate_preregistration']
    assert s['status']=='COMPLETE' and g['predictions_read_only'] and g['outcome_used_only_for_settlement'] and g['parameter_search'] is False and g['threshold_search'] is False and g['draw_override'] is False and g['gate_preregistered_before_first_y0_settlement'] and g['automatic_promotion'] is False
    assert p['discovery_min_settled']==DISCOVERY_MIN and p['confirmation_min_settled']==CONFIRM_MIN and p['no_retuning_on_forward_outcomes'] is True
    print('R43Y0 paired forward gate verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
