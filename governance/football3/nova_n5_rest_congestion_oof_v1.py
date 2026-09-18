#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, random
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

class N5Error(RuntimeError): pass
def require(c:bool,m:str)->None:
    if not c: raise N5Error(m)
def readl(p:Path): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def dt(v:str)->datetime: return datetime.fromisoformat(v.replace("Z","+00:00"))

def align_baseline(source_rows, baseline_rows):
    sids=[str(r["fixture_id"]) for r in source_rows]; bids=[str(r["n2_fixture_id"]) for r in baseline_rows]
    require(len(sids)==len(set(sids)),"SOURCE_DUP_FIXTURE_ID"); require(len(bids)==len(set(bids)),"BASELINE_DUP_FIXTURE_ID")
    require(set(sids)==set(bids),f"BASELINE_ID_SET_MISMATCH:missing={len(set(sids)-set(bids))}:extra={len(set(bids)-set(sids))}")
    by_id={str(r["n2_fixture_id"]):r for r in baseline_rows}; aligned=[by_id[x] for x in sids]
    for s,b in zip(source_rows,aligned):
        require(dt(str(s["kickoff"]))==dt(str(b["kickoff"])),f"BASELINE_KICKOFF_MISMATCH:{s['fixture_id']}")
        require(str(s["home_team_id"])==str(b["home_team_id"]),f"BASELINE_HOME_ID_MISMATCH:{s['fixture_id']}")
        require(str(s["away_team_id"])==str(b["away_team_id"]),f"BASELINE_AWAY_ID_MISMATCH:{s['fixture_id']}")
        require(str(s["league"])==str(b["league"]),f"BASELINE_LEAGUE_MISMATCH:{s['fixture_id']}")
        require(b.get("target_label_read") is False,f"BASELINE_LABEL_READ:{s['fixture_id']}")
    return aligned

def groups(rows):
    out=[]; s=0
    while s<len(rows):
        e=s+1
        while e<len(rows) and rows[e]["kickoff"]==rows[s]["kickoff"]: e+=1
        out.append((s,e)); s=e
    return out

def build_raw(rows, rest_cap_days=30.0):
    hist=defaultdict(list); pending=deque(); out=[None]*len(rows)
    for s,e in groups(rows):
        ko=dt(rows[s]["kickoff"])
        while pending and dt(pending[0]["release_at"])<=ko:
            r=pending.popleft(); rk=dt(r["kickoff"]); hist[r["home_team_id"]].append(rk); hist[r["away_team_id"]].append(rk)
        for i in range(s,e):
            r=rows[i]; vals=[]
            for team in (r["home_team_id"],r["away_team_id"]):
                hs=hist[team]; last=hs[-1] if hs else None
                rest=None if last is None else min(rest_cap_days,max(0.0,(ko-last).total_seconds()/86400.0))
                c7=sum(t>=ko-timedelta(days=7) for t in hs); c14=sum(t>=ko-timedelta(days=14) for t in hs)
                vals.append((rest,float(c7),float(c14),float(len(hs))))
            (hr,h7,h14,hn),(ar,a7,a14,an)=vals
            out[i]=[hr,ar,None if hr is None or ar is None else hr-ar,h7,a7,h7-a7,h14,a14,h14-a14,math.log1p(hn),math.log1p(an)]
        for i in range(s,e): pending.append(rows[i])
    return out

def primitive(row, route, short_lt=4.0, long_gt=7.0):
    hr,ar,rd,h7,a7,d7,h14,a14,d14,hn,an=row
    r1=[hr,ar,rd,hn,an]; r2=[h7,a7,d7,h14,a14,d14]
    if route=="R1_REST_DIFF": return r1
    if route=="R2_CONGESTION_7_14": return r2
    if route=="R3_REST_PLUS_CONGESTION": return r1+r2
    if route=="R4_RECOVERY_NONLINEAR":
        sh=None if hr is None else float(hr<short_lt); sa=None if ar is None else float(ar<short_lt)
        lh=None if hr is None else float(hr>long_gt); la=None if ar is None else float(ar>long_gt)
        inter=None if rd is None else rd*d7
        return r1+r2+[sh,sa,lh,la,inter]
    raise N5Error("UNKNOWN_ROUTE")

def scaler(X,idx):
    d=len(X[0]); means=[]; stds=[]
    for j in range(d):
        vals=[float(X[i][j]) for i in idx if X[i][j] is not None and math.isfinite(float(X[i][j]))]
        m=sum(vals)/len(vals) if vals else 0.0; var=sum((v-m)**2 for v in vals)/len(vals) if vals else 0.0; means.append(m); stds.append(max(math.sqrt(var),1e-9))
    return means,stds
def zrow(row,m,s): return [((m[j] if v is None else float(v))-m[j])/s[j] for j,v in enumerate(row)]
def softmax(base,x,beta):
    eps=1e-15; oh=math.log(max(base[0],eps)/max(base[2],eps)); od=math.log(max(base[1],eps)/max(base[2],eps)); xx=[1.0]+x
    lh=oh+sum(beta[0][j]*xx[j] for j in range(len(xx))); ld=od+sum(beta[1][j]*xx[j] for j in range(len(xx))); mm=max(lh,ld,0.0)
    eh,ed,ea=math.exp(lh-mm),math.exp(ld-mm),math.exp(-mm); t=eh+ed+ea; return [eh/t,ed/t,ea/t]
def loss_grad(beta,X,bases,y,c):
    n=len(X); d=len(beta[0]); loss=0.0; g=[[0.0]*d,[0.0]*d]; eps=1e-15
    for x,b,o in zip(X,bases,y):
        p=softmax(b,x,beta); loss-=math.log(max(p[o],eps)); xx=[1.0]+x; errs=[p[0]-(o==0),p[1]-(o==1)]
        for k in range(2):
            for j,v in enumerate(xx): g[k][j]+=float(errs[k])*v
    loss/=n; g=[[v/n for v in row] for row in g]; reg=1.0/(c*n)
    for k in range(2):
        for j in range(1,d): loss+=0.5*reg*beta[k][j]**2; g[k][j]+=reg*beta[k][j]
    return loss,g
def fit(X,bases,y,c=.25,max_iter=300):
    d=len(X[0])+1; beta=[[0.0]*d,[0.0]*d]; loss,g=loss_grad(beta,X,bases,y,c)
    for it in range(max_iter):
        n2=sum(v*v for row in g for v in row)
        if n2<1e-12: return beta,loss,it+1
        step=1.0; ok=False
        while step>1e-8:
            cand=[[beta[k][j]-step*g[k][j] for j in range(d)] for k in range(2)]; nl,ng=loss_grad(cand,X,bases,y,c)
            if nl<=loss-1e-4*step*n2: beta,loss,g=cand,nl,ng; ok=True; break
            step*=.5
        if not ok or max(abs(v) for row in g for v in row)<1e-6: return beta,loss,it+1
    return beta,loss,max_iter

def blocks(rows,warm=.2,nblocks=5):
    gs=groups(rows); target=math.ceil(len(rows)*warm); cum=0; gi=0
    while gi<len(gs) and cum<target: cum+=gs[gi][1]-gs[gi][0]; gi+=1
    require(gi>0,"EMPTY_WARMUP"); warm_end=gs[gi-1][1]; remain=gs[gi:]; each=(len(rows)-warm_end)/nblocks; out=[]; bs=warm_end; acc=0
    for k,g in enumerate(remain):
        acc+=g[1]-g[0]; left=len(remain)-k-1
        if len(out)<nblocks-1 and acc>=each and left>=nblocks-len(out)-1: out.append((bs,g[1])); bs=g[1]; acc=0
    out.append((bs,len(rows))); require(len(out)==nblocks,"BLOCK_COUNT"); return warm_end,out
def oi(v): return {"home":0,"draw":1,"away":2}[v]
def metrics(ps,y):
    n=len(y); eps=1e-15; ll=sum(-math.log(max(p[o],eps)) for p,o in zip(ps,y))/n
    br=sum(sum((p[k]-(1 if o==k else 0))**2 for k in range(3)) for p,o in zip(ps,y))/n
    rps=sum(((p[0]-(1 if o==0 else 0))**2+(p[0]+p[1]-(1 if o in (0,1) else 0))**2)/2 for p,o in zip(ps,y))/n
    top=sum(max(range(3),key=lambda k:p[k])==o for p,o in zip(ps,y))/n; bins=[[] for _ in range(10)]
    for p,o in zip(ps,y):
        k=max(range(3),key=lambda x:p[x]); c=p[k]; bins[min(9,int(c*10))].append((c,1.0 if k==o else 0.0))
    ece=sum(len(b)/n*abs(sum(x[0] for x in b)/len(b)-sum(x[1] for x in b)/len(b)) for b in bins if b)
    return {"n":n,"logloss":ll,"brier":br,"rps":rps,"top1":top,"ece":ece}
def effects(base,cand,y):
    eps=1e-15; return [math.log(max(cand[i][y[i]],eps))-math.log(max(base[i][y[i]],eps)) for i in range(len(y))]
def boot(vals,reps,seed):
    r=random.Random(seed); n=len(vals); x=sorted(sum(vals[r.randrange(n)] for _ in range(n))/n for _ in range(reps)); return [x[int(.025*(reps-1))],x[int(.975*(reps-1))]]
def qualify(p,b,c):
    q=p["development_protocol"]["qualification"]; return b["logloss"]-c["logloss"]>q["logloss_gain_gt"] and c["brier"]-b["brier"]<=q["candidate_minus_formal_brier_lte"] and c["rps"]-b["rps"]<=q["candidate_minus_formal_rps_lte"] and c["ece"]-b["ece"]<=q["candidate_minus_formal_ece_lte"]
def positive_floor(p,b,c):
    q=p["development_protocol"]["positive_signal_floor"]; return b["logloss"]-c["logloss"]>q["logloss_gain_gt"] and c["brier"]-b["brier"]<=q["candidate_minus_formal_brier_lte"] and c["rps"]-b["rps"]<=q["candidate_minus_formal_rps_lte"] and c["top1"]-b["top1"]>=q["candidate_minus_formal_top1_gte"]

def run(prereg:Path,source:Path,baseline:Path,labels:Path,out:Path):
    p=json.loads(prereg.read_text()); require(p["status"]=="DESIGN_LOCKED_PRELABEL","PREREG")
    rows=readl(source); base_rows=readl(baseline); lab=readl(labels); n=int(p["data"]["development_n"])
    require(len(rows)==len(base_rows)==len(lab)==n,"ROW_N"); require(all(int(r["season_start"])==2022 for r in rows),"SOURCE_SEASON")
    sids=[r["fixture_id"] for r in rows]; lids=[r["fixture_id"] for r in lab]; require(sids==lids and len(lids)==len(set(lids)),"SOURCE_LABEL_ALIGNMENT")
    base_rows=align_baseline(rows,base_rows); probs=[[float(x) for x in r["formal_v2_1x2"]] for r in base_rows]; y=[oi(r["outcome"]) for r in lab]
    raw=build_raw(rows,float(p["development_protocol"]["rest_cap_days"])); warm,bs=blocks(rows,float(p["development_protocol"]["warmup_fraction"]),int(p["development_protocol"]["oof_blocks"]))
    routes=[]; pred_by={}; oof_ref=None
    for spec in p["subroutes"]:
        rid=spec["id"]; prim=[primitive(r,rid,float(p["development_protocol"]["short_rest_days_lt"]),float(p["development_protocol"]["long_rest_days_gt"])) for r in raw]; idx=[]; cand=[]
        for fs,fe in bs:
            tr=list(range(fs)); va=list(range(fs,fe)); m,s=scaler(prim,tr); Xtr=[zrow(prim[i],m,s) for i in tr]; Xva=[zrow(prim[i],m,s) for i in va]
            beta,_,_=fit(Xtr,[probs[i] for i in tr],[y[i] for i in tr],float(p["model"]["ridge_c"])); cand.extend(softmax(probs[i],x,beta) for i,x in zip(va,Xva)); idx.extend(va)
        if oof_ref is None: oof_ref=idx
        require(idx==oof_ref,"OOF_DRIFT"); b=[probs[i] for i in idx]; yy=[y[i] for i in idx]; bm,cm=metrics(b,yy),metrics(cand,yy); ef=effects(b,cand,yy)
        routes.append({"route":rid,"formal":bm,"candidate":cm,"formal_minus_candidate_logloss":bm["logloss"]-cm["logloss"],"candidate_minus_formal_brier":cm["brier"]-bm["brier"],"candidate_minus_formal_rps":cm["rps"]-bm["rps"],"candidate_minus_formal_top1":cm["top1"]-bm["top1"],"candidate_minus_formal_ece":cm["ece"]-bm["ece"],"qualified":qualify(p,bm,cm),"positive_floor":positive_floor(p,bm,cm),"paired_bootstrap_95ci":boot(ef,int(p["metrics"]["uncertainty"]["repetitions"]),int(p["metrics"]["uncertainty"]["seed"]))}); pred_by[rid]=cand
    best=max(routes,key=lambda r:r["formal_minus_candidate_logloss"]); classification="POSITIVE_SIGNAL" if any(r["qualified"] or r["positive_floor"] for r in routes) else "FAIL_RESEARCH_DIRECTION"; selected=best["route"] if classification=="POSITIVE_SIGNAL" else None
    idx=oof_ref or []; yy=[y[i] for i in idx]; b=[probs[i] for i in idx]; chosen=pred_by[best["route"]]; league_report={}
    for league in p["metrics"]["group_report"]:
        if league in {"J1","K1"}: league_report[league]={"status":"NOT_AVAILABLE","n":0,"coverage":0.0,"weight":0,"matrix_delta":0}; continue
        pos=[j for j,i in enumerate(idx) if rows[i]["league"]==league]; lb=[b[j] for j in pos]; lc=[chosen[j] for j in pos]; ly=[yy[j] for j in pos]; bm=metrics(lb,ly); cm=metrics(lc,ly)
        league_report[league]={"status":"DEVELOPMENT_OOF","n":len(pos),"coverage":1.0 if pos else 0.0,"formal":bm,"candidate":cm,"formal_minus_candidate_logloss":bm["logloss"]-cm["logloss"],"matrix_delta":0}
    out.mkdir(parents=True,exist_ok=True)
    result={"schema_version":"football3-nova-n5-rest-congestion-development-oof-v1","status":"N5_DEVELOPMENT_OOF_COMPLETE","classification":classification,"development_n":n,"oof_evaluation_n":len(idx),"warmup_end":warm,"completed_matches_only":True,"feature_time_rule":"only prior fixtures with release_at<=target_kickoff; same-kickoff atomic","routes":routes,"best_signal_route":best["route"],"best_formal_minus_candidate_logloss":best["formal_minus_candidate_logloss"],"selected_route":selected,"league_report":league_report,"n2_2023_isolated_labels_read":0,"n1_2025_isolated_labels_read":0,"n3_2021_isolated_labels_read":0,"candidate_activation_allowed":False,"promotion_allowed":False,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0,"score_matrix":{"formal_v2_unchanged":True,"candidate_matrix_delta":0,"exact_score_metrics_changed":False}}
    (out/"development_oof_result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); return result

def main():
    a=argparse.ArgumentParser(); a.add_argument("--prereg",type=Path,required=True); a.add_argument("--source",type=Path,required=True); a.add_argument("--baseline",type=Path,required=True); a.add_argument("--labels",type=Path,required=True); a.add_argument("--out",type=Path,required=True); x=a.parse_args(); print(json.dumps(run(x.prereg,x.source,x.baseline,x.labels,x.out),sort_keys=True))
if __name__=="__main__": main()
