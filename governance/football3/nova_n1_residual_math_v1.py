#!/usr/bin/env python3
from __future__ import annotations
import math
from collections import defaultdict
from typing import Any
from nova_n1_common_v1 import *
from nova_n1_pit_replay_v1 import probs_from_matrix

def base_feature_names(route: str) -> list[str]:
    if route == "D": return ["D1","D2"]
    if route == "P": return ["P1","P2"]
    if route in ("DP","DPI"): return ["D1","D2","P1","P2"]
    raise N1Error("bad route")

def raw_vector(raw: dict[str,float], route: str) -> list[float]:
    return [float(raw[k]) for k in base_feature_names(route)]

def fit_standardizer(rows: list[ReplayRow], window: int, route: str) -> tuple[list[float],list[float],int]:
    xs = []
    for r in rows:
        if r.season_key not in FIT_SEASONS: continue
        raw = r.raw_features[window]
        if raw is not None: xs.append(raw_vector(raw, route))
    if not xs: raise N1Error("no fit features")
    d = len(xs[0]); means = [math.fsum(x[j] for x in xs)/len(xs) for j in range(d)]; stds = []
    for j,m in enumerate(means):
        v = math.fsum((x[j]-m)**2 for x in xs)/len(xs); s = math.sqrt(v); stds.append(s if s > 1e-12 else 1.0)
    return means,stds,len(xs)

def vectorize(raw: dict[str,float] | None, route: str, means: list[float], stds: list[float]) -> list[float] | None:
    if raw is None: return None
    base = raw_vector(raw, route); z = [(x-m)/s for x,m,s in zip(base,means,stds)]
    if route == "DPI":
        if len(z) != 4: raise N1Error("DPI base dimension drift")
        z = z + [z[0]*z[2], z[0]*z[3], z[1]*z[2], z[1]*z[3]]
    return z

def softmax_offset(p: list[float], x: list[float], theta: list[float]) -> tuple[list[float], list[float]]:
    d=len(x); stride=d+1
    if len(theta)!=2*stride: raise N1Error("theta dimension")
    deltas=[]
    for c in (0,1):
        off=c*stride; deltas.append(theta[off]+math.fsum(theta[off+1+j]*x[j] for j in range(d)))
    deltas.append(0.0)
    logits=[math.log(max(EPS,min(1.0,p[c])))+deltas[c] for c in range(3)]
    mx=max(logits); ex=[math.exp(v-mx) for v in logits]; s=sum(ex)
    return [v/s for v in ex], deltas

def objective_grad(samples: list[tuple[list[float],list[float],int]], theta: list[float]) -> tuple[float,list[float]]:
    if not samples: raise N1Error("empty optimizer samples")
    d=len(samples[0][0]); stride=d+1; loss=0.0; g=[0.0]*len(theta)
    for x,p,y in samples:
        q,_=softmax_offset(p,x,theta); loss -= math.log(max(EPS,q[y]))
        for c in (0,1):
            err=q[c]-(1.0 if y==c else 0.0); off=c*stride; g[off]+=err
            for j,val in enumerate(x): g[off+1+j]+=err*val
    for c in (0,1):
        off=c*stride
        for j in range(d):
            idx=off+1+j; loss += 0.5*L2*theta[idx]*theta[idx]; g[idx] += L2*theta[idx]
    return loss,g

def armijo_objective_delta(samples: list[tuple[list[float],list[float],int]], theta: list[float], cand: list[float]) -> float:
    if not samples: raise N1Error("empty optimizer samples")
    d=len(samples[0][0]); stride=d+1
    if len(theta)!=2*stride or len(cand)!=len(theta): raise N1Error("theta dimension")
    dt=[b-a for a,b in zip(theta,cand)]
    terms=[]
    for x,p,y in samples:
        q,_=softmax_offset(p,x,theta)
        dd=[]
        for c in (0,1):
            off=c*stride
            dd.append(dt[off]+math.fsum(dt[off+1+j]*x[j] for j in range(d)))
        dd.append(0.0)
        # Exact identity: LSE(z+dd)-LSE(z) = log(sum_c q_c * exp(dd_c)).
        # Use log1p/expm1 near zero to avoid catastrophic cancellation, and
        # shifted log-sum-exp for large trial steps to avoid overflow.
        if max(abs(v) for v in dd) < 0.5:
            u=math.fsum(q[c]*math.expm1(dd[c]) for c in range(3))
            lse_delta=math.log1p(u)
        else:
            mx=max(dd)
            lse_delta=mx+math.log(math.fsum(q[c]*math.exp(dd[c]-mx) for c in range(3)))
        terms.append(lse_delta-dd[y])
    penalties=[]
    for c in (0,1):
        off=c*stride
        for j in range(d):
            idx=off+1+j
            penalties.append(0.5*L2*(cand[idx]-theta[idx])*(cand[idx]+theta[idx]))
    return math.fsum(terms)+math.fsum(penalties)

def fit_residual(samples: list[tuple[list[float],list[float],int]]) -> dict[str,Any]:
    d=len(samples[0][0]); theta=[0.0]*(2*(d+1)); loss,g=objective_grad(samples,theta); initial_loss=loss
    for it in range(1,MAX_ITER+1):
        if not math.isfinite(loss) or any(not math.isfinite(v) for v in g): raise N1Error("NONFINITE_OPTIMIZER")
        gin=max(abs(v) for v in g)
        if gin <= GRAD_TOL: return {"theta":theta,"iterations":it-1,"loss":loss,"initial_loss":initial_loss,"grad_inf":gin,"converged":True}
        norm2=math.fsum(v*v for v in g); step=1.0; accepted=False
        for _ in range(MAX_BACKTRACKS):
            cand=[t-step*gg for t,gg in zip(theta,g)]
            delta_loss=armijo_objective_delta(samples,theta,cand)
            if math.isfinite(delta_loss) and delta_loss <= -ARMIJO_C*step*norm2:
                closs,cg=objective_grad(samples,cand)
                if math.isfinite(closs) and all(math.isfinite(v) for v in cg):
                    theta,loss,g=cand,loss+delta_loss,cg; accepted=True; break
            step*=BACKTRACK
        if not accepted: raise N1Error("NO_ARMIJO_STEP")
    gin=max(abs(v) for v in g)
    if gin > GRAD_TOL: raise N1Error(f"NO_CONVERGENCE_{MAX_ITER}")
    return {"theta":theta,"iterations":MAX_ITER,"loss":loss,"initial_loss":initial_loss,"grad_inf":gin,"converged":True}

def project_matrix(matrix: list[tuple[int,int,float]], deltas: list[float]) -> list[tuple[int,int,float]]:
    out=[]; total=0.0
    for hg,ag,p in matrix:
        c=0 if hg>ag else 1 if hg==ag else 2; v=p*math.exp(deltas[c]); total+=v; out.append((hg,ag,v))
    if total<=0 or not math.isfinite(total): raise N1Error("projection mass failure")
    return [(hg,ag,p/total) for hg,ag,p in out]

def rps(p: list[float], y: int) -> float:
    obs=[1.0 if y==i else 0.0 for i in range(3)]
    return (((p[0]-obs[0])**2)+(((p[0]+p[1])-(obs[0]+obs[1]))**2))/2.0

def top2_hit(p: list[float], y:int) -> float:
    inds=sorted(range(3), key=lambda i:(-p[i],i))[:2]; return 1.0 if y in inds else 0.0

def matrix_extras(m: list[tuple[int,int,float]]) -> dict[str,float]:
    return {"home_goals_mean": math.fsum(hg*p for hg,ag,p in m), "away_goals_mean": math.fsum(ag*p for hg,ag,p in m), "over_2_5": math.fsum(p for hg,ag,p in m if hg+ag>=3), "btts": math.fsum(p for hg,ag,p in m if hg>0 and ag>0)}

def classwise_ece(ps: list[list[float]], ys: list[int], bins: int=10) -> float:
    if not ps: return float("nan")
    total=0.0
    for c in range(3):
        for b in range(bins):
            lo=b/bins; hi=(b+1)/bins
            idx=[i for i,p in enumerate(ps) if (p[c]>=lo and (p[c]<hi or (b==bins-1 and p[c]<=hi)))]
            if not idx: continue
            conf=math.fsum(ps[i][c] for i in idx)/len(idx); acc=math.fsum(1.0 if ys[i]==c else 0.0 for i in idx)/len(idx)
            total += (len(idx)/len(ps))*abs(conf-acc)
    return total/3.0

def metric_pack(items: list[tuple[list[float],list[tuple[int,int,float]],int]]) -> dict[str,Any]:
    n=len(items)
    if not n: return {"n":0}
    ps=[x[0] for x in items]; ys=[x[2] for x in items]; extras=[matrix_extras(x[1]) for x in items]
    return {
        "n":n,
        "top1": math.fsum(1.0 if max(range(3),key=lambda i:(ps[k][i],-i))==ys[k] else 0.0 for k in range(n))/n,
        "logloss": math.fsum(-math.log(max(EPS,ps[k][ys[k]])) for k in range(n))/n,
        "brier": math.fsum(math.fsum((ps[k][c]-(1.0 if ys[k]==c else 0.0))**2 for c in range(3)) for k in range(n))/n,
        "rps": math.fsum(rps(ps[k],ys[k]) for k in range(n))/n,
        "top2_coverage": math.fsum(top2_hit(ps[k],ys[k]) for k in range(n))/n,
        "classwise_ece_10": classwise_ece(ps,ys,10),
        "mean_model_center_home_goals": math.fsum(e["home_goals_mean"] for e in extras)/n,
        "mean_model_center_away_goals": math.fsum(e["away_goals_mean"] for e in extras)/n,
        "mean_over_2_5": math.fsum(e["over_2_5"] for e in extras)/n,
        "mean_btts": math.fsum(e["btts"] for e in extras)/n,
    }

def evaluate(rows: list[ReplayRow], season_filter: set[int], window:int, route:str, means:list[float], stds:list[float], theta:list[float]) -> dict[str,Any]:
    base=[]; cand=[]; l1s=[]; avail=0
    per_season=defaultdict(lambda:[[],[]]); per_league=defaultdict(lambda:[[],[]])
    for r in rows:
        if r.season_key not in season_filter: continue
        bp=r.baseline["p"]; bm=r.baseline["matrix"]; y=int(r.y); x=vectorize(r.raw_features[window],route,means,stds)
        if x is None: cp=bp; cm=bm
        else:
            avail+=1; cp,deltas=softmax_offset(bp,x,theta); cm=project_matrix(bm,deltas); mp=probs_from_matrix(cm)
            if max(abs(a-b) for a,b in zip(cp,mp))>5e-10: raise N1Error("projection probability mismatch")
            bmap={(hg,ag):p for hg,ag,p in bm}; cmap={(hg,ag):p for hg,ag,p in cm}; l1s.append(math.fsum(abs(bmap[k]-cmap[k]) for k in bmap))
        base.append((bp,bm,y)); cand.append((cp,cm,y)); per_season[r.season_key][0].append((bp,bm,y)); per_season[r.season_key][1].append((cp,cm,y)); per_league[r.competition_id][0].append((bp,bm,y)); per_league[r.competition_id][1].append((cp,cm,y))
    b=metric_pack(base); c=metric_pack(cand)
    summary={
        "baseline":b,"candidate":c,
        "delta":{"top1_pp":100*(c["top1"]-b["top1"]),"logloss":c["logloss"]-b["logloss"],"brier":c["brier"]-b["brier"],"rps":c["rps"]-b["rps"]},
        "additional_hits":round((c["top1"]-b["top1"])*b["n"]),"expert_available_n":avail,"coverage":avail/b["n"] if b["n"] else 0.0,
        "mean_score_matrix_l1_change_available":math.fsum(l1s)/len(l1s) if l1s else 0.0,"per_season":{},"per_league":{},
    }
    for k,(bb,cc) in sorted(per_season.items()):
        mb,mc=metric_pack(bb),metric_pack(cc); summary["per_season"][str(k)]={"baseline":mb,"candidate":mc,"top1_delta_pp":100*(mc["top1"]-mb["top1"]),"logloss_delta":mc["logloss"]-mb["logloss"]}
    for k,(bb,cc) in sorted(per_league.items()):
        mb,mc=metric_pack(bb),metric_pack(cc); summary["per_league"][k]={"baseline":mb,"candidate":mc,"top1_delta_pp":100*(mc["top1"]-mb["top1"]),"logloss_delta":mc["logloss"]-mb["logloss"]}
    return summary

def make_fit_samples(rows:list[ReplayRow], window:int, route:str, means:list[float], stds:list[float]) -> list[tuple[list[float],list[float],int]]:
    out=[]
    for r in rows:
        if r.season_key not in FIT_SEASONS: continue
        x=vectorize(r.raw_features[window],route,means,stds)
        if x is not None: out.append((x,r.baseline["p"],int(r.y)))
    return out

def development_pass(m:dict[str,Any]) -> bool:
    d=m["delta"]; return d["top1_pp"] >= -0.15 and d["brier"] <= 0.001 and d["rps"] <= 0.001

def selection_key(cfg:dict[str,Any]) -> tuple:
    m=cfg["development"]["candidate"]; return (m["logloss"],m["brier"],m["rps"],-m["top1"],cfg["feature_dimension"],cfg["config_id"])
