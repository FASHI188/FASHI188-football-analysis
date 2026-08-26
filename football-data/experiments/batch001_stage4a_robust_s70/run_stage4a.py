#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from collections import defaultdict
from pathlib import Path
import numpy as np,pandas as pd

HERE=Path(__file__).resolve().parent; DATA=HERE/'data'; OUT=HERE/'results'
S2=HERE.parent/'batch001_stage2_historical_s60_replay';sys.path.insert(0,str(S2));import run_stage2 as s2
r9=s2.r9;r23=s2.r23;r24=s2.r24
PRED=S2/'results'/'batch001_s60_predictions_locked.json'; HISTORY_N=60000;TRAIN_N=24123
TOP1={0:'HOME',1:'DRAW',2:'AWAY'}


def side_rec(x,home):
 return {'gf':int(x['home_goals'] if home else x['away_goals']),'ga':int(x['away_goals'] if home else x['home_goals']),'xgf':float(x['home_xg'] if home else x['away_xg']),'xga':float(x['away_xg'] if home else x['home_xg'])}

def summary(hist,tid):
 z=hist.get(str(tid),[]);o={}
 for n in (5,10):
  q=z[-n:]; den=len(q) or 1
  o[f'count{n}']=len(q);o[f'gf{n}']=sum(v['gf'] for v in q)/den;o[f'ga{n}']=sum(v['ga'] for v in q)/den;o[f'xgf{n}']=sum(v['xgf'] for v in q)/den;o[f'xga{n}']=sum(v['xga'] for v in q)/den
  o[f'gdclip{n}']=sum(max(-3,min(3,v['gf']-v['ga'])) for v in q)/den;o[f'draw{n}']=sum(v['gf']==v['ga'] for v in q)/den;o[f'low2_{n}']=sum(v['gf']+v['ga']<=2 for v in q)/den;o[f'blowout{n}']=sum(abs(v['gf']-v['ga'])>=4 for v in q)/den
 return o

def robust_vec(hist,h,a):
 H=summary(hist,h);A=summary(hist,a);v=[]
 for n in (5,10):
  keys=[f'gf{n}',f'ga{n}',f'xgf{n}',f'xga{n}',f'gdclip{n}',f'draw{n}',f'low2_{n}',f'blowout{n}']
  v += [H[k] for k in keys]+[A[k] for k in keys]+[H[k]-A[k] for k in keys]+[np.log1p(H[f'count{n}']),np.log1p(A[f'count{n}'])]
 return [float(x) for x in v]

def history_robust(rows):
 st=r9.S();hist=defaultdict(list);pred=[];by=defaultdict(list)
 for x in rows:by[x['date']].append(x)
 for d in sorted(by):
  pending=[]
  for x in sorted(by[d],key=lambda z:z['game_id']):
   raw=st.pred(x);rv=robust_vec(hist,x['home_team'],x['away_team']);pred.append({'date':d,'game_id':x['game_id'],'y':r9.actual(x),'raw':raw,'robust':rv});pending.append((x,raw))
  for x,raw in pending:
   st.update(x,raw);hist[str(x['home_team'])].append(side_rec(x,True));hist[str(x['away_team'])].append(side_rec(x,False))
 return pred,st,hist

def fit(train):
 from sklearn.linear_model import LogisticRegression
 from sklearn.pipeline import make_pipeline
 from sklearn.preprocessing import StandardScaler
 X=[r9.feat_k1(x['raw'])+x['robust'] for x in train];y=[x['y'] for x in train]
 m=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0));m.fit(X,y);return m

def predict(m,raw,rv):
 p=m.predict_proba([r9.feat_k1(raw)+rv])[0];cls=list(m[-1].classes_);v=np.zeros(3)
 for c,z in zip(cls,p):v[int(c)]=float(z)
 return r9.decorate(v)

def run():
 lock=json.loads(PRED.read_text(encoding='utf-8'))
 if lock['status']!='PREDICTIONS_LOCKED_BASELINE_ONLY' or lock['rows']!=100:raise RuntimeError('Stage2 prediction lock mismatch')
 pool=s2.load_frozen_pool(); DATA.mkdir(parents=True,exist_ok=True);fp=DATA/'fixtures_safe_stage4a.parquet';r9.download(r9.FIX_URL,fp)
 fx=pd.read_parquet(fp,columns=['id','home_team_id','away_team_id']);byid={str(int(x.id)):x for x in fx.itertuples(index=False)};fp.unlink(missing_ok=True)
 bydate=defaultdict(list)
 for p in lock['predictions']:bydate[p['date']].append(p)
 outrows=[];date_audit=[];changed=0
 for day in sorted(bydate):
  q=sorted(bydate[day],key=lambda x:x['batch_index']);cutoff=pd.to_datetime(q[0]['effective_same_date_cutoff_utc'],utc=True)
  if any(pd.to_datetime(x['effective_same_date_cutoff_utc'],utc=True)!=cutoff for x in q):raise RuntimeError(f'effective cutoff mismatch within {day}')
  eligible=[x for x in pool if x['_known']<cutoff];eligible.sort(key=lambda x:(x['date'],x['game_id']))
  if len(eligible)<HISTORY_N:raise RuntimeError(f'insufficient history {day} {len(eligible)}')
  window=[{k:v for k,v in x.items() if k!='_known'} for x in eligible[-HISTORY_N:]]
  hp,state,hist=history_robust(window);base_model=r24.model(hp[-TRAIN_N:]);m70=fit(hp[-TRAIN_N:]);date_audit.append({'date':day,'cutoff_utc':cutoff.isoformat(),'history_rows':HISTORY_N,'train_rows':TRAIN_N,'candidate_fixed_C':.5})
  for p in q:
   x=byid[str(p['fixture_id'])];cid=str(lock['competition_map'][p['division']]['id']);target={'date':p['date'],'game_id':str(p['fixture_id']),'competition_id':cid,'home_team':str(int(x.home_team_id)),'away_team':str(int(x.away_team_id))}
   raw=state.pred(target);b=r23.pred(base_model,raw);ref=p['S60_replay']
   err=max(abs(b['p_home']-ref['p_home']),abs(b['p_draw']-ref['p_draw']),abs(b['p_away']-ref['p_away']))
   if err>1e-9:raise RuntimeError(f'S60 baseline reproduction drift idx={p["batch_index"]} err={err}')
   rv=robust_vec(hist,target['home_team'],target['away_team']);s70=predict(m70,raw,rv);changed+=int(TOP1[s70['top1']]!=ref['top1'])
   outrows.append({'batch_index':p['batch_index'],'date':p['date'],'division':p['division'],'home':p['home'],'away':p['away'],'fixture_id':str(p['fixture_id']),'effective_cutoff_utc':p['effective_same_date_cutoff_utc'],'S60':ref,'S70_Robust':{'p_home':s70['p_home'],'p_draw':s70['p_draw'],'p_away':s70['p_away'],'top1':TOP1[s70['top1']]},'top1_changed_from_S60':TOP1[s70['top1']]!=ref['top1'],'status':'LOCKED_NO_TARGET_RESULT'})
 outrows.sort(key=lambda x:x['batch_index'])
 out={'schema_version':'football3-batch001-stage4a-robust-s70-v1','status':'S70_ROBUST_PREDICTIONS_LOCKED','rows':100,'cohort_sha256':lock['cohort_sha256'],'candidate':{'name':'S70_Robust','base_architecture':'S60','history_rows':HISTORY_N,'train_rows':TRAIN_N,'classifier':'StandardScaler + multinomial LogisticRegression C=0.5 random_state=0','new_feature_family':'strict-prior recent 5/10 match GF/GA/xGF/xGA, clipped goal differential, draw/low2/blowout rates','hyperparameter_search_on_batch001':False},'governance':{'target_results_loaded':False,'target_postmatch_stats_loaded':False,'target_odds_used':False,'market_used':False,'candidate_design_locked_before_target_scoring':True,'chronologically_prior_results_and_xg_allowed':True,'same_date_results_withheld':True,'S60_reproduced_before_candidate_prediction':True,'accuracy_not_computed':True},'top1_changed_count':changed,'date_audit':date_audit,'predictions':outrows}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'batch001_stage4a_s70_robust_locked.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps({'status':out['status'],'rows':100,'top1_changed_count':changed},indent=2))

def verify():
 s=json.loads((OUT/'batch001_stage4a_s70_robust_locked.json').read_text(encoding='utf-8'));g=s['governance'];c=s['candidate']
 assert s['status']=='S70_ROBUST_PREDICTIONS_LOCKED' and s['rows']==100 and len(s['predictions'])==100
 assert not g['target_results_loaded'] and not g['target_postmatch_stats_loaded'] and not g['target_odds_used'] and not g['market_used'] and g['S60_reproduced_before_candidate_prediction'] and g['accuracy_not_computed']
 assert not c['hyperparameter_search_on_batch001'] and c['history_rows']==60000 and c['train_rows']==24123
 assert [x['batch_index'] for x in s['predictions']]==list(range(1,101));print('BATCH001_STAGE4A_VERIFY_PASS')
if __name__=='__main__':
 if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:raise SystemExit('usage: run_stage4a.py {run|verify}')
 {'run':run,'verify':verify}[sys.argv[1]]()
