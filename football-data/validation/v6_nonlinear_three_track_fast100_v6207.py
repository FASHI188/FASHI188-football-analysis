#!/usr/bin/env python3
"""V6.20.7 nonlinear three-track Fast100 challengers.

Uses a NEW fixed-seed random100 from 2025/26 after V6.20.5 was inspected.
No test tuning. Total-goals challenges the linear hierarchical model with small
HistGradientBoosting hierarchies; candidates must be validation RPS and LogLoss
non-inferior. Exact score keeps its own T_score layer and independently selects the
conditional P(H|T,X) feature/model family by validation exact-score LogLoss.
"""
from __future__ import annotations
import json, math, random, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import v6_pure_independent_three_track_fast100_v6205 as base
from three_track_conflict_gate_v6202 import audit_three_tracks

OUT=ROOT/'manifests'/'v6_nonlinear_three_track_fast100_v6207_status.json'
SEED=20260725+6207
HGB_CFGS=((7,0.05,120),(15,0.04,140))
BALANCES=(0.0,0.25)
EPS=1e-15


def sw(y,alpha):
    if alpha<=0:return None
    c=Counter(y);mx=max(c.values());return [(mx/c[v])**alpha for v in y]

def fit_hgb(X,y,leaf,lr,iters,alpha):
    from sklearn.ensemble import HistGradientBoostingClassifier
    m=HistGradientBoostingClassifier(loss='log_loss',learning_rate=lr,max_iter=iters,max_leaf_nodes=leaf,l2_regularization=1.0,random_state=6207)
    m.fit(X,y,sample_weight=sw(y,alpha));return m

def fit_total_hgb(rows,cfg,alpha,shot_names,comps):
    X=[base.feat(r,shot_names,comps,True) for r in rows];y=[r['actual_total'] for r in rows];leaf,lr,iters=cfg
    coarse=fit_hgb(X,[base.grp(v) for v in y],leaf,lr,iters,alpha);children={}
    for g in range(3):
        ids=[i for i,v in enumerate(y) if base.grp(v)==g]; yy=[y[i] for i in ids]
        if len(set(yy))>1:children[g]=fit_hgb([X[i] for i in ids],yy,leaf,lr,iters,alpha)
    return coarse,children

def choose_total(select_train,valid,shot_names,comps):
    _,linear=base.select_total(select_train,valid,shot_names,comps,False,'rps');board=[]
    for cfg in HGB_CFGS:
        for alpha in BALANCES:
            m=fit_total_hgb(select_train,cfg,alpha,shot_names,comps);q=base.total_metrics(valid,lambda r,m=m:base.total_p(m,r,shot_names,comps,True));q.update({'family':'hgb_process','cfg':list(cfg),'alpha':alpha,'proper_noninferior':q['rps']<=linear['rps'] and q['logloss']<=linear['logloss']});board.append(q)
    ok=[x for x in board if x['proper_noninferior']]
    if ok:
        best=min(ok,key=lambda x:(x['rps'],x['logloss'],-x['top1']));return {'family':'hgb_process','selected':best,'linear_reference':linear,'board':board}
    return {'family':'linear_market','selected':linear,'linear_reference':linear,'board':board}

def make_total_model(rows,choice,shot_names,comps):
    if choice['family']=='hgb_process':
        x=choice['selected'];return fit_total_hgb(rows,tuple(x['cfg']),x['alpha'],shot_names,comps),True
    x=choice['selected'];return base.fit_total(rows,x['C'],x['alpha'],shot_names,comps,False),False


def fit_cond_hgb(rows,cfg,alpha,shot_names,comps):
    leaf,lr,iters=cfg;models={}
    for t in range(1,7):
        sub=[r for r in rows if r['home_goals']+r['away_goals']==t];y=[r['home_goals'] for r in sub]
        if len(sub)>=80 and len(set(y))>1:models[t]=fit_hgb([base.feat(r,shot_names,comps,True) for r in sub],y,leaf,lr,iters,alpha)
    cells=[(h,a) for h in range(11) for a in range(11) if 7<=h+a<=14];cc=Counter((r['home_goals'],r['away_goals']) for r in rows if r['home_goals']+r['away_goals']>=7);z=sum(cc[c]+0.25 for c in cells);return models,{c:(cc[c]+0.25)/z for c in cells}

def cond_score_p(r,score_total_model,cm,q7,shot_names,comps,conditional_process):
    pt=base.total_p(score_total_model,r,shot_names,comps,False);out={(0,0):pt[0]};X=[base.feat(r,shot_names,comps,conditional_process)]
    for t in range(1,7):
        m=cm.get(t)
        if m is None:
            for h in range(t+1):out[(h,t-h)]=pt[t]/(t+1)
        else:
            q=[0.0]*(t+1)
            for k,v in zip(m.classes_,m.predict_proba(X)[0]):q[int(k)]=float(v)
            s=sum(q)
            for h,v in enumerate(q):out[(h,t-h)]=pt[t]*v/s
    for c,v in q7.items():out[c]=pt[7]*v
    s=sum(out.values());return {k:v/s for k,v in out.items()}

def choose_score_cond(select_train,valid,score_total_model,shot_names,comps):
    board=[]
    # Logistic market-only and process versions are separate candidates.
    for proc in (False,True):
        for C in base.CS:
            for alpha in (0.0,0.25,0.5):
                cm,q7=base.fit_cond(select_train,C,alpha,shot_names,comps,proc)
                q=base.score_metrics(valid,lambda r,cm=cm,q7=q7,proc=proc:base.score_p(r,score_total_model,cm,q7,shot_names,comps,proc));q.update({'family':'logistic','process':proc,'C':C,'alpha':alpha});board.append(q)
    # Nonlinear process-only conditional challengers.
    for cfg in HGB_CFGS:
        for alpha in BALANCES:
            cm,q7=fit_cond_hgb(select_train,cfg,alpha,shot_names,comps)
            q=base.score_metrics(valid,lambda r,cm=cm,q7=q7:cond_score_p(r,score_total_model,cm,q7,shot_names,comps,True));q.update({'family':'hgb','process':True,'cfg':list(cfg),'alpha':alpha});board.append(q)
    sel=min(board,key=lambda x:(x['logloss'],-x['top1'],-x['top3']));return board,sel

def fit_chosen_cond(rows,sel,shot_names,comps):
    if sel['family']=='hgb':
        cm,q7=fit_cond_hgb(rows,tuple(sel['cfg']),sel['alpha'],shot_names,comps);return cm,q7,True,'hgb'
    cm,q7=base.fit_cond(rows,sel['C'],sel['alpha'],shot_names,comps,bool(sel['process']));return cm,q7,bool(sel['process']),'logistic'

def score_with_chosen(r,score_total_model,cm,q7,proc,family,shot_names,comps):
    if family=='hgb':return cond_score_p(r,score_total_model,cm,q7,shot_names,comps,proc)
    return base.score_p(r,score_total_model,cm,q7,shot_names,comps,proc)

def main():
    rows,shot_names=base.rows_fast();comps=sorted({r['competition_id'] for r in rows});select_train=[r for r in rows if r['season'] in {'2022/23','2023/24'}];valid=[r for r in rows if r['season']=='2024/25'];final_train=[r for r in rows if r['season'] in {'2023/24','2024/25'}];test=[r for r in rows if r['season']=='2025/26']
    total_choice=choose_total(select_train,valid,shot_names,comps);total_model,total_process=make_total_model(final_train,total_choice,shot_names,comps)
    # Score owns its T_score layer. Keep linear market T_score as a stable base, selected LogLoss-first.
    _,score_t_sel=base.select_total(select_train,valid,shot_names,comps,False,'score_total_logloss');score_total_select=base.fit_total(select_train,score_t_sel['C'],score_t_sel['alpha'],shot_names,comps,False)
    cond_board,cond_sel=choose_score_cond(select_train,valid,score_total_select,shot_names,comps)
    score_total_model=base.fit_total(final_train,score_t_sel['C'],score_t_sel['alpha'],shot_names,comps,False);cm,q7,cond_proc,cond_family=fit_chosen_cond(final_train,cond_sel,shot_names,comps)
    sample=random.Random(SEED).sample(test,100);one=base.one_metrics(sample);tot=base.total_metrics(sample,lambda r:base.total_p(total_model,r,shot_names,comps,total_process));scr=base.score_metrics(sample,lambda r:score_with_chosen(r,score_total_model,cm,q7,cond_proc,cond_family,shot_names,comps))
    # Same-sample linear baselines, selected only on validation.
    _,lin_total_sel=base.select_total(select_train,valid,shot_names,comps,False,'rps');lin_total=base.fit_total(final_train,lin_total_sel['C'],lin_total_sel['alpha'],shot_names,comps,False);lin_tot=base.total_metrics(sample,lambda r:base.total_p(lin_total,r,shot_names,comps,False))
    lin_score_total=base.fit_total(final_train,score_t_sel['C'],score_t_sel['alpha'],shot_names,comps,False);lin_cb,lin_cs=base.select_cond(select_train,valid,score_total_select,shot_names,comps,False);lin_cm,lin_q7=base.fit_cond(final_train,lin_cs['C'],lin_cs['alpha'],shot_names,comps,False);lin_scr=base.score_metrics(sample,lambda r:base.score_p(r,lin_score_total,lin_cm,lin_q7,shot_names,comps,False))
    reasons=Counter();allowed=ahit=0
    for r in sample:
        tp=base.total_p(total_model,r,shot_names,comps,total_process);sp=score_with_chosen(r,score_total_model,cm,q7,cond_proc,cond_family,shot_names,comps);rank=sorted(sp,key=lambda c:(-sp[c],c[0],c[1]));sr=[{'home_goals':h,'away_goals':a,'probability':sp[(h,a)]} for h,a in rank];g=audit_three_tracks(r['one'],tp,sr,score_model_passed=True);reasons.update(g['reasons'])
        if g['score_exact_allowed']:allowed+=1;ahit+=int(rank[0]==(r['home_goals'],r['away_goals']))
    payload={'schema_version':'V6.20.7-nonlinear-three-track-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_NEW_FIXED_SEED_2025_26_FAST100_RESEARCH','design':{'new_seed_after_v6205_inspection':True,'selection_train':['2022/23','2023/24'],'validation':'2024/25','final_train':['2023/24','2024/25'],'test':'2025/26 fixed-seed random100','cross_track_feedback':False,'score_conditional_process_selection_independent_of_score_total_process':True},'validation':{'total_choice':total_choice,'score_t_selected':score_t_sel,'score_conditional_selected':cond_sel,'score_conditional_board':cond_board},'fast100':{'one_x_two':one,'linear_total_reference':lin_tot,'total_challenger':tot,'total_delta_top1_pp':(tot['top1']-lin_tot['top1'])*100,'linear_score_reference':lin_scr,'score_challenger':scr,'score_delta_top1_pp':(scr['top1']-lin_scr['top1'])*100,'score_delta_top3_pp':(scr['top3']-lin_scr['top3'])*100,'score_1_1_modes':scr['mode_counts'].get('1-1',0),'total_4plus_modes':sum(v for k,v in tot['mode_counts'].items() if int(k)>=4)},'conflict_gate':{'coverage':allowed/100,'allowed_top1_accuracy':ahit/allowed if allowed else None,'reason_counts':dict(reasons)},'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'test_used_for_selection':False,'no_manual_tail_or_1_1_adjustment':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'validation_selected':payload['validation'],'fast100':payload['fast100'],'conflict_gate':payload['conflict_gate']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
