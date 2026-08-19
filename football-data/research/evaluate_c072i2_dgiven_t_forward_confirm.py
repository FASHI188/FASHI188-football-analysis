#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import math
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

import run_c072h2_dgiven_t_development as fixed_h2

h2 = fixed_h2.ns
SCHEMA = "C072I2_DGIVENT_FORWARD_CONFIRM_V1"
DIVS = ["E1","E2","E3","SC0","SC1","SC2","SC3","D2","I2","SP2","F2","P1"]
EXPECTED_IDENTITIES = 4184
BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
T_VALUES = [1,2,3,4,5,6]
BOOT_REPS = 5000
BOOT_SEED = 72026
MIN_TOTAL = 3000
MIN_DIV = 80
MIN_T = {1:100,2:100,3:100,4:100,5:100,6:40}


def fetch(season, div):
    req=Request(BASE_URL.format(season=season,div=div),headers={"User-Agent":"Mozilla/5.0 football3-confirmation"})
    return urlopen(req,timeout=90).read()


def parse_identity(raw, div):
    hdr=pd.read_csv(io.BytesIO(raw),nrows=0).columns.tolist()
    req=["Date","HomeTeam","AwayTeam"]
    missing=[x for x in req if x not in hdr]
    if missing: raise RuntimeError(f"{div} missing identity fields {missing}")
    x=pd.read_csv(io.BytesIO(raw),usecols=req)
    x["division"]=div
    x["date"]=pd.to_datetime(x.Date,errors="coerce",dayfirst=True,utc=True)
    if x.date.isna().any(): raise RuntimeError(f"{div} invalid confirmation date")
    return x


def parse_warmup(raw, div):
    req=["Date","HomeTeam","AwayTeam","FTHG","FTAG"]
    x=pd.read_csv(io.BytesIO(raw),usecols=req)
    x["division"]=div; x["date"]=pd.to_datetime(x.Date,errors="coerce",dayfirst=True,utc=True)
    x["FTHG"]=pd.to_numeric(x.FTHG,errors="coerce"); x["FTAG"]=pd.to_numeric(x.FTAG,errors="coerce")
    x=x.dropna(subset=["date","FTHG","FTAG"]).copy(); x["FTHG"]=x.FTHG.astype(int); x["FTAG"]=x.FTAG.astype(int)
    return x


def parse_confirmation(raw, div):
    req=["Date","HomeTeam","AwayTeam","FTHG","FTAG","AvgH","AvgD","AvgA"]
    x=pd.read_csv(io.BytesIO(raw),usecols=req)
    x["division"]=div; x["date"]=pd.to_datetime(x.Date,errors="coerce",dayfirst=True,utc=True)
    x["FTHG"]=pd.to_numeric(x.FTHG,errors="coerce"); x["FTAG"]=pd.to_numeric(x.FTAG,errors="coerce")
    for c in ["AvgH","AvgD","AvgA"]: x[c]=pd.to_numeric(x[c],errors="coerce")
    return x


def mean_sd(vals):
    if not vals: return np.nan,np.nan
    a=np.asarray(vals,float); return float(a.mean()),float(a.std(ddof=0))


def hist_factory(): return {"gf":[],"ga":[]}


def profile(h):
    gfm,gfs=mean_sd(h["gf"]); gam,gas=mean_sd(h["ga"])
    return {"n":len(h["gf"]),"gf_mean":gfm,"gf_sd":gfs,"ga_mean":gam,"ga_sd":gas}


def opening_coords(h,d,a):
    if not (np.isfinite(h) and np.isfinite(d) and np.isfinite(a) and h>1 and d>1 and a>1): return np.nan,np.nan
    p=np.asarray([1/h,1/d,1/a],float); p=p/p.sum(); p=np.clip(p,1e-12,1)
    return float(np.log(p[0]/p[2])), float(np.log(p[1]/math.sqrt(p[0]*p[2])))


def initialize_histories(warm):
    th=defaultdict(hist_factory); ct=defaultdict(list)
    for _,r in warm.sort_values(["date","division","HomeTeam","AwayTeam"]).iterrows():
        div=str(r.division); hk=(div,str(r.HomeTeam)); ak=(div,str(r.AwayTeam)); hg=int(r.FTHG); ag=int(r.FTAG)
        th[hk]["gf"].append(float(hg)); th[hk]["ga"].append(float(ag)); th[ak]["gf"].append(float(ag)); th[ak]["ga"].append(float(hg)); ct[div].append(float(hg+ag))
    return th,ct


def build_confirmation_rows(warm,test):
    th,ct=initialize_histories(warm); rows=[]
    test=test.sort_values(["date","division","HomeTeam","AwayTeam"]).reset_index(drop=True)
    for date,g in test.groupby("date",sort=True):
        for _,r in g.iterrows():
            if pd.isna(r.FTHG) or pd.isna(r.FTAG):
                continue
            div=str(r.division); hk=(div,str(r.HomeTeam)); ak=(div,str(r.AwayTeam)); hp=profile(th[hk]); ap=profile(th[ak]); cm,cs=mean_sd(ct[div])
            os,od=opening_coords(float(r.AvgH),float(r.AvgD),float(r.AvgA))
            T=int(r.FTHG)+int(r.FTAG); H=int(r.FTHG)
            rows.append({
                "date":r.date,"division":div,"T":T,"H":H,
                "competition_total_mean":cm,"competition_total_sd":cs,
                "home_goals_for_mean":hp["gf_mean"],"home_goals_for_sd":hp["gf_sd"],"home_goals_against_mean":hp["ga_mean"],"home_goals_against_sd":hp["ga_sd"],
                "away_goals_for_mean":ap["gf_mean"],"away_goals_for_sd":ap["gf_sd"],"away_goals_against_mean":ap["ga_mean"],"away_goals_against_sd":ap["ga_sd"],
                "log1p_home_result_history_n":math.log1p(hp["n"]),"log1p_away_result_history_n":math.log1p(ap["n"]),
                "open_strength":os,"open_drawness":od,
                "eligible":hp["n"]>=h2["MIN_HISTORY"] and ap["n"]>=h2["MIN_HISTORY"] and np.isfinite(os) and np.isfinite(od),
            })
        # same-date predict-before-update; prior current-season results may update later targets only
        for _,r in g.iterrows():
            if pd.isna(r.FTHG) or pd.isna(r.FTAG): continue
            div=str(r.division); hk=(div,str(r.HomeTeam)); ak=(div,str(r.AwayTeam)); hg=int(r.FTHG); ag=int(r.FTAG)
            th[hk]["gf"].append(float(hg)); th[hk]["ga"].append(float(ag)); th[ak]["gf"].append(float(ag)); th[ak]["ga"].append(float(hg)); ct[div].append(float(hg+ag))
    return pd.DataFrame(rows)


def row_metrics(y,p):
    y=np.asarray(y,int); idx=np.arange(len(y)); one=np.zeros_like(p); one[idx,y]=1.0
    ll=-np.log(np.clip(p[idx,y],1e-15,1)); br=np.sum((p-one)**2,axis=1)
    rps=np.sum((np.cumsum(p,axis=1)[:,:-1]-np.cumsum(one,axis=1)[:,:-1])**2,axis=1)/(p.shape[1]-1)
    rank=np.argsort(-p,axis=1); k=min(3,p.shape[1]); top1=(rank[:,0]==y).astype(float); top3=np.asarray([float(y[i] in rank[i,:k]) for i in range(len(y))])
    return ll,br,rps,top1,top3,rank[:,0]


def aggregate(df):
    if len(df)==0: return {"n":0}
    out={"n":int(len(df))}
    for k in ["b_ll","c_ll","b_brier","c_brier","b_rps","c_rps","b_top1","c_top1","b_top3","c_top3"]: out[k]=float(df[k].mean())
    out["candidate_minus_baseline"]={
        "log_loss":out["c_ll"]-out["b_ll"],"brier":out["c_brier"]-out["b_brier"],"rps":out["c_rps"]-out["b_rps"],"top1":out["c_top1"]-out["b_top1"],"top3":out["c_top3"]-out["b_top3"]
    }
    return out


def bootstrap(d):
    d=np.asarray(d,float); rng=np.random.default_rng(BOOT_SEED); n=len(d); sims=np.empty(BOOT_REPS)
    for i in range(BOOT_REPS): sims[i]=float(d[rng.integers(0,n,n)].mean())
    return {"n":int(n),"reps":BOOT_REPS,"seed":BOOT_SEED,"mean_delta":float(d.mean()),"ci90_low":float(np.quantile(sims,.05)),"ci90_high":float(np.quantile(sims,.95)),"p_lt_zero":float(np.mean(sims<0))}


def main():
    # Frozen H2 training domain / coefficients.
    train_frame=h2["load_source"](); train_feat=h2["build_rows"](train_frame); train=train_feat[train_feat.eligible & train_feat["T"].isin(T_VALUES)].copy()
    tables=h2["baseline_tables"](train)
    models={}
    for T in T_VALUES:
        tr=train[train["T"]==T].copy(); legal=sorted(tr.H.astype(int).unique().tolist())
        if len(tr)<200 or legal!=list(range(T+1)): raise RuntimeError(f"training support gate T={T} n={len(tr)} legal={legal}")
        m=h2["pipe"](); m.fit(tr[h2["OPEN"]],tr.H.to_numpy(int)); models[T]=m

    # Zero-label identity gate is checked before target-effect interpretation.
    raws25={}; ids=[]
    for div in DIVS:
        raw=fetch("2526",div); raws25[div]=raw; ids.append(parse_identity(raw,div))
    ident=pd.concat(ids,ignore_index=True)
    dup=int(ident[["division","Date","HomeTeam","AwayTeam"]].astype(str).duplicated().sum())
    identity_ok=(len(ident)==EXPECTED_IDENTITIES and dup==0 and ident.division.nunique()==12)
    if not identity_ok:
        out={"schema":SCHEMA,"terminal":"STOP_CONFIRMATION_IDENTITY_DRIFT","raw_identities":int(len(ident)),"duplicates":dup,"boundary":{"C072G2_target_values_opened":False,"replacement_rows":0,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"formal_weight":0}}
        Path("football-data/research/c072i2_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n"); print(json.dumps(out,ensure_ascii=False,indent=2)); return

    # Contract is frozen: now first/only target opening is authorized.
    warm=pd.concat([parse_warmup(fetch("2425",div),div) for div in DIVS],ignore_index=True)
    test=pd.concat([parse_confirmation(raws25[div],div) for div in DIVS],ignore_index=True)
    missing=int(test[["FTHG","FTAG"]].isna().any(axis=1).sum())
    rows=build_confirmation_rows(warm,test); elig=rows[rows.eligible & rows["T"].isin(T_VALUES)].copy().sort_values(["date","division"]).reset_index(drop=True)

    div_counts=elig.groupby("division").size().to_dict(); t_counts=elig.groupby("T").size().to_dict()
    coverage=bool(len(elig)>=MIN_TOTAL and elig.division.nunique()==12 and all(div_counts.get(d,0)>=MIN_DIV for d in DIVS) and all(t_counts.get(t,0)>=MIN_T[t] for t in T_VALUES))
    if not coverage:
        out={"schema":SCHEMA,"terminal":"STOP_CONFIRMATION_COVERAGE","raw_identities":int(len(ident)),"missing_target_rows":missing,"eligible_rows":int(len(elig)),"division_counts":div_counts,"T_counts":{str(k):int(v) for k,v in t_counts.items()},"boundary":{"C072G2_target_values_opened":True,"target_rows_first_parsed":int(len(test)),"replacement_rows":0,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"formal_weight":0}}
        Path("football-data/research/c072i2_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n"); print(json.dumps(out,ensure_ascii=False,indent=2)); return

    recs=[]; max_resid=0.0
    for T in T_VALUES:
        te=elig[elig["T"]==T].copy(); y=te.H.to_numpy(int); pc=h2["support_predict"](models[T],te[h2["OPEN"]],T); pb=np.tile(tables[T],(len(te),1))
        max_resid=max(max_resid,float(np.max(np.abs(pc.sum(1)-1))),float(np.max(np.abs(pb.sum(1)-1))))
        bll,bbr,brps,bt1,bt3,bpred=row_metrics(y,pb); cll,cbr,crps,ct1,ct3,cpred=row_metrics(y,pc)
        for i,(_,r) in enumerate(te.reset_index(drop=True).iterrows()):
            recs.append({"date":r.date,"division":r.division,"T":T,"H":int(y[i]),"b_ll":float(bll[i]),"c_ll":float(cll[i]),"b_brier":float(bbr[i]),"c_brier":float(cbr[i]),"b_rps":float(brps[i]),"c_rps":float(crps[i]),"b_top1":float(bt1[i]),"c_top1":float(ct1[i]),"b_top3":float(bt3[i]),"c_top3":float(ct3[i]),"b_pred":int(bpred[i]),"c_pred":int(cpred[i])})
    scored=pd.DataFrame(recs).sort_values(["date","division","T"]).reset_index(drop=True); scored["dll"]=scored.c_ll-scored.b_ll
    pooled=aggregate(scored); bt=bootstrap(scored.dll.to_numpy(float)); cut=len(scored)//2
    halves={"early":aggregate(scored.iloc[:cut]),"late":aggregate(scored.iloc[cut:])}
    by_div={d:aggregate(g) for d,g in scored.groupby("division",sort=True)}; div_wins=sum(v["candidate_minus_baseline"]["log_loss"]<0 for v in by_div.values())
    by_t={str(int(t)):aggregate(g) for t,g in scored.groupby("T",sort=True)}; t_wins=sum(v["candidate_minus_baseline"]["log_loss"]<0 for v in by_t.values())

    even=scored[scored.T.map(lambda x: int(x)%2==0)].copy(); even["draw_h"]=(even.T//2).astype(int)
    def dd(prefix):
        calls=int((even[f"{prefix}_pred"]==even.draw_h).sum()); actual=int((even.H==even.draw_h).sum()); correct=int(((even[f"{prefix}_pred"]==even.draw_h)&(even.H==even.draw_h)).sum())
        return {"calls":calls,"actual":actual,"correct":correct,"precision":correct/calls if calls else None,"recall":correct/actual if actual else None}
    draw_diag={"baseline":dd("b"),"candidate":dd("c")}

    d=pooled["candidate_minus_baseline"]
    gate=bool(d["log_loss"]<0 and bt["ci90_high"]<0 and d["brier"]<=0 and d["rps"]<=0 and d["top1"]>=0 and halves["early"]["candidate_minus_baseline"]["log_loss"]<0 and halves["late"]["candidate_minus_baseline"]["log_loss"]<0 and div_wins>=7 and t_wins>=5 and max_resid<=1e-10)
    out={
        "schema":SCHEMA,"terminal":"C072I2_DGIVENT_FORWARD_CONFIRMATION_PASS" if gate else "C072I2_DGIVENT_CONFIRMATION_FAIL_PARK",
        "identity_gate_pass":True,"coverage_pass":True,"raw_identities":int(len(ident)),"missing_target_rows":missing,"replacement_rows":0,"eligible_rows":int(len(scored)),
        "training_rows":int(len(train)),"selected_candidate":"MODEL_A_OPENING","pooled":pooled,"bootstrap":bt,"chronological_halves":halves,"by_division":by_div,"division_logloss_wins":int(div_wins),"by_exact_T":by_t,"exact_T_logloss_wins":int(t_wins),"draw_diagnostics":draw_diag,"max_probability_sum_residual":max_resid,"confirmation_gate":gate,
        "boundary":{"C072G2_target_values_opened":True,"target_rows_first_parsed":int(len(test)),"replacement_rows":0,"model_coefficients_refit_on_confirmation":False,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"protected_opened":False,"formal_weight":0,"unified_exact_score_matrix_generated":False,"T_ge_7_resolved":False}
    }
    Path("football-data/research/c072i2_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str)+"\n"); print(json.dumps(out,ensure_ascii=False,indent=2,default=str))

if __name__=="__main__": main()
