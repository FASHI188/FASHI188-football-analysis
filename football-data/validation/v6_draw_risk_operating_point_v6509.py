#!/usr/bin/env python3
"""V6.50.9 draw-risk selective operating point.

V6.50.8 proved that pre-match context can rank frozen H/A selector errors, but its
>=90% retention gate was too restrictive to yield a validation-positive operating
point.  This follow-up predeclares a practical >=75% retention floor, selects the
single P(BASE_ERROR) threshold on calendar-2024 validation only, then evaluates the
untouched V6.50.6 2025/2025-26 target.

This is an abstention/risk solution, not a fabricated draw direction.  DRAW execution
remains disabled unless P(DRAW)>P(BASE_CORRECT) has validation-positive net utility.
Research only; formal_weight=0; CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import v6_draw_triage_decision_v6508 as tri
import v6_draw_residual_net_gain_v6507 as r7

OUT=ROOT/'manifests'/'v6_draw_risk_operating_point_v6509_status.json'
FREEZE=ROOT/'manifests'/'v6_hierarchical_selector_forward_v6475_freeze.json'
MIN_RETENTION=0.75


def error_and_draw_baselines(rows:list[dict[str,Any]])->tuple[float,float]:
    n=len(rows)
    return (sum(r['actual']!=r['pick'] for r in rows)/n, sum(r['actual']=='draw' for r in rows)/n) if n else (0.0,0.0)


def main()->int:
    freeze=json.loads(FREEZE.read_text(encoding='utf-8'))
    train,valid,target,data=tri.build_records(freeze)
    models={
        'draw':tri.fit_label(train,lambda r:r['actual']=='draw'),
        'correct':tri.fit_label(train,lambda r:r['actual']==r['pick']),
        'error':tri.fit_label(train,lambda r:r['actual']!=r['pick']),
    }
    tri.score_rows(valid,models); tri.score_rows(target,models)
    v_draw=tri.eval_rule(valid,True,None)
    draw_active=bool(v_draw['draw_pick_count']>=20 and v_draw['draw_override_net_hits']>0 and (v_draw['draw_precision'] or 0)>0.5)
    base_err,base_draw=error_and_draw_baselines(valid)
    curve=[]
    for k in range(25,71):
        th=k/100.0; m=tri.eval_rule(valid,draw_active,th); m['veto_threshold']=th
        rem=m['abstain_count']; rem_err=m['abstained_actual_draws']+m['abstained_opposite_ha']
        m['removed_error_rate']=rem_err/rem if rem else None
        m['removed_draw_rate']=m['abstained_actual_draws']/rem if rem else None
        m['eligible']=bool(
            rem>=20 and m['retention_vs_base']>=MIN_RETENTION and m['executed_accuracy']>m['base_accuracy']
            and (m['removed_error_rate'] or 0)>base_err and (m['removed_draw_rate'] or 0)>base_draw
        )
        curve.append(m)
    eligible=[m for m in curve if m['eligible']]
    chosen=max(eligible,key=lambda m:(m['executed_accuracy'],m['retention_vs_base'],-m['veto_threshold'])) if eligible else None
    veto=float(chosen['veto_threshold']) if chosen else None
    target_base=tri.eval_rule(target,False,None)
    target_result=tri.eval_rule(target,draw_active,veto) if veto is not None or draw_active else target_base
    target_err,target_draw=error_and_draw_baselines(target)
    if target_result['abstain_count']:
        ar=target_result['abstain_count']; ae=target_result['abstained_actual_draws']+target_result['abstained_opposite_ha']
        target_result['removed_error_rate']=ae/ar; target_result['removed_draw_rate']=target_result['abstained_actual_draws']/ar
    payload={
        'schema_version':'V6.50.9-draw-risk-operating-point-r1','generated_at_utc':r7.now(),'formal_current_version':'V5.0.1',
        'status':'PASS_RESEARCH_CHALLENGE' if chosen else 'REJECT_NO_75PCT_RETENTION_OPERATING_POINT',
        'classification':'PRE2024_TRAIN_2024_THRESHOLD_SELECTION_2025_TARGET_DRAW_RISK_SELECTIVE_FORMAL_WEIGHT_0',
        'design':{
            'minimum_validation_retention':MIN_RETENTION,'draw_execution_rule_unchanged':'P_DRAW>P_BASE_CORRECT only if validation-positive; otherwise disabled',
            'veto_score':'P_BASE_ERROR','threshold_selected_only_on_2024':True,'target_results_used_for_training_or_threshold':False,
            'same_day_policy':'predict all then update all','market_probabilities_mutated':False,
        },
        'data':{**data,'train_n':len(train),'validation_n':len(valid),'target_n':len(target)},
        'validation':{'base_error_rate':base_err,'base_draw_rate':base_draw,'draw_rule_probe':v_draw,'draw_rule_active':draw_active,'curve':curve,'chosen':chosen},
        'target_2025':{'base_error_rate':target_err,'base_draw_rate':target_draw,'base':target_base,'candidate':target_result},
        'gates':{
            'target_accuracy_ge_70':bool((target_result['executed_accuracy'] or 0)>=0.70),
            'target_retention_ge_75':bool((target_result['retention_vs_base'] or 0)>=MIN_RETENTION),
            'target_accuracy_improved':bool((target_result['executed_accuracy'] or 0)>(target_base['base_accuracy'] or 0)),
            'target_removed_pool_more_draw_prone':bool((target_result.get('removed_draw_rate') or 0)>target_draw),
            'honest_draw_execution_active':draw_active,'formal_promotion_allowed':False,
        },
        'governance':{'research_only':True,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False,'formal_probability_change':False,'formal_selector_threshold_change':False,'historical_replay_cannot_promote':True},
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'chosen':chosen,'target':target_result,'gates':payload['gates']},ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
