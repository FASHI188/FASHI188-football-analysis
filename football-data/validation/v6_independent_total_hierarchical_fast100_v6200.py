#!/usr/bin/env python3
"""V6.20.0 hierarchical independent total-goals Fast100.

Research-only. Train 2022/23+2023/24, select on 2024/25, evaluate a fixed-seed
100-match sample from untouched 2025/26. Predicts P(T=0..6,7+) independently.
The hierarchy first predicts LOW(0-1)/MID(2-3)/HIGH(4+), then the exact bucket
inside each group. Class-balance strength is validation-selected under strict
RPS + LogLoss non-inferiority versus the O/U2.5-updated baseline.
"""
from __future__ import annotations
import json, math, random, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]; V=ROOT/'validation'; E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import v6_total_shot_residual_v6181 as shot
import validate_joint_market_ipf_crossseason_v6164 as market
import validate_market_ou_kl_projection_v6162 as ou

OUT=ROOT/'manifests'/'v6_independent_total_hierarchical_fast100_v6200_status.json'
SEED=20260725+6200
ALPHAS=(0.0,0.25,0.5,0.75,1.0)
CS=(0.03,0.1,0.3)
EPS=1e-15


def add_market(rows):
    cache={}; out=[]
    for r in rows:
        key=(r['competition_id'],r['season'])
        if key not in cache: cache[key]=market.market_lookup(*key)
        mk=cache[key].get((r['date'],r['home_team'],r['away_team']))
        if not mk: continue
        q=ou.project({str(i):float(r['formal'][i]) for i in range(7)}|{'7+':float(r['formal'][7])},float(mk['p_over25']))
        if q is None: continue
        r=dict(r); r['one']=[float(x) for x in mk['one_x_two']]; r['p_over25']=float(mk['p_over25'])
        r['ou']=[float(q[str(i)]) for i in range(7)]+[float(q['7+'])]
        out.append(r)
    return out


def names(rows):
    return sorted(rows[0]['shots']), sorted({r['competition_id'] for r in rows})


def xvec(r,shot_names,comps):
    x=[math.log(max(EPS,float(v))) for v in r['formal']]
    x += [float(r['shots'][k]) for k in shot_names]
    x += [float(v) for v in r['one']] + [float(r['p_over25'])]
    x += [1.0 if r['competition_id']==c else 0.0 for c in comps]
    return x


def class_weights(y,alpha):
    if alpha<=0:return None
    c=Counter(y); mx=max(c.values())
    return {k:(mx/v)**alpha for k,v in c.items()}


def fit_lr(X,y,C,alpha):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    model=make_pipeline(StandardScaler(),LogisticRegression(C=float(C),max_iter=4000,solver='lbfgs',class_weight=class_weights(y,alpha)))
    model.fit(X,y); return model


def group(y): return 0 if y<=1 else 1 if y<=3 else 2


def fit_hier(rows,C,alpha,shot_names,comps):
    X=[xvec(r,shot_names,comps) for r in rows]; y=[int(r['actual']) for r in rows]
    coarse=fit_lr(X,[group(v) for v in y],C,alpha); children={}
    for g,buckets in {0:(0,1),1:(2,3),2:(4,5,6,7)}.items():
        idx=[i for i,v in enumerate(y) if group(v)==g]
        yy=[y[i] for i in idx]
        if len(set(yy))>=2: children[g]=fit_lr([X[i] for i in idx],yy,C,alpha)
    return coarse,children


def probs(models,r,shot_names,comps):
    coarse,children=models; X=[xvec(r,shot_names,comps)]
    cg=[0.0]*3
    for cls,v in zip(coarse.classes_,coarse.predict_proba(X)[0]): cg[int(cls)]=float(v)
    p=[0.0]*8
    groups={0:(0,1),1:(2,3),2:(4,5,6,7)}
    for g,buckets in groups.items():
        child=children.get(g)
        if child is None:
            for b in buckets:p[b]+=cg[g]/len(buckets)
        else:
            arr=child.predict_proba(X)[0]
            for cls,v in zip(child.classes_,arr): p[int(cls)]+=cg[g]*float(v)
    s=sum(p); return [v/s for v in p]


def rps(p,y):
    cp=0.0; z=0.0
    for k in range(7):
        cp+=p[k]; cy=1.0 if y<=k else 0.0; z+=(cp-cy)**2
    return z/7.0


def metric(rows,getter):
    n=len(rows); h1=h2=0; rr=ll=mae=0.0; modes=Counter()
    for r in rows:
        p=getter(r); y=int(r['actual']); order=sorted(range(8),key=lambda i:(-p[i],i)); m=order[0]
        h1+=m==y; h2+=y in order[:2]; rr+=rps(p,y); ll+=-math.log(max(EPS,p[y])); mae+=abs(m-y); modes[str(m)]+=1
    return {'count':n,'top1':h1/n,'top2':h2/n,'rps':rr/n,'logloss':ll/n,'mode_mae':mae/n,'mode_counts':dict(modes)}


def main():
    raw,_=shot.raw_stat_matches(); lookup,_=shot.lagged_shot_lookup(raw); base_rows,_=shot.formal_rows(lookup); rows=add_market(base_rows)
    shot_names,comps=names(rows)
    train=[r for r in rows if r['season'] in {'2022/23','2023/24'}]; valid=[r for r in rows if r['season']=='2024/25']; test=[r for r in rows if r['season']=='2025/26']
    base_valid=metric(valid,lambda r:r['ou']); board=[]
    for C in CS:
        for alpha in ALPHAS:
            m=fit_hier(train,C,alpha,shot_names,comps); met=metric(valid,lambda r,m=m:probs(m,r,shot_names,comps)); met.update({'C':C,'alpha':alpha,'proper_noninferior':met['rps']<=base_valid['rps'] and met['logloss']<=base_valid['logloss']}); board.append(met)
    elig=[x for x in board if x['proper_noninferior']]
    selected=max(elig,key=lambda x:(x['top1'],-x['rps'],-x['logloss'],-x['alpha'])) if elig else min(board,key=lambda x:(x['rps'],x['logloss']))
    final=fit_hier(train+valid,selected['C'],selected['alpha'],shot_names,comps)
    if len(test)<100: raise RuntimeError('insufficient 2025/26 test rows')
    sample=random.Random(SEED).sample(test,100)
    formal=metric(sample,lambda r:r['formal']); baseline=metric(sample,lambda r:r['ou']); challenger=metric(sample,lambda r:probs(final,r,shot_names,comps))
    actual=Counter(str(int(r['actual'])) for r in sample)
    diag={'actual_counts':dict(actual),'actual_4plus':sum(int(r['actual'])>=4 for r in sample),'ou_mode_4plus':sum(max(range(8),key=lambda i:r['ou'][i])>=4 for r in sample),'challenger_mode_4plus':sum(max(range(8),key=lambda i:probs(final,r,shot_names,comps)[i])>=4 for r in sample),'actual_0_1':sum(int(r['actual'])<=1 for r in sample),'challenger_mode_0_1':sum(max(range(8),key=lambda i:probs(final,r,shot_names,comps)[i])<=1 for r in sample)}
    payload={'schema_version':'V6.20.0-independent-total-hierarchical-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_2025_26_FAST100_RESEARCH','design':{'train':['2022/23','2023/24'],'validation':'2024/25','test':'2025/26 fixed-seed random100','target':'independent P(T=0..6,7+)','hierarchy':'0-1 / 2-3 / 4+ then exact bucket','features':['formal P(T) prior','strictly lagged shots/SOT/corners','de-vigged 1X2','O/U2.5','competition'],'alpha_candidates':list(ALPHAS),'C_candidates':list(CS),'selection_gate':'validation RPS and LogLoss non-inferior to OU baseline, then Top1'},'row_counts':{'all':len(rows),'train':len(train),'validation':len(valid),'test_pool':len(test),'sample':100},'validation_baseline':base_valid,'validation_board':board,'selected':selected,'fast100':{'formal':formal,'ou_baseline':baseline,'hierarchical':challenger,'delta_vs_ou_pp':(challenger['top1']-baseline['top1'])*100,'delta_vs_formal_pp':(challenger['top1']-formal['top1'])*100},'diagnostic':diag,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'test_used_for_selection':False,'same_date_stats_frozen':True,'no_manual_tail_bonus':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps({'selected':selected,'fast100':payload['fast100'],'diagnostic':diag},ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
