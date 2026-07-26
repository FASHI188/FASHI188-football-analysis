#!/usr/bin/env python3
"""V6.26.9 final Fast100 challenge: direct multinomial 1X2 market-residual head.

Model
-----
Market 1X2 logits are an OFFSET, not a fitted feature. The football model contributes only its
log-odds residual versus market:
    dH = log(F_H/F_A) - log(M_H/M_A)
    dD = log(F_D/F_A) - log(M_D/M_A)
For each competition/target season, a parsimonious multinomial correction is trained on strictly
EARLIER seasons only:
    zH = log(M_H/M_A) + beta_H * [1, std(dH), std(dD)]
    zD = log(M_D/M_A) + beta_D * [1, std(dH), std(dD)]
    zA = 0
Parameters minimize prior-season multiclass log loss with deterministic L2 shrinkage lambda=1/n.
The convex objective is solved by gradient descent with Armijo backtracking; there is no test-set
grid search, selector, or fitted hyperparameter. If no prior-season rows exist, equal log pool is
the fixed fallback.

Evaluation
----------
Exact same fixed-seed 100 legal matches as V6.26.4. Total-goals head is unchanged. Exact score is
reconciled last by the V6.26 core. The pre-declared Fast100 continuation gate is:
  * 1X2 Top1 >= formal + 5 percentage points;
  * final joint score log loss no worse than formal;
  * total-goals RPS no worse than formal.
Random100 is diagnostic only and can never promote CURRENT.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))

import three_stage_core_v6260 as core  # noqa: E402
import validate_architecture_order_v6190 as arch  # noqa: E402
import validate_decoupled_1x2_total_fusion_v6191 as dec  # noqa: E402
import validate_market_ou_kl_projection_v6162 as ou  # noqa: E402
import validate_three_stage_random100_v6264 as r100  # noqa: E402
from football_v460_engine import load_config,predict_from_history  # noqa: E402
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import derive_score_marginals  # noqa: E402

OUT=ROOT/'manifests'/'v6_three_stage_1x2_direct_residual_random100_v6269_status.json'
EPS=1e-15


def avg(rows,key):return sum(float(r[key]) for r in rows)/len(rows) if rows else None

def safe_log(x):return math.log(max(EPS,float(x)))

def residual_pair(formal,market):
    return (
        (safe_log(formal[0])-safe_log(formal[2]))-(safe_log(market[0])-safe_log(market[2])),
        (safe_log(formal[1])-safe_log(formal[2]))-(safe_log(market[1])-safe_log(market[2])),
    )

def softmax3(z0,z1):
    m=max(z0,z1,0.0);a=math.exp(z0-m);d=math.exp(z1-m);w=math.exp(-m);s=a+d+w
    return [a/s,d/s,w/s]

def equal_logpool(formal,market):
    raw=[math.sqrt(max(EPS,float(a))*max(EPS,float(b))) for a,b in zip(formal,market)];s=sum(raw)
    return [x/s for x in raw]

def prepare_training(rows):
    if not rows:return None
    raw=[residual_pair(r['formal'],r['market']) for r in rows]
    means=[sum(v[j] for v in raw)/len(raw) for j in range(2)]
    scales=[]
    for j in range(2):
        var=sum((v[j]-means[j])**2 for v in raw)/len(raw);scales.append(max(1e-6,math.sqrt(var)))
    prepared=[]
    for r,(dh,dd) in zip(rows,raw):
        x=[1.0,(dh-means[0])/scales[0],(dd-means[1])/scales[1]]
        mh=safe_log(r['market'][0])-safe_log(r['market'][2]);md=safe_log(r['market'][1])-safe_log(r['market'][2])
        prepared.append((x,mh,md,int(r['actual'])))
    return {'rows':prepared,'means':means,'scales':scales}

def objective_gradient(beta,data,ridge):
    # beta = [H:b0,b1,b2,D:b0,b1,b2]
    obj=0.0;g=[0.0]*6;n=len(data)
    for x,mh,md,y in data:
        zh=mh+sum(beta[j]*x[j] for j in range(3));zd=md+sum(beta[3+j]*x[j] for j in range(3));q=softmax3(zh,zd)
        obj-=safe_log(q[y])
        eh=q[0]-(1.0 if y==0 else 0.0);ed=q[1]-(1.0 if y==1 else 0.0)
        for j in range(3):g[j]+=eh*x[j];g[3+j]+=ed*x[j]
    obj/=n;g=[v/n for v in g]
    # Fixed deterministic shrinkage. Penalize residual coefficients including intercepts.
    obj+=0.5*ridge*sum(v*v for v in beta)
    g=[g[i]+ridge*beta[i] for i in range(6)]
    return obj,g

def fit_model(training):
    prep=prepare_training(training)
    if prep is None:return None,{'status':'FALLBACK_EQUAL_LOGPOOL','training_count':0}
    data=prep['rows'];n=len(data);ridge=1.0/max(1,n);beta=[0.0]*6;converged=False;iters=0
    for it in range(1,301):
        iters=it;obj,g=objective_gradient(beta,data,ridge);g2=sum(v*v for v in g)
        if math.sqrt(g2)<=1e-8:converged=True;break
        step=1.0;accepted=False
        for _ in range(30):
            cand=[b-step*gg for b,gg in zip(beta,g)];cobj,_=objective_gradient(cand,data,ridge)
            if cobj<=obj-1e-4*step*g2:
                beta=cand;accepted=True;break
            step*=0.5
        if not accepted:break
    final_obj,final_g=objective_gradient(beta,data,ridge)
    if math.sqrt(sum(v*v for v in final_g))<=1e-7:converged=True
    model={'beta':beta,'means':prep['means'],'scales':prep['scales']}
    return model,{'status':'TRAINED','training_count':n,'ridge_lambda':ridge,'iterations':iters,'converged':converged,'objective':final_obj,'gradient_norm':math.sqrt(sum(v*v for v in final_g))}

def predict_head(formal,market,model):
    if model is None:return equal_logpool(formal,market),'FALLBACK_EQUAL_LOGPOOL'
    dh,dd=residual_pair(formal,market);x=[1.0,(dh-model['means'][0])/model['scales'][0],(dd-model['means'][1])/model['scales'][1]]
    b=model['beta'];mh=safe_log(market[0])-safe_log(market[2]);md=safe_log(market[1])-safe_log(market[2])
    return softmax3(mh+sum(b[j]*x[j] for j in range(3)),md+sum(b[3+j]*x[j] for j in range(3))),'TRAINED_DIRECT_RESIDUAL'

def joint_log(matrix,hg,ag):
    p=0.0
    for c in matrix:
        if int(c['home_goals'])==int(hg) and int(c['away_goals'])==int(ag):p+=float(c['probability'])
    return -safe_log(p)


def main():
    cfg=load_config();candidates,packs=r100._enumerate_candidates(cfg);order=list(candidates);random.Random(r100.SEED).shuffle(order)
    frozen=order[:min(len(order),r100.ATTEMPT_POOL)];wanted=set(frozen);rank={k:i for i,k in enumerate(frozen)}
    produced={};failures=Counter();model_audit={};max1=maxT=maxM=0.0

    for cid in dec.COMPS:
        prior_training=[];model_audit[cid]={}
        for season in dec.SEASONS:
            pack=packs.get((season,cid))
            if not pack:continue
            model,fa=fit_model(prior_training);model_audit[cid][season]=fa
            bydate=defaultdict(list)
            for m in pack['matches']:bydate[m.date].append(m)
            hist=[];season_rows=[]
            for dt in sorted(bydate):
                day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team));day_rows=[]
                for m in day:
                    key=(season,cid,m.date.isoformat(),m.home_team,m.away_team)
                    if key not in pack['candidate_ids']:continue
                    mk=pack['lookup'].get((m.date.isoformat(),m.home_team,m.away_team))
                    try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=pack['params'],use_team_effects=True)
                    except Exception:p=None
                    if not p:failures['formal_prior']+=1;continue
                    prior=temperature_scale_matrix(p['probabilities']['score_matrix'],pack['temperature']);formal=arch.one_vec(prior);market=[float(x) for x in mk['one_x_two']];actual=arch.result_index(m.home_goals,m.away_goals)
                    day_rows.append({'formal':formal,'market':market,'actual':actual})
                    if key not in wanted:continue
                    head,mode=predict_head(formal,market,model);marg=derive_score_marginals(prior);td=ou.project(marg['total_goals'],float(mk['p_over25']))
                    if td is None:failures['total_projection']+=1;continue
                    targetT=[float(td[k]) for k in ou.TOTAL_KEYS]
                    try:matrix,audit=core.reconcile(prior,head,targetT)
                    except Exception:matrix,audit=None,{'converged':False}
                    if matrix is None or not audit.get('converged'):failures['reconciliation']+=1;continue
                    one=core.one_x_two_vector(matrix);ft=arch.total_vec(prior);nt=core.total_goals_vector(matrix);ti=min(7,m.home_goals+m.away_goals)
                    max1=max(max1,max(abs(a-b) for a,b in zip(one,head)));maxT=max(maxT,max(abs(a-b) for a,b in zip(nt,targetT)));maxM=max(maxM,abs(sum(float(c['probability']) for c in matrix)-1.0))
                    produced[key]={
                      'date':m.date.isoformat(),'competition_id':cid,'season':season,'home':m.home_team,'away':m.away_team,'actual_score':[m.home_goals,m.away_goals],'head_mode':mode,'training_count':fa['training_count'],
                      'formal_1x2_top1':int(max(range(3),key=lambda i:formal[i])==actual),'direct_1x2_top1':int(max(range(3),key=lambda i:one[i])==actual),
                      'formal_1x2_brier':arch.brier3(formal,actual),'direct_1x2_brier':arch.brier3(one,actual),'formal_1x2_logloss':arch.logloss3(formal,actual),'direct_1x2_logloss':arch.logloss3(one,actual),
                      'formal_total_top1':int(max(range(8),key=lambda i:ft[i])==ti),'direct_total_top1':int(max(range(8),key=lambda i:nt[i])==ti),'formal_total_rps':arch.rps8(ft,ti),'direct_total_rps':arch.rps8(nt,ti),
                      'formal_score_top1':arch.score_topk(prior,1,m.home_goals,m.away_goals),'direct_score_top1':arch.score_topk(matrix,1,m.home_goals,m.away_goals),'formal_score_top3':arch.score_topk(prior,3,m.home_goals,m.away_goals),'direct_score_top3':arch.score_topk(matrix,3,m.home_goals,m.away_goals),
                      'formal_joint_log':joint_log(prior,m.home_goals,m.away_goals),'direct_joint_log':joint_log(matrix,m.home_goals,m.away_goals)}
                season_rows.extend(day_rows)
                for m in day:hist.append(m)
            prior_training.extend(season_rows)

    rows=sorted(produced.values(),key=lambda r:rank[(r['season'],r['competition_id'],r['date'],r['home'],r['away'])])[:r100.TARGET]
    summary={'count':len(rows)}
    for prefix in ('formal','direct'):
        for metric in ('1x2_top1','1x2_brier','1x2_logloss','total_top1','total_rps','score_top1','score_top3','joint_log'):
            summary[f'{prefix}_{metric}']=avg(rows,f'{prefix}_{metric}')
    summary['direct_vs_formal_1x2_pp']=((summary['direct_1x2_top1'] or 0)-(summary['formal_1x2_top1'] or 0))*100
    summary['direct_vs_formal_total_pp']=((summary['direct_total_top1'] or 0)-(summary['formal_total_top1'] or 0))*100
    summary['direct_vs_formal_score1_pp']=((summary['direct_score_top1'] or 0)-(summary['formal_score_top1'] or 0))*100
    gate_checks={
      'one_x_two_top1_plus_5pp':summary['direct_1x2_top1'] is not None and summary['formal_1x2_top1'] is not None and summary['direct_1x2_top1']>=summary['formal_1x2_top1']+0.05-1e-12,
      'joint_log_nonworse':summary['direct_joint_log'] is not None and summary['formal_joint_log'] is not None and summary['direct_joint_log']<=summary['formal_joint_log']+1e-12,
      'total_rps_nonworse':summary['direct_total_rps'] is not None and summary['formal_total_rps'] is not None and summary['direct_total_rps']<=summary['formal_total_rps']+1e-12,
    }
    continue_gate=all(gate_checks.values())
    report={'schema_version':'V6.26.9-direct-multinomial-market-residual-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if len(rows)==r100.TARGET else 'PARTIAL','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_FAST100_PRIOR_SEASON_DIRECT_1X2_RESIDUAL',
      'seed':r100.SEED,'target':r100.TARGET,'candidate_population':len(candidates),'failures':dict(failures),'audit':{'max_1x2_residual':max1,'max_total_residual':maxT,'max_mass_residual':maxM,'target_season_results_used_for_training':False,'same_day_history_frozen':True,'asian_handicap_primary_target':False},'summary':summary,'model_audit':model_audit,'fast100_gate':{'checks':gate_checks,'continue_research':continue_gate,'on_failure':'STOP_1X2_FUSION_EXPERIMENTATION_RETAIN_BEST_EXISTING_BASELINE'},'sample':rows,
      'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'random100_is_diagnostic_only':True,'no_test_set_hyperparameter_tuning':True,'full_matrix_moe':False,'head_specific_direct_model':True,'automatic_promotion':False}}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':report['status'],'failures':report['failures'],'audit':report['audit'],'summary':summary,'fast100_gate':report['fast100_gate']},ensure_ascii=False,indent=2));return 0 if len(rows)==r100.TARGET else 2
if __name__=='__main__':raise SystemExit(main())
