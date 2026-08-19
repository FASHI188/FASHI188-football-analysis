#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import math
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SCHEMA = "C072H2_DGIVENT_DEVELOPMENT_V1"
REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
RAW = f"https://raw.githubusercontent.com/nm2890/football-data/{REV}/"
FILES = [
    "data/belgium/jupiler-pro-league.csv",
    "data/egypt/premier-league.csv",
    "data/england/premier-league.csv",
    "data/france/ligue-1.csv",
    "data/germany/bundesliga.csv",
    "data/italy/serie-a.csv",
    "data/netherlands/eredivisie.csv",
    "data/spain/laliga.csv",
]
COLS = [
    "Date","Season","HomeTeam","AwayTeam","FTHG","FTAG",
    "home_odds_open","draw_odds_open","away_odds_open",
    "home_odds_close","draw_odds_close","away_odds_close",
]
BASE = [
    "competition_total_mean", "competition_total_sd",
    "home_goals_for_mean", "home_goals_for_sd", "home_goals_against_mean", "home_goals_against_sd",
    "away_goals_for_mean", "away_goals_for_sd", "away_goals_against_mean", "away_goals_against_sd",
    "log1p_home_result_history_n", "log1p_away_result_history_n",
]
OPEN = BASE + ["open_strength","open_drawness"]
MOVE = OPEN + ["move_strength","move_drawness"]
TEST_SEASONS = [2019,2020,2021,2022,2023]
T_VALUES = [1,2,3,4,5,6]
MIN_HISTORY = 8
MIN_TRAIN_ALL = 15000
MIN_TEST_ALL = 1800
MIN_TRAIN_PER_T = 200
MIN_POOLED = 10000
C_FIXED = 0.1
ALPHA = 1.0
BOOT_REPS = 3000
BOOT_SEED = 72024

# nm2890 CSV headers used by the public repository.
SRC = {
    "Date":"Date", "Season":"Season", "HomeTeam":"HomeTeam", "AwayTeam":"AwayTeam",
    "FTHG":"FTHG", "FTAG":"FTAG",
    "home_odds_open":"1x2_home_open", "draw_odds_open":"1x2_draw_open", "away_odds_open":"1x2_away_open",
    "home_odds_close":"1x2_home_close", "draw_odds_close":"1x2_draw_close", "away_odds_close":"1x2_away_close",
}


def season_start(x):
    s=str(x).replace("-","/")
    for tok in s.split("/"):
        tok=tok.strip()
        if len(tok)==4 and tok.isdigit(): return int(tok)
    digits="".join(ch if ch.isdigit() else " " for ch in s).split()
    for d in digits:
        if len(d)>=4: return int(d[:4])
    return None


def load_source():
    frames=[]
    for path in FILES:
        raw=urlopen(RAW+path, timeout=90).read()
        hdr=pd.read_csv(io.BytesIO(raw), nrows=0).columns.tolist()
        missing=[v for v in SRC.values() if v not in hdr]
        if missing: raise RuntimeError(f"{path} missing columns {missing}")
        df=pd.read_csv(io.BytesIO(raw), usecols=list(SRC.values())).rename(columns={v:k for k,v in SRC.items()})
        df["source_file"]=path
        df["season_start"]=df.Season.map(season_start)
        df["date"]=pd.to_datetime(df.Date, errors="coerce", utc=True)
        df=df[df.season_start.notna() & df.date.notna()].copy()
        df["season_start"]=df.season_start.astype(int)
        # H2 development is strictly through 2023/24; 2024/25 is excluded entirely.
        df=df[df.season_start<=2023].copy()
        df["FTHG"]=pd.to_numeric(df.FTHG, errors="coerce"); df["FTAG"]=pd.to_numeric(df.FTAG, errors="coerce")
        for c in ["home_odds_open","draw_odds_open","away_odds_open","home_odds_close","draw_odds_close","away_odds_close"]:
            df[c]=pd.to_numeric(df[c], errors="coerce")
        df=df.dropna(subset=["FTHG","FTAG"]).copy(); df["FTHG"]=df.FTHG.astype(int); df["FTAG"]=df.FTAG.astype(int)
        frames.append(df)
    x=pd.concat(frames, ignore_index=True)
    return x.sort_values(["date","source_file","HomeTeam","AwayTeam"]).reset_index(drop=True)


def mean_sd(vals):
    if not vals: return np.nan,np.nan
    a=np.asarray(vals,float); return float(a.mean()),float(a.std(ddof=0))


def hist_factory(): return {"gf":[],"ga":[]}


def profile(h):
    gfm,gfs=mean_sd(h["gf"]); gam,gas=mean_sd(h["ga"])
    return {"n":len(h["gf"]),"gf_mean":gfm,"gf_sd":gfs,"ga_mean":gam,"ga_sd":gas}


def devig3(h,d,a):
    if not (np.isfinite(h) and np.isfinite(d) and np.isfinite(a) and h>1 and d>1 and a>1):
        return None
    v=np.asarray([1/h,1/d,1/a],float); return v/v.sum()


def coords(p):
    if p is None: return np.nan,np.nan
    p=np.clip(np.asarray(p,float),1e-12,1.0)
    strength=float(np.log(p[0]/p[2]))
    drawness=float(np.log(p[1]/math.sqrt(p[0]*p[2])))
    return strength,drawness


def build_rows(frame):
    th=defaultdict(hist_factory); ct=defaultdict(list); rows=[]
    for date,g in frame.groupby("date",sort=True):
        for _,r in g.iterrows():
            comp=str(r.source_file); hk=(comp,str(r.HomeTeam)); ak=(comp,str(r.AwayTeam))
            hp=profile(th[hk]); ap=profile(th[ak]); cm,cs=mean_sd(ct[comp])
            po=devig3(float(r.home_odds_open),float(r.draw_odds_open),float(r.away_odds_open))
            pc=devig3(float(r.home_odds_close),float(r.draw_odds_close),float(r.away_odds_close))
            os,od=coords(po); cs2,cd2=coords(pc)
            T=int(r.FTHG)+int(r.FTAG); H=int(r.FTHG)
            rows.append({
                "date":r.date,"season_start":int(r.season_start),"source_file":comp,
                "competition_total_mean":cm,"competition_total_sd":cs,
                "home_goals_for_mean":hp["gf_mean"],"home_goals_for_sd":hp["gf_sd"],
                "home_goals_against_mean":hp["ga_mean"],"home_goals_against_sd":hp["ga_sd"],
                "away_goals_for_mean":ap["gf_mean"],"away_goals_for_sd":ap["gf_sd"],
                "away_goals_against_mean":ap["ga_mean"],"away_goals_against_sd":ap["ga_sd"],
                "log1p_home_result_history_n":math.log1p(hp["n"]),"log1p_away_result_history_n":math.log1p(ap["n"]),
                "open_strength":os,"open_drawness":od,
                "move_strength":cs2-os if np.isfinite(cs2) and np.isfinite(os) else np.nan,
                "move_drawness":cd2-od if np.isfinite(cd2) and np.isfinite(od) else np.nan,
                "eligible":hp["n"]>=MIN_HISTORY and ap["n"]>=MIN_HISTORY,
                "T":T,"H":H,
            })
        # same-date predict-before-update
        for _,r in g.iterrows():
            comp=str(r.source_file); hk=(comp,str(r.HomeTeam)); ak=(comp,str(r.AwayTeam)); hg=int(r.FTHG); ag=int(r.FTAG)
            th[hk]["gf"].append(float(hg)); th[hk]["ga"].append(float(ag))
            th[ak]["gf"].append(float(ag)); th[ak]["ga"].append(float(hg)); ct[comp].append(float(hg+ag))
    return pd.DataFrame(rows)


def pipe():
    return make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(C=C_FIXED,solver="lbfgs",max_iter=3000,class_weight=None,random_state=0))


def baseline_tables(train):
    out={}
    for T in T_VALUES:
        counts=np.zeros(T+1,float)
        for h in train.loc[train.T==T,"H"].astype(int): counts[h]+=1
        vals=counts+ALPHA; out[T]=vals/vals.sum()
    return out


def support_predict(model,X,T):
    raw=model.predict_proba(X); cls=model.named_steps["logisticregression"].classes_.astype(int)
    p=np.zeros((len(X),T+1),float); p[:,cls]=raw; p=np.clip(p,1e-15,1); return p/p.sum(1,keepdims=True)


def score(y,p):
    y=np.asarray(y,int); n=len(y); idx=np.arange(n); one=np.zeros_like(p); one[idx,y]=1.0
    ll=float(np.mean(-np.log(np.clip(p[idx,y],1e-15,1))))
    br=float(np.mean(np.sum((p-one)**2,axis=1)))
    if p.shape[1]>1:
        rps=float(np.mean(np.sum((np.cumsum(p,axis=1)[:,:-1]-np.cumsum(one,axis=1)[:,:-1])**2,axis=1)/(p.shape[1]-1)))
    else: rps=0.0
    rank=np.argsort(-p,axis=1)
    top1=float(np.mean(rank[:,0]==y)); k=min(3,p.shape[1]); top3=float(np.mean([int(y[i] in rank[i,:k]) for i in range(n)]))
    return {"n":int(n),"log_loss":ll,"brier":br,"rps":rps,"top1":top1,"top3":top3}


def pooled_score(parts):
    # weighted average of per-row proper scores by concatenating row losses from mixed support sizes
    vals={"n":0,"log_loss":0.0,"brier":0.0,"rps":0.0,"top1":0.0,"top3":0.0}
    for y,p in parts:
        s=score(y,p); n=s["n"]; vals["n"]+=n
        for k in ["log_loss","brier","rps","top1","top3"]: vals[k]+=s[k]*n
    if vals["n"]:
        for k in ["log_loss","brier","rps","top1","top3"]: vals[k]/=vals["n"]
    return vals


def delta(a,b): return {k:float(a[k]-b[k]) for k in ["log_loss","brier","rps","top1","top3"]}


def row_ll_delta(parts0,parts1):
    ds=[]
    for (y0,p0),(y1,p1) in zip(parts0,parts1):
        assert np.array_equal(y0,y1)
        idx=np.arange(len(y0)); ds.extend((-np.log(np.clip(p1[idx,y0],1e-15,1))+np.log(np.clip(p0[idx,y0],1e-15,1))).tolist())
    return np.asarray(ds,float)


def boot(d,seed):
    rng=np.random.default_rng(seed); n=len(d); sims=np.empty(BOOT_REPS)
    for i in range(BOOT_REPS): sims[i]=float(d[rng.integers(0,n,n)].mean())
    return {"n":int(n),"reps":BOOT_REPS,"seed":seed,"mean_delta":float(d.mean()),"ci90_low":float(np.quantile(sims,.05)),"ci90_high":float(np.quantile(sims,.95)),"p_lt_zero":float(np.mean(sims<0))}


def draw_diag(parts):
    calls=actual=correct=0
    for T,y,p in parts:
        if T%2: continue
        draw_h=T//2; pred=np.argmax(p,axis=1)
        calls+=int(np.sum(pred==draw_h)); actual+=int(np.sum(y==draw_h)); correct+=int(np.sum((pred==draw_h)&(y==draw_h)))
    return {"top1_draw_calls":calls,"actual_draws":actual,"correct_draw_top1":correct,"precision":(correct/calls if calls else None),"recall":(correct/actual if actual else None)}


def evaluate_fold(train,test):
    btabs=baseline_tables(train)
    parts_b=[]; parts_a=[]; parts_m=[]; by_t={}; prob_resid=0.0
    for T in T_VALUES:
        tr=train[train.T==T].copy(); te=test[test.T==T].copy(); y=te.H.to_numpy(int)
        ma=pipe(); mm=pipe(); ma.fit(tr[OPEN],tr.H.to_numpy(int)); mm.fit(tr[MOVE],tr.H.to_numpy(int))
        pa=support_predict(ma,te[OPEN],T); pm=support_predict(mm,te[MOVE],T); pb=np.tile(btabs[T],(len(te),1))
        prob_resid=max(prob_resid,float(np.max(np.abs(pa.sum(1)-1))) if len(pa) else 0,float(np.max(np.abs(pm.sum(1)-1))) if len(pm) else 0)
        parts_b.append((y,pb)); parts_a.append((y,pa)); parts_m.append((y,pm))
        sb,sa,sm=score(y,pb),score(y,pa),score(y,pm)
        by_t[str(T)]={"baseline":sb,"opening":sa,"movement":sm,"opening_minus_baseline":delta(sa,sb),"movement_minus_opening":delta(sm,sa)}
    sb=pooled_score(parts_b); sa=pooled_score(parts_a); sm=pooled_score(parts_m)
    tagged_a=[(T,*parts_a[i]) for i,T in enumerate(T_VALUES)]; tagged_m=[(T,*parts_m[i]) for i,T in enumerate(T_VALUES)]
    return {"baseline":sb,"opening":sa,"movement":sm,"opening_minus_baseline":delta(sa,sb),"movement_minus_opening":delta(sm,sa),"by_T":by_t,"draw_opening":draw_diag(tagged_a),"draw_movement":draw_diag(tagged_m),"prob_resid":prob_resid},parts_b,parts_a,parts_m


def main():
    frame=load_source(); feat=build_rows(frame); elig=feat[feat.eligible & feat.T.isin(T_VALUES)].copy()
    folds={}; pooled_b=[]; pooled_a=[]; pooled_m=[]; wins_a=wins_m=0; coverage=True; max_resid=0.0
    for s in TEST_SEASONS:
        tr=elig[elig.season_start<s].copy(); te=elig[elig.season_start==s].copy()
        per_t={}; cov=len(tr)>=MIN_TRAIN_ALL and len(te)>=MIN_TEST_ALL
        for T in T_VALUES:
            tt=tr[tr.T==T]; legal=sorted(tt.H.astype(int).unique().tolist()); good=len(tt)>=MIN_TRAIN_PER_T and legal==list(range(T+1)); cov &= good
            per_t[str(T)]={"train_rows":int(len(tt)),"test_rows":int((te.T==T).sum()),"legal_H_observed":legal,"coverage":bool(good)}
        coverage &= cov
        if not cov:
            folds[str(s)]={"coverage":False,"train_rows":int(len(tr)),"test_rows":int(len(te)),"per_T":per_t}; continue
        ev,pb,pa,pm=evaluate_fold(tr,te); ev["coverage"]=True; ev["train_rows"]=int(len(tr)); ev["test_rows"]=int(len(te)); ev["per_T_coverage"]=per_t; folds[str(s)]=ev
        wins_a+=int(ev["opening_minus_baseline"]["log_loss"]<0); wins_m+=int(ev["movement_minus_opening"]["log_loss"]<0); max_resid=max(max_resid,ev["prob_resid"])
        pooled_b.extend(pb); pooled_a.extend(pa); pooled_m.extend(pm)
    pooled_n=sum(len(y) for y,_ in pooled_b); coverage &= pooled_n>=MIN_POOLED and len(pooled_b)==len(T_VALUES)*len(TEST_SEASONS)
    if not coverage:
        out={"schema":SCHEMA,"terminal":"STOP_COVERAGE","coverage_pass":False,"folds":folds,"pooled_n":int(pooled_n),"boundary":{"C072G2_2526_targets_opened":False,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"formal_weight":0}}
    else:
        sb=pooled_score(pooled_b); sa=pooled_score(pooled_a); sm=pooled_score(pooled_m); da=delta(sa,sb); dm=delta(sm,sa)
        ba=boot(row_ll_delta(pooled_b,pooled_a),BOOT_SEED); bm=boot(row_ll_delta(pooled_a,pooled_m),BOOT_SEED+1)
        by_t={}; wins_t=0
        for i,T in enumerate(T_VALUES):
            # collect this T across all folds by selecting each 6th part
            bparts=[pooled_b[j] for j in range(i,len(pooled_b),len(T_VALUES))]; aparts=[pooled_a[j] for j in range(i,len(pooled_a),len(T_VALUES))]; mparts=[pooled_m[j] for j in range(i,len(pooled_m),len(T_VALUES))]
            sbt=pooled_score(bparts); sat=pooled_score(aparts); smt=pooled_score(mparts); dat=delta(sat,sbt); dmt=delta(smt,sat); wins_t+=int(dat["log_loss"]<0)
            by_t[str(T)]={"baseline":sbt,"opening":sat,"movement":smt,"opening_minus_baseline":dat,"movement_minus_opening":dmt}
        a_qual=bool(da["log_loss"]<0 and ba["ci90_high"]<0 and da["brier"]<=0 and da["rps"]<=0 and da["top1"]>=0 and wins_a>=4 and wins_t>=5 and max_resid<=1e-10)
        m_qual=bool(dm["log_loss"]<0 and bm["ci90_high"]<0 and dm["brier"]<=0 and dm["rps"]<=0 and dm["top1"]>=0 and wins_m>=4)
        selected="MODEL_B_MOVEMENT" if a_qual and m_qual else "MODEL_A_OPENING" if a_qual else None
        terminal="C072H2_DGIVENT_DEVELOPMENT_PASS_MODEL_B" if selected=="MODEL_B_MOVEMENT" else "C072H2_DGIVENT_DEVELOPMENT_PASS_MODEL_A" if selected=="MODEL_A_OPENING" else "C072H2_DGIVENT_DEVELOPMENT_FAIL_PARK"
        out={"schema":SCHEMA,"terminal":terminal,"coverage_pass":True,"selected_confirmation_candidate":selected,"opening_qualifies":a_qual,"movement_increment_qualifies":m_qual,"fold_logloss_wins_opening":wins_a,"fold_logloss_wins_movement":wins_m,"exact_T_opening_wins":wins_t,"folds":folds,"pooled":{"n":int(pooled_n),"baseline":sb,"opening":sa,"movement":sm,"opening_minus_baseline":da,"movement_minus_opening":dm,"bootstrap_opening_minus_baseline":ba,"bootstrap_movement_minus_opening":bm,"by_T":by_t,"max_probability_sum_residual":max_resid},"boundary":{"development_through_season_start":2023,"C072G2_2526_targets_opened":False,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"protected_opened":False,"formal_weight":0,"unified_exact_score_matrix_generated":False}}
    p=Path("football-data/research/c072h2_summary.json"); p.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n"); print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
