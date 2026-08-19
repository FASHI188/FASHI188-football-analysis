#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd

import audit_c071_opportunity_source as audit
import evaluate_c071b_opportunity_pt_v2 as c071

SCHEMA="C076C_FOURHAZARD_TAIL_DEVELOPMENT_V1"
FIX_SHA="7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
STAT_SHA="2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
DEV_CUTOFF=pd.Timestamp("2024-01-01T00:00:00Z")
CONFIRM_END=pd.Timestamp("2026-07-01T00:00:00Z")
EXPECTED_CONFIRM=72180
TEST_YEARS=[2019,2020,2021,2022,2023]
MIN_TRAIN=500; MIN_TEST=150; MIN_TRAIN_E3=20; MIN_POOLED=1200
BOOT_REPS=3000; BOOT_SEED=76003
TAIL_LIMIT=1e-8; PROB_LIMIT=1e-10


def sha256(p:Path):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''): h.update(b)
 return h.hexdigest()

def utc_ns(x):
 z=pd.to_datetime(x,utc=True,errors='coerce')
 if isinstance(z,pd.Series): return z.dt.as_unit('ns')
 if isinstance(z,pd.DatetimeIndex): return z.as_unit('ns')
 return z

def fit_simple(e):
 e=np.asarray(e,float); return float((e.sum()+.5)/((e+1).sum()+1.0))
def fit_four(e):
 e=np.asarray(e,int); n=len(e)
 h1=((e>=1).sum()+.5)/(n+1.0)
 n1=(e>=1).sum(); h2=((e>=2).sum()+.5)/(n1+1.0)
 n2=(e>=2).sum(); h3=((e>=3).sum()+.5)/(n2+1.0)
 high=e[e>=3]; y=high-3
 rho=(y.sum()+.5)/((y+1).sum()+1.0)
 return tuple(float(v) for v in (h1,h2,h3,rho)), int(len(high))
def p_simple(e,r):
 e=np.asarray(e,int); return (1-r)*np.power(r,e)
def p_four(e,par):
 h1,h2,h3,rho=par; e=np.asarray(e,int); p=np.empty(len(e),float)
 p[e==0]=1-h1; p[e==1]=h1*(1-h2); p[e==2]=h1*h2*(1-h3)
 m=e>=3
 p[m]=h1*h2*h3*(1-rho)*np.power(rho,e[m]-3)
 return p
def residual_simple(k,r): return float(r**(k+1))
def residual_four(k,par):
 h1,h2,h3,rho=par
 return float(h1*h2*h3*(rho**(k-2))) if k>=3 else float(h1*h2*h3)
def choose_k(obs,r,par):
 k=max(3,int(obs))
 while max(residual_simple(k,r),residual_four(k,par))>TAIL_LIMIT:
  k+=1
  if k>500: raise RuntimeError('tail residual closure failed')
 return k
def vec_simple(k,r):
 x=np.arange(k+1); v=np.r_[p_simple(x,r),residual_simple(k,r)]; return v/v.sum()
def vec_four(k,par):
 x=np.arange(k+1); v=np.r_[p_four(x,par),residual_four(k,par)]; return v/v.sum()
def rows(e,exact,vec,k):
 e=np.asarray(e,int); ll=-np.log(np.clip(exact,1e-300,1)); y=np.minimum(e,k+1)
 pm=np.repeat(vec[None,:],len(e),0); one=np.zeros_like(pm); one[np.arange(len(e)),y]=1
 b=np.square(pm-one).sum(1); cp=np.cumsum(pm,1)[:,:-1]; cy=np.cumsum(one,1)[:,:-1]
 rps=np.square(cp-cy).sum(1)/(pm.shape[1]-1); top=(np.argmax(pm,1)==y).astype(float)
 return {'ll':ll,'brier':b,'rps':rps,'top1':top}
def meanrows(r): return {k:float(np.mean(v)) for k,v in r.items()}
def thresh_brier(e,probs):
 e=np.asarray(e,int); return {f'E_ge_{j}':float(np.mean(((e>=j).astype(float)-p)**2)) for j,p in probs.items()}
def simple_thresh(r): return {1:r,2:r*r,3:r**3}
def four_thresh(p):
 h1,h2,h3,_=p; return {1:h1,2:h1*h2,3:h1*h2*h3}
def boot(d):
 d=np.asarray(d,float); rng=np.random.default_rng(BOOT_SEED); sims=np.empty(BOOT_REPS)
 for i in range(BOOT_REPS): sims[i]=d[rng.integers(0,len(d),len(d))].mean()
 return {'n':len(d),'reps':BOOT_REPS,'seed':BOOT_SEED,'mean_delta':float(d.mean()),'ci90_low':float(np.quantile(sims,.05)),'ci90_high':float(np.quantile(sims,.95)),'p_delta_lt_zero':float(np.mean(sims<0))}
def par_sha(obj): return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--fixtures',required=True); ap.add_argument('--stats',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
 fp=Path(a.fixtures); sp=Path(a.stats); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 if sha256(fp)!=FIX_SHA or sha256(sp)!=STAT_SHA: raise RuntimeError('source SHA mismatch')
 c071.utc=utc_ns
 fixtures=pd.read_parquet(fp,columns=audit.FIXTURE_COLS); fixtures.date_utc=utc_ns(fixtures.date_utc); fixtures=fixtures.dropna(subset=audit.FIXTURE_COLS).sort_values(['date_utc','id']).reset_index(drop=True); fixtures.id=fixtures.id.astype('int64')
 stats=pd.read_parquet(sp,columns=audit.STAT_COLS); stats.known_at=utc_ns(stats.known_at); stats=stats.dropna(subset=['fixture_id','known_at'])
 eligible,_=c071.eligible_identities(fixtures,stats); sealed=eligible[(eligible.date_utc>=DEV_CUTOFF)&(eligible.date_utc<CONFIRM_END)]
 if len(sealed)!=EXPECTED_CONFIRM: raise RuntimeError('sealed identity drift')
 labels=c071.read_dev_labels(fp)
 if len(labels) and not (labels.date_utc<DEV_CUTOFF).all(): raise RuntimeError('label horizon breach')
 dev=eligible[eligible.date_utc<DEV_CUTOFF].merge(labels[['id','goals_home','goals_away']],on='id',how='left',validate='one_to_one').dropna(subset=['goals_home','goals_away']).copy()
 dev['goals_home']=dev.goals_home.astype(int); dev['goals_away']=dev.goals_away.astype(int); dev['total']=dev.goals_home+dev.goals_away; dev['year']=dev.date_utc.dt.year.astype(int)
 tail=dev[dev.total>=7].copy(); tail['E']=tail.total-7
 coverage={}; cov=True
 for y in TEST_YEARS:
  tr=tail[tail.year<y]; te=tail[tail.year==y]; ne3=int((tr.E>=3).sum()); coverage[str(y)]={'train_tail':len(tr),'test_tail':len(te),'train_E_ge_3':ne3}; cov &= len(tr)>=MIN_TRAIN and len(te)>=MIN_TEST and ne3>=MIN_TRAIN_E3
 pooled_expected=sum(v['test_tail'] for v in coverage.values()); cov &= pooled_expected>=MIN_POOLED
 boundary={'C075C_consumed_tail_labels_used':False,'C075E_consumed_tail_labels_used':False,'C071_post2024_goal_labels_opened':False,'C071_reserve_52180_opened':False,'C070F_confirmation1597_opened':False,'A05_opened':False,'protected_opened':False,'T_ge_7_D_given_T_model_fit':False,'unified_matrix_generated':False,'formal_weight':0}
 if not cov:
  s={'schema_version':SCHEMA,'status':'STOP_COVERAGE','scientific_effect_evaluated':False,'coverage':coverage,'pooled_expected':pooled_expected,'boundaries':boundary}; (out/'summary.json').write_text(json.dumps(s,indent=2)+'\n'); print(json.dumps(s,indent=2)); return 0
 parts=[]; folds={}; maxres=maxprob=0
 for y in TEST_YEARS:
  tr=tail[tail.year<y]; te=tail[tail.year==y]; etr=tr.E.to_numpy(int); ete=te.E.to_numpy(int)
  r=fit_simple(etr); par,nhi=fit_four(etr); k=choose_k(ete.max(),r,par); vb=vec_simple(k,r); vc=vec_four(k,par); rb=rows(ete,p_simple(ete,r),vb,k); rc=rows(ete,p_four(ete,par),vc,k); sb=meanrows(rb); sc=meanrows(rc)
  rs=max(residual_simple(k,r),residual_four(k,par)); pr=max(abs(vb.sum()-1),abs(vc.sum()-1)); maxres=max(maxres,rs); maxprob=max(maxprob,pr)
  folds[str(y)]={'train_tail':len(tr),'test_tail':len(te),'baseline_r':r,'candidate_h1':par[0],'candidate_h2':par[1],'candidate_h3':par[2],'candidate_rho_tail':par[3],'train_E_ge_3':nhi,'baseline':sb,'candidate':sc,'delta':{m:sc[m]-sb[m] for m in sb},'baseline_threshold_brier':thresh_brier(ete,simple_thresh(r)),'candidate_threshold_brier':thresh_brier(ete,four_thresh(par)),'K':k,'max_tail_residual':rs,'prob_residual':pr}
  parts.append(pd.DataFrame({'year':y,'E':ete,'baseline_ll':rb['ll'],'candidate_ll':rc['ll'],'baseline_brier':rb['brier'],'candidate_brier':rc['brier'],'baseline_rps':rb['rps'],'candidate_rps':rc['rps']}))
 df=pd.concat(parts,ignore_index=True); df['dll']=df.candidate_ll-df.baseline_ll; df.to_csv(out/'row_metrics.csv',index=False)
 pooled={'baseline':{'logloss':float(df.baseline_ll.mean()),'brier':float(df.baseline_brier.mean()),'rps':float(df.baseline_rps.mean())},'candidate':{'logloss':float(df.candidate_ll.mean()),'brier':float(df.candidate_brier.mean()),'rps':float(df.candidate_rps.mean())}}
 delta={m:pooled['candidate'][m]-pooled['baseline'][m] for m in pooled['baseline']}; bs=boot(df.dll.to_numpy()); wins=sum(folds[str(y)]['delta']['ll']<0 for y in TEST_YEARS)
 gate={'pooled_dll_lt0':delta['logloss']<0,'bootstrap90_upper_lt0':bs['ci90_high']<0,'pooled_dbrier_le0':delta['brier']<=0,'pooled_drps_le0':delta['rps']<=0,'fold_wins_ge4':wins>=4,'tail_residual_le1e8':maxres<=TAIL_LIMIT,'prob_residual_le1e10':maxprob<=PROB_LIMIT}; passed=all(gate.values())
 param=None
 if passed:
  e=tail.E.to_numpy(int); par,_=fit_four(e); param={'family':'four-parameter piecewise hazard','h1':par[0],'h2':par[1],'h3':par[2],'rho_tail':par[3],'development_tail_n':len(e),'horizon':'pre-2024 broad C071 threshold8','source_revision':'f0cbe86bee3f28cc4b803f735a5287968741c471'}; param['parameter_sha256']=par_sha(param); (out/'confirmation_ready_parameter.json').write_text(json.dumps(param,indent=2)+'\n')
 s={'schema_version':SCHEMA,'status':'DEVELOPMENT_GATE_PASS_POSTVIEW' if passed else 'FAIL_PARK','scientific_effect_evaluated':True,'formal_weight':0,'postview_development':True,'coverage':coverage,'pooled_oos_tail':len(df),'pooled':{**pooled,'delta_candidate_minus_baseline':delta,'bootstrap':bs,'fold_wins':wins,'max_tail_residual':maxres,'max_prob_residual':maxprob},'folds':folds,'gate':gate,'confirmation_ready_parameter':param,'boundaries':boundary,'stopping_rule':'If FAIL, stop tail-family model shopping under current data program.'}
 (out/'summary.json').write_text(json.dumps(s,indent=2)+'\n'); print(json.dumps(s,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
