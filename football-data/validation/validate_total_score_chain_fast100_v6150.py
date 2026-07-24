#!/usr/bin/env python3
"""V6.15.0 research-only: selective exact-total -> conditional-score Fast100.
Fit 2021/22-2023/24, select rule on 2024/25, untouched test = final 100 complete-market matches of 2025/26.
Historical odds lack original quote timestamps; formal_weight=0.
"""
from __future__ import annotations
import json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; V=ROOT/'validation'
if str(V) not in sys.path: sys.path.insert(0,str(V))
import validate_score_market_state_fast100_v6145 as src
OUT=ROOT/'manifests'/'v6_total_score_chain_fast100_v6150_status.json'
FIT={'2021/22','2022/23','2023/24'}; VALID='2024/25'; TEST='2025/26'
VARIANTS=('league','result','result_ou','result_ou_conf'); PGRID=(.18,.20,.22,.24,.26,.28,.30,.32,.35,.38); GGRID=(0,.01,.02,.03,.04,.05)
MINCELL=30; MINSCORE=20; MINSEL=80; Z=1.6448536269514722

def tb(r): return min(7,sum(r['score']))
def wl(h,n):
    if not n:return None
    p=h/n;z2=Z*Z;d=1+z2/n;c=p+z2/(2*n);q=Z*math.sqrt((p*(1-p)+z2/(4*n))/n);return (c-q)/d

def build(rows):
    g=Counter();c=[defaultdict(Counter) for _ in range(4)]
    for r in rows:
        t=tb(r);cid=r['competition_id'];g[t]+=1;c[0][(cid,)][t]+=1;c[1][(cid,r['result_pick'])][t]+=1;c[2][(cid,r['result_pick'],r['ou_pick'])][t]+=1;c[3][(cid,r['result_pick'],r['ou_pick'],r['conf_bin'])][t]+=1
    return g,c

def pick_counter(r,m,v):
    g,c=m;cid=r['competition_id'];chains={
      'league':[c[0][(cid,)],g],
      'result':[c[1][(cid,r['result_pick'])],c[0][(cid,)],g],
      'result_ou':[c[2][(cid,r['result_pick'],r['ou_pick'])],c[1][(cid,r['result_pick'])],c[0][(cid,)],g],
      'result_ou_conf':[c[3][(cid,r['result_pick'],r['ou_pick'],r['conf_bin'])],c[2][(cid,r['result_pick'],r['ou_pick'])],c[1][(cid,r['result_pick'])],c[0][(cid,)],g]}
    for x in chains[v]:
        if sum(x.values())>=MINCELL:return x
    return chains[v][-1]

def pred_total(r,m,v):
    c=pick_counter(r,m,v);den=sum(c.values())+4;p=[(c[i]+.5)/den for i in range(8)];q=sorted(enumerate(p),key=lambda x:(-x[1],x[0]));return q[0][0],q[0][1],q[0][1]-q[1][1]

def ev(rows,m,v,pmin,gmin):
    out=[]
    for r in rows:
        t,p,g=pred_total(r,m,v)
        if p>=pmin and g>=gmin: out.append((r,t,t==tb(r)))
    h=sum(x[2] for x in out);n=len(out);return {'count':n,'hits':h,'accuracy':h/n if n else None,'wilson90_lower':wl(h,n),'coverage':n/len(rows) if rows else 0,'records':out}

def score_model(rows):
    full=defaultdict(Counter);direct=defaultdict(Counter)
    for r in rows:
        s=tuple(r['score']);t=tb(r);cid=r['competition_id'];keys=[(cid,t,r['result_pick'],r['ou_pick'],r['conf_bin']),(cid,t,r['result_pick'],r['ou_pick']),(cid,t,r['result_pick']),(cid,t),('ALL',t)]
        for k in keys: full[k][s]+=1
        direct[(cid,r['result_pick'],r['ou_pick'])][s]+=1
    return full,direct

def ranked(c,k): return [s for s,_ in sorted(c.items(),key=lambda z:(-z[1],sum(z[0]),z[0]))[:k]]
def scounter(r,t,sm):
    full,_=sm;cid=r['competition_id'];ks=[(cid,t,r['result_pick'],r['ou_pick'],r['conf_bin']),(cid,t,r['result_pick'],r['ou_pick']),(cid,t,r['result_pick']),(cid,t),('ALL',t)]
    for k in ks:
        if sum(full[k].values())>=MINSCORE:return full[k]
    return full[ks[-1]]
def dcounter(r,sm):
    full,d=sm;cid=r['competition_id'];c=d[(cid,r['result_pick'],r['ou_pick'])]
    if sum(c.values())>=MINSCORE:return c
    z=Counter()
    for k,x in full.items():
        if len(k)==2 and k[0]==cid:z.update(x)
    return z

def score_eval(recs,sm):
    a1=a3=d1=d3=cond=cond1=cond3=0;rows=[]
    for r,t,thit in recs:
        actual=tuple(r['score']);q1=ranked(scounter(r,t,sm),1);q3=ranked(scounter(r,t,sm),3);b1=ranked(dcounter(r,sm),1);b3=ranked(dcounter(r,sm),3)
        h1=actual in q1;h3=actual in q3;a1+=h1;a3+=h3;d1+=actual in b1;d3+=actual in b3
        if thit:cond+=1;cond1+=h1;cond3+=h3
        rows.append({'date':r['date'],'total_hit':bool(thit),'score_hit':bool(h1)})
    n=len(recs);return {'count':n,'top1_hits':a1,'top1_accuracy':a1/n if n else None,'top3_hits':a3,'top3_accuracy':a3/n if n else None,'direct_top1_same_subset':d1/n if n else None,'direct_top3_same_subset':d3/n if n else None,'conditional_total_correct_count':cond,'conditional_score_top1':cond1/cond if cond else None,'conditional_score_top3':cond3/cond if cond else None,'rows':rows}

def main():
    rows=src.load();fit=[r for r in rows if r['season'] in FIT];valid=[r for r in rows if r['season']==VALID];testall=[r for r in rows if r['season']==TEST];test=testall[-100:]
    m=build(fit);cand=[]
    for v in VARIANTS:
      for p in PGRID:
       for g in GGRID:
        e=ev(valid,m,v,p,g)
        if e['count']>=MINSEL and e['wilson90_lower'] is not None:cand.append((e['wilson90_lower'],e['accuracy'],e['count'],v,p,g,e))
    cand.sort(reverse=True,key=lambda x:(x[0],x[1],x[2]));best=cand[0];_,_,_,v,p,g,ve=best
    testm=build(fit+valid);te=ev(test,testm,v,p,g);sm=score_model(fit+valid);se=score_eval(te['records'],sm)
    out={'schema_version':'V6.15.0-total-score-chain-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'fit_seasons':sorted(FIT),'validation_season':VALID,'test_season':TEST,'untouched_test_matches':100,'variants':list(VARIANTS),'test_used_for_selection':False,'no_hand_assigned_total_or_score':True},'selected_rule':{'variant':v,'pmin':p,'gapmin':g,'validation':{k:z for k,z in ve.items() if k!='records'}},'test_total':{k:z for k,z in te.items() if k!='records'},'test_score':{k:z for k,z in se.items() if k!='rows'},'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
