#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import brentq,minimize_scalar
from scipy.stats import poisson

CODES=['E1','E2','E3','SC0','SC1','SC2','SC3','D2','I2','SP2','F2','P1']
SNAP_SHA='98e2865fad8206d41c6087d913e82212b9211da7fdbcc35041bb3810fced7828'
ID_SHA='7762c0f94adf3e734d7fce7f73dd203b61a761fafb733f717b939f3db35423ce'
N=4184; EARLY_N=2065; LATE_N=2119; SPLIT=pd.Timestamp('2026-01-01'); MAX_T=60
TH=np.arange(7,60,dtype=int)

def sha256(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def ids_sha(keys): return hashlib.sha256(('\n'.join(sorted(keys))+'\n').encode()).hexdigest()
def devig(o,u):
 io=1.0/o; iu=1.0/u; return io/(io+iu)
def solve_lambda(p):
 f=lambda lam: poisson.sf(2,lam)-p
 return float(brentq(f,0.05,10.0,xtol=1e-12,rtol=1e-12,maxiter=200))
def q_arrays(lam,gamma):
 m=np.asarray(lam,float)*math.exp(-float(gamma)); den=np.clip(poisson.sf(6,m),1e-300,1.0)
 ks=np.arange(7,MAX_T+1)
 q=poisson.pmf(ks[None,:],m[:,None])/den[:,None]
 tail60=poisson.sf(MAX_T,m)/den
 p8=poisson.sf(7,m)/den; p9=poisson.sf(8,m)/den
 return q,tail60,p8,p9
def metrics(y,lam,gamma):
 y=np.asarray(y,int); q,tail60,p8,p9=q_arrays(lam,gamma)
 obs=np.clip(q[np.arange(len(y)),y-7],1e-300,1.0); ll=-np.log(obs)
 one=np.zeros_like(q); one[np.arange(len(y)),y-7]=1.0
 brier=((q-one)**2).sum(axis=1)
 cq=np.cumsum(q,axis=1); truth=(y[:,None]<=TH[None,:]).astype(float)
 rps=((cq[:,:len(TH)]-truth)**2).mean(axis=1)
 return {'ll':ll,'brier':brier,'rps':rps,'r8':p8-(y>=8),'r9':p9-(y>=9),'tail60':tail60,'norm_resid':np.abs(q.sum(axis=1)+tail60-1.0)}
def load_scores(root,early_keys,late_keys):
 scores={}; late_numeric=0; reports=[]
 for code in CODES:
  p=Path(root)/f'{code}.csv'; ne=nl=0
  with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
   rd=csv.DictReader(f)
   for c in ['Date','HomeTeam','AwayTeam','FTHG','FTAG']:
    if c not in (rd.fieldnames or []): raise RuntimeError(f'{code} missing {c}')
   for row in rd:
    d=(row.get('Date') or '').strip(); h=(row.get('HomeTeam') or '').strip(); a=(row.get('AwayTeam') or '').strip()
    if not(d and h and a): continue
    key=f'{code}|{d}|{h}|{a}'
    if key in early_keys:
     hg=pd.to_numeric(row.get('FTHG'),errors='coerce'); ag=pd.to_numeric(row.get('FTAG'),errors='coerce')
     if pd.notna(hg) and pd.notna(ag): scores[key]=(int(hg),int(ag)); ne+=1
    elif key in late_keys:
     nl+=1
     # Deliberately never access FTHG/FTAG for late identities.
   reports.append({'code':code,'early_valid_scores':ne,'late_identities_seen_without_numeric_score_access':nl})
 return scores,late_numeric,reports

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--market-snapshot',required=True); ap.add_argument('--current-dir',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
 out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True); snap=Path(a.market_snapshot)
 d=pd.read_csv(snap,dtype=str)
 if sha256(snap)!=SNAP_SHA or len(d)!=N or ids_sha(d.identity_key.astype(str).tolist())!=ID_SHA: raise RuntimeError('frozen snapshot mismatch')
 d['date']=pd.to_datetime(d.Date,errors='coerce',dayfirst=True); early=d[d.date<SPLIT].copy(); late=d[d.date>=SPLIT].copy()
 if len(early)!=EARLY_N or len(late)!=LATE_N: raise RuntimeError('split drift')
 scores,late_numeric,reports=load_scores(a.current_dir,set(early.identity_key),set(late.identity_key)); coverage=len(scores)/EARLY_N
 early=early[early.identity_key.isin(scores)].copy()
 early['T']=early.identity_key.map(lambda k:sum(scores[k])).astype(int)
 tail=early[early.T>=7].copy(); tail_n=len(tail)
 base={'early_valid_score_count':len(scores),'early_score_coverage':coverage,'early_tail_count':tail_n,'late_numeric_score_access_count':late_numeric}
 if coverage<0.995 or tail_n<35:
  s={'schema_version':'C078E2_MARKET_TAIL_CALIBRATION_V1','status':'STOP_CALIBRATION_COVERAGE',**base,'score_reports':reports,'formal_weight':0}
  (out/'summary.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(s,ensure_ascii=False,indent=2)); return 0
 lamb=[]
 for _,r in tail.iterrows():
  o=float(r['AvgC>2.5']); u=float(r['AvgC<2.5']); lamb.append(solve_lambda(devig(o,u)))
 lam=np.asarray(lamb); y=tail.T.to_numpy(int)
 obj=lambda g:float(np.mean(metrics(y,lam,float(g))['ll']))
 res=minimize_scalar(obj,bounds=(0.0,2.0),method='bounded',options={'xatol':1e-10,'maxiter':2000}); gamma=float(res.x)
 bm=metrics(y,lam,0.0); cm=metrics(y,lam,gamma)
 b={'ll':float(bm['ll'].mean()),'brier':float(bm['brier'].mean()),'rps':float(bm['rps'].mean()),'r8':float(bm['r8'].mean()),'r9':float(bm['r9'].mean())}
 c={'ll':float(cm['ll'].mean()),'brier':float(cm['brier'].mean()),'rps':float(cm['rps'].mean()),'r8':float(cm['r8'].mean()),'r9':float(cm['r9'].mean())}
 delta={k:c[k]-b[k] for k in ['ll','brier','rps']}
 gate={'snapshot_match':True,'late_numeric_score_access_zero':late_numeric==0,'early_score_coverage_ge_0_995':coverage>=0.995,'early_tail_ge_35':tail_n>=35,'lambda_all_finite':bool(np.isfinite(lam).all()),'gamma_optimizer_success':bool(res.success),'gamma_not_at_bound':gamma>1e-6 and gamma<1.999,'tail_dlogloss_lt_0':delta['ll']<0,'tail_dbrier_le_0':delta['brier']<=0,'tail_drps_le_0':delta['rps']<=0,'abs_r8_nonworse':abs(c['r8'])<=abs(b['r8']),'abs_r9_nonworse':abs(c['r9'])<=abs(b['r9']),'tail60_le_1e_8':float(cm['tail60'].max())<=1e-8,'normalization_le_1e_10':float(cm['norm_resid'].max())<=1e-10}
 status='CALIBRATION_PASS_FREEZE_GAMMA' if all(gate.values()) else 'CALIBRATION_FAIL_PARK'
 s={'schema_version':'C078E2_MARKET_TAIL_CALIBRATION_V1','status':status,**base,'gamma':gamma,'optimizer_success':bool(res.success),'lambda_summary':{'min':float(lam.min()),'mean':float(lam.mean()),'max':float(lam.max())},'baseline':b,'candidate':c,'delta_candidate_minus_baseline':delta,'audits':{'max_tail60':float(cm['tail60'].max()),'max_normalization_residual':float(cm['norm_resid'].max())},'gate':gate,'score_reports':reports,'label_boundary':{'late_numeric_score_access_count':late_numeric,'late_score_values_stored':False,'late_score_values_hashed':False,'late_goal_totals_computed':False,'late_metrics_computed':False},'formal_weight':0}
 state={'gamma':gamma,'market_snapshot_sha256':SNAP_SHA,'market_identity_sha256':ID_SHA,'split_date':'2026-01-01','lambda_definition':'closing O/U2.5 de-vig -> Poisson P(T>=3) inversion','tail_transform':'q_gamma proportional q0*exp(-gamma*(t-7))'}
 (out/'summary.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n'); (out/'frozen_gamma.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(s,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
