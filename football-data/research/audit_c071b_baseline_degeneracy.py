#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
import audit_c071_opportunity_source as audit
import evaluate_c071b_opportunity_pt_v2 as e

def desc(frame,cols):
 out={}
 for c in cols:
  x=pd.to_numeric(frame[c],errors='coerce')
  out[c]={"nonnull":int(x.notna().sum()),"missing":int(x.isna().sum()),"nunique":int(x.nunique(dropna=True)),"mean":float(x.mean()) if x.notna().any() else None,"sd":float(x.std(ddof=0)) if x.notna().any() else None,"min":float(x.min()) if x.notna().any() else None,"max":float(x.max()) if x.notna().any() else None}
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--fixtures',required=True); ap.add_argument('--stats',required=True); ap.add_argument('--out',required=True); a=ap.parse_args(); fp=Path(a.fixtures); sp=Path(a.stats)
 if e.sha256(fp)!=e.FIX_SHA or e.sha256(sp)!=e.STAT_SHA: raise RuntimeError('sha drift')
 fixtures=pd.read_parquet(fp,columns=audit.FIXTURE_COLS); fixtures.date_utc=e.utc(fixtures.date_utc); fixtures=fixtures.dropna(subset=audit.FIXTURE_COLS).sort_values(['date_utc','id']).reset_index(drop=True)
 stats=pd.read_parquet(sp,columns=audit.STAT_COLS); stats.known_at=e.utc(stats.known_at); stats=stats.dropna(subset=['fixture_id','known_at'])
 eligible,comp=e.eligible_identities(fixtures,stats); labels=e.read_dev_labels(fp); ident_pre=fixtures[fixtures.date_utc<e.DEV_CUTOFF].copy(); rt,rl,_=e.result_events(ident_pre,labels); ot,_=e.opportunity_events(comp)
 dev=eligible[eligible.date_utc<e.DEV_CUTOFF].merge(labels[['id','goals_home','goals_away']],on='id',how='left',validate='one_to_one').dropna(subset=['goals_home','goals_away']).sort_values(['date_utc','id']).reset_index(drop=True); dev['target']=np.minimum(dev.goals_home.astype(int)+dev.goals_away.astype(int),7)
 feat=e.build_features(dev,rt,rl,ot); feat['target']=dev.target.to_numpy(int)
 folds={}
 for name,(start,end) in e.FOLDS.items():
  tr=feat[feat.date_utc<start]; te=feat[(feat.date_utc>=start)&(feat.date_utc<end)]; m=e.model(); m.fit(tr[e.BASE],tr.target.to_numpy(int)); p=e.predict(m,te[e.BASE]); s4=p[:,4:].sum(1); s5=p[:,5:].sum(1); lr=m.named_steps['logisticregression']; coef=lr.coef_
  folds[name]={"train_rows":len(tr),"test_rows":len(te),"baseline_train_features":desc(tr,e.BASE),"baseline_test_features":desc(te,e.BASE),"coef_l2":float(np.linalg.norm(coef)),"coef_max_abs":float(np.max(np.abs(coef))),"p_ge4":{"sd":float(np.std(s4)),"min":float(np.min(s4)),"max":float(np.max(s4)),"unique_round12":int(len(np.unique(np.round(s4,12))))},"p_ge5":{"sd":float(np.std(s5)),"min":float(np.min(s5)),"max":float(np.max(s5)),"unique_round12":int(len(np.unique(np.round(s5,12))))},"auc4":e.metr(te.target.to_numpy(int),p)['auc_t_ge_4'],"auc5":e.metr(te.target.to_numpy(int),p)['auc_t_ge_5']}
 result={"status":"C071B_BASELINE_TECH_AUDIT_COMPLETE","postview_technical_only":True,"confirmation_goal_rows_read":0,"confirmation_scored":False,"overall_baseline_features":desc(feat,e.BASE),"folds":folds,"formal_weight":0}
 Path(a.out).write_text(json.dumps(result,indent=2)+"\n"); print(json.dumps(result,indent=2))
if __name__=='__main__': main()
