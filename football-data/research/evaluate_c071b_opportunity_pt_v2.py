#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import audit_c071_opportunity_source as audit

SCHEMA_VERSION="C071B_OPPORTUNITY_PT_DEVELOPMENT_V1"
FIX_SHA="7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
STAT_SHA="2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
DEV_CUTOFF=pd.Timestamp("2024-01-01T00:00:00Z")
CONFIRM_END=pd.Timestamp("2026-07-01T00:00:00Z")
EXPECTED_CONFIRM=72180
RESULT_DELAY=pd.Timedelta(minutes=105)
K=8; C=0.1; BOOT_REPS=2000; BOOT_SEED=71102
FOLDS={
 "fold_1":(pd.Timestamp("2022-01-01T00:00:00Z"),pd.Timestamp("2022-07-01T00:00:00Z")),
 "fold_2":(pd.Timestamp("2022-07-01T00:00:00Z"),pd.Timestamp("2023-01-01T00:00:00Z")),
 "fold_3":(pd.Timestamp("2023-01-01T00:00:00Z"),pd.Timestamp("2023-07-01T00:00:00Z")),
 "fold_4":(pd.Timestamp("2023-07-01T00:00:00Z"),pd.Timestamp("2024-01-01T00:00:00Z")),
}
RESULT_METRICS=["goals_for","goals_against"]
OPP_METRICS=["shots_total_for","shots_total_against","shots_on_goal_for","shots_on_goal_against","shots_inside_box_for","shots_inside_box_against","shots_outside_share_for","shots_outside_share_against","penalties_for","penalties_against"]
OPP_SD=[m for m in OPP_METRICS if not m.startswith("penalties_")]
BASE=["league_total_mean","league_total_sd","home_goals_for_mean","home_goals_for_sd","home_goals_against_mean","home_goals_against_sd","away_goals_for_mean","away_goals_for_sd","away_goals_against_mean","away_goals_against_sd","log1p_home_result_history_n","log1p_away_result_history_n"]
MEAN=[f"{s}_{m}_mean" for s in ("home","away") for m in OPP_METRICS]
SD=[f"{s}_{m}_sd" for s in ("home","away") for m in OPP_SD]
MEAN_MODEL=BASE+MEAN
DIST_MODEL=BASE+MEAN+SD

def sha256(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(8*1024*1024),b""): h.update(b)
 return h.hexdigest()

def utc(x): return pd.to_datetime(x,utc=True,errors="coerce")

def read_dev_labels(path):
 d=ds.dataset(path,format="parquet")
 t=d.to_table(columns=["id","date_utc","goals_home","goals_away"],filter=ds.field("date_utc")<datetime(2024,1,1))
 x=t.to_pandas(); x["date_utc"]=utc(x.date_utc)
 if len(x) and not (x.date_utc<DEV_CUTOFF).all(): raise RuntimeError("goal-label horizon breach")
 return x

def eligible_identities(fixtures,stats):
 s=stats.copy(); s["core_complete"]=s[audit.CORE].notna().all(axis=1)
 comp=s[s.core_complete].merge(fixtures[["id","home_team_id","away_team_id"]],left_on="fixture_id",right_on="id",how="inner",validate="one_to_one")
 hist=pd.concat([pd.DataFrame({"team_id":comp.home_team_id.astype(int),"known_at":comp.known_at}),pd.DataFrame({"team_id":comp.away_team_id.astype(int),"known_at":comp.known_at})],ignore_index=True)
 hn,an,mask=audit.prior_counts(fixtures,hist,8)
 out=fixtures.loc[mask,audit.FIXTURE_COLS].copy(); out["home_prior_complete_stats"]=hn[mask]; out["away_prior_complete_stats"]=an[mask]
 return out.sort_values(["date_utc","id"]).reset_index(drop=True),comp

def result_events(identity,labels):
 x=identity.merge(labels[["id","goals_home","goals_away"]],on="id",how="inner",validate="one_to_one").dropna(subset=["goals_home","goals_away"]).copy()
 x["goals_home"]=x.goals_home.astype(float); x["goals_away"]=x.goals_away.astype(float); x["available_at"]=x.date_utc+RESULT_DELAY
 h=pd.DataFrame({"team_id":x.home_team_id.astype(int),"available_at":x.available_at,"goals_for":x.goals_home,"goals_against":x.goals_away})
 a=pd.DataFrame({"team_id":x.away_team_id.astype(int),"available_at":x.available_at,"goals_for":x.goals_away,"goals_against":x.goals_home})
 l=pd.DataFrame({"league_id":x.league_id.astype(int),"available_at":x.available_at,"total_goals":x.goals_home+x.goals_away})
 return pd.concat([h,a],ignore_index=True),l,len(x)

def share(num,den):
 n=num.to_numpy(float); d=den.to_numpy(float); out=np.zeros(len(n)); np.divide(n,d,out=out,where=d>0); return out

def opportunity_events(comp):
 x=comp[comp.known_at<DEV_CUTOFF].copy()
 h=pd.DataFrame({"team_id":x.home_team_id.astype(int),"available_at":x.known_at,
  "shots_total_for":x.home_shots_total.astype(float),"shots_total_against":x.away_shots_total.astype(float),
  "shots_on_goal_for":x.home_shots_on_goal.astype(float),"shots_on_goal_against":x.away_shots_on_goal.astype(float),
  "shots_inside_box_for":x.home_shots_inside_box.astype(float),"shots_inside_box_against":x.away_shots_inside_box.astype(float),
  "shots_outside_share_for":share(x.home_shots_outside_box,x.home_shots_total),"shots_outside_share_against":share(x.away_shots_outside_box,x.away_shots_total),
  "penalties_for":x.home_penalties.astype(float),"penalties_against":x.away_penalties.astype(float)})
 a=pd.DataFrame({"team_id":x.away_team_id.astype(int),"available_at":x.known_at,
  "shots_total_for":x.away_shots_total.astype(float),"shots_total_against":x.home_shots_total.astype(float),
  "shots_on_goal_for":x.away_shots_on_goal.astype(float),"shots_on_goal_against":x.home_shots_on_goal.astype(float),
  "shots_inside_box_for":x.away_shots_inside_box.astype(float),"shots_inside_box_against":x.home_shots_inside_box.astype(float),
  "shots_outside_share_for":share(x.away_shots_outside_box,x.away_shots_total),"shots_outside_share_against":share(x.home_shots_outside_box,x.home_shots_total),
  "penalties_for":x.away_penalties.astype(float),"penalties_against":x.home_penalties.astype(float)})
 return pd.concat([h,a],ignore_index=True),len(x)

def cache(events,key,time,metrics):
 out={}
 for k,g in events.sort_values([key,time]).groupby(key,sort=False):
  v=g[metrics].to_numpy(float); t=g[time].astype("int64").to_numpy(); out[int(k)]=(t,np.cumsum(v,axis=0),np.cumsum(v*v,axis=0))
 return out

def snapshot(target,key,c,metrics,prefix,count_name):
 n=len(target); means={m:np.full(n,np.nan) for m in metrics}; sds={m:np.full(n,np.nan) for m in metrics}; cnt=np.zeros(n,dtype=np.int32); tns=target.date_utc.astype("int64").to_numpy(); keys=target[key].astype("Int64")
 for k,idx in keys.groupby(keys).groups.items():
  if pd.isna(k) or int(k) not in c: continue
  rows=np.asarray(list(idx),dtype=int); tt,cs,cq=c[int(k)]; p=np.searchsorted(tt,tns[rows],side="left")-1; good=p>=0
  if not good.any(): continue
  rr=rows[good]; pp=p[good]; nn=(pp+1).astype(float); cnt[rr]=pp+1; ss=cs[pp]; qq=cq[pp]; mu=ss/nn[:,None]; sd=np.sqrt(np.maximum(qq/nn[:,None]-mu*mu,0.0))
  for j,m in enumerate(metrics): means[m][rr]=mu[:,j]; sds[m][rr]=sd[:,j]
 z=pd.DataFrame(index=target.index)
 for m in metrics: z[f"{prefix}_{m}_mean"]=means[m]; z[f"{prefix}_{m}_sd"]=sds[m]
 z[count_name]=cnt; return z

def build_features(target,rteam,rleague,oteam):
 rc=cache(rteam,"team_id","available_at",RESULT_METRICS); oc=cache(oteam,"team_id","available_at",OPP_METRICS); lc=cache(rleague,"league_id","available_at",["total_goals"])
 hr=snapshot(target,"home_team_id",rc,RESULT_METRICS,"home","home_result_history_n"); ar=snapshot(target,"away_team_id",rc,RESULT_METRICS,"away","away_result_history_n")
 ho=snapshot(target,"home_team_id",oc,OPP_METRICS,"home","home_opportunity_history_n"); ao=snapshot(target,"away_team_id",oc,OPP_METRICS,"away","away_opportunity_history_n")
 le=snapshot(target,"league_id",lc,["total_goals"],"league","league_result_history_n")
 le=le.rename(columns={"league_total_goals_mean":"league_total_mean","league_total_goals_sd":"league_total_sd"})
 out=pd.concat([target.reset_index(drop=True),le.reset_index(drop=True),hr.reset_index(drop=True),ar.reset_index(drop=True),ho.reset_index(drop=True),ao.reset_index(drop=True)],axis=1)
 out["log1p_home_result_history_n"]=np.log1p(out.home_result_history_n.astype(float)); out["log1p_away_result_history_n"]=np.log1p(out.away_result_history_n.astype(float))
 return out

def model(): return make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(C=C,max_iter=2000,class_weight=None,random_state=0,solver="lbfgs"))
def predict(m,X):
 p=m.predict_proba(X); cls=m.named_steps["logisticregression"].classes_.astype(int); out=np.zeros((len(X),K)); out[:,cls]=p; out=np.clip(out,1e-15,1); return out/out.sum(axis=1,keepdims=True)
def metr(y,p):
 y=np.asarray(y,int); one=np.eye(K)[y]; cp=np.cumsum(p,axis=1)[:,:-1]; cy=np.cumsum(one,axis=1)[:,:-1]
 def auc(t):
  yy=(y>=t).astype(int); return float(roc_auc_score(yy,p[:,t:].sum(axis=1))) if len(np.unique(yy))==2 else None
 return {"n":len(y),"log_loss":float(log_loss(y,p,labels=list(range(K)))),"rps":float(np.mean(np.sum((cp-cy)**2,axis=1)/(K-1))),"brier":float(np.mean(np.sum((p-one)**2,axis=1))),"auc_t_ge_4":auc(4),"auc_t_ge_5":auc(5)}
def delt(a,b): return {k:(None if a[k] is None or b[k] is None else float(a[k]-b[k])) for k in ["log_loss","rps","brier","auc_t_ge_4","auc_t_ge_5"]}
def boot(y,p0,p1):
 y=np.asarray(y,int); ii=np.arange(len(y)); d=-np.log(np.clip(p1[ii,y],1e-15,1))+np.log(np.clip(p0[ii,y],1e-15,1)); rng=np.random.default_rng(BOOT_SEED); sims=np.empty(BOOT_REPS); n=len(d)
 for i in range(BOOT_REPS): sims[i]=d[rng.integers(0,n,n)].mean()
 return {"matches":n,"mean_delta_log_loss":float(d.mean()),"ci90_low":float(np.quantile(sims,.05)),"ci90_high":float(np.quantile(sims,.95)),"reps":BOOT_REPS,"seed":BOOT_SEED}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--fixtures",required=True); ap.add_argument("--stats",required=True); ap.add_argument("--out-dir",required=True); a=ap.parse_args(); fp=Path(a.fixtures); sp=Path(a.stats); od=Path(a.out_dir); od.mkdir(parents=True,exist_ok=True)
 if sha256(fp)!=FIX_SHA or sha256(sp)!=STAT_SHA: raise RuntimeError("source SHA mismatch")
 fixtures=pd.read_parquet(fp,columns=audit.FIXTURE_COLS); fixtures.date_utc=utc(fixtures.date_utc); fixtures=fixtures.dropna(subset=audit.FIXTURE_COLS).sort_values(["date_utc","id"]).reset_index(drop=True)
 stats=pd.read_parquet(sp,columns=audit.STAT_COLS); stats.known_at=utc(stats.known_at); stats=stats.dropna(subset=["fixture_id","known_at"])
 eligible,comp=eligible_identities(fixtures,stats); conf=eligible[(eligible.date_utc>=DEV_CUTOFF)&(eligible.date_utc<CONFIRM_END)].copy()
 if len(conf)!=EXPECTED_CONFIRM: raise RuntimeError(f"confirmation identity drift {len(conf)}")
 conf.to_csv(od/"sealed_confirmation_identity.csv",index=False)
 labels=read_dev_labels(fp); ident_pre=fixtures[fixtures.date_utc<DEV_CUTOFF].copy(); rteam,rleague,nres=result_events(ident_pre,labels); oteam,nopp=opportunity_events(comp)
 dev=eligible[eligible.date_utc<DEV_CUTOFF].merge(labels[["id","goals_home","goals_away"]],on="id",how="left",validate="one_to_one").dropna(subset=["goals_home","goals_away"]).sort_values(["date_utc","id"]).reset_index(drop=True); dev["target"]=np.minimum(dev.goals_home.astype(int)+dev.goals_away.astype(int),7)
 feat=build_features(dev,rteam,rleague,oteam); feat["target"]=dev.target.to_numpy(int)
 # Hard PIT identity sanity from the independently frozen threshold-8 gate.
 if int(feat.home_opportunity_history_n.min())<8 or int(feat.away_opportunity_history_n.min())<8: raise RuntimeError("threshold8 PIT feature history drift")
 folds={}; ys=[]; pbs=[]; pms=[]; pds=[]; dw=mw=0
 for name,(start,end) in FOLDS.items():
  tr=feat[feat.date_utc<start]; te=feat[(feat.date_utc>=start)&(feat.date_utc<end)];
  if len(tr)<1000 or len(te)<1000: raise RuntimeError(f"insufficient {name} {len(tr)}/{len(te)}")
  yt=tr.target.to_numpy(int); ye=te.target.to_numpy(int); probs={}
  for key,cols in {"baseline":BASE,"mean_candidate":MEAN_MODEL,"distribution_candidate":DIST_MODEL}.items():
   m=model(); m.fit(tr[cols],yt); probs[key]=predict(m,te[cols])
  mb=metr(ye,probs["baseline"]); mm=metr(ye,probs["mean_candidate"]); md=metr(ye,probs["distribution_candidate"]); db=delt(md,mb); dm=delt(mm,mb); dd=delt(md,mm); dw+=db["log_loss"]<0; mw+=dm["log_loss"]<0
  folds[name]={"train_rows":len(tr),"test_rows":len(te),"test_start":str(start),"test_end_exclusive":str(end),"baseline":mb,"mean_candidate":mm,"distribution_candidate":md,"distribution_minus_baseline":db,"mean_minus_baseline":dm,"distribution_minus_mean":dd}
  ys.append(ye); pbs.append(probs["baseline"]); pms.append(probs["mean_candidate"]); pds.append(probs["distribution_candidate"])
 y=np.concatenate(ys); pb=np.vstack(pbs); pm=np.vstack(pms); pdist=np.vstack(pds); mb=metr(y,pb); mm=metr(y,pm); md=metr(y,pdist); primary=delt(md,mb); mean_delta=delt(mm,mb); disp=delt(md,mm); bp=boot(y,pb,pdist); bd=boot(y,pm,pdist); signal=bool(primary["log_loss"]<0 and bp["ci90_high"]<0 and dw>=3 and primary["rps"]<=0)
 result={"schema_version":SCHEMA_VERSION,"status":"C071B_DEVELOPMENT_COMPLETE","verdict":"C071B_OPPORTUNITY_PT_DEVELOPMENT_SIGNAL" if signal else "C071B_OPPORTUNITY_PT_STABLE_INCREMENT_NOT_ESTABLISHED","source":{"fixtures_sha256":FIX_SHA,"match_stats_sha256":STAT_SHA,"fixture_identity_rows":len(fixtures),"eligible_threshold8_identity_rows":len(eligible)},"label_boundary":{"development_goal_projection_rows_returned":len(labels),"development_goal_projection_max_date":str(labels.date_utc.max()),"labels_at_or_after_2024_returned":int((labels.date_utc>=DEV_CUTOFF).sum()),"confirmation_identity_rows":len(conf),"confirmation_target_goal_rows_read":0,"confirmation_scored":False},"history":{"historical_played_result_matches_pre2024":nres,"core_complete_opportunity_matches_used_pre2024":nopp,"strict_result_delay_minutes":105,"strict_stats_rule":"known_at < target kickoff"},"development":{"eligible_labeled_rows_pre2024":len(feat),"oos_rows":len(y),"folds":folds,"pooled":{"baseline":mb,"mean_candidate":mm,"distribution_candidate":md,"distribution_minus_baseline":primary,"mean_minus_baseline":mean_delta,"distribution_minus_mean":disp,"distribution_fold_logloss_wins":int(dw),"mean_fold_logloss_wins":int(mw),"primary_match_bootstrap":bp,"dispersion_increment_match_bootstrap":bd},"development_signal_gate":signal},"feature_contract":{"baseline_features":BASE,"mean_candidate_features":MEAN_MODEL,"distribution_candidate_features":DIST_MODEL,"provider_xg_used":False,"closing_1x2_used":False},"boundary":{"postview_development_only":True,"fresh_confirmation_claim_allowed":False,"formal_weight":0,"C070F_confirmation1597_opened":False,"A05_opened":False,"protected_opened":False}}
 (od/"summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n"); print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
