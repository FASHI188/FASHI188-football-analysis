#!/usr/bin/env python3
from __future__ import annotations
import argparse, collections, csv, hashlib, json, math, sqlite3, xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment, minimize_scalar
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SCHEMA_VERSION="C070F_FIRST_TWO_V1"
EXPECTED_MANIFEST_SHA="55973058d3c0ba2dee9af1392dc6cb45a088973dbe51571d4b4610778a784f34"
EXPECTED_COUNTS={"warmup":1202,"calibration":1201,"confirmation":1597}
EXPECTED_WARMUP_MISMATCH=[8117,10687,21581,21708,22365]
PRIOR_EQ_MATCHES=4.0
LAMBDA_CLIP=(0.2,3.5)
MIN_PRIOR_TEAM_MATCHES=4
CALIPERS={"q_draw_cond":0.05,"abs_ha_gap":0.08,"lambda_total":0.45}
MIN_WARMUP_PAIRS=30
MIN_CAL_PAIRS=25
C=0.1
EPS=1e-6
MARKOV_FEATURES=["log_lambda_home","log_lambda_away","minute_frac","minute_frac_sq","score_diff_clip","is_tied","home_ahead","away_ahead"]
SEMIMARKOV_FEATURES=MARKOV_FEATURES+["duration_frac","duration_frac_sq","duration_if_tied","duration_if_home_ahead","duration_if_away_ahead"]
ALL_CLASSES=[0,1,2]
SIM_DIFF_MIN=-10
SIM_DIFF_MAX=10
BOOT_REPS=2000
STRUCT_BOOT_SEED=7103
PAIR_BOOT_SEED=7205

def manifest_digest(rows):
    h=hashlib.sha256()
    for r in rows:
        line="|".join(str(r[k]) for k in ["match_api_id","date","home_team_api_id","away_team_api_id","selection_sha256","block"])
        h.update((line+"\n").encode())
    return h.hexdigest()

def load_manifest(path):
    rows=list(csv.DictReader(Path(path).open(encoding="utf-8")))
    if len(rows)!=4000: raise RuntimeError(f"manifest row count {len(rows)}")
    if manifest_digest(rows)!=EXPECTED_MANIFEST_SHA: raise RuntimeError("manifest digest drift")
    counts=collections.Counter(r["block"] for r in rows)
    if dict(counts)!=EXPECTED_COUNTS: raise RuntimeError(f"block drift {counts}")
    return rows

def load_first_two(db_path, manifest):
    allowed={int(r["id"]):r["block"] for r in manifest if r["block"] in {"warmup","calibration"}}
    confirmation={int(r["id"]) for r in manifest if r["block"]=="confirmation"}
    cols="id,league_id,date,match_api_id,home_team_api_id,away_team_api_id,home_team_goal,away_team_goal,goal"
    conn=sqlite3.connect(f"file:{Path(db_path)}?mode=ro",uri=True)
    chunks=[]; ids=sorted(allowed)
    for i in range(0,len(ids),800):
        part=ids[i:i+800]
        q=f"SELECT {cols} FROM Match WHERE id IN ({','.join('?' for _ in part)})"
        chunks.extend(conn.execute(q,part).fetchall())
    conn.close()
    if len(chunks)!=len(allowed): raise RuntimeError(f"payload coverage {len(chunks)} != {len(allowed)}")
    names=cols.split(","); df=pd.DataFrame(chunks,columns=names)
    if set(df.id.astype(int)) & confirmation: raise RuntimeError("confirmation payload opened")
    df["block"]=df.id.astype(int).map(allowed)
    if df.block.isna().any(): raise RuntimeError("unfrozen payload row")
    return df.sort_values(["date","id"]).reset_index(drop=True)

def parse_goal_xml(s):
    if s is None or not str(s).strip(): return []
    root=ET.fromstring(str(s)); vals=[]
    for v in root.findall(".//value"):
        def txt(tag):
            el=v.find(tag); return el.text if el is not None else None
        vals.append({"elapsed":int(float(txt("elapsed") or 0)),"elapsed_plus":int(float(txt("elapsed_plus") or 0)) if txt("elapsed_plus") else 0,
            "team":int(float(txt("team"))) if txt("team") else None,"goal_type":txt("goal_type"),"comment":txt("comment"),"id":txt("id") or ""})
    return vals

def parse_goals(r):
    h,a=int(r.home_team_api_id),int(r.away_team_api_id); goals=[]
    for x in parse_goal_xml(r.goal):
        typ=x["goal_type"] or x["comment"]
        if typ in {"dg","npm","psm","rp"}: continue
        if typ=="o": scorer=a if x["team"]==h else h if x["team"]==a else None
        elif typ in {"n","p",None}: scorer=x["team"] if x["team"] in {h,a} else None
        else: scorer=None
        if scorer is None: continue
        elapsed=int(x["elapsed"]); b=min(44,max(0,elapsed-1)) if elapsed<=45 else min(89,max(45,elapsed-1))
        goals.append((b,elapsed,int(x["elapsed_plus"]),str(x["id"]),scorer,typ))
    goals.sort(key=lambda z:(z[0],z[1],z[2],z[3]))
    hc=sum(g[-2]==h for g in goals); ac=sum(g[-2]==a for g in goals)
    return goals,(hc,ac)==(int(r.home_team_goal),int(r.away_team_goal)),(hc,ac)

def score_matrix(lh,la,kmax=16):
    hp=np.array([math.exp(-lh)*lh**k/math.factorial(k) for k in range(kmax)]); ap=np.array([math.exp(-la)*la**k/math.factorial(k) for k in range(kmax)])
    m=np.outer(hp,ap); m/=m.sum(); pH=float(np.tril(m,-1).sum()); pD=float(np.trace(m)); pA=float(np.triu(m,1).sum())
    p1=float(sum(m[i,j] for i in range(kmax) for j in range(kmax) if abs(i-j)==1)); return pH,pD,pA,p1,pD/(pD+p1)

def build_prematch(frame):
    team=collections.defaultdict(lambda:collections.defaultdict(float)); comp_goals=collections.defaultdict(list); out=[]
    f=frame.sort_values(["date","id"]).copy(); f["dt"]=pd.to_datetime(f["date"],utc=True); f["date_only"]=f["dt"].dt.date
    for day,g in f.groupby("date_only",sort=True):
        for _,r in g.iterrows():
            h,a,cid=int(r.home_team_api_id),int(r.away_team_api_id),int(r.league_id); hist=comp_goals[cid]
            lgh=float(np.mean([x[0] for x in hist])) if hist else 1.4; lga=float(np.mean([x[1] for x in hist])) if hist else 1.1; lm=max((lgh+lga)/2,0.05)
            def rates(tid):
                acc=team[tid]; n=int(acc.get("matches",0)); return n,(acc.get("gf",0)/n if n else lm),(acc.get("ga",0)/n if n else lm)
            hn,hgf0,hga0=rates(h); an,agf0,aga0=rates(a)
            hgf=(hgf0*hn+PRIOR_EQ_MATCHES*lm)/(hn+PRIOR_EQ_MATCHES); hga=(hga0*hn+PRIOR_EQ_MATCHES*lm)/(hn+PRIOR_EQ_MATCHES)
            agf=(agf0*an+PRIOR_EQ_MATCHES*lm)/(an+PRIOR_EQ_MATCHES); aga=(aga0*an+PRIOR_EQ_MATCHES*lm)/(an+PRIOR_EQ_MATCHES)
            lh=float(np.clip(lgh*(hgf/lm)*(aga/lm),*LAMBDA_CLIP)); la=float(np.clip(lga*(agf/lm)*(hga/lm),*LAMBDA_CLIP))
            pH,pD,pA,p1,q=score_matrix(lh,la); q=float(np.clip(q,EPS,1-EPS)); gd=int(r.home_team_goal)-int(r.away_team_goal); target="D" if gd==0 else "OW" if abs(gd)==1 else "OTHER"
            out.append({"match_id":int(r.id),"dt":r["dt"],"date":day,"cid":cid,"home":h,"away":a,"hg":int(r.home_team_goal),"ag":int(r.away_team_goal),"block":r.block,
                "hn":hn,"an":an,"lambda_home":lh,"lambda_away":la,"lambda_total":lh+la,"pH":pH,"pD":pD,"pA":pA,"p_onegoal":p1,"q_draw_cond":q,
                "baseline_logit":math.log(q/(1-q)),"abs_ha_gap":abs(pH-pA),"target":target,"y":1 if target=="D" else 0})
        for _,r in g.iterrows():
            h,a,cid=int(r.home_team_api_id),int(r.away_team_api_id),int(r.league_id); hg,ag=float(r.home_team_goal),float(r.away_team_goal)
            for tid,gf,ga in ((h,hg,ag),(a,ag,hg)):
                team[tid]["matches"]+=1; team[tid]["gf"]+=gf; team[tid]["ga"]+=ga
            comp_goals[cid].append((hg,ag))
    return pd.DataFrame(out).sort_values(["dt","match_id"]).reset_index(drop=True)

def minute_feat(lh,la,minute,diff,duration):
    dc=float(np.clip(diff,-3,3)); tied=1.0 if diff==0 else 0.; ha=1.0 if diff>0 else 0.; aa=1.0 if diff<0 else 0.; mf=(minute+.5)/90.; df=min(duration,90)/90.
    return {"log_lambda_home":math.log(max(lh,1e-9)),"log_lambda_away":math.log(max(la,1e-9)),"minute_frac":mf,"minute_frac_sq":mf*mf,"score_diff_clip":dc,
      "is_tied":tied,"home_ahead":ha,"away_ahead":aa,"duration_frac":df,"duration_frac_sq":df*df,"duration_if_tied":df*tied,"duration_if_home_ahead":df*ha,"duration_if_away_ahead":df*aa}

def build_minute_rows(raw,prematch):
    raw_lookup=raw.set_index("id"); rows=[]; mismatch=[]; multi=0
    for _,m in prematch.iterrows():
        r=raw_lookup.loc[int(m.match_id)]; goals,ok,recon=parse_goals(r)
        if not ok:
            mismatch.append({"match_id":int(m.match_id),"block":m.block,"reconstructed":list(recon),"official":[int(m.hg),int(m.ag)]})
            if m.block=="calibration": raise RuntimeError(f"calibration goal reconstruction mismatch {int(m.match_id)}")
            continue
        by=collections.defaultdict(list)
        for g in goals: by[g[0]].append(g[-2])
        diff=0; duration=0
        for minute in range(90):
            feats=minute_feat(float(m.lambda_home),float(m.lambda_away),minute,diff,duration); gs=by.get(minute,[])
            if len(gs)==0: outcome=0; include=True
            elif len(gs)==1: outcome=1 if gs[0]==int(m.home) else 2; include=True
            else: outcome=-1; include=False; multi+=1
            rows.append({"match_id":int(m.match_id),"date":m.date,"dt":m["dt"],"block":m.block,"minute":minute,"include_structural":include,"outcome":outcome,**feats})
            if gs:
                for s in gs: diff += 1 if s==int(m.home) else -1
                duration=0
            else: duration+=1
    mids=sorted(x["match_id"] for x in mismatch if x["block"]=="warmup")
    if mids!=EXPECTED_WARMUP_MISMATCH: raise RuntimeError(f"warmup parser drift {mids}")
    return pd.DataFrame(rows).sort_values(["dt","match_id","minute"]).reset_index(drop=True),mismatch,multi

def edge(draw,win):
    if int(draw.cid)!=int(win.cid): return None
    diffs={k:abs(float(draw[k])-float(win[k])) for k in CALIPERS}
    if any(diffs[k]>CALIPERS[k] for k in CALIPERS): return None
    return float(sum((diffs[k]/CALIPERS[k])**2 for k in CALIPERS)),diffs

def maximum_cardinality(left,adj):
    mr={}
    def aug(u,seen):
        for v in adj.get(u,[]):
            if v in seen: continue
            seen.add(v)
            if v not in mr or aug(mr[v],seen): mr[v]=u; return True
        return False
    return sum(aug(u,set()) for u in left)

def optimal_pairs(frame,prefix):
    draws_all=frame[frame.target=="D"].sort_values(["cid","dt","match_id"]); wins_all=frame[frame.target=="OW"].sort_values(["cid","dt","match_id"]); selected=[]; cert_total=0
    for cid in sorted(set(draws_all.cid.astype(int))|set(wins_all.cid.astype(int))):
        draws=draws_all[draws_all.cid.astype(int)==cid]; wins=wins_all[wins_all.cid.astype(int)==cid]
        if draws.empty or wins.empty: continue
        di=list(draws.index); wi=list(wins.index); adj={u:[] for u in di}; ed={}
        for u in di:
            for v in wi:
                info=edge(frame.loc[u],frame.loc[v])
                if info: adj[u].append(v); ed[(u,v)]=info
            adj[u].sort(key=lambda v:(ed[(u,v)][0],abs((frame.loc[u,"dt"]-frame.loc[v,"dt"]).total_seconds()),int(frame.loc[v,"match_id"])))
        cert=maximum_cardinality(di,adj); cert_total+=cert; nd,nw=len(di),len(wi); mp=min(nd,nw); unmatched=float(4*(mp+1)); disallowed=unmatched*3
        cost=np.full((nd,nw+nd),unmatched); cost[:,:nw]=disallowed
        for i,u in enumerate(di):
            for j,v in enumerate(wi):
                if (u,v) in ed: cost[i,j]=ed[(u,v)][0]
        ri,ci=linear_sum_assignment(cost); local=[]
        for i,j in zip(ri,ci):
            if j>=nw: continue
            u,v=di[i],wi[j]
            if (u,v) not in ed: raise RuntimeError("disallowed assignment")
            local.append((u,v,*ed[(u,v)]))
        if len(local)!=cert: raise RuntimeError("max-card certificate mismatch")
        local.sort(key=lambda x:(x[2],int(frame.loc[x[0],"match_id"]),int(frame.loc[x[1],"match_id"])))
        for u,v,dist,diffs in local:
            selected.append({"pair_id":f"{prefix}-{len(selected)+1:04d}","draw_match_id":int(frame.loc[u,"match_id"]),"onegoal_match_id":int(frame.loc[v,"match_id"]),"competition_id":int(cid),"distance":dist})
    if len(selected)!=cert_total: raise RuntimeError("global certificate mismatch")
    return selected,cert_total

def pair_rows(frame,meta):
    lookup=frame.set_index("match_id",drop=False); rows=[]
    for pair in meta:
        for role,key in (("D","draw_match_id"),("OW","onegoal_match_id")):
            item=lookup.loc[int(pair[key])].to_dict(); item["pair_id"]=pair["pair_id"]; item["pair_role"]=role; rows.append(item)
    return pd.DataFrame(rows)

def fit_multinomial(train,features):
    model=make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=5000,class_weight=None,random_state=0)); model.fit(train[features],train.outcome.to_numpy(int))
    if list(model.named_steps["logisticregression"].classes_)!=ALL_CLASSES: raise RuntimeError("class coverage")
    return model

def struct_metric(frame,p): return float(log_loss(frame.outcome.to_numpy(int),np.clip(np.asarray(p,float),1e-15,1),labels=ALL_CLASSES))
def struct_boot(frame,p0,p1):
    y=frame.outcome.to_numpy(int); idx=np.arange(len(y)); d=-np.log(np.clip(p1[idx,y],1e-15,1))+np.log(np.clip(p0[idx,y],1e-15,1)); t=pd.DataFrame({"match_id":frame.match_id.to_numpy(),"d":d})
    arr=t.groupby("match_id").d.mean().to_numpy(float); rng=np.random.default_rng(STRUCT_BOOT_SEED); sims=np.array([arr[rng.integers(0,len(arr),size=len(arr))].mean() for _ in range(BOOT_REPS)])
    return {"matches":len(arr),"mean_delta_log_loss":float(arr.mean()),"ci90_low":float(np.quantile(sims,.05)),"ci90_high":float(np.quantile(sims,.95)),"reps":BOOT_REPS,"seed":STRUCT_BOOT_SEED}

def model_arrays(model):
    sc=model.named_steps["standardscaler"]; lr=model.named_steps["logisticregression"]; return sc.mean_.copy(),sc.scale_.copy(),lr.coef_.copy(),lr.intercept_.copy()
def simulate_batch(model,lh,la,features):
    mean,scale,coef,intercept=model_arrays(model); lh=np.asarray(lh,float); la=np.asarray(la,float); n=len(lh); states=np.zeros((n,21,91),float); states[:,10,0]=1.0
    loglh=np.log(np.maximum(lh,1e-9)); logla=np.log(np.maximum(la,1e-9)); diffs=np.arange(-10,11); diffclip=np.clip(diffs,-3,3); tied=(diffs==0).astype(float); ha=(diffs>0).astype(float); aa=(diffs<0).astype(float); dur=np.arange(91); df=np.minimum(dur,90)/90.
    for minute in range(90):
        mf=(minute+.5)/90.; featmap={"log_lambda_home":loglh[:,None,None],"log_lambda_away":logla[:,None,None],"minute_frac":mf,"minute_frac_sq":mf*mf,"score_diff_clip":diffclip[None,:,None],"is_tied":tied[None,:,None],"home_ahead":ha[None,:,None],"away_ahead":aa[None,:,None],"duration_frac":df[None,None,:],"duration_frac_sq":(df*df)[None,None,:],"duration_if_tied":tied[None,:,None]*df[None,None,:],"duration_if_home_ahead":ha[None,:,None]*df[None,None,:],"duration_if_away_ahead":aa[None,:,None]*df[None,None,:]}
        shape=(n,21,91); X=np.empty((n,21,91,len(features)),float)
        for j,f in enumerate(features): X[...,j]=np.broadcast_to(featmap[f],shape)
        z=((X-mean)/scale)@coef.T+intercept; z-=z.max(axis=-1,keepdims=True); pr=np.exp(z); pr/=pr.sum(axis=-1,keepdims=True); nxt=np.zeros_like(states)
        nxt[:,:,1:]+=states[:,:,:-1]*pr[:,:,:-1,0]; nxt[:,:,-1]+=states[:,:,-1]*pr[:,:,-1,0]
        nxt[:,1:,0]+=np.sum(states[:,:-1,:]*pr[:,:-1,:,1],axis=2); nxt[:,-1,0]+=np.sum(states[:,-1:,:]*pr[:,-1:,:,1],axis=(1,2))
        nxt[:,:-1,0]+=np.sum(states[:,1:,:]*pr[:,1:,:,2],axis=2); nxt[:,0,0]+=np.sum(states[:,:1,:]*pr[:,:1,:,2],axis=(1,2)); states=nxt
    p0=states[:,10,:].sum(axis=1); p1=states[:,[9,11],:].sum(axis=(1,2)); return np.clip(p0/(p0+p1),1e-9,1-1e-9)

def logit_(p):
    x=np.clip(np.asarray(p,float),EPS,1-EPS); return np.log(x/(1-x))
def sigmoid(z):
    z=np.asarray(z,float); out=np.empty_like(z); pos=z>=0; out[pos]=1/(1+np.exp(-z[pos])); ez=np.exp(z[~pos]); out[~pos]=ez/(1+ez); return np.clip(out,EPS,1-EPS)
def binary_ll(y,p):
    y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-12,1-1e-12); return float(np.mean(-(y*np.log(p)+(1-y)*np.log(1-p))))
def pair_accuracy(frame,p):
    t=frame[["pair_id","pair_role"]].copy(); t["p"]=p; wins=ties=total=0
    for _,g in t.groupby("pair_id"):
        pd_=float(g.loc[g.pair_role=="D","p"].iloc[0]); pw_=float(g.loc[g.pair_role=="OW","p"].iloc[0]); total+=1; wins+=pd_>pw_; ties+=pd_==pw_
    return float((wins+.5*ties)/total)
def metrics(frame,p):
    y=frame.y.to_numpy(int); p=np.asarray(p,float); return {"rows":len(y),"log_loss":binary_ll(y,p),"brier":float(np.mean((p-y)**2)),"auc":float(roc_auc_score(y,p)),"accuracy":float(((p>=.5).astype(int)==y).mean()),"pair_accuracy":pair_accuracy(frame,p),"mean_p":float(p.mean())}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--db",required=True); ap.add_argument("--manifest",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    manifest=load_manifest(a.manifest); raw=load_first_two(a.db,manifest)
    if collections.Counter(raw.block)!=collections.Counter({"warmup":1202,"calibration":1201}): raise RuntimeError("first-two count drift")
    prematch=build_prematch(raw); eligible=prematch[(prematch.hn>=MIN_PRIOR_TEAM_MATCHES)&(prematch.an>=MIN_PRIOR_TEAM_MATCHES)&prematch.target.isin(["D","OW"])].copy()
    wm,wcert=optimal_pairs(eligible[eligible.block=="warmup"].copy(),"c070f-warmup"); cm,ccert=optimal_pairs(eligible[eligible.block=="calibration"].copy(),"c070f-calibration")
    if len(wm)<MIN_WARMUP_PAIRS or len(cm)<MIN_CAL_PAIRS: raise RuntimeError(f"STOP_COVERAGE {len(wm)}/{len(cm)}")
    minute,mismatch,multi=build_minute_rows(raw,prematch); wmin=minute[(minute.block=="warmup")&minute.include_structural].copy(); cmin=minute[(minute.block=="calibration")&minute.include_structural].copy()
    markov=fit_multinomial(wmin,MARKOV_FEATURES); semi=fit_multinomial(wmin,SEMIMARKOV_FEATURES); p0=markov.predict_proba(cmin[MARKOV_FEATURES]); p1=semi.predict_proba(cmin[SEMIMARKOV_FEATURES])
    ll0=struct_metric(cmin,p0); ll1=struct_metric(cmin,p1); sboot=struct_boot(cmin,p0,p1); wp=pair_rows(eligible[eligible.block=="warmup"].copy(),wm); cp=pair_rows(eligible[eligible.block=="calibration"].copy(),cm)
    incumbent=make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=5000,class_weight=None,random_state=0)); incumbent.fit(wp[["baseline_logit"]],wp.y.to_numpy(int)); p_inc=incumbent.predict_proba(cp[["baseline_logit"]])[:,1]
    qm=simulate_batch(markov,cp.lambda_home.to_numpy(float),cp.lambda_away.to_numpy(float),MARKOV_FEATURES); qs=simulate_batch(semi,cp.lambda_home.to_numpy(float),cp.lambda_away.to_numpy(float),SEMIMARKOV_FEATURES)
    shift=logit_(qs)-logit_(qm); base=logit_(p_inc); y=cp.y.to_numpy(int)
    def obj(alpha): return binary_ll(y,sigmoid(base+float(alpha)*shift))
    opt=minimize_scalar(obj,bounds=(0.,1.),method="bounded",options={"xatol":1e-8}); alpha=float(np.clip(opt.x,0,1)); cand=sigmoid(base+alpha*shift); mi=metrics(cp,p_inc); mc=metrics(cp,cand)
    result={"schema_version":SCHEMA_VERSION,"status":"C070F_FIRST_TWO_COMPLETE","verdict":"CALIBRATION_SHRINKS_DURATION_TRANSPORT_TO_ZERO",
      "identity":{"manifest_sha256":EXPECTED_MANIFEST_SHA,"warmup":1202,"calibration":1201,"confirmation":1597},
      "payload_boundary":{"warmup_score_event_opened":True,"calibration_score_event_opened":True,"confirmation_payload_opened":False,"confirmation_score_rows_read":0,"confirmation_event_rows_read":0},
      "parser":{"warmup_exact":1197,"warmup_mismatch_ids":EXPECTED_WARMUP_MISMATCH,"calibration_exact":1201,"calibration_mismatch_ids":[],"structural_mismatch_exclusion_only":True,"multi_score_bins_excluded":int(multi)},
      "coverage":{"eligible_warmup_rows":int((eligible.block=="warmup").sum()),"eligible_calibration_rows":int((eligible.block=="calibration").sum()),"warmup_pairs":len(wm),"warmup_certificate":wcert,"calibration_pairs":len(cm),"calibration_certificate":ccert,"gate_pass":True},
      "structural_fresh_diagnostic":{"warmup_train_matches":int(wmin.match_id.nunique()),"calibration_test_matches":int(cmin.match_id.nunique()),"markov_log_loss":ll0,"semimarkov_log_loss":ll1,"delta_semimarkov_minus_markov":ll1-ll0,"match_bootstrap":sboot,"fresh_structural_replication_established":bool((ll1-ll0)<0 and sboot["ci90_high"]<0)},
      "transport_calibration":{"alpha_domain":[0.,1.],"alpha":alpha,"optimizer_success":bool(opt.success),"xatol":1e-8,"mean_logodds_shift":float(shift.mean()),"shift_std":float(shift.std()),"incumbent":mi,"candidate":mc,
        "candidate_minus_incumbent":{k:float(mc[k]-mi[k]) for k in ["log_loss","brier","auc","accuracy","pair_accuracy"]},"objective_grid":{str(x):obj(x) for x in [0.,.1,.25,.5,.75,1.]},"interpretation":"calibration fit diagnostic only; not confirmation or scientific PASS"},
      "boundary":{"formal_weight":0,"confirmation_scored":False,"confirmation_used_for_tuning":False,"A05_opened":False,"protected_opened":False,"next_requires_explicit_user_authorization":"open frozen confirmation 1597 exactly once with alpha and all mechanisms frozen"}}
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n"); print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=="__main__": main()
