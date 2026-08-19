#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

import evaluate_c072e2_ou25_movement_directt as e2
import run_c072h2_dgiven_t_development as fixed_h2

h2=fixed_h2.ns
SCHEMA="C072K2_JOINT_LOW_SCORE_CONFIRM_V1"
DIVS=["EC","T1","G1"]
EXPECTED_IDENTITIES=1094
URL="https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
BOOT_REPS=5000
SEED_HYBRID=72027
SEED_LOW=72028

CELLS=[]
for total in range(7):
    for home in range(total+1):
        CELLS.append((home,total-home))
TAIL_INDEX=len(CELLS)
assert TAIL_INDEX==28
CELL_INDEX={x:i for i,x in enumerate(CELLS)}
CELL_NAMES=[f"{h}-{a}" for h,a in CELLS]+["TAIL_7PLUS"]


def fetch(season,div):
    req=Request(URL.format(season=season,div=div),headers={"User-Agent":"Mozilla/5.0 football3-joint-confirm"})
    return urlopen(req,timeout=90).read()


def identity(raw,div):
    x=pd.read_csv(io.BytesIO(raw),usecols=["Date","HomeTeam","AwayTeam"])
    x["division"]=div; x["date"]=pd.to_datetime(x.Date,errors="coerce",dayfirst=True,utc=True)
    if x.date.isna().any(): raise RuntimeError(f"invalid date {div}")
    return x


def warmup(raw,div):
    x=pd.read_csv(io.BytesIO(raw),usecols=["Date","HomeTeam","AwayTeam","FTHG","FTAG"])
    x["division"]=div; x["date"]=pd.to_datetime(x.Date,errors="coerce",dayfirst=True,utc=True)
    x["FTHG"]=pd.to_numeric(x.FTHG,errors="coerce"); x["FTAG"]=pd.to_numeric(x.FTAG,errors="coerce")
    x=x.dropna(subset=["date","FTHG","FTAG"]).copy(); x["FTHG"]=x.FTHG.astype(int); x["FTAG"]=x.FTAG.astype(int)
    return x


def target(raw,div):
    cols=["Date","HomeTeam","AwayTeam","FTHG","FTAG","AvgH","AvgD","AvgA","Avg>2.5","Avg<2.5","AvgC>2.5","AvgC<2.5"]
    x=pd.read_csv(io.BytesIO(raw),usecols=cols)
    x["division"]=div; x["date"]=pd.to_datetime(x.Date,errors="coerce",dayfirst=True,utc=True)
    for c in ["FTHG","FTAG","AvgH","AvgD","AvgA","Avg>2.5","Avg<2.5","AvgC>2.5","AvgC<2.5"]: x[c]=pd.to_numeric(x[c],errors="coerce")
    return x


def mean_sd(vals):
    if not vals:return np.nan,np.nan
    a=np.asarray(vals,float); return float(a.mean()),float(a.std(ddof=0))


def hf():return {"gf":[],"ga":[]}


def prof(h):
    gfm,gfs=mean_sd(h["gf"]); gam,gas=mean_sd(h["ga"])
    return len(h["gf"]),gfm,gfs,gam,gas


def logit(p):
    p=float(np.clip(p,1e-8,1-1e-8)); return math.log(p/(1-p))


def market_features(r):
    vals=[r["AvgH"],r["AvgD"],r["AvgA"],r["Avg>2.5"],r["Avg<2.5"],r["AvgC>2.5"],r["AvgC<2.5"]]
    if not all(np.isfinite(vals)) or not all(float(v)>1 for v in vals): return None
    p=np.asarray([1/r["AvgH"],1/r["AvgD"],1/r["AvgA"]],float); p=p/p.sum(); p=np.clip(p,1e-12,1)
    os=float(np.log(p[0]/p[2])); od=float(np.log(p[1]/math.sqrt(p[0]*p[2])))
    po=(1/r["Avg>2.5"])/((1/r["Avg>2.5"])+(1/r["Avg<2.5"])); pc=(1/r["AvgC>2.5"])/((1/r["AvgC>2.5"])+(1/r["AvgC<2.5"]))
    ol=logit(po); ml=logit(pc)-ol
    return os,od,ol,ml


def initialize(w):
    th=defaultdict(hf); ct=defaultdict(list)
    for _,r in w.sort_values(["date","division","HomeTeam","AwayTeam"]).iterrows():
        d=str(r.division); hk=(d,str(r.HomeTeam)); ak=(d,str(r.AwayTeam)); hg=int(r.FTHG); ag=int(r.FTAG)
        th[hk]["gf"].append(hg); th[hk]["ga"].append(ag); th[ak]["gf"].append(ag); th[ak]["ga"].append(hg); ct[d].append(hg+ag)
    return th,ct


def build_rows(w,t):
    th,ct=initialize(w); rows=[]
    t=t.sort_values(["date","division","HomeTeam","AwayTeam"]).reset_index(drop=True)
    for date,g in t.groupby("date",sort=True):
        for _,r in g.iterrows():
            if pd.isna(r.FTHG) or pd.isna(r.FTAG): continue
            mf=market_features(r)
            d=str(r.division); hk=(d,str(r.HomeTeam)); ak=(d,str(r.AwayTeam)); hn,hfm,hfs,ham,has=prof(th[hk]); an,afm,afs,aam,aas=prof(th[ak]); cm,cs=mean_sd(ct[d])
            if mf is None: os=od=ol=ml=np.nan
            else: os,od,ol,ml=mf
            hg=int(r.FTHG); ag=int(r.FTAG)
            rows.append({"date":r.date,"division":d,"home":str(r.HomeTeam),"away":str(r.AwayTeam),"home_goals":hg,"away_goals":ag,"total":hg+ag,
             "competition_total_mean":cm,"competition_total_sd":cs,
             "home_goals_for_mean":hfm,"home_goals_for_sd":hfs,"home_goals_against_mean":ham,"home_goals_against_sd":has,
             "away_goals_for_mean":afm,"away_goals_for_sd":afs,"away_goals_against_mean":aam,"away_goals_against_sd":aas,
             "log1p_home_result_history_n":math.log1p(hn),"log1p_away_result_history_n":math.log1p(an),
             "open_strength":os,"open_drawness":od,"open_logit":ol,"movement_logit":ml,
             "eligible":hn>=8 and an>=8 and all(np.isfinite([os,od,ol,ml]))})
        for _,r in g.iterrows():
            if pd.isna(r.FTHG) or pd.isna(r.FTAG): continue
            d=str(r.division); hk=(d,str(r.HomeTeam)); ak=(d,str(r.AwayTeam)); hg=int(r.FTHG); ag=int(r.FTAG)
            th[hk]["gf"].append(hg); th[hk]["ga"].append(ag); th[ak]["gf"].append(ag); th[ak]["ga"].append(hg); ct[d].append(hg+ag)
    return pd.DataFrame(rows)


def generic_metrics(y,p):
    y=np.asarray(y,int); idx=np.arange(len(y)); one=np.zeros_like(p); one[idx,y]=1.0
    ll=-np.log(np.clip(p[idx,y],1e-15,1)); br=np.sum((p-one)**2,axis=1); rank=np.argsort(-p,axis=1); top1=(rank[:,0]==y).astype(float); k=min(3,p.shape[1]); top3=np.asarray([float(y[i] in rank[i,:k]) for i in range(len(y))]); actual_rank=np.asarray([int(np.flatnonzero(rank[i]==y[i])[0])+1 for i in range(len(y))],float)
    return {"ll":ll,"brier":br,"top1":top1,"top3":top3,"rank":actual_rank,"pred":rank[:,0]}


def summarize(m):
    return {"n":int(len(m["ll"])),"log_loss":float(np.mean(m["ll"])),"brier":float(np.mean(m["brier"])),"top1":float(np.mean(m["top1"])),"top3":float(np.mean(m["top3"])),"mean_rank":float(np.mean(m["rank"]))}


def delta(a,b):return {k:float(a[k]-b[k]) for k in ["log_loss","brier","top1","top3","mean_rank"]}


def boot(d,seed):
    d=np.asarray(d,float); rng=np.random.default_rng(seed); n=len(d); sims=np.empty(BOOT_REPS)
    for i in range(BOOT_REPS): sims[i]=float(d[rng.integers(0,n,n)].mean())
    return {"n":int(n),"reps":BOOT_REPS,"seed":seed,"mean_delta":float(d.mean()),"ci90_low":float(np.quantile(sims,.05)),"ci90_high":float(np.quantile(sims,.95)),"p_lt_zero":float(np.mean(sims<0))}


def score_diag(names,y,pred):
    out={}
    for target_name in ["0-0","1-1"]:
        j=names.index(target_name); calls=int(np.sum(pred==j)); actual=int(np.sum(y==j)); correct=int(np.sum((pred==j)&(y==j)))
        out[target_name]={"calls":calls,"call_rate":calls/len(y) if len(y) else None,"actual":actual,"correct":correct,"precision":correct/calls if calls else None,"recall":correct/actual if actual else None}
    counts=Counter(names[int(i)] for i in pred)
    out["top1_call_counts"]=dict(counts.most_common())
    return out


def main():
    # Fit frozen P(T) models on historical development domain only.
    pt_frame,_=e2.load_source(); pt_feat=e2.build_rows(pt_frame); pt_train=pt_feat[pt_feat.eligible].copy(); ypt=pt_train.target.to_numpy(int)
    pt_ref=e2.pipeline(); pt_both=e2.pipeline(); pt_ref.fit(pt_train[e2.REF],ypt); pt_both.fit(pt_train[e2.CAND],ypt)

    # Fit frozen D|T Model A + portable empirical baseline on historical H2 domain only.
    d_frame=h2["load_source"](); d_feat=h2["build_rows"](d_frame); d_train=d_feat[d_feat.eligible & d_feat["T"].isin([1,2,3,4,5,6])].copy(); d_tabs=h2["baseline_tables"](d_train); d_models={}
    for total in range(1,7):
        tr=d_train[d_train["T"]==total].copy(); m=h2["pipe"](); m.fit(tr[h2["OPEN"]],tr.H.to_numpy(int)); d_models[total]=m

    # Identity gate before target opening.
    raws={}; ids=[]
    for div in DIVS:
        raw=fetch("2526",div); raws[div]=raw; ids.append(identity(raw,div))
    ident=pd.concat(ids,ignore_index=True); dup=int(ident[["division","Date","HomeTeam","AwayTeam"]].astype(str).duplicated().sum())
    if len(ident)!=EXPECTED_IDENTITIES or dup!=0:
        out={"schema":SCHEMA,"terminal":"STOP_K2_IDENTITY_DRIFT","raw_identities":int(len(ident)),"duplicates":dup,"boundary":{"J2_target_values_opened":False,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"formal_weight":0}}
        Path("football-data/research/c072k2_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n"); print(json.dumps(out,ensure_ascii=False,indent=2)); return

    # Contract frozen: first target opening.
    w=pd.concat([warmup(fetch("2425",d),d) for d in DIVS],ignore_index=True)
    t=pd.concat([target(raws[d],d) for d in DIVS],ignore_index=True)
    missing=int(t[["FTHG","FTAG"]].isna().any(axis=1).sum()); rows=build_rows(w,t); z=rows[rows.eligible].copy().sort_values(["date","division","home","away"]).reset_index(drop=True)
    div_counts=z.groupby("division").size().to_dict(); low_count=int((z.total<=6).sum())
    coverage=bool(len(z)>=800 and low_count>=740 and all(div_counts.get(d,0)>=150 for d in DIVS))
    if not coverage:
        out={"schema":SCHEMA,"terminal":"STOP_K2_COVERAGE","raw_identities":int(len(ident)),"missing_target_rows":missing,"eligible_hybrid_rows":int(len(z)),"eligible_low_rows":low_count,"division_counts":div_counts,"boundary":{"J2_target_values_opened":True,"target_rows_first_parsed":int(len(t)),"replacement_rows":0,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"formal_weight":0}}
        Path("football-data/research/c072k2_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str)+"\n"); print(json.dumps(out,ensure_ascii=False,indent=2,default=str)); return

    pref=e2.predict8(pt_ref,z[e2.REF]); pcand=e2.predict8(pt_both,z[e2.CAND]); dbase={}; dcand={}; max_resid=max(float(np.max(np.abs(pref.sum(1)-1))),float(np.max(np.abs(pcand.sum(1)-1))))
    for total in range(1,7):
        dbase[total]=np.tile(d_tabs[total],(len(z),1)); dcand[total]=h2["support_predict"](d_models[total],z[h2["OPEN"]],total)
        max_resid=max(max_resid,float(np.max(np.abs(dcand[total].sum(1)-1))))

    def joint(pt,dtype):
        p=np.zeros((len(z),29),float); p[:,CELL_INDEX[(0,0)]]=pt[:,0]
        for total in range(1,7):
            dd=dbase[total] if dtype=="emp" else dcand[total]
            for home in range(total+1): p[:,CELL_INDEX[(home,total-home)]]=pt[:,total]*dd[:,home]
        p[:,TAIL_INDEX]=pt[:,7]
        return p/p.sum(1,keepdims=True)

    mats={"BASE":joint(pref,"emp"),"PT_ONLY":joint(pcand,"emp"),"D_ONLY":joint(pref,"cand"),"BOTH":joint(pcand,"cand")}
    max_resid=max(max_resid,max(float(np.max(np.abs(p.sum(1)-1))) for p in mats.values()))
    yhy=np.asarray([CELL_INDEX[(int(r.home_goals),int(r.away_goals))] if int(r.total)<=6 else TAIL_INDEX for _,r in z.iterrows()],int)
    hy={k:generic_metrics(yhy,p) for k,p in mats.items()}; hys={k:summarize(v) for k,v in hy.items()}

    low=z.total.to_numpy(int)<=6; zlow=z.loc[low].reset_index(drop=True); ylow=np.asarray([CELL_INDEX[(int(r.home_goals),int(r.away_goals))] for _,r in zlow.iterrows()],int)
    lowm={}
    for k,p in mats.items():
        q=p[low,:28].copy(); q=q/q.sum(1,keepdims=True); lowm[k]=generic_metrics(ylow,q)
    lows={k:summarize(v) for k,v in lowm.items()}

    dh=delta(hys["BOTH"],hys["BASE"]); dl=delta(lows["BOTH"],lows["BASE"])
    bh=boot(hy["BOTH"]["ll"]-hy["BASE"]["ll"],SEED_HYBRID); bl=boot(lowm["BOTH"]["ll"]-lowm["BASE"]["ll"],SEED_LOW)

    # Frozen robustness on hybrid space.
    work=z[["date","division"]].copy(); work["dll"]=hy["BOTH"]["ll"]-hy["BASE"]["ll"]; cut=len(work)//2
    halves={"early":{"n":int(cut),"dlogloss":float(work.dll.iloc[:cut].mean())},"late":{"n":int(len(work)-cut),"dlogloss":float(work.dll.iloc[cut:].mean())}}
    by_div={d:{"n":int(len(g)),"dlogloss":float(g.dll.mean())} for d,g in work.groupby("division",sort=True)}; div_wins=sum(v["dlogloss"]<0 for v in by_div.values())

    diag={"BASE":score_diag(CELL_NAMES[:28],ylow,lowm["BASE"]["pred"]),"PT_ONLY":score_diag(CELL_NAMES[:28],ylow,lowm["PT_ONLY"]["pred"]),"D_ONLY":score_diag(CELL_NAMES[:28],ylow,lowm["D_ONLY"]["pred"]),"BOTH":score_diag(CELL_NAMES[:28],ylow,lowm["BOTH"]["pred"])}
    ablations={k:{"hybrid":hys[k],"hybrid_minus_BASE":delta(hys[k],hys["BASE"]),"low_score":lows[k],"low_score_minus_BASE":delta(lows[k],lows["BASE"])} for k in ["PT_ONLY","D_ONLY","BOTH"]}

    gate=bool(dh["log_loss"]<0 and bh["ci90_high"]<0 and dh["brier"]<=0 and dl["log_loss"]<0 and bl["ci90_high"]<0 and dl["brier"]<=0 and dl["top1"]>=0 and halves["early"]["dlogloss"]<0 and halves["late"]["dlogloss"]<0 and div_wins>=2 and max_resid<=1e-10)
    out={"schema":SCHEMA,"terminal":"C072K2_JOINT_LOW_SCORE_CONFIRMATION_PASS" if gate else "C072K2_JOINT_LOW_SCORE_CONFIRMATION_FAIL_PARK","identity_gate_pass":True,"coverage_pass":True,"raw_identities":int(len(ident)),"missing_target_rows":missing,"replacement_rows":0,"eligible_hybrid_rows":int(len(z)),"eligible_low_score_rows":int(low_count),"hybrid_29_state":{"BASE":hys["BASE"],"BOTH":hys["BOTH"],"BOTH_minus_BASE":dh,"bootstrap":bh},"conditional_28_score":{"BASE":lows["BASE"],"BOTH":lows["BOTH"],"BOTH_minus_BASE":dl,"bootstrap":bl},"ablations":ablations,"score_top1_diagnostics":diag,"chronological_halves_hybrid":halves,"by_division_hybrid":by_div,"division_logloss_wins":int(div_wins),"max_probability_sum_residual":max_resid,"confirmation_gate":gate,"boundary":{"J2_target_values_opened":True,"target_rows_first_parsed":int(len(t)),"component_coefficients_refit_on_J2":False,"replacement_rows":0,"TAIL_7PLUS_remains_aggregate":True,"T_ge_7_exact_scores_resolved":False,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"protected_opened":False,"formal_weight":0,"full_unified_exact_score_matrix_generated":False}}
    Path("football-data/research/c072k2_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str)+"\n"); print(json.dumps(out,ensure_ascii=False,indent=2,default=str))

if __name__=="__main__": main()
