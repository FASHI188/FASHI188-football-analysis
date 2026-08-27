#!/usr/bin/env python3
from __future__ import annotations
import csv,json,math,sys
from datetime import datetime
from pathlib import Path
HERE=Path(__file__).resolve().parent;OUT=HERE/'results';EXP=HERE.parent;FD=HERE.parent.parent
PRED=EXP/'batch004_s92_draw_gate'/'results'/'batch004_s92_predictions_locked.json'
RAW={'E0':FD/'raw'/'ENG_PremierLeague'/'2025-26.csv','D1':FD/'raw'/'GER_Bundesliga'/'2025-26.csv','I1':FD/'raw'/'ITA_SerieA'/'2025-26.csv','SP1':FD/'raw'/'ESP_LaLiga'/'2025-26.csv','F1':FD/'raw'/'FRA_Ligue1'/'2025-26.csv'}
MODELS=('S60','S70_Robust','S80_RobustCompactDraw','S91_RobustSideDrawHead','S92_HistoricalDrawGate');C={'HOME':0,'DRAW':1,'AWAY':2};N=('HOME','DRAW','AWAY')
def date(s):
 for f in ('%d/%m/%Y','%d/%m/%y'):
  try:return datetime.strptime(s.strip(),f).date().isoformat()
  except ValueError:pass
 raise ValueError(s)
def src(p):
 o={}
 with p.open(encoding='utf-8-sig',newline='') as f:
  for r in csv.DictReader(f):
   ds=(r.get('Date')or'').strip();h=(r.get('HomeTeam')or'').strip();a=(r.get('AwayTeam')or'').strip();gh=(r.get('FTHG')or'').strip();ga=(r.get('FTAG')or'').strip()
   if ds and h and a and gh!='' and ga!='':o[(date(ds),h,a)]=(int(float(gh)),int(float(ga)))
 return o
def y(gh,ga):return 0 if gh>ga else 1 if gh==ga else 2
def pv(q):return [float(q['p_home']),float(q['p_draw']),float(q['p_away'])]
def metrics(rows,k):
 hits=0;ll=br=rps=0.;picks=[0]*3;ph=[0]*3;acts=[0]*3
 for r in rows:
  yy=r['y'];q=r[k];p=pv(q);t=C[q['top1']];hits+=t==yy;picks[t]+=1;ph[t]+=t==yy;acts[yy]+=1;ll-=math.log(max(p[yy],1e-15));br+=sum((p[i]-(i==yy))**2 for i in range(3));rps+=((p[0]-(yy==0))**2+((p[0]+p[1])-(yy<=1))**2)/2
 n=len(rows);return {'count':n,'hits':hits,'top1_accuracy':hits/n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_picks':dict(zip(('home','draw','away'),picks)),'top1_hits':dict(zip(('home','draw','away'),ph)),'actuals':dict(zip(('home','draw','away'),acts)),'draw_recall':ph[1]/acts[1] if acts[1] else None,'false_draw_picks':picks[1]-ph[1]}
def delta(a,b):return {'hits':a['hits']-b['hits'],'top1_pp':100*(a['top1_accuracy']-b['top1_accuracy']),'logloss':a['logloss']-b['logloss'],'brier':a['brier']-b['brier'],'rps':a['rps']-b['rps'],'draw_picks':a['top1_picks']['draw']-b['top1_picks']['draw'],'draw_hits':a['top1_hits']['draw']-b['top1_hits']['draw']}
def changed(rows,b,c):
 z=[r for r in rows if r[b]['top1']!=r[c]['top1']];g=l=bw=0;detail=[]
 for r in z:
  bc=C[r[b]['top1']]==r['y'];cc=C[r[c]['top1']]==r['y'];g+=(not bc and cc);l+=(bc and not cc);bw+=(not bc and not cc);detail.append({'batch_index':r['batch_index'],'match':f"{r['home']} vs {r['away']}",'score':f"{r['home_goals']}-{r['away_goals']}",'actual':r['actual'],'base':r[b]['top1'],'candidate':r[c]['top1'],'base_correct':bc,'candidate_correct':cc})
 return {'count':len(z),'gains':g,'losses':l,'both_wrong':bw,'rows':detail}
def run():
 s=json.loads(PRED.read_text());g=s['governance']
 if s['status']!='BATCH004_S92_PREDICTIONS_LOCKED' or s['rows']!=100 or g['target_results_loaded'] or not g['accuracy_not_computed']:raise RuntimeError('invalid pre-reveal lock')
 sources={d:src(p) for d,p in RAW.items()};rows=[];missing=[]
 for p in s['predictions']:
  z=sources[p['division']].get((p['date'],p['home'],p['away']))
  if z is None:missing.append(p['batch_index']);continue
  gh,ga=z;yy=y(gh,ga);rows.append({**p,'home_goals':gh,'away_goals':ga,'y':yy,'actual':N[yy]})
 if missing or len(rows)!=100:raise RuntimeError(f'reveal incomplete {len(rows)}/100 missing={missing[:10]}')
 rows.sort(key=lambda r:r['batch_index']);mm={k:metrics(rows,k) for k in MODELS}
 out={'schema_version':'football3-batch004-reveal-score-v1','status':'BATCH004_REVEALED_SCORED','cohort_sha256':s['cohort_sha256'],'governance':{'S92_predictions_locked_before_reveal':True,'locked_prediction_commit':'829c881dcef9bf3535c0b503f136a5b69b1d06ff','locked_prediction_run_id':33042299685,'locked_prediction_artifact_digest':'sha256:aff236e1ecb984fe2b9a526ed5139123c914996f8def9ba143d2a196cdf6b699','outcomes_first_loaded_in_this_reveal_stage':True,'predictions_modified_after_reveal':False,'odds_used':False,'market_used':False,'Batch004_not_fresh_for_future_model_selection_claims':True},'models':mm,'deltas_vs_S70':{k:delta(mm[k],mm['S70_Robust']) for k in MODELS if k!='S70_Robust'},'changed_decisions':{'S92_vs_S91':changed(rows,'S91_RobustSideDrawHead','S92_HistoricalDrawGate'),'S92_vs_S70':changed(rows,'S70_Robust','S92_HistoricalDrawGate'),'S92_vs_S60':changed(rows,'S60','S92_HistoricalDrawGate')},'scored_rows':rows}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'summary_batch004_reveal.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n');print(json.dumps({'status':out['status'],'models':mm,'changed_decisions':out['changed_decisions']},indent=2))
def verify():
 s=json.loads((OUT/'summary_batch004_reveal.json').read_text());g=s['governance'];assert s['status']=='BATCH004_REVEALED_SCORED' and len(s['scored_rows'])==100 and s['cohort_sha256']=='5ec0327c090e4321f15f9682b89c14885e39b30133ec403e4f51541a0006c32a';assert g['S92_predictions_locked_before_reveal'] and g['outcomes_first_loaded_in_this_reveal_stage'] and not g['predictions_modified_after_reveal'] and not g['odds_used'] and not g['market_used'];a=s['models']['S91_RobustSideDrawHead'];b=s['models']['S92_HistoricalDrawGate'];assert abs(a['logloss']-b['logloss'])<1e-12 and abs(a['brier']-b['brier'])<1e-12 and abs(a['rps']-b['rps'])<1e-12;print('BATCH004_REVEAL_VERIFY_PASS')
if __name__=='__main__':
 if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:raise SystemExit('usage: score_batch004.py {run|verify}')
 {'run':run,'verify':verify}[sys.argv[1]]()
