#!/usr/bin/env python3
from __future__ import annotations

import json,sys
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
for p in (ROOT/'experiments'/'r43t0_dynamic_bivariate_residual_state',ROOT/'experiments'/'r43u0_fixed_diagonal_inflation',ROOT/'experiments'/'r43q0_sharp_market_score_base'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import run_r43t0 as t0  # noqa:E402
import run_r43u0 as u0  # noqa:E402
import run_r43q0 as q0  # noqa:E402

OUT=HERE/'results'/'summary_r43v0_low_event_generation_expert.json'
EXPERT_LAMBDA_SCALE=0.80
MAX_EXPERT_WEIGHT=0.30
WEIGHT_ZERO_TOTAL=2.80
WEIGHT_FULL_TOTAL=1.60
BREAKTHROUGH_PP=1.0


def load(p:Path):return json.loads(p.read_text(encoding='utf-8'))

def expert_weight(total:float)->float:
    z=(WEIGHT_ZERO_TOTAL-float(total))/(WEIGHT_ZERO_TOTAL-WEIGHT_FULL_TOTAL)
    return MAX_EXPERT_WEIGHT*float(np.clip(z,0.0,1.0))

def build_scored():
    rows=t0.build_rows();groups=t0.group_rows(rows);x=np.zeros(2);P=np.eye(2)*t0.INITIAL_VAR;warm=[];scored=[];scoring=False
    for group in groups:
        xp=t0.STATE_AR*x;Pp=(t0.STATE_AR**2)*P+np.eye(2)*t0.PROCESS_VAR
        if not scoring and len(warm)>=t0.WARMUP_MIN:scoring=True
        for r in group:
            dh,da=t0.project_lambdas(float(r['lambda_home']),float(r['lambda_away']),xp)
            base=u0.inflate(q0.score_matrix(dh,da)); w=expert_weight(dh+da)
            expert=u0.inflate(q0.score_matrix(max(0.05,EXPERT_LAMBDA_SCALE*dh),max(0.05,EXPERT_LAMBDA_SCALE*da)))
            mix=(1.0-w)*base+w*expert;mix/=mix.sum()
            r['u0_diagonal']=q0.matrix_1x2(base);r['low_event_expert']=q0.matrix_1x2(mix);r['expert_weight']=w;r['dynamic_total']=dh+da
            (scored if scoring else warm).append(r)
        x,P=t0.simultaneous_update(xp,Pp,group)
    return warm,scored

def run():
    warm,rows=build_scored();market=t0.metrics(rows,'market');base=t0.metrics(rows,'u0_diagonal');cand=t0.metrics(rows,'low_event_expert')
    db=t0.delta(base,cand);dm=t0.delta(market,cand);folds=[]
    for i,f in enumerate(t0.time_folds(rows,t0.FOLDS),1):
        mm=t0.metrics(f,'market');bm=t0.metrics(f,'u0_diagonal');cm=t0.metrics(f,'low_event_expert')
        folds.append({'fold':i,'n':len(f),'dates':[f[0]['kickoff_utc'],f[-1]['kickoff_utc']],'market':mm,'u0_diagonal':bm,'low_event_expert':cm,'expert_minus_u0':t0.delta(bm,cm),'expert_minus_market':t0.delta(mm,cm),'mean_expert_weight':float(np.mean([r['expert_weight'] for r in f])),'active_expert_n':sum(1 for r in f if r['expert_weight']>0)})
    nonneg=sum(1 for f in folds if f['expert_minus_u0']['accuracy_pp']>=-1e-12);posll=sum(1 for f in folds if f['expert_minus_u0']['logloss']<0)
    gate=bool(db['accuracy_pp']>=0 and db['logloss']<0 and db['brier']<0 and db['rps']<0 and db['draw_logloss']<0 and db['draw_brier']<0 and dm['accuracy_pp']>=0 and nonneg>=2 and posll>=2 and cand['top1_picks']['draw']>=base['top1_picks']['draw'])
    result={'schema_version':'football3-r43v0-low-event-generation-expert-v1','status':'COMPLETE','classification':'POSTVIEW_DEVELOPMENT_ON_EXISTING_PREMATCH_FROZEN_MARKETS','formal_weight':0,'question':'Does a fixed low-event score generator add stable information beyond the frozen R43U0 diagonal-inflated dynamic matrix?',
    'governance':{'inherits_r43t0_state_unchanged':True,'inherits_r43u0_diagonal_factor_unchanged':True,'expert_parameter_search':False,'threshold_search':False,'draw_count_forced':False,'unified_argmax':True,'outcomes_used_before_prediction':False,'main_merge':False,'publication':False},
    'design':{'expert_lambda_scale':EXPERT_LAMBDA_SCALE,'max_expert_weight':MAX_EXPERT_WEIGHT,'weight_zero_total':WEIGHT_ZERO_TOTAL,'weight_full_total':WEIGHT_FULL_TOTAL,'weight_rule':'linear prematch dynamic total only, clipped to [0,max_weight]','expert_distribution':'same home/away lambda ratio, lower total, inherited 1.25 diagonal inflation','warmup_n':len(warm),'folds':t0.FOLDS,'breakthrough_pp':BREAKTHROUGH_PP,'full_volume_target_accuracy_floor':0.53},
    'coverage':{'scored_n':len(rows),'expert_active_n':sum(1 for r in rows if r['expert_weight']>0),'mean_expert_weight':float(np.mean([r['expert_weight'] for r in rows]))},
    'aggregate':{'direct_market':market,'u0_diagonal_base':base,'low_event_expert':cand,'expert_minus_u0':db,'expert_minus_market':dm,'nonnegative_top1_folds_vs_u0':nonneg,'positive_logloss_folds_vs_u0':posll},'folds':folds,
    'gate':{'architecture_passed':gate,'full_volume_53pct_target_met':bool(cand['top1_accuracy']>=0.53),'breakthrough_candidate_vs_market':bool(gate and dm['accuracy_pp']>=BREAKTHROUGH_PP),'action':'FREEZE_LOW_EVENT_EXPERT_FOR_NEW_FORWARD_CONFIRMATION' if gate else 'RETAIN_R43U0_AND_DO_NOT_RETUNE_LOW_EVENT_EXPERT_ON_THESE_MATCHES'}}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2));return result

def verify():
    x=load(OUT);g=x['governance'];assert x['status']=='COMPLETE' and x['formal_weight']==0
    assert g['inherits_r43t0_state_unchanged'] and g['inherits_r43u0_diagonal_factor_unchanged'] and g['expert_parameter_search'] is False and g['draw_count_forced'] is False and g['unified_argmax']
    d=x['design'];assert d['expert_lambda_scale']==EXPERT_LAMBDA_SCALE and d['max_expert_weight']==MAX_EXPERT_WEIGHT
    print('R43V0 contract verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
