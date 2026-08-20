#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import brentq,minimize_scalar
from scipy.stats import poisson

BASE_CODES=['E1','E2','E3','SC0','SC1','SC2','SC3','D2','I2','SP2','F2','P1']
SUPP_CODES=['EC','T1','G1']
BASE_SNAP_SHA='98e2865fad8206d41c6087d913e82212b9211da7fdbcc35041bb3810fced7828'
BASE_ID_SHA='7762c0f94adf3e734d7fce7f73dd203b61a761fafb733f717b939f3db35423ce'
SUPP_SNAP_SHA='bdb07dadb84e6d48b8d13d26977dbe59ba1ed479bed9227e880b28a319d4cf02'
SUPP_ID_SHA='d6caa480c1cbd6becc29991d0e31da169ba4659e9d3d4ac0562afa625eaa3c46'
BASE_N=4184; EARLY_N=2065; LATE_N=2119; SUPP_N=1082
SPLIT=pd.Timestamp('2026-01-01'); MAX_T=60; TH=np.arange(7,60,dtype=int)

def sha256(p:Path):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def ids_sha(keys): return hashlib.sha256(('\n'.join(sorted(keys))+'\n').encode()).hexdigest()
def devig(o,u):
 io=1.0/o; iu=1.0/u; return io/(io+iu)
def solve_lambda(p):
 return float(brentq(lambda x:poisson.sf(2,x)-p,0.05,10.0,xtol=1e-12,rtol=1e-12,maxiter=200))
def q_arrays(lam,gamma):
 m=np.asarray(lam,float)*math.exp(-float(gamma)); den=np.clip(poisson.sf(6,m),1e-300,1.0)
 ks=np.arange(7,MAX_T+1); q=poisson.pmf(ks[None,:],m[:,None])/den[:,None]
 tail60=poisson.sf(MAX_T,m)/den; p8=poisson.sf(7,m)/den; p9=poisson.sf(8,m)/den
 return q,tail60,p8,p9
def row_metrics(y,lam,gamma):
 y=np.asarray(y,int); q,t60,p8,p9=q_arrays(lam,gamma)
 obs=np.clip(q[np.arange(len(y)),y-7],1e-300,1.0); ll=-np.log(obs)
 one=np.zeros_like(q); one[np.arange(len(y)),y-7]=1.0
 brier=((q-one)**2).sum(axis=1); cq=np.cumsum(q,axis=1); truth=(y[:,None]<=TH[None,:]).astype(float)
 rps=((cq[:,:len(TH)]-truth)**2).mean(axis=1)
 return {'ll':ll,'brier':brier,'rps':rps,'r8':p8-(y>=8),'r9':p9-(y>=9),'tail60':t60,'norm':np.abs(q.sum(axis=1)+t60-1.0)}
def summarize(m): return {k:float(np.mean(v)) for k,v in m.items() if k not in {'tail60','norm'}}
def load_snapshot(path,expected_sha,expected_n,expected_id_sha):
 p=Path(path)
 if sha256(p)!=expected_sha: raise RuntimeError(f'snapshot sha mismatch {p}')
 d=pd.read_csv(p,dtype=str)
 if len(d)!=expected_n or ids_sha(d.identity_key.astype(str).tolist())!=expected_id_sha: raise RuntimeError(f'snapshot identity mismatch {p}')
 d['date']=pd.to_datetime(d['Date'],errors='coerce',dayfirst=True)
 if d['date'].isna().any(): raise RuntimeError('bad date')
 return d
def load_scores(root,codes,allowed_keys,forbidden_keys=None):
 allowed_keys=set(allowed_keys); forbidden_keys=set(forbidden_keys or []); out={}; forbidden_numeric=0; reports=[]
 for code in codes:
  p=Path(root)/f'{code}.csv'; ok=seen_forbid=0
  if not p.is_file(): raise RuntimeError(f'missing {code}')
  with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
   rd=csv.DictReader(f); hdr=rd.fieldnames or []
   for c in ['Date','HomeTeam','AwayTeam','FTHG','FTAG']:
    if c not in hdr: raise RuntimeError(f'{code} missing {c}')
   for r in rd:
    ds=(r.get('Date') or '').strip(); h=(r.get('HomeTeam') or '').strip(); a=(r.get('AwayTeam') or '').strip()
    if not(ds and h and a): continue
    key=f'{code}|{ds}|{h}|{a}'
    if key in allowed_keys:
     hg=pd.to_numeric(r.get('FTHG'),errors='coerce'); ag=pd.to_numeric(r.get('FTAG'),errors='coerce')
     if pd.notna(hg) and pd.notna(ag) and float(hg)>=0 and float(ag)>=0: out[key]=(int(hg),int(ag)); ok+=1
    elif key in forbidden_keys:
     # Explicitly do not touch FTHG/FTAG values for forbidden identities.
     seen_forbid+=1
   reports.append({'code':code,'allowed_valid_scores':ok,'forbidden_identities_seen_without_numeric_score_access':seen_forbid})
 return out,forbidden_numeric,reports
def tail_frame(snapshot,scores,block):
 d=snapshot[snapshot.identity_key.isin(scores)].copy(); d['T']=d.identity_key.map(lambda k:sum(scores[k])).astype(int); d=d[d['T']>=7].copy()
 if len(d)==0: return d
 d['block']=block
 d['lambda']=[solve_lambda(devig(float(o),float(u))) for o,u in zip(d['AvgC>2.5'],d['AvgC<2.5'])]
 return d

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--base-market',required=True); ap.add_argument('--supp-market',required=True); ap.add_argument('--base-current-dir',required=True); ap.add_argument('--supp-current-dir',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
 outdir=Path(a.out_dir); outdir.mkdir(parents=True,exist_ok=True)
 base=load_snapshot(a.base_market,BASE_SNAP_SHA,BASE_N,BASE_ID_SHA); supp=load_snapshot(a.supp_market,SUPP_SNAP_SHA,SUPP_N,SUPP_ID_SHA)
 early=base[base.date<SPLIT].copy(); late=base[base.date>=SPLIT].copy()
 if len(early)!=EARLY_N or len(late)!=LATE_N: raise RuntimeError('base split drift')
 bscores,late_access,brep=load_scores(a.base_current_dir,BASE_CODES,set(early.identity_key),set(late.identity_key))
 sscores,supp_forbid,srep=load_scores(a.supp_current_dir,SUPP_CODES,set(supp.identity_key),set())
 bcov=len(bscores)/EARLY_N; scov=len(sscores)/SUPP_N
 bt=tail_frame(early,bscores,'original_early'); st=tail_frame(supp,sscores,'supplemental')
 btail=len(bt); stail=len(st); pooled=pd.concat([bt,st],ignore_index=True); ntail=len(pooled)
 coverage_gate={'original_early_score_coverage_ge_0_995':bcov>=.995,'original_early_tail_exact_26':btail==26,'supp_score_coverage_ge_0_995':scov>=.995,'supp_tail_ge_15':stail>=15,'pooled_tail_ge_45':ntail>=45,'all_lambda_finite':bool(len(pooled)>0 and np.isfinite(pooled['lambda'].astype(float)).all()),'late_numeric_score_access_zero':late_access==0}
 base_info={'original_early_valid_scores':len(bscores),'original_early_score_coverage':bcov,'original_early_tail_count':btail,'supp_valid_scores':len(sscores),'supp_score_coverage':scov,'supp_tail_count':stail,'pooled_tail_count':ntail,'late_numeric_score_access_count':late_access}
 if not all(coverage_gate.values()):
  s={'schema_version':'C078E4_EXPANDED_CALIBRATION_V1','status':'STOP_EXPANDED_CALIBRATION_COVERAGE',**base_info,'coverage_gate':coverage_gate,'base_score_reports':brep,'supp_score_reports':srep,'formal_weight':0}
  (outdir/'summary.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(s,ensure_ascii=False,indent=2)); return 0
 y=pooled['T'].to_numpy(int); lam=pooled['lambda'].to_numpy(float)
 obj=lambda g:float(np.mean(row_metrics(y,lam,float(g))['ll']))
 res=minimize_scalar(obj,bounds=(0.0,2.0),method='bounded',options={'xatol':1e-10,'maxiter':2000}); gamma=float(res.x)
 bm=row_metrics(y,lam,0.0); cm=row_metrics(y,lam,gamma); bsum=summarize(bm); csum=summarize(cm)
 delta={k:csum[k]-bsum[k] for k in ['ll','brier','rps']}
 block_metrics={}
 for name,fr in [('original_early',bt),('supplemental',st)]:
  yy=fr['T'].to_numpy(int); ll=fr['lambda'].to_numpy(float); x0=row_metrics(yy,ll,0.0); x1=row_metrics(yy,ll,gamma)
  s0=summarize(x0); s1=summarize(x1); block_metrics[name]={'n':len(fr),'baseline':s0,'candidate':s1,'dlogloss':s1['ll']-s0['ll']}
 gate={**coverage_gate,'gamma_optimizer_success':bool(res.success),'gamma_not_at_bound':gamma>1e-6 and gamma<1.999,'pooled_dlogloss_lt_0':delta['ll']<0,'pooled_dbrier_le_0':delta['brier']<=0,'pooled_drps_le_0':delta['rps']<=0,'original_block_dlogloss_le_0':block_metrics['original_early']['dlogloss']<=0,'supp_block_dlogloss_le_0':block_metrics['supplemental']['dlogloss']<=0,'abs_r8_nonworse':abs(csum['r8'])<=abs(bsum['r8']),'abs_r9_nonworse':abs(csum['r9'])<=abs(bsum['r9']),'tail60_le_1e_8':float(np.max(cm['tail60']))<=1e-8,'normalization_le_1e_10':float(np.max(cm['norm']))<=1e-10}
 status='EXPANDED_CALIBRATION_PASS_FREEZE_GAMMA' if all(gate.values()) else 'EXPANDED_CALIBRATION_FAIL_PARK'
 summary={'schema_version':'C078E4_EXPANDED_CALIBRATION_V1','status':status,**base_info,'gamma':gamma,'optimizer_success':bool(res.success),'baseline':bsum,'candidate':csum,'delta_candidate_minus_baseline':delta,'block_metrics':block_metrics,'audits':{'max_tail60':float(np.max(cm['tail60'])),'max_normalization_residual':float(np.max(cm['norm']))},'gate':gate,'base_score_reports':brep,'supp_score_reports':srep,'label_boundary':{'late_numeric_score_access_count':late_access,'late_score_values_stored':False,'late_score_values_hashed':False,'late_goal_totals_computed':False,'late_metrics_computed':False},'formal_weight':0}
 state={'gamma':gamma,'base_market_snapshot_sha256':BASE_SNAP_SHA,'supp_market_snapshot_sha256':SUPP_SNAP_SHA,'pooled_tail_count':ntail,'lambda_definition':'closing O/U2.5 devig -> Poisson P(T>=3) inversion','tail_transform':'q_gamma proportional q0*exp(-gamma*(t-7))'}
 (outdir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n'); (outdir/'frozen_gamma.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
 cols=['identity_key','block','code','Date','HomeTeam','AwayTeam','T','lambda']; pooled[cols].to_csv(outdir/'calibration_tail_receipt.csv',index=False)
 print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
