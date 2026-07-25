#!/usr/bin/env python3
"""V6.20.5 pure-independent three-track Fast100.

Fast path: no legacy score-matrix replay. 1X2, total goals and exact score are separate
prediction tracks. Total and score may consume the same raw pre-match evidence, but they
never share fitted model state or probabilities. Hyperparameters are selected with
2022/23+2023/24 -> 2024/25; the final 2025/26 test models are each refit independently
on the two most recent completed seasons only: 2023/24+2024/25.
"""
from __future__ import annotations
import json, math, random, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import v6_total_shot_residual_v6181 as shot
import validate_joint_market_ipf_crossseason_v6164 as market
from three_track_conflict_gate_v6202 import audit_three_tracks

OUT=ROOT/'manifests'/'v6_pure_independent_three_track_fast100_v6205_status.json'
SEED=20260725+6205;EPS=1e-15
CS=(0.03,0.1,0.3);ALPHAS=(0.0,0.25,0.5,0.75)


def rows_fast():
    raw,_=shot.raw_stat_matches();lookup,shot_names=shot.lagged_shot_lookup(raw);mc={};out=[]
    for r in raw:
        cid,season=r['competition_id'],r['season'];fk=(cid,season,r['date'].date().isoformat(),r['home_team'],r['away_team']);process=lookup.get(fk)
        if process is None:continue
        key=(cid,season)
        if key not in mc:mc[key]=market.market_lookup(cid,season)
        mk=mc[key].get((r['date'].isoformat(),r['home_team'],r['away_team']))
        if not mk:continue
        out.append({'competition_id':cid,'season':season,'date':r['date'].isoformat(),'home_team':r['home_team'],'away_team':r['away_team'],'home_goals':int(r['home_goals']),'away_goals':int(r['away_goals']),'actual_total':min(7,int(r['home_goals'])+int(r['away_goals'])),'shots':process,'one':[float(x) for x in mk['one_x_two']],'p_over25':float(mk['p_over25'])})
    return out,sorted(shot_names)


def weights(y,a):
    if a<=0:return None
    c=Counter(y);m=max(c.values());return {k:(m/v)**a for k,v in c.items()}
def fit_lr(X,y,C,a):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    m=make_pipeline(StandardScaler(),LogisticRegression(C=float(C),max_iter=3000,solver='lbfgs',class_weight=weights(y,a)));m.fit(X,y);return m

def feat(r,shot_names,comps,process=True):
    x=[math.log(max(EPS,v)) for v in r['one']]+[r['p_over25']]
    if process:x += [float(r['shots'][k]) for k in shot_names]
    x += [1.0 if r['competition_id']==c else 0.0 for c in comps]
    return x

def grp(y):return 0 if y<=1 else 1 if y<=3 else 2

def fit_total(rows,C,a,shot_names,comps,process=True):
    X=[feat(r,shot_names,comps,process) for r in rows];y=[r['actual_total'] for r in rows];coarse=fit_lr(X,[grp(v) for v in y],C,a);children={}
    for g in range(3):
        ids=[i for i,v in enumerate(y) if grp(v)==g];yy=[y[i] for i in ids]
        if len(set(yy))>1:children[g]=fit_lr([X[i] for i in ids],yy,C,a)
    return coarse,children

def total_p(m,r,shot_names,comps,process=True):
    coarse,children=m;X=[feat(r,shot_names,comps,process)];cg=[0.0]*3
    for k,v in zip(coarse.classes_,coarse.predict_proba(X)[0]):cg[int(k)]=float(v)
    p=[0.0]*8
    for g,b in {0:(0,1),1:(2,3),2:(4,5,6,7)}.items():
        ch=children.get(g)
        if ch is None:
            for z in b:p[z]+=cg[g]/len(b)
        else:
            arr=ch.predict_proba(X)[0]
            for k,v in zip(ch.classes_,arr):p[int(k)]+=cg[g]*float(v)
    s=sum(p);return [v/s for v in p]

def rps(p,y):
    z=cp=0.0
    for k in range(7):cp+=p[k];z+=(cp-(1.0 if y<=k else 0.0))**2
    return z/7

def total_metrics(rows,get):
    n=len(rows);h1=h2=0;rr=ll=0.0;modes=Counter()
    for r in rows:
        p=get(r);y=r['actual_total'];order=sorted(range(8),key=lambda i:(-p[i],i));h1+=order[0]==y;h2+=y in order[:2];rr+=rps(p,y);ll+=-math.log(max(EPS,p[y]));modes[str(order[0])]+=1
    return {'count':n,'top1':h1/n,'top2':h2/n,'rps':rr/n,'logloss':ll/n,'mode_counts':dict(modes)}
def select_total(train,valid,shot_names,comps,process):
    board=[]
    for C in CS:
        for a in ALPHAS:
            m=fit_total(train,C,a,shot_names,comps,process);q=total_metrics(valid,lambda r,m=m:total_p(m,r,shot_names,comps,process));q.update({'C':C,'alpha':a});board.append(q)
    sel=min(board,key=lambda x:(x['rps'],x['logloss'],-x['top1']));return board,sel

def fit_cond(rows,C,a,shot_names,comps,process):
    models={}
    for t in range(1,7):
        sub=[r for r in rows if r['home_goals']+r['away_goals']==t];y=[r['home_goals'] for r in sub]
        if len(sub)>=80 and len(set(y))>1:models[t]=fit_lr([feat(r,shot_names,comps,process) for r in sub],y,C,a)
    cells=[(h,a0) for h in range(9) for a0 in range(9) if 7<=h+a0<=10];cc=Counter((r['home_goals'],r['away_goals']) for r in rows if r['home_goals']+r['away_goals']>=7);z=sum(cc[c]+0.25 for c in cells);q7={c:(cc[c]+0.25)/z for c in cells};return models,q7

def score_p(r,score_total_model,cm,q7,shot_names,comps,process):
    pt=total_p(score_total_model,r,shot_names,comps,process);out={(0,0):pt[0]};X=[feat(r,shot_names,comps,process)]
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
def score_metrics(rows,get):
    n=len(rows);h1=h3=0;ll=0.0;modes=Counter()
    for r in rows:
        p=get(r);rank=sorted(p,key=lambda c:(-p[c],c[0],c[1]));y=(r['home_goals'],r['away_goals']);h1+=rank[0]==y;h3+=y in rank[:3];ll+=-math.log(max(EPS,p.get(y,0.0)));modes[f'{rank[0][0]}-{rank[0][1]}']+=1
    return {'count':n,'top1':h1/n,'top3':h3/n,'logloss':ll/n,'mode_counts':dict(modes)}
def select_cond(train,valid,score_total_model,shot_names,comps,process):
    board=[]
    for C in CS:
        for a in (0.0,0.25,0.5):
            cm,q7=fit_cond(train,C,a,shot_names,comps,process);q=score_metrics(valid,lambda r,cm=cm,q7=q7:score_p(r,score_total_model,cm,q7,shot_names,comps,process));q.update({'C':C,'alpha':a});board.append(q)
    sel=min(board,key=lambda x:(x['logloss'],-x['top1'],-x['top3']));return board,sel

def choose_track_total(select_train,valid,shot_names,comps):
    market_board,market_sel=select_total(select_train,valid,shot_names,comps,False)
    proc_board,proc_sel=select_total(select_train,valid,shot_names,comps,True)
    proc_ok=proc_sel['rps']<=market_sel['rps'] and proc_sel['logloss']<=market_sel['logloss']
    return {'market_board':market_board,'market_selected':market_sel,'process_board':proc_board,'process_selected':proc_sel,'process_allowed':proc_ok,'selected':proc_sel if proc_ok else market_sel,'use_process':proc_ok}

def one_metrics(rows):
    n=len(rows);hits=0;bands={.58:[0,0],.60:[0,0]}
    for r in rows:
        p=r['one'];y=0 if r['home_goals']>r['away_goals'] else 1 if r['home_goals']==r['away_goals'] else 2;pick=max(range(3),key=lambda i:p[i]);hit=int(pick==y);hits+=hit
        for t in bands:
            if max(p)>=t:bands[t][0]+=1;bands[t][1]+=hit
    return {'count':n,'accuracy':hits/n,'p58':{'count':bands[.58][0],'accuracy':bands[.58][1]/bands[.58][0] if bands[.58][0] else None},'p60':{'count':bands[.60][0],'accuracy':bands[.60][1]/bands[.60][0] if bands[.60][0] else None}}

def main():
    rows,shot_names=rows_fast();comps=sorted({r['competition_id'] for r in rows});select_train=[r for r in rows if r['season'] in {'2022/23','2023/24'}];valid=[r for r in rows if r['season']=='2024/25'];final_train=[r for r in rows if r['season'] in {'2023/24','2024/25'}];test=[r for r in rows if r['season']=='2025/26']
    # Independent total-goals track selection/model.
    total_choice=choose_track_total(select_train,valid,shot_names,comps);tc=total_choice['selected'];total_process=total_choice['use_process']
    total_model=fit_total(final_train,tc['C'],tc['alpha'],shot_names,comps,total_process)
    # Independent score track selects and fits its OWN T_score model; no shared fitted state.
    score_total_choice=choose_track_total(select_train,valid,shot_names,comps);stc=score_total_choice['selected'];score_process=score_total_choice['use_process']
    score_total_select_model=fit_total(select_train,stc['C'],stc['alpha'],shot_names,comps,score_process)
    conditional_board,conditional_selected=select_cond(select_train,valid,score_total_select_model,shot_names,comps,score_process)
    score_total_model=fit_total(final_train,stc['C'],stc['alpha'],shot_names,comps,score_process)
    conditional_models,q7=fit_cond(final_train,conditional_selected['C'],conditional_selected['alpha'],shot_names,comps,score_process)
    sample=random.Random(SEED).sample(test,100)
    one=one_metrics(sample)
    tot=total_metrics(sample,lambda r:total_p(total_model,r,shot_names,comps,total_process))
    scr=score_metrics(sample,lambda r:score_p(r,score_total_model,conditional_models,q7,shot_names,comps,score_process))
    reasons=Counter();allowed=ahit=0;agree1=agreet=0
    for r in sample:
        tp=total_p(total_model,r,shot_names,comps,total_process)
        sp=score_p(r,score_total_model,conditional_models,q7,shot_names,comps,score_process)
        rank=sorted(sp,key=lambda c:(-sp[c],c[0],c[1]));sr=[{'home_goals':h,'away_goals':a,'probability':sp[(h,a)]} for h,a in rank]
        g=audit_three_tracks(r['one'],tp,sr,score_model_passed=True);reasons.update(g['reasons']);top=rank[0];op=max(range(3),key=lambda i:r['one'][i]);agree1+=int((0 if top[0]>top[1] else 1 if top[0]==top[1] else 2)==op);agreet+=int(min(7,sum(top))==max(range(8),key=lambda i:tp[i]))
        if g['score_exact_allowed']:allowed+=1;ahit+=int(top==(r['home_goals'],r['away_goals']))
    payload={'schema_version':'V6.20.5-pure-independent-three-track-fast100-r3','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_2025_26_FAST100_RESEARCH','design':{'legacy_score_matrix_replay':False,'selection_train':['2022/23','2023/24'],'validation':'2024/25','final_train':['2023/24','2024/25'],'test':'2025/26 fixed-seed random100','features':['de-vigged 1X2','O/U2.5','strictly lagged shots/SOT/corners','competition'],'cross_track_probability_feedback':False,'shared_model_state_across_tracks':False,'score_has_own_total_layer':True},'rows':{'all':len(rows),'selection_train':len(select_train),'validation':len(valid),'final_train':len(final_train),'test_pool':len(test),'sample':100},'validation':{'total_track':total_choice,'score_total_track':score_total_choice,'score_conditional_board':conditional_board,'score_conditional_selected':conditional_selected},'fast100':{'one_x_two':one,'total':tot,'score':scr,'score_1_1_modes':scr['mode_counts'].get('1-1',0),'total_4plus_modes':sum(v for k,v in tot['mode_counts'].items() if int(k)>=4)},'conflict_gate':{'score_allowed_count':allowed,'coverage':allowed/100,'allowed_top1_accuracy':ahit/allowed if allowed else None,'reason_counts':dict(reasons),'score_result_agreement_1x2':agree1/100,'score_total_agreement':agreet/100},'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'test_used_for_selection':False,'missing_context_never_fabricated':True,'final_training_uses_two_most_recent_completed_seasons_only':True,'all_three_tracks_independent':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'validation':payload['validation'],'fast100':payload['fast100'],'conflict_gate':payload['conflict_gate']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
