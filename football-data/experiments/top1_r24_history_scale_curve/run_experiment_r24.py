#!/usr/bin/env python3
from __future__ import annotations
import csv,json,sys
from collections import defaultdict
from pathlib import Path
import numpy as np,pandas as pd

HERE=Path(__file__).resolve().parent;DATA=HERE/'data';OUT=HERE/'results';R9=HERE.parent/'top1_r9b_xg_hf';sys.path.insert(0,str(R9));import run_experiment_r9b as r9
SCALES=((20,0,1),(40,20000,2),(60,40000,3),(80,60000,4));FIX_SHA='7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7';STAT_SHA='2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9'

def history(rows):
 st=r9.S();pred=[];by=defaultdict(list)
 for x in rows:by[x['date']].append(x)
 for d in sorted(by):
  q=[]
  for x in sorted(by[d],key=lambda z:z['game_id']):raw=st.pred(x);pred.append({'date':d,'game_id':x['game_id'],'y':r9.actual(x),'raw':raw});q.append((x,raw))
  for x,raw in q:st.update(x,raw)
 return pred

def model(train):
 from sklearn.linear_model import LogisticRegression
 from sklearn.pipeline import make_pipeline
 from sklearn.preprocessing import StandardScaler
 m=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0));m.fit([r9.feat_k1(x['raw']) for x in train],[x['y'] for x in train]);return m

def attach(rows,m,key):
 pr=m.predict_proba([r9.feat_k1(x['raw']) for x in rows]);cls=list(m[-1].classes_)
 for x,p in zip(rows,pr):
  v=np.zeros(3)
  for c,z in zip(cls,p):v[int(c)]=float(z)
  x[key]=r9.decorate(v)

def delta(a,b):return {'hits':a['hits']-b['hits'],'top1_pp':100*(a['top1_accuracy']-b['top1_accuracy']),'logloss':a['logloss']-b['logloss'],'brier':a['brier']-b['brier'],'rps':a['rps']-b['rps']}

def extra_pool(base):
 DATA.mkdir(parents=True,exist_ok=True);fp=DATA/'fixtures.parquet';sp=DATA/'match_stats.parquet';r9.download(r9.FIX_URL,fp);r9.download(r9.STAT_URL,sp)
 if r9.fsha(fp)!=FIX_SHA or r9.fsha(sp)!=STAT_SHA:raise RuntimeError('R24 upstream source hash drift')
 fx=pd.read_parquet(fp,columns=['id','date_utc','league_id','home_team_id','away_team_id','goals_home','goals_away','status_norm','is_played']);st=pd.read_parquet(sp,columns=['fixture_id','home_xg','away_xg','xg_covered','xg_nulled','known_at'])
 st=st[(st.xg_covered==True)&(st.xg_nulled==False)&st.home_xg.notna()&st.away_xg.notna()];fx=fx[(fx.is_played==True)&(fx.status_norm=='FT')&fx.goals_home.notna()&fx.goals_away.notna()];df=fx.merge(st,left_on='id',right_on='fixture_id',how='inner',validate='one_to_one');df['date']=pd.to_datetime(df.date_utc,utc=True).dt.date.astype(str);df['known']=pd.to_datetime(df.known_at,utc=True);df=df[(df.known>pd.to_datetime(df.date_utc,utc=True))&df.home_xg.between(0,6)&df.away_xg.between(0,6)].sort_values(['date','id']).drop_duplicates('id')
 first=min(x['date'] for x in base);pre=df[df.date<first]
 if len(pre)<60000:raise RuntimeError(f'R24 needs 60000 strict earlier rows; only {len(pre)}')
 ex=[]
 for z in pre.tail(60000).itertuples(index=False):ex.append({'date':z.date,'game_id':str(int(z.id)),'competition_id':str(int(z.league_id)),'home_team':str(int(z.home_team_id)),'away_team':str(int(z.away_team_id)),'home_goals':int(z.goals_home),'away_goals':int(z.goals_away),'home_xg':float(z.home_xg),'away_xg':float(z.away_xg),'xg_known_at':z.known.isoformat()})
 if {x['game_id'] for x in ex}&{x['game_id'] for x in base}:raise RuntimeError('R24 history overlap')
 p=DATA/'extra_r24_xg_60000.csv'
 with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=r9.FIELDS);w.writeheader();w.writerows(ex)
 fp.unlink(missing_ok=True);sp.unlink(missing_ok=True);return ex,p

def run():
 base=r9.load();bp=history(base);b1=r9.boundary(bp,r9.TARGET_BURN);b2=r9.boundary(bp,b1+r9.TARGET_TRAIN);b3=r9.boundary(bp,b2+r9.TARGET_VAL);ntrain=b2-b1
 bval=[dict(x) for x in bp[b2:b3]];btest=[dict(x) for x in bp[b3:]];bm=model(bp[b1:b2]);attach(bval,bm,'S20');attach(btest,bm,'S20');baseline_v=r9.metrics(bval,'S20');baseline_t=r9.metrics(btest,'S20')
 if (baseline_v['hits'],baseline_t['hits'])!=(2064,1877):raise RuntimeError('R24 baseline reproduction failed')
 ex,p=extra_pool(base);results={};ident_v=[x['game_id'] for x in bval];ident_t=[x['game_id'] for x in btest]
 for total,extra_n,mult in SCALES:
  if total==20:pv,pt=bval,btest
  else:
   e=ex[-extra_n:];hp=history(e+base);off=extra_n;pv=[dict(x) for x in hp[off+b2:off+b3]];pt=[dict(x) for x in hp[off+b3:]]
   if [x['game_id'] for x in pv]!=ident_v or [x['game_id'] for x in pt]!=ident_t:raise RuntimeError(f'R24 identity mismatch S{total}')
   train_n=mult*ntrain;m=model(hp[off+b2-train_n:off+b2]);attach(pv,m,f'S{total}');attach(pt,m,f'S{total}')
  key=f'S{total}';v=r9.metrics(pv,key);t=r9.metrics(pt,key);results[key]={'history_rows':total*1000,'train_rows':mult*ntrain,'validation':v,'test':t,'delta_validation_vs_S20':delta(v,baseline_v),'delta_test_vs_S20':delta(t,baseline_t)}
 if (results['S40']['validation']['hits'],results['S40']['test']['hits'])!=(2067,1895):raise RuntimeError(f"R24 S40 lock failed {results['S40']['validation']['hits']}/{results['S40']['test']['hits']}")
 manifest={'schema_version':'football3-top1-r24-history-scale-curve','source_dataset':'eatpizzanot/soccer-dataset','license':'CC-BY-4.0','fixtures_sha256':FIX_SHA,'match_stats_sha256':STAT_SHA,'base_snapshot_sha256':'6ea5f6d98a6b43c1f34df58f08edfa52819415f79da88428947caae68d9170ba','base_first_date':min(x['date'] for x in base),'base_last_date':max(x['date'] for x in base),'extra_rows':len(ex),'extra_first_date':ex[0]['date'],'extra_last_date':ex[-1]['date'],'extra_sha256':r9.fsha(p),'selection':'latest 60000 valid FT strict-prior xG rows with date strictly before exact R9b first date'};DATA.mkdir(parents=True,exist_ok=True);(DATA/'source_manifest_r24.json').write_text(json.dumps(manifest,indent=2)+'\n')
 s={'schema_version':'football3-top1-r24-history-scale-curve','status':'COMPLETE','classification':'DEVELOPMENT_FIXED_TAIL_SAMPLE_SCALE_CURVE','formal_weight':0,'governance':{'validation_identity_exactly_same_as_R9b':True,'test_identity_exactly_same_as_R9b':True,'scales_predeclared':[20,40,60,80],'classifier_train_multipliers_predeclared':[1,2,3,4],'regularization_C_fixed':.5,'same_date_results_and_xg_withheld':True,'strict_prior_xg':True,'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False,'hyperparameter_search_used':False,'fresh_R23_used_for_selection':False,'formal_promotion_allowed_from_this_run':False},'source_manifest':manifest,'baseline':{'validation':baseline_v,'test':baseline_t},'scales':results};OUT.mkdir(parents=True,exist_ok=True);(OUT/'summary_r24.json').write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n');print(json.dumps(s,indent=2))

def verify():
 s=json.loads((OUT/'summary_r24.json').read_text());g=s['governance'];assert g['scales_predeclared']==[20,40,60,80] and g['classifier_train_multipliers_predeclared']==[1,2,3,4];assert g['validation_identity_exactly_same_as_R9b'] and g['test_identity_exactly_same_as_R9b'];assert g['same_date_results_and_xg_withheld'] and g['strict_prior_xg'];assert not g['odds_used'] and not g['market_prices_used'] and not g['hyperparameter_search_used'] and not g['fresh_R23_used_for_selection'];assert s['scales']['S40']['validation']['hits']==2067 and s['scales']['S40']['test']['hits']==1895;print('R24_VERIFY_PASS')
if __name__=='__main__':
 if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:raise SystemExit('usage: run_experiment_r24.py {run|verify}')
 {'run':run,'verify':verify}[sys.argv[1]]()
