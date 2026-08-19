#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

FILES = {
    "ARG":"Argentina","AUT":"Austria","BRA":"Brazil","CHN":"China","DNK":"Denmark","FIN":"Finland",
    "IRL":"Ireland","JPN":"Japan","MEX":"Mexico","NOR":"Norway","POL":"Poland","ROU":"Romania",
    "RUS":"Russia","SWE":"Sweden","SWZ":"Switzerland","USA":"USA",
}
TRAIN_END = pd.Timestamp("2023-12-31 23:59:59")
POLICY_START = pd.Timestamp("2024-01-01")
POLICY_END = pd.Timestamp("2024-12-31 23:59:59")
TEST_START = pd.Timestamp("2025-01-01")
TEST_END = pd.Timestamp("2025-12-31 23:59:59")
ALPHA = 0.5
EMPIRICAL_PRIOR_MASS = 5.0
BOOTSTRAPS = 5000
BOOTSTRAP_SEED = 51006
K = 5
USECOLS = ["Date", "Home", "Away", "HG", "AG"]


def fetch(code: str):
    url = f"https://www.football-data.co.uk/new/{code}.csv"
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0 C074K frozen exact-tail confirmation"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read(); status = int(getattr(resp, "status", 200))
            header = pd.read_csv(io.BytesIO(raw), nrows=0).columns.tolist()
            missing = [c for c in USECOLS if c not in header]
            if missing:
                return None, {"url":url,"status":status,"reason":f"missing_columns:{missing}","header_columns":header}
            # Contract is already frozen; only identity/date and the two 90-minute goal labels are materialized.
            d = pd.read_csv(io.BytesIO(raw), usecols=USECOLS, low_memory=False)
            return d, {"url":url,"status":status,"bytes":len(raw),"materialized_columns":USECOLS}
        except Exception as exc:
            last = f"{type(exc).__name__}:{exc}"
            time.sleep(1.5 * (attempt + 1))
    return None, {"url":url,"status":0,"reason":last}


def load_all():
    frames=[]; audit=[]
    for code,name in FILES.items():
        d,meta=fetch(code)
        if d is None:
            audit.append({"source_code":code,"name":name,"used":False,"rows":0,**meta}); continue
        d["HG"]=pd.to_numeric(d["HG"],errors="coerce"); d["AG"]=pd.to_numeric(d["AG"],errors="coerce")
        d["date"]=pd.to_datetime(d["Date"],errors="coerce",dayfirst=True)
        valid=d[["Home","Away","HG","AG"]].notna().all(axis=1) & d["date"].notna() & (d[["HG","AG"]]>=0).all(axis=1)
        d=d.loc[valid].copy(); d["source_code"]=code; d["source_name"]=name
        d["total_goals"]=(d["HG"]+d["AG"]).astype(int); d["tail_excess"]=d["total_goals"]-7
        frames.append(d[["source_code","source_name","date","Home","Away","HG","AG","total_goals","tail_excess"]])
        audit.append({
            "source_code":code,"name":name,"used":True,"rows":int(len(d)),
            "train_rows":int((d.date<=TRAIN_END).sum()),
            "policy_rows":int(d.date.between(POLICY_START,POLICY_END).sum()),
            "test_2025_rows":int(d.date.between(TEST_START,TEST_END).sum()),
            "train_tail_rows":int(((d.date<=TRAIN_END)&(d.total_goals>=7)).sum()),
            "policy_tail_rows":int((d.date.between(POLICY_START,POLICY_END)&(d.total_goals>=7)).sum()),
            "test_tail_rows":int((d.date.between(TEST_START,TEST_END)&(d.total_goals>=7)).sum()),
            "date_min":None if len(d)==0 else str(d.date.min()),"date_max":None if len(d)==0 else str(d.date.max()),
            **meta,
        })
    if not frames: raise RuntimeError("no extra16 score files loaded")
    raw=pd.concat(frames,ignore_index=True).sort_values(["date","source_code","Home","Away"]).reset_index(drop=True)
    return raw,audit


def fixed_components(y, p):
    y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-15,1.0); p/=p.sum(axis=1,keepdims=True)
    one=np.eye(K)[y]
    return pd.DataFrame({
        "logloss":-np.log(p[np.arange(len(y)),y]),
        "brier":np.square(p-one).sum(axis=1),
        "rps":np.square(np.cumsum(p,axis=1)[:,:-1]-np.cumsum(one,axis=1)[:,:-1]).sum(axis=1)/4.0,
        "top1":(np.argmax(p,axis=1)==y).astype(float),
        "top2":np.asarray([float(y[i] in np.argsort(-p[i])[:2]) for i in range(len(y))]),
    })


def summary(c):
    return {k:float(c[k].mean()) for k in c.columns}


def geometric_q(frame):
    e=frame.tail_excess.to_numpy(int)
    return float((e.sum()+ALPHA)/(len(e)+e.sum()+2.0*ALPHA))


def hurdle_parameters(frame):
    e=frame.tail_excess.to_numpy(int); zero=int((e==0).sum()); pos=e[e>0]
    pi=(zero+ALPHA)/(len(e)+2.0*ALPHA)
    continuations=int((pos-1).sum()); stops=len(pos)
    r=(continuations+ALPHA)/(continuations+stops+2.0*ALPHA)
    return float(pi),float(r)


def geometric_probability(q,n):
    v=np.asarray([1-q,(1-q)*q,(1-q)*q**2,(1-q)*q**3,q**4],float)
    return np.tile(v,(n,1))


def hurdle_probability(pi,r,n):
    v=np.asarray([pi,(1-pi)*(1-r),(1-pi)*(1-r)*r,(1-pi)*(1-r)*r**2,(1-pi)*r**3],float)
    return np.tile(v,(n,1))


def select_law(train, policy):
    y=np.minimum(policy.tail_excess.to_numpy(int),4)
    q=geometric_q(train); pi,r=hurdle_parameters(train)
    candidates={"pooled_geometric":geometric_probability(q,len(policy)),"pooled_hurdle_geometric":hurdle_probability(pi,r,len(policy))}
    receipts=[]
    for name,p in candidates.items():
        receipts.append({"candidate":name,"policy_metrics":summary(fixed_components(y,p)),"probability_sum_max_residual":float(np.max(np.abs(p.sum(axis=1)-1.0)))})
    selected=min(receipts,key=lambda row:(row["policy_metrics"]["logloss"],row["candidate"]))["candidate"]
    return selected,receipts


def fit_selected(name, fit, n):
    if name=="pooled_geometric":
        q=geometric_q(fit)
        return geometric_probability(q,n), {"q":q,"full_support":"P(E=e)=(1-q)q^e, e>=0"}
    pi,r=hurdle_parameters(fit)
    return hurdle_probability(pi,r,n), {"pi_zero_excess":pi,"continuation":r,"full_support":"P(E=0)=pi; P(E=e>=1)=(1-pi)(1-r)r^(e-1)"}


def empirical_baseline(fit,test):
    pooled=np.bincount(np.minimum(fit.tail_excess.to_numpy(int),4),minlength=5).astype(float)
    prior=pooled/pooled.sum()*EMPIRICAL_PRIOR_MASS
    by={code:np.bincount(np.minimum(g.tail_excess.to_numpy(int),4),minlength=5).astype(float) for code,g in fit.groupby("source_code")}
    rows=[]
    for code in test.source_code:
        counts=by.get(code,pooled); v=counts+prior; rows.append(v/v.sum())
    return np.asarray(rows)


def cluster_bootstrap(meta, model, baseline):
    codes=meta.source_code.astype(str).to_numpy(); groups=sorted(np.unique(codes)); idx=[np.flatnonzero(codes==g) for g in groups]
    rng=np.random.default_rng(BOOTSTRAP_SEED); picks=rng.integers(0,len(groups),size=(BOOTSTRAPS,len(groups)))
    counts=np.asarray([len(i) for i in idx],float); denom=counts[picks].sum(axis=1)
    out={}
    for metric in model.columns:
        d=np.asarray([float((model.loc[i,metric]-baseline.loc[i,metric]).sum()) for i in idx])
        vals=d[picks].sum(axis=1)/denom
        better=vals<0 if metric in {"logloss","brier","rps"} else vals>0
        out[metric]={"mean_delta_model_minus_baseline":float(vals.mean()),"p05":float(np.quantile(vals,.05)),"p95":float(np.quantile(vals,.95)),"probability_model_better":float(better.mean())}
    return out


def survival(params, total_threshold):
    e=total_threshold-7
    if e<=0:return 1.0
    if "q" in params:return float(params["q"]**e)
    return float((1.0-params["pi_zero_excess"])*params["continuation"]**(e-1))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out",default="artifacts/c074k_extra16_tail_confirmation"); args=ap.parse_args()
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    raw,audit=load_all()
    duplicate=int(raw.duplicated(["source_code","date","Home","Away"]).sum())
    train_all=raw[raw.date<=TRAIN_END].copy(); policy_all=raw[raw.date.between(POLICY_START,POLICY_END)].copy(); test_all=raw[raw.date.between(TEST_START,TEST_END)].copy()
    train=train_all[train_all.total_goals>=7].copy(); policy=policy_all[policy_all.total_goals>=7].copy(); test=test_all[test_all.total_goals>=7].copy()
    train_sources=sorted(train_all.source_code.unique().tolist()); policy_sources=sorted(policy_all.source_code.unique().tolist()); test_sources=sorted(test_all.source_code.unique().tolist())
    test_bins=sorted(np.unique(np.minimum(test.tail_excess.to_numpy(int),4)).astype(int).tolist()) if len(test) else []
    first4=sum(b in test_bins for b in [0,1,2,3])
    coverage={
        "all_score_rows":{"train":int(len(train_all)),"policy":int(len(policy_all)),"test":int(len(test_all))},
        "tail_rows":{"train":int(len(train)),"policy":int(len(policy)),"test":int(len(test))},
        "train_score_sources":train_sources,"policy_score_sources":policy_sources,"test_score_sources":test_sources,
        "confirmation_bins_present":test_bins,"confirmation_first4_bins_present":first4,"duplicate_identity_rows_all_loaded":duplicate,
        "checks":{
            "train_score_sources_ge_12":len(train_sources)>=12,
            "policy_score_sources_ge_12":len(policy_sources)>=12,
            "test_score_sources_eq_16":len(test_sources)==16,
            "train_tail_rows_ge_300":len(train)>=300,
            "policy_tail_rows_ge_50":len(policy)>=50,
            "test_tail_rows_ge_80":len(test)>=80,
            "at_least_3_first4_confirmation_bins":first4>=3,
            "duplicate_identity_rows_eq_0":duplicate==0,
        },
    }
    if not all(coverage["checks"].values()):
        result={"experiment":"C074-K","contract":"research/C074K_CONTRACT.md","formal_weight":0,"terminal":"STOP_DATA/COVERAGE","coverage":coverage,"source_audit":audit,"sealed_assets_touched":{"C071_reserve_52180":0,"C070F_confirmation_1597":0,"A05":0,"protected":0}}
        (out/"summary.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8"); pd.DataFrame(audit).to_csv(out/"source_files.csv",index=False)
        print(json.dumps({"terminal":result["terminal"],"coverage":coverage},indent=2)); return

    selected,policy_receipts=select_law(train,policy); fit=pd.concat([train,policy],ignore_index=True)
    mp,params=fit_selected(selected,fit,len(test)); bp=empirical_baseline(fit,test); y=np.minimum(test.tail_excess.to_numpy(int),4)
    mc=fixed_components(y,mp).reset_index(drop=True); bc=fixed_components(y,bp).reset_index(drop=True); ms=summary(mc); bs=summary(bc); delta={k:ms[k]-bs[k] for k in ms}
    meta=test[["source_code"]].reset_index(drop=True); boot=cluster_bootstrap(meta,mc,bc)
    obs=np.bincount(y,minlength=5).astype(float); obs/=obs.sum(); pred=mp.mean(axis=0); cal=np.abs(pred-obs); max_cal=float(cal.max()); max_prob=float(np.max(np.abs(mp.sum(axis=1)-1.0)))
    per_source=[]
    for code,indexes in meta.groupby("source_code").groups.items():
        idx=np.asarray(list(indexes),int); m=summary(mc.loc[idx]); b=summary(bc.loc[idx]); per_source.append({"source_code":code,"tail_n":int(len(idx)),**{f"model_{k}":v for k,v in m.items()},**{f"baseline_{k}":v for k,v in b.items()},**{f"delta_{k}":m[k]-b[k] for k in m}})
    gate={
        "bootstrap90_upper_dlogloss_lt_0":boot["logloss"]["p95"]<0,
        "bootstrap90_upper_dbrier_lt_0":boot["brier"]["p95"]<0,
        "bootstrap90_upper_drps_lt_0":boot["rps"]["p95"]<0,
        "probability_conservation_le_1e_12":max_prob<=1e-12,
        "max_pooled_bin_calibration_residual_le_0_05":max_cal<=0.05,
    }
    passed=all(gate.values())
    exact_counts={str(t):int((test.total_goals==t).sum()) for t in sorted(test.total_goals.unique().astype(int))}
    result={
        "experiment":"C074-K","contract":"research/C074K_CONTRACT.md","formal_weight":0,
        "confirmation_domain":"Football-Data.co.uk extra16 calendar-2025",
        "candidate_family_origin":{"source":"V5.1 R1 frozen before this domain","laws":["pooled_geometric","pooled_hurdle_geometric"],"alpha":ALPHA,"evaluation_bins":["7","8","9","10","11+"],"empirical_prior_mass":EMPIRICAL_PRIOR_MASS},
        "chronology":{"train":"Date<2024-01-01","policy":"calendar 2024","test":"calendar 2025"},
        "coverage":coverage,"selected_law":selected,"policy_candidates":policy_receipts,"selected_parameters":params,
        "model_metrics":ms,"empirical_baseline_metrics":bs,"delta_model_minus_empirical":delta,
        "cluster_bootstrap_90":{"samples":BOOTSTRAPS,"seed":BOOTSTRAP_SEED,"by":"source_code","metrics":boot},
        "confirmation_exact_total_counts":exact_counts,"observed_eval_bin_distribution":obs.tolist(),"predicted_eval_bin_distribution":pred.tolist(),"absolute_bin_calibration_residuals":cal.tolist(),"max_abs_bin_calibration_residual":max_cal,
        "tail_survival":{"T>=12":survival(params,12),"T>=15":survival(params,15),"T>=20":survival(params,20),"T>=30":survival(params,30),"T>=60":survival(params,60)},
        "probability_sum_max_abs_residual":max_prob,"per_source":per_source,"gate_checks":gate,
        "terminal":"CONFIRMATION_PASS_EXACT_TAIL" if passed else "CONFIRMATION_FAIL_EXACT_TAIL_MATRIX_BLOCKED",
        "source_audit":audit,"sealed_assets_touched":{"C071_reserve_52180":0,"C070F_confirmation_1597":0,"A05":0,"protected":0},
        "boundary":"PASS confirms only exact 7+ tail disaggregation as a research component. Unified matrix and formal exact-score output remain separately gated."
    }
    (out/"summary.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8"); pd.DataFrame(per_source).to_csv(out/"source_tail_metrics.csv",index=False); pd.DataFrame(audit).to_csv(out/"source_files.csv",index=False)
    print(json.dumps({"terminal":result["terminal"],"coverage":coverage,"selected_law":selected,"selected_parameters":params,"model":ms,"baseline":bs,"delta":delta,"bootstrap":boot,"max_calibration_residual":max_cal,"gate":gate},indent=2))

if __name__=="__main__": main()
