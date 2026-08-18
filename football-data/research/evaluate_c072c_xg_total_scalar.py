#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
import evaluate_c072b_shotquality_pt as b

SCHEMA_VERSION="C072C_XG_TOTAL_SCALAR_V1"
REPS=5000; SEED=72003
SCALAR="xg_total_intensity"
CAND=b.BASE+[SCALAR]

def boot(y,p0,p1):
 y=np.asarray(y,int); idx=np.arange(len(y)); d=-np.log(np.clip(p1[idx,y],1e-15,1))+np.log(np.clip(p0[idx,y],1e-15,1)); rng=np.random.default_rng(SEED); sims=np.empty(REPS); n=len(d)
 for i in range(REPS): sims[i]=d[rng.integers(0,n,n)].mean()
 return {"matches":n,"mean_delta_log_loss":float(d.mean()),"ci90_low":float(np.quantile(sims,.05)),"ci90_high":float(np.quantile(sims,.95)),"reps":REPS,"seed":SEED}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); a=ap.parse_args(); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 frame=b.metadata(); aggs=b.download_aggregates(frame); feat=b.build_rows(frame,aggs); elig=feat[feat.eligible].copy()
 elig[SCALAR]=0.5*(elig.home_xg_for_per_match_mean+elig.away_xg_against_per_match_mean+elig.away_xg_for_per_match_mean+elig.home_xg_against_per_match_mean)
 folds={}; ys=[]; pbs=[]; pcs=[]; wins=0
 for name,(start,end) in b.FOLDS.items():
  tr=elig[elig.date<start].copy(); te=elig[(elig.date>=start)&(elig.date<end)].copy()
  if len(tr)<b.MIN_TRAIN or len(te)<b.MIN_TEST: raise RuntimeError(f'coverage drift {name} {len(tr)}/{len(te)}')
  yt=tr.target.to_numpy(int); ye=te.target.to_numpy(int)
  mb=b.pipeline(); mc=b.pipeline(); mb.fit(tr[b.BASE],yt); mc.fit(tr[CAND],yt); pb=b.pred(mb,te[b.BASE]); pc=b.pred(mc,te[CAND]); xb=b.metrics(ye,pb); xc=b.metrics(ye,pc); d=b.delta(xc,xb); wins+=d['log_loss']<0
  folds[name]={"train_rows":len(tr),"test_rows":len(te),"baseline":xb,"candidate":xc,"candidate_minus_baseline":d,"scalar_train":{"mean":float(tr[SCALAR].mean()),"sd":float(tr[SCALAR].std(ddof=0)),"min":float(tr[SCALAR].min()),"max":float(tr[SCALAR].max())},"scalar_test":{"mean":float(te[SCALAR].mean()),"sd":float(te[SCALAR].std(ddof=0)),"min":float(te[SCALAR].min()),"max":float(te[SCALAR].max())}}
  ys.append(ye); pbs.append(pb); pcs.append(pc)
 y=np.concatenate(ys); pb=np.vstack(pbs); pc=np.vstack(pcs); xb=b.metrics(y,pb); xc=b.metrics(y,pc); d=b.delta(xc,xb); bt=boot(y,pb,pc); signal=bool(d['log_loss']<0 and bt['ci90_high']<0 and wins>=2 and d['rps']<=0)
 result={"schema_version":SCHEMA_VERSION,"status":"C072C_POSTVIEW_REFINEMENT_COMPLETE","verdict":"C072C_XG_TOTAL_SCALAR_DEVELOPMENT_SIGNAL" if signal else "C072C_XG_TOTAL_SCALAR_INCREMENT_NOT_ESTABLISHED","source":{"repo":b.src.REPO,"commit":b.src.COMMIT,"male_event_matches":len(frame)},"scalar":{"formula":"0.5*(home_xg_for_pm + away_xg_against_pm + away_xg_for_pm + home_xg_against_pm)","search_performed":False},"development":{"eligible_rows":len(elig),"folds":folds,"pooled":{"baseline":xb,"candidate":xc,"candidate_minus_baseline":d,"fold_logloss_wins":int(wins),"match_bootstrap":bt},"signal_gate":signal},"stopping_rule":{"if_fail":"park current StatsBomb-xG representation; no alternate weights/transforms/windows/subsets on same labels"},"boundary":{"postview_only":True,"fresh_confirmation_claim_allowed":False,"formal_weight":0,"C071_confirmation72180_opened":False,"C070F_confirmation1597_opened":False,"A05_opened":False,"protected_opened":False}}
 (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
