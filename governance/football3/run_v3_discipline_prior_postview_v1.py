#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, hashlib, importlib.util, json, math
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

FIELDS=("yellow_cards","red_cards","fouls")
R9_SHA="6ea5f6d98a6b43c1f34df58f08edfa52819415f79da88428947caae68d9170ba"
FIX_SHA="7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
STAT_SHA="2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
EXPECTED={
 "validation":{"count":4096,"hits":2064,"logloss":1.003825477004625,"brier":0.6008825047888506,"rps":0.2075482221062071},
 "test":{"count":3805,"hits":1877,"logloss":1.0184146385512838,"brier":0.6098120193893798,"rps":0.20875578015943605},
}
class Stop(RuntimeError): pass

def fsha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()

def loadmod(path):
 spec=importlib.util.spec_from_file_location("r9frozen",path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def check_contract(path):
 c=json.loads(Path(path).read_text())
 required={"schema":"f3-v3-discipline-prior-postview-v1","status":"POST_VIEW_DEVELOPMENT_ONLY","independent":False,"promotion":False,"baseline":"R9_K1_FIXED","candidate":"R9_K1_PLUS_STRICT_PRIOR_TEAM_DISCIPLINE","candidate_feature_count":6,"same_date_update_allowed":False,"current_match_discipline_allowed":False,"referee_allowed":False,"formal_weight":0,"matrix_delta":0}
 for k,v in required.items():
  if c.get(k)!=v: raise Stop(f"STOP_CONTRACT_{k}")
 if c.get("pass_gates")!=["VAL_LOGLOSS_LT_0","TEST_LOGLOSS_LT_0","TEST_BRIER_LE_0","TEST_RPS_LE_0","VAL_BRIER_RPS_LE_0","VAL_TOP1_GE_0","TEST_TOP1_GE_0","TEST_LOGLOSS_CI_UPPER_LT_0"]: raise Stop("STOP_GATE_DRIFT")
 return c

def read_r9(path):
 out=[]
 with Path(path).open(encoding='utf-8') as f:
  for r in csv.DictReader(f):
   r['home_goals']=int(r['home_goals']);r['away_goals']=int(r['away_goals']);r['home_xg']=float(r['home_xg']);r['away_xg']=float(r['away_xg']);out.append(r)
 out.sort(key=lambda r:(r['date'],r['game_id']))
 if len(out)!=20000: raise Stop("STOP_R9_COUNT")
 return out

def source_rows(r9_rows,fixtures_path,stats_path):
 ids={int(r['game_id']) for r in r9_rows}
 fx=pd.read_parquet(fixtures_path,columns=['id','date_utc','home_team_id','away_team_id'])
 fx=fx[fx['id'].isin(ids)].drop_duplicates('id')
 stcols=['fixture_id','known_at','home_yellow_cards','away_yellow_cards','home_red_cards','away_red_cards','home_fouls','away_fouls']
 st=pd.read_parquet(stats_path,columns=stcols)
 st=st[st['fixture_id'].isin(ids)].drop_duplicates('fixture_id')
 if fx['id'].nunique()!=20000 or st['fixture_id'].nunique()!=20000: raise Stop("STOP_SOURCE_JOIN_COUNT")
 q=fx.merge(st,left_on='id',right_on='fixture_id',validate='one_to_one')
 q['date_utc']=pd.to_datetime(q['date_utc'],utc=True);q['known_at']=pd.to_datetime(q['known_at'],utc=True)
 by={int(r.id):r for r in q.itertuples(index=False)}
 for z in r9_rows:
  r=by[int(z['game_id'])]
  if str(int(r.home_team_id))!=z['home_team'] or str(int(r.away_team_id))!=z['away_team']: raise Stop("STOP_TEAM_IDENTITY")
  if r.known_at <= r.date_utc: raise Stop("STOP_DISCIPLINE_NOT_POSTMATCH")
 return by

class Prior:
 def __init__(self):
  self.s=defaultdict(lambda:defaultdict(float));self.n=defaultdict(lambda:defaultdict(int));self.gs=defaultdict(float);self.gn=defaultdict(int)
 def add(self,team,vals):
  for f,v in vals.items():
   if v is None or (isinstance(v,float) and math.isnan(v)): continue
   x=float(v)
   if x<0 or not math.isfinite(x): raise Stop("STOP_BAD_DISCIPLINE_VALUE")
   self.s[str(team)][f]+=x;self.n[str(team)][f]+=1;self.gs[f]+=x;self.gn[f]+=1
 def mean(self,team,f):
  t=str(team)
  if self.n[t][f]: return self.s[t][f]/self.n[t][f]
  return self.gs[f]/self.gn[f] if self.gn[f] else 0.0
 def feat(self,h,a):
  return [self.mean(h,f) for f in FIELDS]+[self.mean(a,f) for f in FIELDS]

def discipline_features(r9_rows,src):
 bydate=defaultdict(list)
 for r in r9_rows: bydate[r['date']].append(r)
 pending=[]; state=Prior(); out={}
 for ds in sorted(bydate):
  today=sorted(bydate[ds],key=lambda r:r['game_id'])
  first_k=min(src[int(r['game_id'])].date_utc for r in today)
  keep=[]
  for ev in pending:
   if ev['source_date']<ds and ev['known_at']<first_k:
    state.add(ev['home_team'],ev['home_vals']);state.add(ev['away_team'],ev['away_vals'])
   else: keep.append(ev)
  pending=keep
  for r in today: out[r['game_id']]=state.feat(r['home_team'],r['away_team'])
  for r in today:
   z=src[int(r['game_id'])]
   pending.append({'source_date':ds,'known_at':z.known_at,'home_team':r['home_team'],'away_team':r['away_team'],
    'home_vals':{'yellow_cards':z.home_yellow_cards,'red_cards':z.home_red_cards,'fouls':z.home_fouls},
    'away_vals':{'yellow_cards':z.away_yellow_cards,'red_cards':z.away_red_cards,'fouls':z.away_fouls}})
 return out

def metrics(rows,key):
 n=len(rows); hit=ll=br=rps=0
 for r in rows:
  p=r[key];y=r['y'];v=np.array([p['p_home'],p['p_draw'],p['p_away']],float);t=int(np.argmax(v));hit+=t==y;ll-=math.log(max(v[y],1e-15));br+=sum((v[i]-(i==y))**2 for i in range(3));rps+=((v[0]-(y==0))**2+((v[0]+v[1])-(y<=1))**2)/2
 return {'count':n,'hits':hit,'top1_accuracy':hit/n,'logloss':ll/n,'brier':br/n,'rps':rps/n}

def loss_delta_by_date(rows):
 d=defaultdict(list)
 for r in rows:
  y=r['y'];a=r['K1'];b=r['D1'];la=-math.log(max([a['p_home'],a['p_draw'],a['p_away']][y],1e-15));lb=-math.log(max([b['p_home'],b['p_draw'],b['p_away']][y],1e-15));d[r['date']].append(lb-la)
 return d

def block_ci(rows,reps=5000,seed=20260914):
 d=loss_delta_by_date(rows);keys=sorted(d);rng=np.random.default_rng(seed);vals=[]
 for _ in range(reps):
  sample=rng.choice(keys,size=len(keys),replace=True);z=[x for k in sample for x in d[k]];vals.append(float(np.mean(z)))
 return {'low':float(np.quantile(vals,.025)),'high':float(np.quantile(vals,.975)),'replicates':reps,'seed':seed,'unit':'target_date'}

def delta(a,b): return {k:b[k]-a[k] for k in ('logloss','brier','rps','top1_accuracy')}

def main():
 ap=argparse.ArgumentParser()
 for n in ('r9_csv','r9_runner','fixtures','match_stats','contract','output'):ap.add_argument('--'+n.replace('_','-'),dest=n,required=True)
 x=ap.parse_args();c=check_contract(x.contract)
 if fsha(x.r9_csv)!=R9_SHA or fsha(x.fixtures)!=FIX_SHA or fsha(x.match_stats)!=STAT_SHA: raise Stop("STOP_SOURCE_SHA_DRIFT")
 r9=loadmod(x.r9_runner);rows=read_r9(x.r9_csv);src=source_rows(rows,x.fixtures,x.match_stats);df=discipline_features(rows,src)
 st=r9.S();pred=[];by=defaultdict(list)
 for r in rows:by[r['date']].append(r)
 for ds in sorted(by):
  pending=[]
  for rr in sorted(by[ds],key=lambda q:q['game_id']):
   p=st.pred(rr);pred.append({'date':ds,'game_id':rr['game_id'],'y':r9.actual(rr),'raw':p,'disc':df[rr['game_id']]});pending.append((rr,p))
  for rr,p in pending:st.update(rr,p)
 b1=r9.boundary(pred,4000);b2=r9.boundary(pred,b1+8000);b3=r9.boundary(pred,b2+4000);train=pred[b1:b2];val=pred[b2:b3];test=pred[b3:]
 if [b1,b2-b1,b3-b2,len(test)]!=[4058,8041,4096,3805]: raise Stop("STOP_SPLIT_DRIFT")
 from sklearn.linear_model import LogisticRegression
 from sklearn.pipeline import make_pipeline
 from sklearn.preprocessing import StandardScaler
 y=[r['y'] for r in train]
 def model(): return make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0))
 m0=model();m1=model();m0.fit([r9.feat_k1(r['raw']) for r in train],y);m1.fit([r9.feat_k1(r['raw'])+r['disc'] for r in train],y)
 for subset in (val,test):
  p0=m0.predict_proba([r9.feat_k1(r['raw']) for r in subset]);p1=m1.predict_proba([r9.feat_k1(r['raw'])+r['disc'] for r in subset])
  for r,a,b in zip(subset,p0,p1):r['K1']=r9.decorate(a);r['D1']=r9.decorate(b)
 vm0,vm1,tm0,tm1=metrics(val,'K1'),metrics(val,'D1'),metrics(test,'K1'),metrics(test,'D1')
 for name,m in [('validation',vm0),('test',tm0)]:
  e=EXPECTED[name]
  if m['count']!=e['count'] or m['hits']!=e['hits'] or any(abs(m[k]-e[k])>1e-9 for k in ('logloss','brier','rps')): raise Stop(f"STOP_BASELINE_DRIFT_{name}")
 vd,td=delta(vm0,vm1),delta(tm0,tm1);ci=block_ci(test,c['bootstrap']['replicates'],c['bootstrap']['seed'])
 gates={
  'G1_VAL_LOGLOSS_LT_0':vd['logloss']<0,
  'G2_TEST_LOGLOSS_LT_0':td['logloss']<0,
  'G3_TEST_BRIER_LE_0':td['brier']<=0,
  'G4_TEST_RPS_LE_0':td['rps']<=0,
  'G5_VAL_BRIER_RPS_LE_0':vd['brier']<=0 and vd['rps']<=0,
  'G6_VAL_TOP1_GE_0':vd['top1_accuracy']>=0,
  'G7_TEST_TOP1_GE_0':td['top1_accuracy']>=0,
  'G8_TEST_LOGLOSS_CI_UPPER_LT_0':ci['high']<0,
 }
 decision='POST_VIEW_DEVELOPMENT_SIGNAL_PRESENT_REQUIRES_INDEPENDENT_CONFIRMATION' if all(gates.values()) else 'SCIENTIFIC_EVALUATION_FAIL_CLOSE_NO_RETUNE'
 out={'schema':'f3-v3-discipline-prior-postview-receipt-v1','decision':decision,'status':'POST_VIEW_DEVELOPMENT_ONLY','independent':False,'promotion':False,'snapshot_sha256':R9_SHA,'cohort_rows':20000,'splits':{'burn':b1,'train':len(train),'validation':len(val),'test':len(test)},'baseline':{'validation':vm0,'test':tm0},'candidate':{'validation':vm1,'test':tm1},'delta_candidate_minus_baseline':{'validation':vd,'test':td},'bootstrap_test_logloss_delta':ci,'gates':gates,'current_match_discipline_used':False,'same_date_update_used':False,'referee_used':False,'tuning':False,'future_matches_used':False,'formal_weight':0,'matrix_delta':0}
 Path(x.output).parent.mkdir(parents=True,exist_ok=True);Path(x.output).write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps(out,sort_keys=True))
if __name__=='__main__':main()
