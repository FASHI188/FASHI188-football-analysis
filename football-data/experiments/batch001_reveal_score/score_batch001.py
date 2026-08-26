#!/usr/bin/env python3
from __future__ import annotations
import csv,io,json,math,urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent;OUT=HERE/'results'
PRED=HERE.parent/'batch001_stage4a_robust_s70'/'results'/'batch001_stage4a_s70_robust_locked.json'
BASE='https://www.football-data.co.uk/mmz4281/2526'; DIVS=('E0','D1','I1','SP1','F1')
CLASS={'HOME':0,'DRAW':1,'AWAY':2}; NAMES=['HOME','DRAW','AWAY']

def parse_date(s):
    for f in ('%d/%m/%Y','%d/%m/%y'):
        try:return datetime.strptime(s.strip(),f).date().isoformat()
        except ValueError:pass
    raise ValueError(s)

def source(div):
    req=urllib.request.Request(f'{BASE}/{div}.csv',headers={'User-Agent':'football3-batch001-reveal/1.0'})
    with urllib.request.urlopen(req,timeout=60) as r:text=r.read().decode('utf-8-sig',errors='replace')
    out={}
    for z in csv.DictReader(io.StringIO(text)):
        ds=(z.get('Date') or '').strip();h=(z.get('HomeTeam') or '').strip();a=(z.get('AwayTeam') or '').strip();gh=(z.get('FTHG') or '').strip();ga=(z.get('FTAG') or '').strip()
        if not ds or not h or not a or gh=='' or ga=='':continue
        k=(parse_date(ds),h,a)
        if k in out:raise RuntimeError(f'duplicate reveal key {div} {k}')
        out[k]=(int(float(gh)),int(float(ga)))
    return out

def actual(gh,ga):return 0 if gh>ga else 1 if gh==ga else 2

def pv(q):return [float(q['p_home']),float(q['p_draw']),float(q['p_away'])]

def one_metrics(rows,key):
    n=len(rows);hits=0;ll=br=rps=0.;picks=[0]*3;ph=[0]*3;acts=[0]*3
    for r in rows:
        y=r['y'];p=pv(r[key]);t=CLASS[r[key]['top1']];hits+=t==y;picks[t]+=1;ph[t]+=t==y;acts[y]+=1
        ll-=math.log(max(p[y],1e-15));br+=sum((p[i]-(i==y))**2 for i in range(3));rps+=((p[0]-(y==0))**2+((p[0]+p[1])-(y<=1))**2)/2
    return {'count':n,'hits':hits,'top1_accuracy':hits/n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_picks':dict(zip(['home','draw','away'],picks)),'top1_hits':dict(zip(['home','draw','away'],ph)),'actuals':dict(zip(['home','draw','away'],acts))}

def delta(a,b):return {'hits':a['hits']-b['hits'],'top1_pp':100*(a['top1_accuracy']-b['top1_accuracy']),'logloss':a['logloss']-b['logloss'],'brier':a['brier']-b['brier'],'rps':a['rps']-b['rps']}

def conf_bucket(q):
    m=max(pv(q))
    if m<.40:return '<40'
    if m<.45:return '40-45'
    if m<.50:return '45-50'
    if m<.60:return '50-60'
    return '60+'

def run():
    s=json.loads(PRED.read_text(encoding='utf-8'))
    if s['status']!='S70_ROBUST_PREDICTIONS_LOCKED' or s['rows']!=100:raise RuntimeError('Stage4A lock missing/mismatch')
    g=s['governance'];
    if g['target_results_loaded'] or not g['candidate_design_locked_before_target_scoring'] or not g['accuracy_not_computed']:raise RuntimeError('Stage4A pre-reveal governance violated')
    src={d:source(d) for d in DIVS}; rows=[];missing=[]
    for p in s['predictions']:
        k=(p['date'],p['home'],p['away']);z=src[p['division']].get(k)
        if z is None:missing.append({'idx':p['batch_index'],'division':p['division'],'key':k});continue
        gh,ga=z;y=actual(gh,ga);bs=CLASS[p['S60']['top1']];cs=CLASS[p['S70_Robust']['top1']]
        rows.append({**p,'home_goals':gh,'away_goals':ga,'y':y,'actual':NAMES[y],'S60_correct':bs==y,'S70_correct':cs==y})
    if missing or len(rows)!=100:raise RuntimeError(f'reveal mapping incomplete {len(rows)}/100 missing={missing[:5]}')
    rows.sort(key=lambda x:x['batch_index']);b=one_metrics(rows,'S60');c=one_metrics(rows,'S70_Robust')
    changed=[r for r in rows if r['S60']['top1']!=r['S70_Robust']['top1']]
    change={'count':len(changed),'gains':sum((not r['S60_correct']) and r['S70_correct'] for r in changed),'losses':sum(r['S60_correct'] and (not r['S70_correct']) for r in changed),'both_wrong':sum((not r['S60_correct']) and (not r['S70_correct']) for r in changed),'both_correct':sum(r['S60_correct'] and r['S70_correct'] for r in changed),'rows':[{'batch_index':r['batch_index'],'match':f"{r['home']} vs {r['away']}",'actual':r['actual'],'S60':r['S60']['top1'],'S70':r['S70_Robust']['top1'],'S60_correct':r['S60_correct'],'S70_correct':r['S70_correct']} for r in changed]}
    per={}
    for d in DIVS:
        z=[r for r in rows if r['division']==d];bb=one_metrics(z,'S60');cc=one_metrics(z,'S70_Robust');per[d]={'count':len(z),'S60':bb,'S70_Robust':cc,'delta':delta(cc,bb)}
    conf={}
    for key in ('S60','S70_Robust'):
        conf[key]={}
        for bucket in ('<40','40-45','45-50','50-60','60+'):
            z=[r for r in rows if conf_bucket(r[key])==bucket]
            conf[key][bucket]={'count':len(z),'hits':sum(CLASS[r[key]['top1']]==r['y'] for r in z),'accuracy':(sum(CLASS[r[key]['top1']]==r['y'] for r in z)/len(z) if z else None)}
    errors=[]
    for r in rows:
        if not r['S70_correct']:
            p=pv(r['S70_Robust']);errors.append({'batch_index':r['batch_index'],'division':r['division'],'match':f"{r['home']} vs {r['away']}",'score':f"{r['home_goals']}-{r['away_goals']}",'actual':r['actual'],'S60_top1':r['S60']['top1'],'S70_top1':r['S70_Robust']['top1'],'S70_max_prob':max(p),'S70_actual_prob':p[r['y']]})
    errors.sort(key=lambda x:(-x['S70_max_prob'],x['batch_index']))
    out={'schema_version':'football3-batch001-reveal-score-v1','status':'BATCH001_REVEALED_SCORED','classification':'RETROSPECTIVE_PSEUDO_PROSPECTIVE_100_MATCH_BATCH','cohort_sha256':s['cohort_sha256'],'governance':{'Stage4A_predictions_locked_before_reveal':True,'Stage4A_run_id':32982624971,'Stage4A_artifact_digest':'sha256:459b084c896a46919bf848be39fbb60591c0b6ad0d501312bd7fbfffda6dcece','outcomes_first_loaded_in_this_reveal_stage':True,'predictions_modified_after_reveal':False,'odds_used':False,'market_used':False,'Batch001_may_be_used_for_diagnosis_after_this_run':True,'Batch001_not_fresh_for_future_model_selection_claims':True},'S60':b,'S70_Robust':c,'delta_S70_minus_S60':delta(c,b),'changed_decisions':change,'per_division':per,'confidence_buckets':conf,'S70_errors_by_confidence':errors,'scored_rows':rows}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'summary_batch001_reveal.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps({k:out[k] for k in ('status','S60','S70_Robust','delta_S70_minus_S60','changed_decisions')},indent=2,ensure_ascii=False))

def verify():
    s=json.loads((OUT/'summary_batch001_reveal.json').read_text(encoding='utf-8'));g=s['governance']
    assert s['status']=='BATCH001_REVEALED_SCORED' and len(s['scored_rows'])==100
    assert g['Stage4A_predictions_locked_before_reveal'] and g['outcomes_first_loaded_in_this_reveal_stage'] and not g['predictions_modified_after_reveal'] and not g['odds_used'] and not g['market_used']
    assert s['changed_decisions']['count']==4
    print('BATCH001_REVEAL_VERIFY_PASS')
if __name__=='__main__':
    import sys
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:raise SystemExit('usage: score_batch001.py {run|verify}')
    {'run':run,'verify':verify}[sys.argv[1]]()
