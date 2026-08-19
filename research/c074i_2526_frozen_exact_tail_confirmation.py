#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

BASE_URL = "https://www.football-data.co.uk/mmz4281"
DIVS = {
    "E1": "England Championship", "E2": "England League One", "E3": "England League Two",
    "SC0": "Scotland Premiership", "SC1": "Scotland Championship", "SC2": "Scotland League One", "SC3": "Scotland League Two",
    "D2": "Germany 2. Bundesliga", "I2": "Italy Serie B", "SP2": "Spain Segunda Division", "F2": "France Ligue 2",
    "P1": "Portugal Primeira Liga", "G1": "Greece Super League", "T1": "Turkey Super Lig",
}
TRAIN_FIRST = 2009
TRAIN_LAST = 2023
POLICY = 2024
TEST = 2025
ALPHA = 0.5
EMPIRICAL_PRIOR_MASS = 5.0
BOOTSTRAPS = 5000
BOOTSTRAP_SEED = 51006
K = 5
SCORE_COLS = ["Div","Date","HomeTeam","AwayTeam","FTHG","FTAG"]


def season_code(sy: int) -> str:
    return f"{sy % 100:02d}{(sy+1) % 100:02d}"


def fetch_score_file(sy: int, div: str):
    code = season_code(sy); url=f"{BASE_URL}/{code}/{div}.csv"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 C074I frozen exact-tail confirmation"})
    last=None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=30) as resp:
                raw=resp.read(); status=int(getattr(resp,"status",200))
            header=pd.read_csv(io.BytesIO(raw),nrows=0).columns.tolist()
            missing=[c for c in SCORE_COLS if c not in header]
            if missing:
                return None,{"url":url,"status":status,"reason":f"missing_columns:{missing}","header_columns":header}
            d=pd.read_csv(io.BytesIO(raw),usecols=SCORE_COLS,low_memory=False)
            return d,{"url":url,"status":status,"bytes":len(raw),"materialized_columns":SCORE_COLS}
        except urllib.error.HTTPError as exc:
            if exc.code==404:
                return None,{"url":url,"status":404,"reason":"not_available"}
            last=f"HTTPError:{exc.code}"
        except Exception as exc:
            last=f"{type(exc).__name__}:{exc}"
        time.sleep(1.5*(attempt+1))
    return None,{"url":url,"status":0,"reason":last}


def load_all():
    frames=[]; audit=[]
    for sy in range(TRAIN_FIRST,TEST+1):
        for div,name in DIVS.items():
            d,meta=fetch_score_file(sy,div)
            if d is None:
                audit.append({"season_start":sy,"season_code":season_code(sy),"div":div,"league":name,"used":False,"rows":0,**meta}); continue
            for c in ["FTHG","FTAG"]: d[c]=pd.to_numeric(d[c],errors="coerce")
            d["date"]=pd.to_datetime(d["Date"],errors="coerce",dayfirst=True)
            valid=d[["HomeTeam","AwayTeam","FTHG","FTAG"]].notna().all(axis=1) & d["date"].notna() & (d[["FTHG","FTAG"]]>=0).all(axis=1)
            d=d.loc[valid].copy()
            d["season_start"]=sy; d["competition_id"]=div; d["league_name"]=name
            d["total_goals"]=(d["FTHG"]+d["FTAG"]).astype(int)
            d["tail_excess"]=d["total_goals"]-7
            frames.append(d[["season_start","competition_id","league_name","date","HomeTeam","AwayTeam","FTHG","FTAG","total_goals","tail_excess"]])
            audit.append({"season_start":sy,"season_code":season_code(sy),"div":div,"league":name,"used":True,"rows":int(len(d)),"tail_rows":int((d.total_goals>=7).sum()),**meta})
    if not frames: raise RuntimeError("no score files loaded")
    raw=pd.concat(frames,ignore_index=True).sort_values(["date","competition_id","HomeTeam","AwayTeam"]).reset_index(drop=True)
    return raw,audit


def fixed_components(y,prob):
    prob=np.clip(np.asarray(prob,float),1e-15,1.0); prob/=prob.sum(axis=1,keepdims=True)
    one=np.eye(K)[np.asarray(y,int)]
    return pd.DataFrame({
        "logloss":-np.log(prob[np.arange(len(y)),np.asarray(y,int)]),
        "brier":np.square(prob-one).sum(axis=1),
        "rps":np.square(np.cumsum(prob,axis=1)[:,:-1]-np.cumsum(one,axis=1)[:,:-1]).sum(axis=1)/4.0,
        "top1":(np.argmax(prob,axis=1)==np.asarray(y,int)).astype(float),
        "top2":np.asarray([np.asarray(y,int)[i] in np.argsort(-prob[i])[:2] for i in range(len(y))],float),
    })


def metric_summary(c): return {k:float(c[k].mean()) for k in c.columns}


def geometric_q(frame):
    e=frame.tail_excess.to_numpy(int)
    return float((e.sum()+ALPHA)/(len(e)+e.sum()+2*ALPHA))


def hurdle_params(frame):
    e=frame.tail_excess.to_numpy(int); z=int((e==0).sum()); pos=e[e>0]
    pi=(z+ALPHA)/(len(e)+2*ALPHA)
    continuations=int((pos-1).sum()); stops=len(pos)
    r=(continuations+ALPHA)/(continuations+stops+2*ALPHA)
    return float(pi),float(r)


def geometric_prob(q,n):
    v=np.asarray([1-q,(1-q)*q,(1-q)*q**2,(1-q)*q**3,q**4],float)
    return np.tile(v,(n,1))


def hurdle_prob(pi,r,n):
    v=np.asarray([pi,(1-pi)*(1-r),(1-pi)*(1-r)*r,(1-pi)*(1-r)*r**2,(1-pi)*r**3],float)
    return np.tile(v,(n,1))


def select_law(train,policy):
    y=np.minimum(policy.tail_excess.to_numpy(int),4)
    q=geometric_q(train); pi,r=hurdle_params(train)
    cand={"pooled_geometric":geometric_prob(q,len(policy)),"pooled_hurdle_geometric":hurdle_prob(pi,r,len(policy))}
    receipts=[]
    for name,p in cand.items():
        s=metric_summary(fixed_components(y,p)); receipts.append({"candidate":name,"policy_metrics":s,"probability_sum_max_residual":float(np.max(np.abs(p.sum(axis=1)-1)))})
    selected=min(receipts,key=lambda x:(x["policy_metrics"]["logloss"],x["candidate"]))["candidate"]
    return selected,receipts


def fit_selected(name,fit,n):
    if name=="pooled_geometric":
        q=geometric_q(fit); return geometric_prob(q,n),{"q":q,"full_support":"P(E=e)=(1-q)q^e, e>=0"}
    pi,r=hurdle_params(fit); return hurdle_prob(pi,r,n),{"pi_zero_excess":pi,"continuation":r,"full_support":"P(E=0)=pi; P(E=e>=1)=(1-pi)(1-r)r^(e-1)"}


def empirical_baseline(fit,test):
    pooled=np.bincount(np.minimum(fit.tail_excess.to_numpy(int),4),minlength=5).astype(float)
    prior=pooled/pooled.sum()*EMPIRICAL_PRIOR_MASS
    by={c:np.bincount(np.minimum(g.tail_excess.to_numpy(int),4),minlength=5).astype(float) for c,g in fit.groupby("competition_id")}
    rows=[]
    for c in test.competition_id:
        counts=by.get(c,pooled); v=counts+prior; rows.append(v/v.sum())
    return np.asarray(rows)


def cluster_bootstrap(meta,model,baseline):
    groups=sorted(meta.competition_id.astype(str).unique()); indexes=[np.flatnonzero(meta.competition_id.astype(str).to_numpy()==g) for g in groups]
    rng=np.random.default_rng(BOOTSTRAP_SEED); picks=rng.integers(0,len(groups),size=(BOOTSTRAPS,len(groups)))
    counts=np.asarray([len(i) for i in indexes],float); denom=counts[picks].sum(axis=1)
    out={}
    for metric in model.columns:
        d=np.asarray([float((model.loc[i,metric]-baseline.loc[i,metric]).sum()) for i in indexes])
        vals=d[picks].sum(axis=1)/denom
        better=vals<0 if metric in {"logloss","brier","rps"} else vals>0
        out[metric]={"mean_delta_model_minus_baseline":float(vals.mean()),"p05":float(np.quantile(vals,.05)),"p95":float(np.quantile(vals,.95)),"probability_model_better":float(better.mean())}
    return out


def survival(params,threshold):
    e=threshold-7
    if e<=0:return 1.0
    if "q" in params:return float(params["q"]**e)
    return float((1-params["pi_zero_excess"])*params["continuation"]**(e-1))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out",default="artifacts/c074i_exact_tail_confirmation"); args=ap.parse_args()
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    raw,audit=load_all()
    duplicate=int(raw.duplicated(["competition_id","date","HomeTeam","AwayTeam"]).sum())
    train_all=raw[(raw.season_start>=TRAIN_FIRST)&(raw.season_start<=TRAIN_LAST)]
    policy_all=raw[raw.season_start==POLICY]
    test_all=raw[raw.season_start==TEST]
    train=train_all[train_all.total_goals>=7].copy(); policy=policy_all[policy_all.total_goals>=7].copy(); test=test_all[test_all.total_goals>=7].copy()
    train_divs=sorted(train.competition_id.unique().tolist()); policy_score_divs=sorted(policy_all.competition_id.unique().tolist()); test_score_divs=sorted(test_all.competition_id.unique().tolist())
    test_bins=sorted(np.unique(np.minimum(test.tail_excess.to_numpy(int),4)).astype(int).tolist()) if len(test) else []
    first4_present=sum(b in test_bins for b in [0,1,2,3])
    coverage={
        "all_score_rows":{"train":int(len(train_all)),"policy":int(len(policy_all)),"test":int(len(test_all))},
        "tail_rows":{"train":int(len(train)),"policy":int(len(policy)),"test":int(len(test))},
        "train_tail_divisions":train_divs,"policy_score_divisions":policy_score_divs,"test_score_divisions":test_score_divs,
        "confirmation_bins_present":test_bins,"confirmation_first4_bins_present":first4_present,"duplicate_identity_rows_all_loaded":duplicate,
        "checks":{
            "train_tail_divisions_ge_10":len(train_divs)>=10,
            "policy_score_divisions_ge_10":len(policy_score_divs)>=10,
            "test_score_divisions_eq_14":len(test_score_divs)==14,
            "train_tail_rows_ge_300":len(train)>=300,
            "policy_tail_rows_ge_50":len(policy)>=50,
            "test_tail_rows_ge_80":len(test)>=80,
            "at_least_3_first4_confirmation_bins":first4_present>=3,
            "duplicate_identity_rows_eq_0":duplicate==0,
        }
    }
    if not all(coverage["checks"].values()):
        summary={"experiment":"C074-I","formal_weight":0,"terminal":"STOP_DATA/COVERAGE","coverage":coverage,"source_audit":audit,
                 "sealed_assets_touched":{"C071_reserve_52180":0,"C070F_confirmation_1597":0,"A05":0,"protected":0}}
        (out/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8"); pd.DataFrame(audit).to_csv(out/"source_files.csv",index=False)
        print(json.dumps({"terminal":summary["terminal"],"coverage":coverage},indent=2)); return

    selected,policy_receipts=select_law(train,policy)
    fit=pd.concat([train,policy],ignore_index=True)
    model_p,params=fit_selected(selected,fit,len(test)); base_p=empirical_baseline(fit,test)
    y=np.minimum(test.tail_excess.to_numpy(int),4)
    mc=fixed_components(y,model_p); bc=fixed_components(y,base_p)
    ms=metric_summary(mc); bs=metric_summary(bc); d={k:ms[k]-bs[k] for k in ms}
    meta=test[["competition_id"]].reset_index(drop=True); boot=cluster_bootstrap(meta,mc.reset_index(drop=True),bc.reset_index(drop=True))
    obs=np.bincount(y,minlength=5).astype(float); obs/=obs.sum(); pred=model_p.mean(axis=0); cal_res=np.abs(pred-obs); max_cal=float(cal_res.max())
    max_prob=float(np.max(np.abs(model_p.sum(axis=1)-1.0)))
    per_div=[]
    for div,gidx in test.reset_index(drop=True).groupby("competition_id").groups.items():
        idx=np.asarray(list(gidx),int); m=metric_summary(mc.loc[idx]); b=metric_summary(bc.loc[idx]); per_div.append({"division":div,"tail_n":int(len(idx)),**{f"model_{k}":v for k,v in m.items()},**{f"baseline_{k}":v for k,v in b.items()},**{f"delta_{k}":m[k]-b[k] for k in m}})
    exact_counts={str(t):int((test.total_goals==t).sum()) for t in sorted(test.total_goals.unique().astype(int))}
    gate={
        "bootstrap90_upper_dlogloss_lt_0":boot["logloss"]["p95"]<0,
        "bootstrap90_upper_dbrier_lt_0":boot["brier"]["p95"]<0,
        "bootstrap90_upper_drps_lt_0":boot["rps"]["p95"]<0,
        "probability_conservation_le_1e_12":max_prob<=1e-12,
        "max_pooled_bin_calibration_residual_le_0_05":max_cal<=0.05,
    }
    passed=all(gate.values())
    summary={
        "experiment":"C074-I","contract":"research/C074I_CONTRACT.md","formal_weight":0,
        "confirmation_domain":"Football-Data.co.uk untouched 2025/26 secondary/other divisions",
        "candidate_family_origin":{"source":"V5.1 R1 frozen before this domain","laws":["pooled_geometric","pooled_hurdle_geometric"],"alpha":ALPHA,"evaluation_bins":["7","8","9","10","11+"],"empirical_prior_mass":EMPIRICAL_PRIOR_MASS},
        "chronology":{"train":"2009/10-2023/24 available","policy":"2024/25","test":"2025/26"},
        "coverage":coverage,"selected_law":selected,"policy_candidates":policy_receipts,"selected_parameters":params,
        "model_metrics":ms,"empirical_baseline_metrics":bs,"delta_model_minus_empirical":d,
        "cluster_bootstrap_90":{"samples":BOOTSTRAPS,"seed":BOOTSTRAP_SEED,"by":"competition_id","metrics":boot},
        "confirmation_exact_total_counts":exact_counts,"observed_eval_bin_distribution":obs.tolist(),"predicted_eval_bin_distribution":pred.tolist(),
        "absolute_bin_calibration_residuals":cal_res.tolist(),"max_abs_bin_calibration_residual":max_cal,
        "tail_survival":{"T>=12":survival(params,12),"T>=15":survival(params,15),"T>=20":survival(params,20),"T>=30":survival(params,30),"T>=60":survival(params,60)},
        "probability_sum_max_abs_residual":max_prob,"per_division":per_div,"gate_checks":gate,
        "terminal":"CONFIRMATION_PASS_EXACT_TAIL" if passed else "CONFIRMATION_FAIL_EXACT_TAIL_MATRIX_BLOCKED",
        "source_audit":audit,
        "sealed_assets_touched":{"C071_reserve_52180":0,"C070F_confirmation_1597":0,"A05":0,"protected":0},
        "boundary":"PASS confirms only exact 7+ tail disaggregation as a research component. Unified matrix and formal exact-score output remain separately gated."
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    pd.DataFrame(per_div).to_csv(out/"division_tail_metrics.csv",index=False); pd.DataFrame(audit).to_csv(out/"source_files.csv",index=False)
    print(json.dumps({"terminal":summary["terminal"],"coverage":coverage,"selected_law":selected,"params":params,"model":ms,"baseline":bs,"delta":d,"bootstrap":boot,"max_calibration_residual":max_cal,"gate":gate},indent=2))

if __name__=="__main__":main()
