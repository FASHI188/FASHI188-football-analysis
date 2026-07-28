#!/usr/bin/env python3
"""V6.51.0 selective DRAW re-entry challenge.

Starting from the validation-selected V6.50.9 error-risk veto, attempt to re-enter a
small subset of vetoed matches as an honest DRAW direction.  The DRAW threshold is
selected only on calendar-2024 validation.  The objective is maximum restored coverage
subject to >=70% combined validation accuracy, >=50% validation DRAW precision, and at
least 20 validation DRAW picks.  The exact 2025/2025-26 target is then evaluated once.

No target result is used for threshold selection. Research only, formal_weight=0.
"""
from __future__ import annotations
import json,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import v6_draw_triage_decision_v6508 as tri
import v6_draw_risk_operating_point_v6509 as op
import v6_draw_residual_net_gain_v6507 as r7

OUT=ROOT/'manifests'/'v6_selective_draw_reentry_v6510_status.json'
FREEZE=ROOT/'manifests'/'v6_hierarchical_selector_forward_v6475_freeze.json'
MIN_RET=0.75


def eval_reentry(rows:list[dict[str,Any]], veto:float, draw_th:float|None)->dict[str,Any]:
    hits=executed=draw_n=draw_hits=abstain=0; dc={}
    for r in rows:
        risk=r['p_error_model']>=veto
        if not risk:
            executed+=1; hits+=int(r['actual']==r['pick']); dc[r['pick']]=dc.get(r['pick'],0)+1; continue
        if draw_th is not None and r['p_draw_model']>=draw_th:
            executed+=1; draw_n+=1; draw_hits+=int(r['actual']=='draw'); hits+=int(r['actual']=='draw'); dc['draw']=dc.get('draw',0)+1
        else:
            abstain+=1; dc['abstain']=dc.get('abstain',0)+1
    return {'count':len(rows),'executed_count':executed,'hits':hits,'accuracy':hits/executed if executed else None,'retention_vs_base':executed/len(rows) if rows else None,'draw_pick_count':draw_n,'draw_hits':draw_hits,'draw_precision':draw_hits/draw_n if draw_n else None,'abstain_count':abstain,'decision_counts':dc}


def main()->int:
    freeze=json.loads(FREEZE.read_text(encoding='utf-8'))
    train,valid,target,data=tri.build_records(freeze)
    models={'draw':tri.fit_label(train,lambda r:r['actual']=='draw'),'correct':tri.fit_label(train,lambda r:r['actual']==r['pick']),'error':tri.fit_label(train,lambda r:r['actual']!=r['pick'])}
    tri.score_rows(valid,models); tri.score_rows(target,models)
    # Reconstruct V6.50.9 veto selection using validation only.
    base_err,base_draw=op.error_and_draw_baselines(valid); veto_curve=[]
    for k in range(25,71):
        th=k/100; m=tri.eval_rule(valid,False,th); rem=m['abstain_count']; rem_err=m['abstained_actual_draws']+m['abstained_opposite_ha']
        re=rem_err/rem if rem else None; rd=m['abstained_actual_draws']/rem if rem else None
        eligible=bool(rem>=20 and m['retention_vs_base']>=MIN_RET and m['executed_accuracy']>m['base_accuracy'] and (re or 0)>base_err and (rd or 0)>base_draw)
        veto_curve.append({**m,'veto_threshold':th,'removed_error_rate':re,'removed_draw_rate':rd,'eligible':eligible})
    elig=[m for m in veto_curve if m['eligible']]
    chosen_v=max(elig,key=lambda m:(m['executed_accuracy'],m['retention_vs_base'],-m['veto_threshold'])) if elig else None
    if not chosen_v: raise RuntimeError('V6.50.9 veto operating point unavailable')
    veto=float(chosen_v['veto_threshold'])
    veto_only_valid=eval_reentry(valid,veto,None)
    # Select DRAW threshold on validation only. Maximize restored coverage while maintaining the accuracy contract.
    curve=[]
    for k in range(15,51):
        th=k/100; m=eval_reentry(valid,veto,th); m['draw_threshold']=th
        m['eligible']=bool(m['draw_pick_count']>=20 and (m['draw_precision'] or 0)>=0.50 and (m['accuracy'] or 0)>=0.70 and m['executed_count']>veto_only_valid['executed_count'])
        curve.append(m)
    elig_d=[m for m in curve if m['eligible']]
    chosen_d=max(elig_d,key=lambda m:(m['executed_count'],m['accuracy'],m['draw_threshold'])) if elig_d else None
    draw_th=float(chosen_d['draw_threshold']) if chosen_d else None
    target_veto=eval_reentry(target,veto,None); target_result=eval_reentry(target,veto,draw_th) if draw_th is not None else target_veto
    payload={
        'schema_version':'V6.51.0-selective-draw-reentry-r1','generated_at_utc':r7.now(),'formal_current_version':'V5.0.1',
        'status':'PASS_RESEARCH_CHALLENGE' if chosen_d else 'REJECT_NO_VALIDATION_SAFE_DRAW_REENTRY',
        'classification':'PRE2024_TRAIN_2024_DRAW_THRESHOLD_SELECTION_2025_TARGET_SELECTIVE_DRAW_REENTRY_FORMAL_WEIGHT_0',
        'design':{'veto_threshold_source':'reconstructed V6.50.9 validation-only rule','draw_threshold_selected_only_on_2024':True,'draw_reentry_objective':'maximize restored coverage subject validation accuracy>=0.70, draw precision>=0.50, draw picks>=20','target_results_used_for_training_or_threshold':False,'market_probabilities_mutated':False,'same_day_policy':'predict all then update all'},
        'data':{**data,'train_n':len(train),'validation_n':len(valid),'target_n':len(target)},
        'validation':{'chosen_veto':chosen_v,'veto_only':veto_only_valid,'draw_threshold_curve':curve,'chosen_draw':chosen_d},
        'target_2025':{'veto_only':target_veto,'with_draw_reentry':target_result},
        'gates':{'target_accuracy_ge_70':bool((target_result['accuracy'] or 0)>=0.70),'target_draw_picks_positive':bool(target_result['draw_pick_count']>0),'target_draw_precision_ge_50':bool((target_result['draw_precision'] or 0)>=0.50),'target_coverage_improved_vs_veto_only':bool(target_result['executed_count']>target_veto['executed_count']),'formal_promotion_allowed':False},
        'governance':{'research_only':True,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False,'formal_probability_change':False,'formal_selector_threshold_change':False,'historical_replay_cannot_promote':True},
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'chosen_veto':veto,'chosen_draw':chosen_d,'target':target_result,'gates':payload['gates']},ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
