#!/usr/bin/env python3
"""V6.20.9 ordinal-total + Bayes score-allocation Fast100.

NEW fixed-seed 2025/26 random100 after V6.20.7 was inspected. No test tuning.
Total track models seven ordered survival probabilities P(T>k), k=0..6, then
monotone-projects them into P(T=0..6,7+). Score track owns its own T_score model and
allocates each total across home/draw/away score cells using de-vigged market result
probabilities times training-only P(T=t|result) likelihoods; within each feasible result
group it uses smoothed training score shares. No manual 1-1 or tail adjustments.
"""
from __future__ import annotations
import json, math, random, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import v6_pure_independent_three_track_fast100_v6205 as base

OUT=ROOT/'manifests'/'v6_ordinal_total_bayes_score_fast100_v6209_status.json'
SEED=20260725+6209
CS=(0.03,0.1,0.3)
GAMMAS=(0.75,1.0,1.25)
BETAS=(0.5,1.0)
EPS=1e-15


def fit_ord(rows,C,shot_names,comps,process):
    models=[]
    for k in range(7):
        y=[int(r['actual_total']>k) for r in rows]
        models.append(base.fit_lr([base.feat(r,shot_names,comps,process) for r in rows],y,C,0.0))
    return models

def pava_nonincreasing(vals):
    blocks=[]
    for i,v in enumerate(vals):
        blocks.append([i,i,float(v),1.0])
        while len(blocks)>=2 and blocks[-2][2] < blocks[-1][2]:
            b=blocks.pop();a=blocks.pop();w=a[3]+b[3];avg=(a[2]*a[3]+b[2]*b[3])/w;blocks.append([a[0],b[1],avg,w])
    out=[0.0]*len(vals)
    for lo,hi,v,_ in blocks:
        for i in range(lo,hi+1):out[i]=min(1.0,max(0.0,v))
    return out

def ord_p(models,r,shot_names,comps,process):
    X=[base.feat(r,shot_names,comps,process)];s=[]
    for m in models:
        arr=m.predict_proba(X)[0];mp={int(c):float(v) for c,v in zip(m.classes_,arr)};s.append(mp.get(1,0.0))
    s=pava_nonincreasing(s);p=[0.0]*8;p[0]=1-s[0]
    for t in range(1,7):p[t]=s[t-1]-s[t]
    p[7]=s[6];z=sum(p);return [max(0.0,v)/z for v in p]

def choose_ord_total(train,valid,shot_names,comps,objective='rps'):
    board=[]
    for process in (False,True):
        for C in CS:
            m=fit_ord(train,C,shot_names,comps,process);q=base.total_metrics(valid,lambda r,m=m,process=process:ord_p(m,r,shot_names,comps,process));q.update({'family':'ordinal','process':process,'C':C});board.append(q)
    if objective=='logloss':sel=min(board,key=lambda x:(x['logloss'],x['rps'],-x['top1']))
    else:sel=min(board,key=lambda x:(x['rps'],x['logloss'],-x['top1']))
    return board,sel

def result_idx(h,a):return 0 if h>a else 1 if h==a else 2

def fit_bayes_stats(rows):
    # likelihood counts P(total bucket | result) and cell shares P(score | total,result)
    res_tot=Counter();res_n=Counter();cell=Counter();tr=Counter()
    for r in rows:
        h,a=int(r['home_goals']),int(r['away_goals']);t=min(7,h+a);z=result_idx(h,a);res_tot[(z,t)]+=1;res_n[z]+=1;cell[(t,z,h,a)]+=1;tr[(t,z)]+=1
    likelihood={}
    for z in range(3):
        den=res_n[z]+8.0
        for t in range(8):likelihood[(z,t)]=(res_tot[(z,t)]+1.0)/den
    return likelihood,cell,tr

def feasible_cells(t,z):
    if t==7:
        return [(h,a) for h in range(11) for a in range(11) if 7<=h+a<=14 and result_idx(h,a)==z]
    return [(h,t-h) for h in range(t+1) if result_idx(h,t-h)==z]

def score_bayes_p(r,pt,stats,gamma,beta):
    likelihood,cell,tr=stats;out={}
    for t,mass in enumerate(pt):
        if mass<=0:continue
        groups=[];gw=[]
        for z in range(3):
            cells=feasible_cells(t,z)
            if not cells:continue
            w=(max(EPS,float(r['one'][z]))**gamma)*(max(EPS,likelihood[(z,t)])**beta)
            groups.append((z,cells));gw.append(w)
        zs=sum(gw)
        for (z,cells),w in zip(groups,gw):
            gm=mass*w/zs if zs>0 else mass/len(groups);den=tr[(t,z)]+0.5*len(cells)
            for h,a in cells:out[(h,a)]=out.get((h,a),0.0)+gm*(cell[(t,z,h,a)]+0.5)/den
    s=sum(out.values());return {k:v/s for k,v in out.items()}

def choose_score(train,valid,shot_names,comps):
    stats=fit_bayes_stats(train);t_candidates=[]
    # Score chooses its own T_score family on validation exact-score logloss, not total-track selection.
    _,hier=base.select_total(train,valid,shot_names,comps,False,'score_total_logloss');hm=base.fit_total(train,hier['C'],hier['alpha'],shot_names,comps,False);t_candidates.append(('hierarchy',hier,lambda r,hm=hm:base.total_p(hm,r,shot_names,comps,False)))
    ob,os=choose_ord_total(train,valid,shot_names,comps,'logloss')
    om=fit_ord(train,os['C'],shot_names,comps,bool(os['process']));t_candidates.append(('ordinal',os,lambda r,om=om,proc=bool(os['process']):ord_p(om,r,shot_names,comps,proc)))
    board=[]
    for family,tmeta,getpt in t_candidates:
        for gamma in GAMMAS:
            for beta in BETAS:
                q=base.score_metrics(valid,lambda r,getpt=getpt,gamma=gamma,beta=beta:score_bayes_p(r,getpt(r),stats,gamma,beta));q.update({'t_family':family,'t_meta':tmeta,'gamma':gamma,'beta':beta});board.append(q)
    sel=min(board,key=lambda x:(x['logloss'],-x['top1'],-x['top3']));return board,sel,ob

def build_score_final(train,sel,shot_names,comps):
    stats=fit_bayes_stats(train)
    if sel['t_family']=='ordinal':
        x=sel['t_meta'];m=fit_ord(train,x['C'],shot_names,comps,bool(x['process']));get=lambda r:ord_p(m,r,shot_names,comps,bool(x['process']))
    else:
        x=sel['t_meta'];m=base.fit_total(train,x['C'],x['alpha'],shot_names,comps,False);get=lambda r:base.total_p(m,r,shot_names,comps,False)
    return lambda r:score_bayes_p(r,get(r),stats,sel['gamma'],sel['beta'])

def main():
    rows,shot_names=base.rows_fast();comps=sorted({r['competition_id'] for r in rows});select_train=[r for r in rows if r['season'] in {'2022/23','2023/24'}];valid=[r for r in rows if r['season']=='2024/25'];final_train=[r for r in rows if r['season'] in {'2023/24','2024/25'}];test=[r for r in rows if r['season']=='2025/26']
    # Independent total track: ordinal candidate must beat the hierarchy on both proper scores to replace it.
    _,hier=base.select_total(select_train,valid,shot_names,comps,False,'rps');ord_board,ord_sel=choose_ord_total(select_train,valid,shot_names,comps,'rps');ord_pass=ord_sel['rps']<=hier['rps'] and ord_sel['logloss']<=hier['logloss']
    if ord_pass:
        total_family='ordinal';total_meta=ord_sel;tm=fit_ord(final_train,ord_sel['C'],shot_names,comps,bool(ord_sel['process']));get_total=lambda r:ord_p(tm,r,shot_names,comps,bool(ord_sel['process']))
    else:
        total_family='hierarchy';total_meta=hier;tm=base.fit_total(final_train,hier['C'],hier['alpha'],shot_names,comps,False);get_total=lambda r:base.total_p(tm,r,shot_names,comps,False)
    score_board,score_sel,score_ord_board=choose_score(select_train,valid,shot_names,comps);get_score=build_score_final(final_train,score_sel,shot_names,comps)
    sample=random.Random(SEED).sample(test,100);one=base.one_metrics(sample);tot=base.total_metrics(sample,get_total);scr=base.score_metrics(sample,get_score)
    # Same-sample references selected only from validation.
    ref_tm=base.fit_total(final_train,hier['C'],hier['alpha'],shot_names,comps,False);ref_tot=base.total_metrics(sample,lambda r:base.total_p(ref_tm,r,shot_names,comps,False))
    _,ref_score_t=base.select_total(select_train,valid,shot_names,comps,False,'score_total_logloss');ref_stm=base.fit_total(select_train,ref_score_t['C'],ref_score_t['alpha'],shot_names,comps,False);_,ref_cond=base.select_cond(select_train,valid,ref_stm,shot_names,comps,False);ref_stm_final=base.fit_total(final_train,ref_score_t['C'],ref_score_t['alpha'],shot_names,comps,False);ref_cm,ref_q7=base.fit_cond(final_train,ref_cond['C'],ref_cond['alpha'],shot_names,comps,False);ref_scr=base.score_metrics(sample,lambda r:base.score_p(r,ref_stm_final,ref_cm,ref_q7,shot_names,comps,False))
    payload={'schema_version':'V6.20.9-ordinal-total-bayes-score-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_NEW_FIXED_SEED_2025_26_FAST100_RESEARCH','design':{'new_seed_after_v6207_inspection':True,'selection_train':['2022/23','2023/24'],'validation':'2024/25','final_train':['2023/24','2024/25'],'test':'2025/26 fixed-seed random100','total_method':'ordinal survival CDF with PAVA candidate','score_method':'own T_score + Bayes P(result|T,market) + empirical score share','cross_track_feedback':False,'manual_1_1_or_tail_adjustment':False},'validation':{'hier_total_reference':hier,'ordinal_total_selected':ord_sel,'ordinal_total_board':ord_board,'ordinal_total_pass':ord_pass,'score_selected':score_sel,'score_board':score_board,'score_ordinal_total_board':score_ord_board},'fast100':{'one_x_two':one,'linear_total_reference':ref_tot,'total_family':total_family,'total_selected_meta':total_meta,'total_challenger':tot,'total_delta_top1_pp':(tot['top1']-ref_tot['top1'])*100,'linear_score_reference':ref_scr,'score_challenger':scr,'score_delta_top1_pp':(scr['top1']-ref_scr['top1'])*100,'score_delta_top3_pp':(scr['top3']-ref_scr['top3'])*100,'score_1_1_modes':scr['mode_counts'].get('1-1',0),'total_4plus_modes':sum(v for k,v in tot['mode_counts'].items() if int(k)>=4)},'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'test_used_for_selection':False,'all_tracks_independent':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'validation_selected':{'ordinal_total':ord_sel,'ordinal_pass':ord_pass,'score':score_sel},'fast100':payload['fast100']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
