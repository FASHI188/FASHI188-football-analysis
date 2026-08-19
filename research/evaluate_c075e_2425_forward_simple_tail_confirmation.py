#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, re
from pathlib import Path
import numpy as np
import pandas as pd

SCHEMA="C075E_2425_FORWARD_SIMPLE_TAIL_CONFIRMATION_V1"
FILES=[
"2024-25/at.1.json","2024-25/at.2.json","2024-25/au.1.json","2024-25/de.2.json",
"2024-25/en.2.json","2024-25/en.3.json","2024-25/en.4.json","2024-25/es.2.json",
"2024-25/fr.2.json","2024-25/gr.1.json","2024-25/it.2.json","2024-25/ma.1.json",
"2024-25/mx.1.json","2024-25/pt.1.json","2024-25/sco.1.json","2024-25/tr.1.json"]
IDENTITY_N=5388
IDENTITY_SHA="2d054e8d514a8d7bd6ce421ffe452876b3070b88871b12ca72eabc0061bb04f4"
R=0.3158284023668639; MEAN_E=0.4616216216216215; K=15; PARAM_SHA="4d782338ea14288d608814c7f9f6b51044edbce0a224501b72bdbf2e7e24e411"
BOOT_REPS=5000; BOOT_SEED=75005; KS_REPS=5000; KS_SEED=75006; Z=1.6448536269514722
VALID_MIN=.98; TAIL_MIN=60; BLOCK_MIN=20; FILE_TAIL_MIN=8; ELIGIBLE_FILES_MIN=5; TEMP_CUTOFF="2025-01-01"
STR=r'"((?:\\.|[^"\\])*)"'; DATE=re.compile(r'"date"\s*:\s*'+STR); T1=re.compile(r'"team1"\s*:\s*'+STR); T2=re.compile(r'"team2"\s*:\s*'+STR)

def dec(x): return json.loads('"'+x+'"')
def digest(keys): return hashlib.sha256(("\n".join(sorted(keys))+"\n").encode()).hexdigest()
def objects(text):
 m=re.search(r'"matches"\s*:\s*\[',text)
 if not m: raise RuntimeError('matches array absent')
 i=m.end(); n=len(text); ins=False; esc=False; arr=1
 while i<n and arr>0:
  c=text[i]
  if ins:
   if esc: esc=False
   elif c=='\\': esc=True
   elif c=='"': ins=False
   i+=1; continue
  if c=='"': ins=True; i+=1; continue
  if c=='[': arr+=1; i+=1; continue
  if c==']': arr-=1; i+=1; continue
  if c!='{' or arr!=1: i+=1; continue
  st=i; b=0; s=False; e=False
  while i<n:
   ch=text[i]
   if s:
    if e:e=False
    elif ch=='\\':e=True
    elif ch=='"':s=False
   else:
    if ch=='"':s=True
    elif ch=='{':b+=1
    elif ch=='}':
     b-=1
     if b==0: i+=1; yield text[st:i]; break
   i+=1
  else: raise RuntimeError('unterminated object')
def ident(o):
 d=DATE.search(o); h=T1.search(o); a=T2.search(o)
 if not(d and h and a): return None
 return dec(d.group(1)),dec(h.group(1)),dec(a.group(1))
def reproduce(root):
 keys=[]
 for rel in FILES:
  for o in objects((root/rel).read_text(encoding='utf-8')):
   x=ident(o)
   if x is None: raise RuntimeError(f'identity failure {rel}')
   keys.append(f'{rel}|{x[0]}|{x[1]}|{x[2]}')
 if len(keys)!=IDENTITY_N or len(set(keys))!=IDENTITY_N or digest(keys)!=IDENTITY_SHA: raise RuntimeError('frozen identity drift')
 return set(keys)
def scores(root,frozen):
 rows=[]; decoded=0
 for rel in FILES:
  for o in objects((root/rel).read_text(encoding='utf-8')):
   x=ident(o); key=f'{rel}|{x[0]}|{x[1]}|{x[2]}'
   if key not in frozen: continue
   decoded+=1; m=json.loads(o); s=m.get('score'); ft=s.get('ft') if isinstance(s,dict) else None
   valid=isinstance(ft,list) and len(ft)==2 and all(isinstance(v,int) and not isinstance(v,bool) and v>=0 for v in ft)
   rows.append({'identity_key':key,'source_file':rel,'date':x[0],'team1':x[1],'team2':x[2],'score_valid':bool(valid),'home_ft':int(ft[0]) if valid else None,'away_ft':int(ft[1]) if valid else None})
 if decoded!=IDENTITY_N: raise RuntimeError('projection count drift')
 return pd.DataFrame(rows),decoded
def rh(e):
 e=np.asarray(e,float); return float(e.sum()/(e.sum()+len(e)))
def boot(e,seed):
 e=np.asarray(e,int); rng=np.random.default_rng(seed); rr=np.empty(BOOT_REPS); mm=np.empty(BOOT_REPS)
 for i in range(BOOT_REPS):
  x=e[rng.integers(0,len(e),len(e))]; rr[i]=rh(x); mm[i]=x.mean()
 return {'n':len(e),'r_hat':rh(e),'r_ci90_low':float(np.quantile(rr,.05)),'r_ci90_high':float(np.quantile(rr,.95)),'mean_excess':float(e.mean()),'mean_ci90_low':float(np.quantile(mm,.05)),'mean_ci90_high':float(np.quantile(mm,.95)),'reps':BOOT_REPS,'seed':seed}
def ksstat(e):
 e=np.asarray(e,int); grid=np.arange(max(int(e.max(initial=0)),K)+1); emp=np.array([(e<=j).mean() for j in grid]); mod=1-np.power(R,grid+1); return float(np.max(np.abs(emp-mod)))
def ksmc(e):
 e=np.asarray(e,int); obs=ksstat(e); rng=np.random.default_rng(KS_SEED); sims=np.empty(KS_REPS)
 for i in range(KS_REPS): sims[i]=ksstat(rng.geometric(1-R,len(e))-1)
 return {'observed_D':obs,'reps':KS_REPS,'seed':KS_SEED,'simulated_D_95pct':float(np.quantile(sims,.95)),'monte_carlo_pvalue':float((1+(sims>=obs-1e-15).sum())/(KS_REPS+1))}
def wilson(k,n):
 p=k/n; den=1+Z*Z/n; c=(p+Z*Z/(2*n))/den; h=Z*math.sqrt(p*(1-p)/n+Z*Z/(4*n*n))/den; return {'success':int(k),'n':int(n),'observed':float(p),'low90':float(c-h),'high90':float(c+h)}
def fixed_scores(e):
 e=np.asarray(e,int); exact=(1-R)*np.power(R,e); ll=-np.log(np.clip(exact,1e-300,1)); probs=np.array([(1-R)*R**j for j in range(K+1)]+[R**(K+1)],float); y=np.minimum(e,K+1); one=np.zeros((len(e),len(probs))); one[np.arange(len(e)),y]=1; pm=np.repeat(probs[None,:],len(e),axis=0); b=np.square(pm-one).sum(1); cp=np.cumsum(pm,1)[:,:-1]; cy=np.cumsum(one,1)[:,:-1]; rps=np.square(cp-cy).sum(1)/(len(probs)-1); return {'logloss':float(ll.mean()),'brier':float(b.mean()),'rps':float(rps.mean()),'probability_sum_abs_residual':float(abs(probs.sum()-1)),'unenumerated_tail_residual':float(R**(K+1)),'enumeration_K':K}
def group_report(tail,field,minn,seedbase):
 out={}; eligible=inside=0
 for i,(name,g) in enumerate(sorted(tail.groupby(field),key=lambda x:str(x[0]))):
  e=g.excess.to_numpy(int); rec={'n':len(e),'eligible':len(e)>=minn,'r_hat':rh(e),'mean_excess':float(e.mean())}
  if len(e)>=minn:
   eligible+=1; z=boot(e,seedbase+i+1); ok=z['r_ci90_low']<=R<=z['r_ci90_high']; rec.update(z); rec['frozen_r_inside_ci90']=bool(ok); inside+=int(ok)
  out[str(name)]=rec
 return out,eligible,inside,(inside/eligible if eligible else 0)

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--source-root',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args(); root=Path(a.source_root); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 frozen=reproduce(root)  # identity-only pass before any score object is decoded
 s,decoded=scores(root,frozen)
 valid=s[s.score_valid].copy(); valid_frac=len(valid)/IDENTITY_N; valid['exact_total']=valid.home_ft.astype(int)+valid.away_ft.astype(int); tail=valid[valid.exact_total>=7].copy(); tail['excess']=tail.exact_total.astype(int)-7; tail['temporal_block']=np.where(tail.date.astype(str).str[:10]<TEMP_CUTOFF,'early','late')
 block_counts=tail.groupby('temporal_block').size().to_dict(); file_counts=tail.groupby('source_file').size().to_dict(); eligible_file_n=sum(v>=FILE_TAIL_MIN for v in file_counts.values())
 coverage={'valid_score_n':len(valid),'frozen_identity_n':IDENTITY_N,'valid_score_fraction':valid_frac,'pooled_tail_n':len(tail),'early_tail_n':int(block_counts.get('early',0)),'late_tail_n':int(block_counts.get('late',0)),'eligible_competition_files_n_ge_8':int(eligible_file_n),'file_tail_counts':{k:int(v) for k,v in file_counts.items()}}
 covgate={'valid_score_fraction_ge_0_98':valid_frac>=VALID_MIN,'pooled_tail_n_ge_60':len(tail)>=TAIL_MIN,'early_tail_n_ge_20':block_counts.get('early',0)>=BLOCK_MIN,'late_tail_n_ge_20':block_counts.get('late',0)>=BLOCK_MIN,'eligible_competition_files_ge_5':eligible_file_n>=ELIGIBLE_FILES_MIN}
 base={'schema_version':SCHEMA,'formal_weight':0,'frozen_tail_law':{'r':R,'predicted_mean_excess':MEAN_E,'K':K,'parameter_sha256':PARAM_SHA},'external_identity':{'count':IDENTITY_N,'sha256':IDENTITY_SHA},'score_projection_boundary':{'decoded_frozen_match_objects':decoded,'decoded_nonfrozen_match_objects':0,'score_field_used':'score.ft only','missing_replacement_count':0,'external_parameter_refit':False,'external_recalibration':False,'C075C_consumed_tail_labels_reused':False},'coverage':coverage,'coverage_gate':covgate,'protected_boundaries':{'C071_reserve_52180_opened':False,'C070F_confirmation1597_opened':False,'A05_opened':False,'protected_opened':False,'T_ge_7_D_given_T_tested':False,'unified_matrix_generated':False}}
 if not all(covgate.values()):
  base.update({'status':'STOP_COVERAGE_FORWARD_LABELS_CONSUMED','scientific_effect_evaluated':False}); (out/'summary.json').write_text(json.dumps(base,ensure_ascii=False,indent=2)+'\n'); s[['identity_key','source_file','date','score_valid']].to_csv(out/'score_projection_accounting.csv',index=False); print(json.dumps(base,ensure_ascii=False,indent=2)); return 0
 e=tail.excess.to_numpy(int); pooled=boot(e,BOOT_SEED); ks=ksmc(e); thresholds={}
 for n in (1,2,3):
  w=wilson(int((e>=n).sum()),len(e)); w['frozen_probability']=float(R**n); w['frozen_probability_inside_wilson90']=bool(w['low90']<=R**n<=w['high90']); thresholds[f'E_ge_{n}']=w
 blocks,be,bi,bfrac=group_report(tail,'temporal_block',BLOCK_MIN,75100); files,fe,fi,ffrac=group_report(tail,'source_file',FILE_TAIL_MIN,75200); metrics=fixed_scores(e)
 gate={'coverage_pass':True,'pooled_frozen_r_inside_bootstrap90':pooled['r_ci90_low']<=R<=pooled['r_ci90_high'],'pooled_frozen_mean_inside_bootstrap90':pooled['mean_ci90_low']<=MEAN_E<=pooled['mean_ci90_high'],'discrete_ks_p_ge_0_05':ks['monte_carlo_pvalue']>=.05,'E_ge_1_probability_inside_wilson90':thresholds['E_ge_1']['frozen_probability_inside_wilson90'],'E_ge_2_probability_inside_wilson90':thresholds['E_ge_2']['frozen_probability_inside_wilson90'],'early_and_late_both_eligible':be==2,'early_and_late_both_contain_frozen_r':be==2 and bi==2,'eligible_competition_files_ge_5':fe>=ELIGIBLE_FILES_MIN,'competition_containment_fraction_ge_half':ffrac>=.5,'tail_residual_le_1e_8':metrics['unenumerated_tail_residual']<=1e-8,'probability_sum_residual_le_1e_10':metrics['probability_sum_abs_residual']<=1e-10}
 status='CONFIRMATION_PASS' if all(gate.values()) else 'CONFIRMATION_FAIL_PARK'
 tail[['identity_key','source_file','date','temporal_block','exact_total','excess']].to_csv(out/'tail_confirmation_rows.csv',index=False); s[['identity_key','source_file','date','score_valid']].to_csv(out/'score_projection_accounting.csv',index=False)
 summary={**base,'status':status,'scientific_effect_evaluated':True,'estimand':'q(T=7+e|T>=7), fixed unconditional geometric law','pooled_forward':{**pooled,'frozen_r':R,'frozen_mean_excess':MEAN_E,'exact_tail_scores':metrics},'shape_gof':ks,'threshold_calibration':thresholds,'temporal_stability':{'eligible':be,'contains_frozen_r':bi,'containment_fraction':bfrac,'blocks':blocks},'competition_file_stability':{'eligible':fe,'contains_frozen_r':fi,'containment_fraction':ffrac,'blocks':files},'gate':gate,'claim_boundary':'exact-tail research component only; even PASS remains formal_weight=0 and unified score matrix stays closed until T>=7 D|T is separately validated','stopping_rule':'one-shot forward labels consumed; no retuning/reselection permitted'}
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
