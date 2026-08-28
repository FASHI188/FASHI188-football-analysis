#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
T0DIR=ROOT/'experiments'/'r43t0_dynamic_bivariate_residual_state'
Q0DIR=ROOT/'experiments'/'r43q0_sharp_market_score_base'
for p in (T0DIR,Q0DIR):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import run_r43t0 as t0  # noqa:E402
import run_r43q0 as q0  # noqa:E402

OUT=HERE/'results'/'summary_r43u0_fixed_diagonal_inflation.json'
DIAGONAL_FACTOR=1.25
BREAKTHROUGH_PP=1.0


def load(p:Path):return json.loads(p.read_text(encoding='utf-8'))


def inflate(m:np.ndarray)->np.ndarray:
    z=np.array(m,dtype=float,copy=True)
    for i in range(min(z.shape)):z[i,i]*=DIAGONAL_FACTOR
    z/=z.sum();return z


def build_scored():
    rows=t0.build_rows();groups=t0.group_rows(rows)
    x=np.zeros(2);P=np.eye(2)*t0.INITIAL_VAR;warm=[];scored=[];scoring=False
    for group in groups:
        xp=t0.STATE_AR*x;Pp=(t0.STATE_AR**2)*P+np.eye(2)*t0.PROCESS_VAR
        if not scoring and len(warm)>=t0.WARMUP_MIN:scoring=True
        for r in group:
            dh,da=t0.project_lambdas(float(r['lambda_home']),float(r['lambda_away']),xp)
            dm=q0.score_matrix(dh,da)
            r['dynamic_matrix']=q0.matrix_1x2(dm)
            r['diagonal_inflated']=q0.matrix_1x2(inflate(dm))
            (scored if scoring else warm).append(r)
        x,P=t0.simultaneous_update(xp,Pp,group)
    return warm,scored


def run():
    warm,rows=build_scored()
    market=t0.metrics(rows,'market');dynamic=t0.metrics(rows,'dynamic_matrix');diag=t0.metrics(rows,'diagonal_inflated')
    dmarket=t0.delta(market,diag);ddyn=t0.delta(dynamic,diag)
    folds=[]
    for i,f in enumerate(t0.time_folds(rows,t0.FOLDS),1):
        mm=t0.metrics(f,'market');dm=t0.metrics(f,'dynamic_matrix');im=t0.metrics(f,'diagonal_inflated')
        folds.append({'fold':i,'n':len(f),'dates':[f[0]['kickoff_utc'],f[-1]['kickoff_utc']],'market':mm,'dynamic_matrix':dm,'diagonal_inflated':im,'inflated_minus_market':t0.delta(mm,im),'inflated_minus_dynamic':t0.delta(dm,im)})
    nonneg=sum(1 for f in folds if f['inflated_minus_market']['accuracy_pp']>=-1e-12)
    posll=sum(1 for f in folds if f['inflated_minus_market']['logloss']<0)
    gate=bool(dmarket['accuracy_pp']>=0 and dmarket['logloss']<0 and dmarket['brier']<0 and dmarket['rps']<0 and dmarket['draw_logloss']<0 and dmarket['draw_brier']<0 and nonneg>=2 and posll>=2 and diag['top1_picks']['draw']>0)
    result={'schema_version':'football3-r43u0-fixed-diagonal-inflation-v1','status':'COMPLETE','classification':'POSTVIEW_DEVELOPMENT_ON_EXISTING_PREMATCH_FROZEN_MARKETS','formal_weight':0,
    'question':'Does one fixed score-matrix diagonal inflation factor naturally activate draw Top1 while improving full 1X2 scoring?','governance':{'inherits_r43t0_dynamic_state_unchanged':True,'diagonal_factor_search':False,'parameter_search':False,'threshold_search':False,'draw_count_forced':False,'unified_argmax':True,'outcomes_used_before_prediction':False,'main_merge':False,'publication':False},
    'design':{'diagonal_factor':DIAGONAL_FACTOR,'scope':'all h==a score cells only','off_diagonal_cells_unchanged_before_renormalization':True,'warmup_n':len(warm),'folds':t0.FOLDS,'breakthrough_pp':BREAKTHROUGH_PP,'full_volume_target_accuracy_floor':0.53},
    'coverage':{'scored_n':len(rows)},'aggregate':{'direct_market':market,'dynamic_bivariate_matrix':dynamic,'diagonal_inflated_matrix':diag,'inflated_minus_market':dmarket,'inflated_minus_dynamic':ddyn,'nonnegative_top1_folds':nonneg,'positive_logloss_folds':posll},'folds':folds,
    'gate':{'architecture_passed':gate,'full_volume_53pct_target_met':bool(diag['top1_accuracy']>=0.53),'breakthrough_candidate':bool(gate and dmarket['accuracy_pp']>=BREAKTHROUGH_PP),'action':'FREEZE_DIAGONAL_INFLATION_FOR_NEW_FORWARD_CONFIRMATION' if gate else 'DO_NOT_PROMOTE_AND_DO_NOT_RETUNE_DIAGONAL_FACTOR_ON_THESE_MATCHES'}}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2));return result


def verify():
    x=load(OUT);g=x['governance'];assert x['status']=='COMPLETE' and x['formal_weight']==0
    assert g['inherits_r43t0_dynamic_state_unchanged'] and g['diagonal_factor_search'] is False and g['threshold_search'] is False and g['draw_count_forced'] is False and g['unified_argmax']
    assert x['design']['diagonal_factor']==DIAGONAL_FACTOR
    print('R43U0 contract verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
