#!/usr/bin/env python3
"""V6.51.1 class-separating discrete Naive Bayes draw specialist.

Motivation: V6.51.0 showed that the logistic draw posterior cannot safely re-enter
vetoed matches as DRAW.  This challenger tests a materially different model family:
a draw-vs-not-draw Naive Bayes whose continuous pre-match features are discretized
using ONLY pre-2024 labels to maximize univariate class separation.

This is inspired by the class-separating discretization/NB methodology in
Pérez-Blanco & Salmerón (2025), but is NOT claimed to reproduce that paper's exact
feature set or clustering algorithm.

Leakage discipline:
- feature cutpoints, feature ranking, NB likelihoods: dates < 2024-01-01 only;
- V6.50.9 error-veto and DRAW execution threshold: calendar 2024 validation only;
- exact V6.50.6 2025/2025-26 target: evaluated once, never used for selection;
- same-day team state is frozen before all results from that day are applied.

Research only; formal_weight=0; CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations

import bisect
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation', ROOT/'engine'):
    if str(p) not in sys.path:
        sys.path.insert(0,str(p))

import v6_draw_triage_decision_v6508 as tri
import v6_draw_risk_operating_point_v6509 as op
import v6_draw_residual_net_gain_v6507 as r7

OUT=ROOT/'manifests'/'v6_draw_discrete_nb_v6511_status.json'
FREEZE=ROOT/'manifests'/'v6_hierarchical_selector_forward_v6475_freeze.json'
MIN_BIN=100
TOP_FEATURES=8
LAPLACE=1.0
MIN_RETENTION=0.75
EPS=1e-12


def entropy(pos:int, neg:int)->float:
    n=pos+neg
    if n<=0: return 0.0
    out=0.0
    for c in (pos,neg):
        if c:
            p=c/n; out-=p*math.log(p)
    return out


def quantile(values:list[float], q:float)->float:
    if not values: return 0.0
    xs=sorted(values)
    x=(len(xs)-1)*q; lo=int(math.floor(x)); hi=int(math.ceil(x))
    if lo==hi: return xs[lo]
    return xs[lo]*(hi-x)+xs[hi]*(x-lo)


def split_score(values:list[float], y:list[int], cuts:list[float])->float:
    bins=[[0,0] for _ in range(len(cuts)+1)]
    for v,yy in zip(values,y):
        b=bisect.bisect_right(cuts,v); bins[b][0 if yy==1 else 1]+=1
    if any(sum(b)<MIN_BIN for b in bins): return -math.inf
    n=len(y); cond=sum((sum(b)/n)*entropy(b[0],b[1]) for b in bins)
    base=entropy(sum(y), n-sum(y))
    return base-cond


def learn_discretizer(rows:list[dict[str,Any]], j:int)->dict[str,Any]:
    vals=[float(r['x'][j]) for r in rows]; y=[1 if r['actual']=='draw' else 0 for r in rows]
    cand=sorted(set(round(quantile(vals,q),12) for q in (0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9)))
    best_cuts=[]; best=0.0
    for c in cand:
        s=split_score(vals,y,[c])
        if s>best: best=s; best_cuts=[c]
    # At most 3 bins. Pair search is small and entirely pre-target.
    for a_i in range(len(cand)):
        for b_i in range(a_i+1,len(cand)):
            cuts=[cand[a_i],cand[b_i]]; s=split_score(vals,y,cuts)
            if s>best: best=s; best_cuts=cuts
    return {'feature_index':j,'cuts':best_cuts,'information_gain_nats':best}


def fit_nb(rows:list[dict[str,Any]])->dict[str,Any]:
    # x[0] is intercept; x[1:26] are the 25 continuous pre-match features from V6.50.7.
    disc=[learn_discretizer(rows,j) for j in range(1,26)]
    disc=sorted(disc,key=lambda z:(-z['information_gain_nats'],z['feature_index']))[:TOP_FEATURES]
    draw_n=sum(r['actual']=='draw' for r in rows); nondraw_n=len(rows)-draw_n
    feature_tables={}
    for d in disc:
        j=d['feature_index']; cuts=d['cuts']; nb=len(cuts)+1
        tab={'draw':[0]*nb,'not_draw':[0]*nb}
        for r in rows:
            b=bisect.bisect_right(cuts,float(r['x'][j])); tab['draw' if r['actual']=='draw' else 'not_draw'][b]+=1
        feature_tables[str(j)]={'cuts':cuts,'counts':tab,'gain':d['information_gain_nats']}
    # Categorical likelihoods are represented once, not as correlated competition one-hots.
    comp_draw=Counter(); comp_non=Counter(); pick_draw=Counter(); pick_non=Counter()
    for r in rows:
        if r['actual']=='draw': comp_draw[r['competition_id']]+=1; pick_draw[r['pick']]+=1
        else: comp_non[r['competition_id']]+=1; pick_non[r['pick']]+=1
    return {
        'draw_n':draw_n,'not_draw_n':nondraw_n,'feature_tables':feature_tables,
        'selected_features':disc,'comp_draw':dict(comp_draw),'comp_non':dict(comp_non),
        'pick_draw':dict(pick_draw),'pick_non':dict(pick_non),
        'competition_count':len(set(r['competition_id'] for r in rows)),
    }


def posterior(model:dict[str,Any], r:dict[str,Any])->float:
    dn=int(model['draw_n']); nn=int(model['not_draw_n']); n=dn+nn
    ld=math.log((dn+LAPLACE)/(n+2*LAPLACE)); ln=math.log((nn+LAPLACE)/(n+2*LAPLACE))
    for sj,t in model['feature_tables'].items():
        j=int(sj); cuts=[float(x) for x in t['cuts']]; b=bisect.bisect_right(cuts,float(r['x'][j])); nb=len(cuts)+1
        cd=int(t['counts']['draw'][b]); cn=int(t['counts']['not_draw'][b])
        ld+=math.log((cd+LAPLACE)/(dn+LAPLACE*nb)); ln+=math.log((cn+LAPLACE)/(nn+LAPLACE*nb))
    cid=r['competition_id']; pick=r['pick']; nc=max(1,int(model['competition_count']))
    ld+=math.log((int(model['comp_draw'].get(cid,0))+LAPLACE)/(dn+LAPLACE*nc))
    ln+=math.log((int(model['comp_non'].get(cid,0))+LAPLACE)/(nn+LAPLACE*nc))
    ld+=math.log((int(model['pick_draw'].get(pick,0))+LAPLACE)/(dn+2*LAPLACE))
    ln+=math.log((int(model['pick_non'].get(pick,0))+LAPLACE)/(nn+2*LAPLACE))
    m=max(ld,ln); ed=math.exp(ld-m); en=math.exp(ln-m)
    return ed/(ed+en)


def reconstruct_veto(valid:list[dict[str,Any]])->dict[str,Any]:
    base_err,base_draw=op.error_and_draw_baselines(valid); curve=[]
    for k in range(25,71):
        th=k/100.0; m=tri.eval_rule(valid,False,th); rem=m['abstain_count']; rem_err=m['abstained_actual_draws']+m['abstained_opposite_ha']
        re=rem_err/rem if rem else None; rd=m['abstained_actual_draws']/rem if rem else None
        m.update({'veto_threshold':th,'removed_error_rate':re,'removed_draw_rate':rd})
        m['eligible']=bool(rem>=20 and m['retention_vs_base']>=MIN_RETENTION and m['executed_accuracy']>m['base_accuracy'] and (re or 0)>base_err and (rd or 0)>base_draw)
        curve.append(m)
    eligible=[m for m in curve if m['eligible']]
    if not eligible: raise RuntimeError('V6.50.9 veto cannot be reconstructed')
    return max(eligible,key=lambda m:(m['executed_accuracy'],m['retention_vs_base'],-m['veto_threshold']))


def eval_rule(rows:list[dict[str,Any]], veto:float, draw_th:float|None)->dict[str,Any]:
    hits=executed=draw_n=draw_hits=abstain=0; decisions=Counter(); veto_pool=0
    for r in rows:
        risky=float(r['p_error_model'])>=veto
        if not risky:
            executed+=1; hits+=int(r['actual']==r['pick']); decisions[r['pick']]+=1; continue
        veto_pool+=1
        if draw_th is not None and float(r['p_draw_nb'])>=draw_th:
            executed+=1; draw_n+=1; ok=r['actual']=='draw'; draw_hits+=int(ok); hits+=int(ok); decisions['draw']+=1
        else:
            abstain+=1; decisions['abstain']+=1
    return {
        'count':len(rows),'executed_count':executed,'hits':hits,'accuracy':hits/executed if executed else None,
        'retention_vs_base':executed/len(rows) if rows else None,'veto_pool_count':veto_pool,
        'draw_pick_count':draw_n,'draw_hits':draw_hits,'draw_precision':draw_hits/draw_n if draw_n else None,
        'abstain_count':abstain,'decision_counts':dict(decisions),
    }


def main()->int:
    freeze=json.loads(FREEZE.read_text(encoding='utf-8'))
    train,valid,target,data=tri.build_records(freeze)
    # P_BASE_ERROR is the already-established risk ranker; coefficients remain pre-2024.
    err_model=tri.fit_label(train,lambda r:r['actual']!=r['pick'])
    for rows in (valid,target):
        for r in rows: r['p_error_model']=r7.predict(err_model,r['x'])
    chosen_veto=reconstruct_veto(valid); veto=float(chosen_veto['veto_threshold'])
    nb=fit_nb(train)
    for rows in (valid,target):
        for r in rows: r['p_draw_nb']=posterior(nb,r)
    veto_only_valid=eval_rule(valid,veto,None)
    # Draw threshold selection is validation-only. Combined accuracy gate protects the existing >70% H/A surface.
    curve=[]
    for k in range(10,91):
        th=k/100.0; m=eval_rule(valid,veto,th); m['draw_threshold']=th
        m['eligible']=bool(m['draw_pick_count']>=20 and (m['draw_precision'] or 0)>=0.50 and (m['accuracy'] or 0)>=0.70 and m['executed_count']>veto_only_valid['executed_count'])
        curve.append(m)
    eligible=[m for m in curve if m['eligible']]
    chosen=max(eligible,key=lambda m:(m['executed_count'],m['accuracy'],m['draw_precision'],m['draw_threshold'])) if eligible else None
    # Diagnostic best precision with >=5 picks, never used as an execution rule if eligibility fails.
    diag=[m for m in curve if m['draw_pick_count']>=5]
    best_precision=max(diag,key=lambda m:(m['draw_precision'] or 0,m['draw_pick_count'])) if diag else None
    draw_th=float(chosen['draw_threshold']) if chosen else None
    target_veto=eval_rule(target,veto,None)
    target_result=eval_rule(target,veto,draw_th) if draw_th is not None else target_veto
    payload={
        'schema_version':'V6.51.1-class-separating-draw-nb-r1','generated_at_utc':r7.now(),'formal_current_version':'V5.0.1',
        'status':'PASS_RESEARCH_CHALLENGE' if chosen else 'REJECT_NO_VALIDATION_SAFE_NB_DRAW_REENTRY',
        'classification':'PRE2024_SUPERVISED_DISCRETIZATION_NB_2024_THRESHOLD_2025_TARGET_FORMAL_WEIGHT_0',
        'method':{
            'inspiration':'Pérez-Blanco & Salmerón 2025 class-oriented discretization + Naive Bayes draw prediction; implementation is project-specific, not an exact reproduction',
            'cutpoint_learning':'pre-2024 only; decile candidate cutpoints, up to 3 bins, maximize binary class information gain with minimum bin support',
            'feature_selection':'top 8 continuous features by pre-2024 information gain','categoricals':['competition','base_pick_direction'],
            'nb_laplace':LAPLACE,'veto_threshold_source':'V6.50.9 reconstructed on 2024 only','draw_threshold_selected_only_on_2024':True,
            'target_results_used_for_training_or_threshold':False,'market_probabilities_mutated':False,'same_day_policy':'predict all then update all',
        },
        'data':{**data,'train_n':len(train),'validation_n':len(valid),'target_n':len(target)},
        'model':{'selected_features':nb['selected_features'],'training_draw_rate':nb['draw_n']/(nb['draw_n']+nb['not_draw_n'])},
        'validation':{'chosen_veto':chosen_veto,'veto_only':veto_only_valid,'draw_threshold_curve':curve,'chosen_draw':chosen,'best_draw_precision_diagnostic_min5':best_precision},
        'target_2025':{'veto_only':target_veto,'with_nb_draw_reentry':target_result},
        'gates':{
            'target_accuracy_ge_70':bool((target_result['accuracy'] or 0)>=0.70),'target_draw_picks_positive':bool(target_result['draw_pick_count']>0),
            'target_draw_precision_ge_50':bool((target_result['draw_precision'] or 0)>=0.50),'target_coverage_improved_vs_veto_only':bool(target_result['executed_count']>target_veto['executed_count']),
            'formal_promotion_allowed':False,
        },
        'governance':{'research_only':True,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False,'formal_probability_change':False,'formal_selector_threshold_change':False,'historical_replay_cannot_promote':True},
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'selected_features':nb['selected_features'],'best_precision':best_precision,'chosen':chosen,'target':target_result,'gates':payload['gates']},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
