#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
for p in (ROOT/'experiments'/'r43u0_fixed_diagonal_inflation',ROOT/'experiments'/'r43t0_dynamic_bivariate_residual_state'):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import run_r43u0 as u0
import run_r43t0 as t0

OUT=HERE/'results'/'summary_r43x0_high_confidence_coverage.json'
TARGETS=(0.20,0.30)
SEED_N=18
FOLDS=3
COVERAGE_TOL=0.05
ACCURACY_TARGET=0.60
WILSON_Z=1.6448536269514722


def load(p:Path): return json.loads(p.read_text(encoding='utf-8'))

def confidence(p:dict[str,float])->float:
    v=sorted((float(x) for x in p.values()),reverse=True)
    return v[0]-v[1]

def hist_threshold(scores:list[float],target:float)->float:
    if not scores: raise RuntimeError('empty confidence history')
    return float(np.quantile(np.asarray(scores,dtype=float),1.0-target,method='higher'))

def wilson_lower(hits:int,n:int,z:float=WILSON_Z):
    if n<=0:return None
    p=hits/n;d=1+z*z/n
    return float((p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/d)

def selected_metrics(rows:list[dict])->dict:
    if not rows:
        return {'count':0,'hits':0,'top1_accuracy':None,'logloss':None,'brier':None,'rps':None,'top1_picks':{'home':0,'draw':0,'away':0},'top1_hits':{'home':0,'draw':0,'away':0},'actuals':{'home':0,'draw':0,'away':0},'draw_calibration':{'n':0},'wilson90_lower':None}
    m=t0.metrics(rows,'u0')
    m['wilson90_lower']=wilson_lower(int(m['hits']),int(m['count']))
    return m

def run():
    _,rows=u0.build_scored()
    for r in rows:
        r['u0']=r['diagonal_inflated']
        r['confidence_margin']=confidence(r['u0'])
    full=t0.metrics(rows,'u0')
    seed=rows[:SEED_N];test=rows[SEED_N:]
    folds=t0.time_folds(test,FOLDS)
    arms={}
    for target in TARGETS:
        history=[float(r['confidence_margin']) for r in seed]
        selected_all=[];fr=[]
        for i,f in enumerate(folds,1):
            threshold=hist_threshold(history,target)
            selected=[r for r in f if float(r['confidence_margin'])>=threshold]
            m=selected_metrics(selected)
            fr.append({'fold':i,'test_n':len(f),'selected_n':len(selected),'coverage':len(selected)/len(f),'threshold_from_prior_prediction_scores':threshold,'metrics':m,'dates':[f[0]['kickoff_utc'],f[-1]['kickoff_utc']]})
            selected_all.extend(selected)
            history.extend(float(r['confidence_margin']) for r in f)
        agg=selected_metrics(selected_all);coverage=len(selected_all)/len(test)
        valid_fold_acc=[x['metrics']['top1_accuracy'] for x in fr if x['metrics']['top1_accuracy'] is not None]
        folds_over60=sum(a>ACCURACY_TARGET for a in valid_fold_acc)
        min_fold=min(valid_fold_acc) if valid_fold_acc else None
        coverage_ok=abs(coverage-target)<=COVERAGE_TOL+1e-12
        stable=bool(agg['top1_accuracy'] is not None and agg['top1_accuracy']>ACCURACY_TARGET and coverage_ok and len(valid_fold_acc)==FOLDS and folds_over60>=2 and min_fold>=0.50 and (agg['wilson90_lower'] or 0)>=0.50)
        arms[str(int(target*100))]={'target_coverage':target,'realized_coverage':coverage,'selected_n':len(selected_all),'eligible_n':len(test),'aggregate':agg,'folds':fr,'folds_over_60pct':folds_over60,'minimum_fold_accuracy':min_fold,'coverage_within_5pp':coverage_ok,'stable_over_60_gate':stable}
    any_gate=any(v['stable_over_60_gate'] for v in arms.values())
    result={'schema_version':'football3-r43x0-high-confidence-coverage-v1','status':'COMPLETE','classification':'POSTVIEW_SELECTION_LAYER_DEVELOPMENT_ON_FROZEN_R43U0','formal_weight':0,
    'question':'Can one fixed U0 confidence-margin ranking deliver stable >60% Top1 at 20% or 30% rolling OOS coverage without changing probabilities or forcing outcomes?',
    'governance':{'inherits_r43u0_probabilities_unchanged':True,'selection_only':True,'confidence_rule_search':False,'threshold_search':False,'coverage_targets_preregistered':True,'thresholds_use_prior_prediction_scores_only':True,'outcomes_used_for_thresholds':False,'top1_changed_by_selection':False,'draw_override':False,'draw_count_forced':False,'main_merge':False,'publication':False},
    'design':{'confidence_score':'U0 Top1 probability minus second-highest U0 probability','target_coverages':list(TARGETS),'rolling_threshold_rule':'prior-score empirical quantile only','seed_n':SEED_N,'folds':FOLDS,'coverage_tolerance_pp':100*COVERAGE_TOL,'accuracy_target_strictly_greater_than':ACCURACY_TARGET,'stability_gate':'coverage within 5pp AND aggregate accuracy >60% AND >=2/3 folds >60% AND no fold <50% AND Wilson90 lower >=50%'},
    'coverage':{'u0_scored_total':len(rows),'selection_seed_n':len(seed),'selection_oos_n':len(test)},'full_volume_reference':full,'arms':arms,
    'gate':{'any_high_confidence_arm_passed':any_gate,'action':'FREEZE_PASSING_HIGH_CONFIDENCE_ARM_FOR_NEW_FORWARD_CONFIRMATION' if any_gate else 'NO_STABLE_60PCT_HIGH_CONFIDENCE_ARM_DO_NOT_TUNE_ON_THESE_MATCHES'}}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2));return result

def verify():
    x=load(OUT);g=x['governance'];d=x['design']
    assert x['formal_weight']==0 and g['inherits_r43u0_probabilities_unchanged'] and g['selection_only']
    assert not g['confidence_rule_search'] and not g['threshold_search'] and g['thresholds_use_prior_prediction_scores_only'] and not g['outcomes_used_for_thresholds']
    assert d['target_coverages']==[0.2,0.3] and d['confidence_score']=='U0 Top1 probability minus second-highest U0 probability'
    print('R43X0 contract verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
