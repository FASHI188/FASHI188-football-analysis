#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import audit_c072_statsbomb_shotquality_source as src

SCHEMA_VERSION = "C072B_SHOTQUALITY_PT_DEVELOPMENT_V1"
END_DATE = pd.Timestamp("2016-06-01")
MIN_HISTORY = 8
K = 8
C = 0.1
BOOT_REPS = 5000
BOOT_SEED = 72002
FOLDS = {
    "fold_1": (pd.Timestamp("2015-12-01"), pd.Timestamp("2016-02-01")),
    "fold_2": (pd.Timestamp("2016-02-01"), pd.Timestamp("2016-03-15")),
    "fold_3": (pd.Timestamp("2016-03-15"), pd.Timestamp("2016-06-01")),
}
MIN_TRAIN = 350
MIN_TEST = 250

BASE = [
    "competition_total_mean", "competition_total_sd",
    "home_goals_for_mean", "home_goals_for_sd", "home_goals_against_mean", "home_goals_against_sd",
    "away_goals_for_mean", "away_goals_for_sd", "away_goals_against_mean", "away_goals_against_sd",
    "log1p_home_result_history_n", "log1p_away_result_history_n",
]
VOL_PER_SIDE = [
    "shots_for_per_match_mean", "shots_against_per_match_mean",
    "xg_for_per_match_mean", "xg_for_per_match_sd",
    "xg_against_per_match_mean", "xg_against_per_match_sd",
]
QUALITY_PER_SIDE = [
    "xg_per_shot_for_mean", "xg_per_shot_for_sd", "xg_per_shot_for_q25", "xg_per_shot_for_q50", "xg_per_shot_for_q75",
    "xg_per_shot_against_mean", "xg_per_shot_against_sd", "xg_per_shot_against_q25", "xg_per_shot_against_q50", "xg_per_shot_against_q75",
    "big_chance_for_share", "big_chance_against_share", "low_quality_for_share", "low_quality_against_share",
    "open_play_xg_for_share", "open_play_xg_against_share", "set_piece_xg_for_share", "set_piece_xg_against_share",
]
VOL = [f"{side}_{x}" for side in ("home", "away") for x in VOL_PER_SIDE]
QUALITY = [f"{side}_{x}" for side in ("home", "away") for x in QUALITY_PER_SIDE]
VOLUME_MODEL = BASE + VOL
QUALITY_MODEL = BASE + VOL + QUALITY


def pick(obj, path, default=None):
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def metadata():
    competitions = src.get_json(src.RAW + "data/competitions.json")
    tree = src.get_json(src.API_TREE)
    if tree.get("truncated"):
        raise RuntimeError("source tree truncated")
    event_ids = set()
    match_paths = set()
    for item in tree.get("tree", []):
        p = item.get("path", "")
        if p.startswith("data/events/") and p.endswith(".json"):
            try: event_ids.add(int(Path(p).stem))
            except Exception: pass
        if p.startswith("data/matches/") and p.endswith(".json"):
            match_paths.add(p)
    rows = []
    for c in competitions:
        if c.get("competition_gender") != "male":
            continue
        cid, sid = int(c["competition_id"]), int(c["season_id"])
        path = f"data/matches/{cid}/{sid}.json"
        if path not in match_paths:
            continue
        for m in src.get_json(src.RAW + path):
            mid = int(m["match_id"])
            date = pd.Timestamp(str(m.get("match_date")))
            if date >= END_DATE or mid not in event_ids:
                continue
            rows.append({
                "match_id":mid, "date":date, "competition_id":cid, "season_id":sid,
                "competition_name":c.get("competition_name"), "season_name":c.get("season_name"),
                "home_team_id":int(pick(m,"home_team.home_team_id")), "away_team_id":int(pick(m,"away_team.away_team_id")),
                "home_score":int(m["home_score"]), "away_score":int(m["away_score"]),
            })
    f = pd.DataFrame(rows).sort_values(["date","match_id"]).reset_index(drop=True)
    if f.empty or f.match_id.duplicated().any():
        raise RuntimeError("metadata identity failure")
    return f


def event_aggregate(mid: int):
    events = src.get_json(src.RAW + f"data/events/{mid}.json")
    by_team = defaultdict(lambda: {"xg":[], "open_xg":0.0, "set_xg":0.0, "big":0, "low":0})
    for e in events:
        if pick(e,"type.name") != "Shot":
            continue
        team = pick(e,"team.id")
        xg = pick(e,"shot.statsbomb_xg")
        if team is None or xg is None:
            raise RuntimeError(f"shot xG/team missing match={mid}")
        team = int(team); xg = float(xg); d=by_team[team]; d["xg"].append(xg)
        typ = pick(e,"shot.type.name")
        if typ == "Open Play": d["open_xg"] += xg
        if typ in {"Free Kick","Corner"}: d["set_xg"] += xg
        d["big"] += int(xg >= .30)
        d["low"] += int(xg <= .05)
    return mid, dict(by_team)


def download_aggregates(frame: pd.DataFrame):
    ids = frame.match_id.astype(int).tolist(); out={}; failures=[]
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs={ex.submit(event_aggregate,mid):mid for mid in ids}
        for i,fut in enumerate(as_completed(futs),1):
            mid=futs[fut]
            try:
                got_mid, agg=fut.result(); out[got_mid]=agg
            except Exception as exc:
                failures.append({"match_id":mid,"error":f"{type(exc).__name__}:{exc}"})
            if i%200==0: print(f"event aggregates {i}/{len(ids)}",flush=True)
    if failures:
        raise RuntimeError(f"event aggregate failures={failures[:5]} total={len(failures)}")
    return out


def mean_sd(vals):
    if not vals: return (np.nan,np.nan)
    a=np.asarray(vals,float); return float(a.mean()),float(a.std(ddof=0))

def q(vals,p): return float(np.quantile(np.asarray(vals,float),p)) if vals else np.nan

def safe_share(num,den): return float(num/den) if den>1e-15 else 0.0

def team_profile(h):
    shots_for=h["shots_for_match"]; shots_against=h["shots_against_match"]; xf=h["xg_for_match"]; xa=h["xg_against_match"]
    xf_mu,xf_sd=mean_sd(xf); xa_mu,xa_sd=mean_sd(xa); sf_mu,_=mean_sd(shots_for); sa_mu,_=mean_sd(shots_against)
    allf=h["xg_for_shots"]; alla=h["xg_against_shots"]; fmu,fsd=mean_sd(allf); amu,asd=mean_sd(alla)
    return {
        "result_history_n":len(h["gf"]),
        "goals_for_mean":mean_sd(h["gf"])[0], "goals_for_sd":mean_sd(h["gf"])[1],
        "goals_against_mean":mean_sd(h["ga"])[0], "goals_against_sd":mean_sd(h["ga"])[1],
        "event_history_n":len(shots_for),
        "shots_for_per_match_mean":sf_mu, "shots_against_per_match_mean":sa_mu,
        "xg_for_per_match_mean":xf_mu, "xg_for_per_match_sd":xf_sd, "xg_against_per_match_mean":xa_mu, "xg_against_per_match_sd":xa_sd,
        "xg_per_shot_for_mean":fmu,"xg_per_shot_for_sd":fsd,"xg_per_shot_for_q25":q(allf,.25),"xg_per_shot_for_q50":q(allf,.5),"xg_per_shot_for_q75":q(allf,.75),
        "xg_per_shot_against_mean":amu,"xg_per_shot_against_sd":asd,"xg_per_shot_against_q25":q(alla,.25),"xg_per_shot_against_q50":q(alla,.5),"xg_per_shot_against_q75":q(alla,.75),
        "big_chance_for_share":safe_share(h["big_for"],len(allf)),"big_chance_against_share":safe_share(h["big_against"],len(alla)),
        "low_quality_for_share":safe_share(h["low_for"],len(allf)),"low_quality_against_share":safe_share(h["low_against"],len(alla)),
        "open_play_xg_for_share":safe_share(h["open_for"],sum(allf)),"open_play_xg_against_share":safe_share(h["open_against"],sum(alla)),
        "set_piece_xg_for_share":safe_share(h["set_for"],sum(allf)),"set_piece_xg_against_share":safe_share(h["set_against"],sum(alla)),
    }

def fresh_history():
    return {"gf":[],"ga":[],"shots_for_match":[],"shots_against_match":[],"xg_for_match":[],"xg_against_match":[],"xg_for_shots":[],"xg_against_shots":[],"big_for":0,"big_against":0,"low_for":0,"low_against":0,"open_for":0.0,"open_against":0.0,"set_for":0.0,"set_against":0.0}

def side_agg(agg,team):
    d=agg.get(int(team),{"xg":[],"open_xg":0.0,"set_xg":0.0,"big":0,"low":0}); return d

def update_team(h, own, opp, gf, ga):
    h["gf"].append(float(gf)); h["ga"].append(float(ga)); h["shots_for_match"].append(float(len(own["xg"]))); h["shots_against_match"].append(float(len(opp["xg"]))); h["xg_for_match"].append(float(sum(own["xg"]))); h["xg_against_match"].append(float(sum(opp["xg"]))); h["xg_for_shots"].extend(map(float,own["xg"])); h["xg_against_shots"].extend(map(float,opp["xg"])); h["big_for"]+=int(own["big"]); h["big_against"]+=int(opp["big"]); h["low_for"]+=int(own["low"]); h["low_against"]+=int(opp["low"]); h["open_for"]+=float(own["open_xg"]); h["open_against"]+=float(opp["open_xg"]); h["set_for"]+=float(own["set_xg"]); h["set_against"]+=float(opp["set_xg"])

def build_rows(frame, aggs):
    hist=defaultdict(fresh_history); comp_tot=defaultdict(list); rows=[]
    for date,g in frame.groupby("date",sort=True):
        for _,r in g.iterrows():
            hp=team_profile(hist[int(r.home_team_id)]); ap=team_profile(hist[int(r.away_team_id)]); cm,cs=mean_sd(comp_tot[int(r.competition_id)])
            row={"match_id":int(r.match_id),"date":r.date,"competition_id":int(r.competition_id),"competition_name":r.competition_name,"home_team_id":int(r.home_team_id),"away_team_id":int(r.away_team_id),"competition_total_mean":cm,"competition_total_sd":cs,"target":min(int(r.home_score)+int(r.away_score),7)}
            for side,p in (("home",hp),("away",ap)):
                for k,v in p.items(): row[f"{side}_{k}"]=v
            row["log1p_home_result_history_n"]=math.log1p(hp["result_history_n"]); row["log1p_away_result_history_n"]=math.log1p(ap["result_history_n"])
            row["eligible"]=hp["event_history_n"]>=MIN_HISTORY and ap["event_history_n"]>=MIN_HISTORY
            rows.append(row)
        # Same-date targets all predicted before these updates.
        for _,r in g.iterrows():
            agg=aggs[int(r.match_id)]; h=int(r.home_team_id); a=int(r.away_team_id); hd=side_agg(agg,h); ad=side_agg(agg,a)
            update_team(hist[h],hd,ad,int(r.home_score),int(r.away_score)); update_team(hist[a],ad,hd,int(r.away_score),int(r.home_score)); comp_tot[int(r.competition_id)].append(float(int(r.home_score)+int(r.away_score)))
    return pd.DataFrame(rows).sort_values(["date","match_id"]).reset_index(drop=True)

def pipeline(): return make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(C=C,max_iter=3000,class_weight=None,random_state=0,solver="lbfgs"))
def pred(m,X):
    p=m.predict_proba(X); cls=m.named_steps["logisticregression"].classes_.astype(int); out=np.zeros((len(X),K)); out[:,cls]=p; out=np.clip(out,1e-15,1); return out/out.sum(axis=1,keepdims=True)
def metrics(y,p):
    y=np.asarray(y,int); one=np.eye(K)[y]; cp=np.cumsum(p,axis=1)[:,:-1]; cy=np.cumsum(one,axis=1)[:,:-1]
    def auc(t):
        yy=(y>=t).astype(int); return float(roc_auc_score(yy,p[:,t:].sum(1))) if len(np.unique(yy))==2 else None
    return {"n":int(len(y)),"log_loss":float(log_loss(y,p,labels=list(range(K)))),"rps":float(np.mean(np.sum((cp-cy)**2,axis=1)/(K-1))),"brier":float(np.mean(np.sum((p-one)**2,axis=1))),"auc_t_ge_4":auc(4),"auc_t_ge_5":auc(5)}
def delta(a,b): return {k:(None if a[k] is None or b[k] is None else float(a[k]-b[k])) for k in ["log_loss","rps","brier","auc_t_ge_4","auc_t_ge_5"]}
def bootstrap(y,p0,p1):
    y=np.asarray(y,int); idx=np.arange(len(y)); d=-np.log(np.clip(p1[idx,y],1e-15,1))+np.log(np.clip(p0[idx,y],1e-15,1)); rng=np.random.default_rng(BOOT_SEED); sims=np.empty(BOOT_REPS); n=len(d)
    for i in range(BOOT_REPS): sims[i]=d[rng.integers(0,n,n)].mean()
    return {"matches":n,"mean_delta_log_loss":float(d.mean()),"ci90_low":float(np.quantile(sims,.05)),"ci90_high":float(np.quantile(sims,.95)),"reps":BOOT_REPS,"seed":BOOT_SEED}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out-dir",required=True); a=ap.parse_args(); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    frame=metadata(); print(f"male historical event matches pre-end={len(frame)}",flush=True); aggs=download_aggregates(frame); feat=build_rows(frame,aggs); elig=feat[feat.eligible].copy(); target=elig[(elig.date>=pd.Timestamp("2015-12-01"))&(elig.date<END_DATE)].copy()
    folds={}; ys=[]; pbs=[]; pvs=[]; pqs=[]; qw=vw=0
    for name,(start,end) in FOLDS.items():
        tr=elig[elig.date<start].copy(); te=elig[(elig.date>=start)&(elig.date<end)].copy()
        if len(tr)<MIN_TRAIN or len(te)<MIN_TEST: raise RuntimeError(f"frozen coverage fail {name} train={len(tr)} test={len(te)}")
        yt=tr.target.to_numpy(int); ye=te.target.to_numpy(int); probs={}
        for key,cols in {"baseline":BASE,"xg_volume":BASE+VOL,"shot_quality":QUALITY_MODEL}.items():
            m=pipeline(); m.fit(tr[cols],yt); probs[key]=pred(m,te[cols])
        mb=metrics(ye,probs["baseline"]); mv=metrics(ye,probs["xg_volume"]); mq=metrics(ye,probs["shot_quality"]); qd=delta(mq,mb); vd=delta(mv,mb); qi=delta(mq,mv); qw+=qd["log_loss"]<0; vw+=vd["log_loss"]<0
        folds[name]={"train_rows":len(tr),"test_rows":len(te),"baseline":mb,"xg_volume":mv,"shot_quality":mq,"shot_quality_minus_baseline":qd,"xg_volume_minus_baseline":vd,"shot_quality_minus_xg_volume":qi}
        ys.append(ye); pbs.append(probs["baseline"]); pvs.append(probs["xg_volume"]); pqs.append(probs["shot_quality"])
    y=np.concatenate(ys); pb=np.vstack(pbs); pv=np.vstack(pvs); pqv=np.vstack(pqs); mb=metrics(y,pb); mv=metrics(y,pv); mq=metrics(y,pqv); qd=delta(mq,mb); vd=delta(mv,mb); qi=delta(mq,mv); bq=bootstrap(y,pb,pqv); bi=bootstrap(y,pv,pqv); signal=bool(qd["log_loss"]<0 and bq["ci90_high"]<0 and qw>=2 and qd["rps"]<=0)
    result={"schema_version":SCHEMA_VERSION,"status":"C072B_DEVELOPMENT_COMPLETE","verdict":"C072B_SHOTQUALITY_PT_DEVELOPMENT_SIGNAL" if signal else "C072B_SHOTQUALITY_PT_STABLE_INCREMENT_NOT_ESTABLISHED","source":{"repo":src.REPO,"commit":src.COMMIT,"male_event_matches_pre_2016_06_01":len(frame),"event_aggregates":len(aggs)},"development":{"eligible_threshold8_rows_pre_end":len(elig),"target_window_rows":len(target),"oos_rows":len(y),"folds":folds,"pooled":{"baseline":mb,"xg_volume":mv,"shot_quality":mq,"shot_quality_minus_baseline":qd,"xg_volume_minus_baseline":vd,"shot_quality_minus_xg_volume":qi,"shot_quality_fold_ll_wins":int(qw),"xg_volume_fold_ll_wins":int(vw),"primary_bootstrap":bq,"quality_increment_bootstrap":bi},"development_signal_gate":signal},"feature_contract":{"baseline":BASE,"xg_volume":BASE+VOL,"shot_quality":QUALITY_MODEL,"shot_outcome_used":False,"same_date_update":False},"boundary":{"retrospective_only":True,"fresh_confirmation_claim_allowed":False,"formal_weight":0,"C071_confirmation72180_opened":False,"C070F_confirmation1597_opened":False,"A05_opened":False,"protected_opened":False}}
    (out/"summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n"); print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
