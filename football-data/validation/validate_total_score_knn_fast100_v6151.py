#!/usr/bin/env python3
"""V6.15.1 research-only continuous market-state KNN: exact total -> conditional score.
Hyperparameters are selected on three chronological validation seasons; 2025/26 final 100 is untouched.
"""
from __future__ import annotations
import heapq,json,math,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import validate_score_market_state_fast100_v6145 as src
OUT=ROOT/'manifests'/'v6_total_score_knn_fast100_v6151_status.json'
VALS=('2022/23','2023/24','2024/25');TEST='2025/26';KS=(75,150,300);OW=(1.0,2.0,3.0);PGRID=(.20,.22,.24,.26,.28,.30,.32,.35);GGRID=(0,.01,.02,.03,.04);MINSEL=120;Z=1.6448536269514722

def sy(s):return int(s[:4])
def tb(r):return min(7,sum(r['score']))
def wl(h,n):
    if not n:return None
    p=h/n;z2=Z*Z;d=1+z2/n;c=p+z2/(2*n);q=Z*math.sqrt((p*(1-p)+z2/(4*n))/n);return (c-q)/d

def vec(r):
    h,d,a=r['one_x_two'];return (float(h),float(d),float(a),float(r['p_over']))
def dist(x,y,w):return (x[0]-y[0])**2+(x[1]-y[1])**2+(x[2]-y[2])**2+w*(x[3]-y[3])**2

def neigh(r,train,k,w,total=None):
    x=vec(r);cid=r['competition_id'];pool=[q for q in train if q['competition_id']==cid and (total is None or tb(q)==total)]
    if len(pool)<max(30,min(k,60)):pool=[q for q in train if total is None or tb(q)==total]
    return heapq.nsmallest(min(k,len(pool)),pool,key=lambda q:dist(x,vec(q),w))
def pt(r,train,k,w):
    nn=neigh(r,train,k,w);c=Counter(tb(q) for q in nn);den=len(nn)+4;p=[(c[i]+.5)/den for i in range(8)];z=sorted(enumerate(p),key=lambda x:(-x[1],x[0]));return z[0][0],z[0][1],z[0][1]-z[1][1]
def ps(r,t,train,k,w):
    nn=neigh(r,train,max(40,k//2),w,total=t);c=Counter(tuple(q['score']) for q in nn);return [s for s,_ in sorted(c.items(),key=lambda x:(-x[1],sum(x[0]),x[0]))]

def predict_set(rows,train,k,w):
    out=[]
    for r in rows:
        t,p,g=pt(r,train,k,w);out.append((r,t,p,g,t==tb(r)))
    return out
def filt(pred,pmin,gmin):return [x for x in pred if x[2]>=pmin and x[3]>=gmin]
def ev(sel):
    h=sum(x[4] for x in sel);n=len(sel);return {'count':n,'hits':h,'accuracy':h/n if n else None,'wilson90_lower':wl(h,n)}

def main():
    rows=src.load();predcache={};cands=[]
    for k in KS:
      for w in OW:
        folds=[]
        for vs in VALS:
            train=[r for r in rows if sy(r['season'])<sy(vs)];valid=[r for r in rows if r['season']==vs]
            pr=predict_set(valid,train,k,w);predcache[(k,w,vs)]=pr;folds.append((vs,len(valid)))
        for p in PGRID:
          for g in GGRID:
            stats=[];ok=True
            for vs,_ in folds:
                s=ev(filt(predcache[(k,w,vs)],p,g));stats.append(s)
                if s['count']<MINSEL:ok=False
            if ok:
                totaln=sum(s['count'] for s in stats);totalh=sum(s['hits'] for s in stats);worst=min(s['accuracy'] for s in stats);cands.append((worst,wl(totalh,totaln),totalh/totaln,totaln,k,w,p,g,stats))
    cands.sort(reverse=True,key=lambda x:(x[0],x[1],x[2],x[3]));best=cands[0];worst,lower,acc,n,k,w,p,g,vstats=best
    train=[r for r in rows if sy(r['season'])<sy(TEST)];testall=[r for r in rows if r['season']==TEST];test=testall[-100:];pred=predict_set(test,train,k,w);sel=filt(pred,p,g);te=ev(sel)
    s1=s3=cond=cond1=cond3=0;details=[]
    for r,t,pp,gg,th in sel:
        ranks=ps(r,t,train,k,w);actual=tuple(r['score']);h1=actual in ranks[:1];h3=actual in ranks[:3];s1+=h1;s3+=h3
        if th:cond+=1;cond1+=h1;cond3+=h3
        details.append({'date':r['date'],'competition_id':r['competition_id'],'actual':list(actual),'pred_total':t,'total_hit':bool(th),'top1':list(ranks[0]) if ranks else None})
    sn=len(sel);score={'count':sn,'top1_hits':s1,'top1_accuracy':s1/sn if sn else None,'top3_hits':s3,'top3_accuracy':s3/sn if sn else None,'conditional_total_correct_count':cond,'conditional_top1':cond1/cond if cond else None,'conditional_top3':cond3/cond if cond else None}
    out={'schema_version':'V6.15.1-total-score-knn-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'validation_seasons':list(VALS),'test_season':TEST,'untouched_test_matches':100,'k_grid':list(KS),'over_distance_weights':list(OW),'selection_objective':'maximize worst validation-season accuracy, then aggregate Wilson90 lower','test_used_for_selection':False},'selected_rule':{'k':k,'over_weight':w,'pmin':p,'gapmin':g,'validation_worst_accuracy':worst,'validation_aggregate_accuracy':acc,'validation_aggregate_wilson90_lower':lower,'validation_count':n,'validation_folds':vstats},'test_total':te,'test_score':score,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
