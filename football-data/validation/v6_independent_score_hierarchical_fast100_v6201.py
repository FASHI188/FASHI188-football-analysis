#!/usr/bin/env python3
"""V6.20.1 hierarchical independent exact-score Fast100.

Score is calculated independently as P(T_score) * P(H | T_score, X). It does not
consume the output of the independent 1X2 or total tracks. Shared raw pre-match
market/process features are allowed. Train 2022/23+2023/24, select on 2024/25,
then fixed-seed random100 from 2025/26.
"""
from __future__ import annotations
import json, math, random, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; V=ROOT/'validation'; E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import v6_total_shot_residual_v6181 as shot
import v6_conditional_score_shot_challenge_v6184 as oldscore
import validate_joint_market_ipf_crossseason_v6164 as market
import validate_market_ou_kl_projection_v6162 as ou
import v6_independent_total_hierarchical_fast100_v6200 as total

OUT=ROOT/'manifests'/'v6_independent_score_hierarchical_fast100_v6201_status.json'
SEED=20260725+6201
COND_CS=(0.03,0.1,0.3)
COND_ALPHAS=(0.0,0.25,0.5)
EPS=1e-15


def total_vec(matrix):
    p=[0.0]*8
    for c in matrix:p[min(7,int(c['home_goals'])+int(c['away_goals']))]+=float(c['probability'])
    s=sum(p);return [x/s for x in p]


def enrich(rows):
    cache={};out=[]
    for r0 in rows:
        r=dict(r0); key=(r['competition_id'],r['season'])
        if key not in cache:cache[key]=market.market_lookup(*key)
        mk=cache[key].get((r['date'],r['home_team'],r['away_team']))
        if not mk:continue
        r['one']=[float(x) for x in mk['one_x_two']];r['p_over25']=float(mk['p_over25']);r['formal']=total_vec(r['formal_matrix']);r['actual']=min(7,int(r['actual_total']))
        q=ou.project({str(i):r['formal'][i] for i in range(7)}|{'7+':r['formal'][7]},r['p_over25'])
        if q is None:continue
        r['ou']=[float(q[str(i)]) for i in range(7)]+[float(q['7+'])];out.append(r)
    return out


def cweight(y,alpha):
    if alpha<=0:return None
    c=Counter(y);mx=max(c.values());return {k:(mx/v)**alpha for k,v in c.items()}


def cond_x(r,shot_names,comps):
    x=[math.log(max(EPS,float(v))) for v in r['one']]+[float(r['p_over25'])]
    x += [float(r['shots'][k]) for k in shot_names]
    x += [1.0 if r['competition_id']==c else 0.0 for c in comps]
    return x


def fit_cond(rows,C,alpha,shot_names,comps):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    models={}
    for t in range(1,7):
        sub=[r for r in rows if int(r['home_goals'])+int(r['away_goals'])==t]
        y=[int(r['home_goals']) for r in sub]
        if len(sub)<80 or len(set(y))<2:continue
        m=make_pipeline(StandardScaler(),LogisticRegression(C=float(C),max_iter=4000,solver='lbfgs',class_weight=cweight(y,alpha)))
        m.fit([cond_x(r,shot_names,comps) for r in sub],y);models[t]=m
    # Frozen empirical 7+ conditional with additive smoothing over a bounded support.
    cells=[(h,a) for h in range(9) for a in range(9) if 7<=h+a<=10]
    cc=Counter((int(r['home_goals']),int(r['away_goals'])) for r in rows if int(r['home_goals'])+int(r['away_goals'])>=7)
    z=sum(cc[c]+0.25 for c in cells); q7={c:(cc[c]+0.25)/z for c in cells}
    return models,q7


def score_probs(r,total_models,cond_models,q7,shot_names,comps):
    pt=total.probs(total_models,r,shot_names,comps);out={}
    out[(0,0)]=pt[0]
    X=[cond_x(r,shot_names,comps)]
    for t in range(1,7):
        m=cond_models.get(t)
        if m is None:
            for h in range(t+1):out[(h,t-h)]=pt[t]/(t+1)
        else:
            q=[0.0]*(t+1)
            for cls,v in zip(m.classes_,m.predict_proba(X)[0]):q[int(cls)]=float(v)
            s=sum(q)
            for h,v in enumerate(q):out[(h,t-h)]=pt[t]*(v/s)
    for c,v in q7.items():out[c]=pt[7]*v
    s=sum(out.values());return {k:v/s for k,v in out.items()}


def formal_probs(r):
    d={(int(c['home_goals']),int(c['away_goals'])):float(c['probability']) for c in r['formal_matrix']};s=sum(d.values());return {k:v/s for k,v in d.items()}


def metrics(rows,getter):
    n=len(rows);h1=h3=0;ll=0.0;modes=Counter();draw_modes=0
    for r in rows:
        p=getter(r); ranked=sorted(p,key=lambda c:(-p[c],c[0],c[1])); actual=(int(r['home_goals']),int(r['away_goals'])); mode=ranked[0]
        h1+=mode==actual;h3+=actual in ranked[:3];ll+=-math.log(max(EPS,p.get(actual,0.0)));modes[f'{mode[0]}-{mode[1]}']+=1;draw_modes+=mode[0]==mode[1]
    return {'count':n,'top1':h1/n,'top3':h3/n,'logloss':ll/n,'draw_mode_rate':draw_modes/n,'mode_counts':dict(modes)}


def main():
    raw,_=shot.raw_stat_matches();lookup,_=shot.lagged_shot_lookup(raw);base_rows,_=oldscore.build_rows(lookup);rows=enrich(base_rows)
    shot_names=sorted(rows[0]['shots']);comps=sorted({r['competition_id'] for r in rows})
    train=[r for r in rows if r['season'] in {'2022/23','2023/24'}];valid=[r for r in rows if r['season']=='2024/25'];test=[r for r in rows if r['season']=='2025/26']
    # Score track independently selects its own total hierarchy on validation.
    base_total=total.metric(valid,lambda r:r['ou']);tboard=[]
    for C in total.CS:
        for alpha in total.ALPHAS:
            tm=total.fit_hier(train,C,alpha,shot_names,comps);met=total.metric(valid,lambda r,tm=tm:total.probs(tm,r,shot_names,comps));met.update({'C':C,'alpha':alpha,'proper_noninferior':met['rps']<=base_total['rps'] and met['logloss']<=base_total['logloss']});tboard.append(met)
    te=[x for x in tboard if x['proper_noninferior']];tsel=max(te,key=lambda x:(x['top1'],-x['rps'],-x['logloss'])) if te else min(tboard,key=lambda x:(x['rps'],x['logloss']))
    tm_train=total.fit_hier(train,tsel['C'],tsel['alpha'],shot_names,comps)
    baseline=metrics(valid,formal_probs);board=[]
    for C in COND_CS:
        for alpha in COND_ALPHAS:
            cm,q7=fit_cond(train,C,alpha,shot_names,comps);met=metrics(valid,lambda r,cm=cm,q7=q7:score_probs(r,tm_train,cm,q7,shot_names,comps));met.update({'C':C,'alpha':alpha,'logloss_noninferior':met['logloss']<=baseline['logloss']});board.append(met)
    elig=[x for x in board if x['logloss_noninferior']];sel=max(elig,key=lambda x:(x['top1'],x['top3'],-x['logloss'])) if elig else min(board,key=lambda x:x['logloss'])
    tm=total.fit_hier(train+valid,tsel['C'],tsel['alpha'],shot_names,comps);cm,q7=fit_cond(train+valid,sel['C'],sel['alpha'],shot_names,comps)
    sample=random.Random(SEED).sample(test,100)
    base=metrics(sample,formal_probs);chall=metrics(sample,lambda r:score_probs(r,tm,cm,q7,shot_names,comps))
    payload={'schema_version':'V6.20.1-independent-score-hierarchical-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_2025_26_FAST100_RESEARCH','design':{'train':['2022/23','2023/24'],'validation':'2024/25','test':'2025/26 fixed-seed random100','score_factorization':'independent P(T_score) x P(H|T_score,X)','cross_track_probability_feedback':False,'features':['strictly lagged shots/SOT/corners','de-vigged raw 1X2 market','O/U2.5','competition'],'no_manual_1_1_penalty':True},'row_counts':{'all':len(rows),'train':len(train),'validation':len(valid),'test_pool':len(test),'sample':100},'selected_total':tsel,'conditional_validation_baseline':baseline,'conditional_board':board,'selected_conditional':sel,'fast100':{'formal_score':base,'independent_score':chall,'delta_top1_pp':(chall['top1']-base['top1'])*100,'delta_top3_pp':(chall['top3']-base['top3'])*100},'collapse_audit':{'formal_1_1_modes':base['mode_counts'].get('1-1',0),'challenger_1_1_modes':chall['mode_counts'].get('1-1',0),'formal_draw_mode_rate':base['draw_mode_rate'],'challenger_draw_mode_rate':chall['draw_mode_rate']},'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'test_used_for_selection':False,'no_manual_score_bonus_or_penalty':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'selected_total':tsel,'selected_conditional':sel,'fast100':payload['fast100'],'collapse_audit':payload['collapse_audit']},ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
