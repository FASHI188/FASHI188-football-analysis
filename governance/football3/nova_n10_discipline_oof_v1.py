#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,math
import numpy as np
from collections import defaultdict,deque
from datetime import datetime
from pathlib import Path
class E(RuntimeError):pass
def req(c,m):
    if not c:raise E(m)
def readl(p):return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
def dt(v):return datetime.fromisoformat(str(v).replace("Z","+00:00"))
def groups(rows):
    out=[];s=0
    while s<len(rows):
        e=s+1
        while e<len(rows) and rows[e]["kickoff"]==rows[s]["kickoff"]:e+=1
        out.append((s,e));s=e
    return out
def align(rows,base):
    ids=[r["fixture_id"] for r in rows];m={r["n2_fixture_id"]:r for r in base};req(set(ids)==set(m),"BASE_IDS");out=[m[x] for x in ids]
    for s,b in zip(rows,out):
        req(dt(s["kickoff"])==dt(b["kickoff"]),"KO");req(s["home_team_id"]==b["home_team_id"] and s["away_team_id"]==b["away_team_id"] and s["league"]==b["league"],"IDENTITY");req(b.get("target_label_read") is False,"BASE_LABEL")
    return out
def summarize(hist,route,p):
    if not hist:return None
    w5=int(p["development_protocol"]["w5"]);w10=int(p["development_protocol"]["w10"]);alpha=float(p["development_protocol"]["ewma_alpha"])
    if route=="R1_W5_YELLOW":
        x=hist[-w5:];return [sum(a for a,b in x)/len(x),math.log1p(len(hist))]
    if route=="R2_W10_YELLOW_RED":
        x=hist[-w10:];return [sum(a for a,b in x)/len(x),sum(b for a,b in x)/len(x),math.log1p(len(hist))]
    if route=="R3_EWMA035":
        y=r=0.;first=True
        for a,b in hist:
            if first:y=float(a);r=float(b);first=False
            else:y=alpha*float(a)+(1-alpha)*y;r=alpha*float(b)+(1-alpha)*r
        return [y,r,math.log1p(len(hist))]
    if route=="R4_W5_W10_VOLATILITY":
        a5=hist[-w5:];a10=hist[-w10:];ys=[a for a,b in a5];my=sum(ys)/len(ys);sd=math.sqrt(sum((v-my)**2 for v in ys)/len(ys));rr=sum(1 for a,b in a5 if b>0)/len(a5)
        return [my,sum(b for a,b in a5)/len(a5),sum(a for a,b in a10)/len(a10),sum(b for a,b in a10)/len(a10),sd,rr,math.log1p(len(hist))]
    raise E("ROUTE")
def pair(h,a,d):
    hv=[None]*d if h is None else h;av=[None]*d if a is None else a;req(len(hv)==d and len(av)==d,"PAIR_DIM");return hv+av+[None if hv[j] is None or av[j] is None else hv[j]-av[j] for j in range(d)]
def raw(rows,route,p):
    dims={"R1_W5_YELLOW":2,"R2_W10_YELLOW_RED":3,"R3_EWMA035":3,"R4_W5_W10_VOLATILITY":7};d=dims[route];hist=defaultdict(list);pending=deque();out=[]
    for s,e in groups(rows):
        ko=dt(rows[s]["kickoff"])
        while pending and dt(pending[0]["release_at"])<=ko:
            r=pending.popleft();hist[r["home_team_id"]].append((float(r["home_yellow"]),float(r["home_red"])));hist[r["away_team_id"]].append((float(r["away_yellow"]),float(r["away_red"])))
        for i in range(s,e):
            r=rows[i];out.append(pair(summarize(hist[r["home_team_id"]],route,p),summarize(hist[r["away_team_id"]],route,p),d))
        for i in range(s,e):pending.append(rows[i])
    return out
def scaler(X,idx):
    m=[];sd=[]
    for j in range(len(X[0])):
        v=[float(X[i][j]) for i in idx if X[i][j] is not None];z=sum(v)/len(v) if v else 0.;var=sum((x-z)**2 for x in v)/len(v) if v else 0.;m.append(z);sd.append(max(math.sqrt(var),1e-9))
    return m,sd
def zrow(x,m,s):return [((m[j] if v is None else float(v))-m[j])/s[j] for j,v in enumerate(x)]
def softmax(base,x,B):
    eps=1e-15;oh=math.log(max(base[0],eps)/max(base[2],eps));od=math.log(max(base[1],eps)/max(base[2],eps));xx=[1.]+x;lh=oh+sum(B[0][j]*xx[j] for j in range(len(xx)));ld=od+sum(B[1][j]*xx[j] for j in range(len(xx)));mm=max(lh,ld,0.);a,d,z=math.exp(lh-mm),math.exp(ld-mm),math.exp(-mm);t=a+d+z;return [a/t,d/t,z/t]
def fit(X,bases,y,c=.25,iters=300):
    Xn=np.asarray([[1.]+list(map(float,x)) for x in X]);bn=np.asarray(bases);yn=np.asarray(y);eps=1e-15;off=np.column_stack((np.log(np.maximum(bn[:,0],eps)/np.maximum(bn[:,2],eps)),np.log(np.maximum(bn[:,1],eps)/np.maximum(bn[:,2],eps))));Y=np.column_stack((yn==0,yn==1)).astype(float);reg=1/(c*len(Xn));B=np.zeros((2,Xn.shape[1]))
    def og(B):
        z2=off+Xn@B.T;z=np.column_stack((z2,np.zeros(len(Xn))));z-=z.max(axis=1,keepdims=True);e=np.exp(z);pr=e/e.sum(axis=1,keepdims=True);loss=float(-np.log(np.maximum(pr[np.arange(len(yn)),yn],eps)).mean())+.5*reg*float((B[:,1:]**2).sum());g=((pr[:,:2]-Y).T@Xn)/len(Xn);g[:,1:]+=reg*B[:,1:];return loss,g
    loss,g=og(B)
    for _ in range(iters):
        n2=float((g*g).sum())
        if n2<1e-12:break
        st=1.;ok=False
        while st>1e-8:
            C=B-st*g;nl,ng=og(C)
            if nl<=loss-1e-4*st*n2:B,loss,g=C,nl,ng;ok=True;break
            st*=.5
        if not ok or float(np.max(np.abs(g)))<1e-6:break
    return B.tolist()
def blocks(rows,w=.2,n=5):
    gs=groups(rows);target=math.ceil(len(rows)*w);c=i=0
    while i<len(gs) and c<target:c+=gs[i][1]-gs[i][0];i+=1
    we=gs[i-1][1];rem=gs[i:];each=(len(rows)-we)/n;out=[];s=we;a=0
    for k,g in enumerate(rem):
        a+=g[1]-g[0];left=len(rem)-k-1
        if len(out)<n-1 and a>=each and left>=n-len(out)-1:out.append((s,g[1]));s=g[1];a=0
    out.append((s,len(rows)));req(len(out)==n,"BLOCKS");return we,out
def oi(v):return {"home":0,"draw":1,"away":2}[v]
def metrics(ps,y):
    n=len(y);eps=1e-15;ll=sum(-math.log(max(p[o],eps)) for p,o in zip(ps,y))/n;br=sum(sum((p[k]-(1 if o==k else 0))**2 for k in range(3)) for p,o in zip(ps,y))/n;rps=sum(((p[0]-(1 if o==0 else 0))**2+(p[0]+p[1]-(1 if o in(0,1) else 0))**2)/2 for p,o in zip(ps,y))/n;top=sum(max(range(3),key=lambda k:p[k])==o for p,o in zip(ps,y))/n;bins=[[] for _ in range(10)]
    for p,o in zip(ps,y):k=max(range(3),key=lambda q:p[q]);c=p[k];bins[min(9,int(c*10))].append((c,1. if k==o else 0.))
    ece=sum(len(z)/n*abs(sum(a for a,b in z)/len(z)-sum(b for a,b in z)/len(z)) for z in bins if z);return {"n":n,"logloss":ll,"brier":br,"rps":rps,"top1":top,"ece":ece}
def boot(v,reps,seed):
    a=np.asarray(v);n=len(a);rng=np.random.default_rng(seed);m=[];left=reps
    while left:
        b=min(500,left);idx=rng.integers(0,n,size=(b,n));m.extend(a[idx].mean(axis=1).tolist());left-=b
    m.sort();return [m[int(.025*(reps-1))],m[int(.975*(reps-1))]]
def run(prereg,source,baseline,labels,out):
    p=json.loads(Path(prereg).read_text());rows=readl(source);base=align(rows,readl(baseline));lab=readl(labels);req(len(rows)==len(base)==len(lab)==p["data"]["development_n"],"N");req([r["fixture_id"] for r in rows]==[r["fixture_id"] for r in lab],"LABEL_ALIGN");probs=[[float(x) for x in r["formal_v2_1x2"]] for r in base];y=[oi(r["outcome"]) for r in lab];warm,bs=blocks(rows,p["development_protocol"]["warmup_fraction"],p["development_protocol"]["oof_blocks"]);routes=[];pred={};ref=None
    for sp in p["subroutes"]:
        rid=sp["id"];rr=raw(rows,rid,p);idx=[];cp=[]
        for s,e in bs:
            tr=list(range(s));va=list(range(s,e));m,sd=scaler(rr,tr);X=[zrow(rr[i],m,sd) for i in tr];XV=[zrow(rr[i],m,sd) for i in va];B=fit(X,[probs[i] for i in tr],[y[i] for i in tr],p["model"]["ridge_c"]);cp.extend(softmax(probs[i],x,B) for i,x in zip(va,XV));idx.extend(va)
        if ref is None:ref=idx
        req(idx==ref,"OOF");bp=[probs[i] for i in idx];yy=[y[i] for i in idx];bm,cm=metrics(bp,yy),metrics(cp,yy);eff=[math.log(max(cp[j][yy[j]],1e-15))-math.log(max(bp[j][yy[j]],1e-15)) for j in range(len(yy))];q=p["development_protocol"]["qualification"];pf=p["development_protocol"]["positive_signal_floor"];qual=bm["logloss"]-cm["logloss"]>q["logloss_gain_gt"] and cm["brier"]-bm["brier"]<=q["candidate_minus_formal_brier_lte"] and cm["rps"]-bm["rps"]<=q["candidate_minus_formal_rps_lte"] and cm["ece"]-bm["ece"]<=q["candidate_minus_formal_ece_lte"];pos=bm["logloss"]-cm["logloss"]>pf["logloss_gain_gt"] and cm["brier"]-bm["brier"]<=pf["candidate_minus_formal_brier_lte"] and cm["rps"]-bm["rps"]<=pf["candidate_minus_formal_rps_lte"] and cm["top1"]-bm["top1"]>=pf["candidate_minus_formal_top1_gte"];routes.append({"route":rid,"formal":bm,"candidate":cm,"formal_minus_candidate_logloss":bm["logloss"]-cm["logloss"],"candidate_minus_formal_brier":cm["brier"]-bm["brier"],"candidate_minus_formal_rps":cm["rps"]-bm["rps"],"candidate_minus_formal_top1":cm["top1"]-bm["top1"],"candidate_minus_formal_ece":cm["ece"]-bm["ece"],"qualified":qual,"positive_floor":pos,"paired_bootstrap_95ci":boot(eff,p["metrics"]["uncertainty"]["repetitions"],p["metrics"]["uncertainty"]["seed"])});pred[rid]=cp
    best=max(routes,key=lambda r:r["formal_minus_candidate_logloss"]);cls="POSITIVE_SIGNAL" if any(r["qualified"] or r["positive_floor"] for r in routes) else "FAIL_CURRENT_IMPLEMENTATION";idx=ref;yy=[y[i] for i in idx];bp=[probs[i] for i in idx];cp=pred[best["route"]];lr={}
    for lg in p["metrics"]["group_report"]:
        if lg in {"J1","K1"}:lr[lg]={"status":"NOT_AVAILABLE","n":0,"coverage":0.,"weight":0,"matrix_delta":0};continue
        pos=[j for j,i in enumerate(idx) if rows[i]["league"]==lg];b=[bp[j] for j in pos];c=[cp[j] for j in pos];z=[yy[j] for j in pos];bm,cm=metrics(b,z),metrics(c,z);lr[lg]={"status":"DEVELOPMENT_OOF","n":len(pos),"coverage":1.,"formal":bm,"candidate":cm,"formal_minus_candidate_logloss":bm["logloss"]-cm["logloss"],"matrix_delta":0}
    res={"schema_version":"football3-nova-n10-discipline-development-oof-v1","status":"N10_DISCIPLINE_OOF_COMPLETE","classification":cls,"development_n":len(rows),"oof_evaluation_n":len(idx),"warmup_end":warm,"completed_matches_only":True,"feature_time_rule":"prior discipline rows only with release_at<=target kickoff; same-kickoff atomic","routes":routes,"best_signal_route":best["route"],"best_formal_minus_candidate_logloss":best["formal_minus_candidate_logloss"],"selected_route":best["route"] if cls=="POSITIVE_SIGNAL" else None,"league_report":lr,"referee_status":"NOT_AVAILABLE","referee_weight":0,"referee_matrix_delta":0,"n1_2025_isolated_labels_read":0,"n2_2023_isolated_labels_read":0,"n3_2021_isolated_labels_read":0,"candidate_activation_allowed":False,"promotion_allowed":False,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0,"score_matrix":{"formal_v2_unchanged":True,"candidate_matrix_delta":0,"exact_score_metrics_changed":False}}
    Path(out).mkdir(parents=True,exist_ok=True);Path(out,"development_oof_result.json").write_text(json.dumps(res,indent=2,sort_keys=True)+"\n");return res
def main():
    a=argparse.ArgumentParser();a.add_argument("--prereg",required=True);a.add_argument("--source",required=True);a.add_argument("--baseline",required=True);a.add_argument("--labels",required=True);a.add_argument("--out",required=True);x=a.parse_args();print(json.dumps(run(x.prereg,x.source,x.baseline,x.labels,x.out),sort_keys=True))
if __name__=="__main__":main()
