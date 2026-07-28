#!/usr/bin/env python3
"""V6.50.8 three-state draw triage challenge.

Extends V6.50.7 after direct H/A->DRAW override found no positive 2024 validation
region.  Three independently trained pre-2024 probabilities are estimated for every
frozen-selector H/A decision:
  P(actual DRAW), P(base H/A correct), P(base H/A error).

Decision:
1) DRAW is executable only if the untuned Bayes comparison P(DRAW)>P(BASE_CORRECT)
   shows positive expected hit utility and the entire rule is validation-positive.
2) Otherwise a single 2024-selected error-risk threshold may ABSTAIN on dangerous
   H/A picks, with >=90% retention of the base selected sample.
3) Remaining rows keep the original H/A direction.

No market probability is rewritten.  Coefficients train strictly before 2024-01-01;
only the veto threshold is chosen on calendar 2024; the exact V6.50.6 2025/2025-26
season contract is untouched until final evaluation.

Research only, formal_weight=0, CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations
import json, math, sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/'validation'; ENGINE=ROOT/'engine'
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import v6_draw_residual_net_gain_v6507 as r7
import v6_fullseason_2025_replay_v6506 as base
import evaluate_direct_total_margin_matrix_v6477 as core
from diagnose_1x2_market_anchor_v697 import _extract_odds

OUT=ROOT/'manifests'/'v6_draw_triage_decision_v6508_status.json'
FREEZE=ROOT/'manifests'/'v6_hierarchical_selector_forward_v6475_freeze.json'
TRAIN_END='2024-01-01'; VALID_END='2025-01-01'; DIRECTIONS=('home','draw','away')
COMPS=list(core.base.TARGET_COMPETITIONS)


def extend_x(x:list[float], cid:str, pick:str)->list[float]:
    return list(x)+[1.0 if pick=='home' else 0.0]+[1.0 if cid==c else 0.0 for c in COMPS]


def build_records(freeze:dict[str,Any])->tuple[list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]],dict[str,Any]]:
    rows,meta=core.read_rows(); raw_cache,raw_meta=base.load_raw_row_cache(rows)
    by_day=defaultdict(list)
    for r in rows: by_day[r7.date_key(r['date'])].append(r)
    state=r7.ContextState(); train=[]; valid=[]; target=[]; market=selected=0
    for day in sorted(by_day):
        day_rows=sorted(by_day[day],key=lambda z:(str(z['competition_id']),str(z['home_team']),str(z['away_team'])))
        frozen=[]
        for r in day_rows:
            raw=raw_cache.get((str(r['source_file']),int(r['row_index'])))
            if raw is None: continue
            ex=_extract_odds(raw)
            if ex is None: continue
            q,_=ex; q={d:float(q[d]) for d in DIRECTIONS}; s=sum(q.values())
            if not math.isfinite(s) or s<=0: continue
            q={d:q[d]/s for d in DIRECTIONS}; market+=1
            dec=base.selector_decision(q,str(r['competition_id']),freeze)
            if not bool(dec['selected']) or str(dec['pick'])=='draw': continue
            selected+=1; pick=str(dec['pick']); actual=base.result_actual(int(r['hg']),int(r['ag']))
            x=extend_x(state.features(r,q,pick),str(r['competition_id']),pick)
            frozen.append({'date':day,'competition_id':str(r['competition_id']),'season':str(r['season']),'pick':pick,'actual':actual,'x':x,'q':q})
        for rec in frozen:
            if rec['date']<TRAIN_END: train.append(rec)
            elif rec['date']<VALID_END: valid.append(rec)
            if rec['season']==base.TARGET_SEASONS.get(rec['competition_id']): target.append(rec)
        state.update_day(day_rows)
    return train,valid,target,{'source_meta':meta,'raw_meta':raw_meta,'market_rows':market,'selected_h_a_rows':selected}


def fit_label(rows:list[dict[str,Any]], fn)->dict[str,Any]:
    rr=[{'x':r['x'],'y':1 if fn(r) else 0} for r in rows]
    return r7.fit_logistic(rr,epochs=100,lr=0.18,l2=0.01)


def score_rows(rows:list[dict[str,Any]], models:dict[str,Any])->None:
    for r in rows:
        r['p_draw_model']=r7.predict(models['draw'],r['x'])
        r['p_correct_model']=r7.predict(models['correct'],r['x'])
        r['p_error_model']=r7.predict(models['error'],r['x'])
        r['draw_utility']=r['p_draw_model']-r['p_correct_model']


def eval_rule(rows:list[dict[str,Any]], draw_active:bool, veto:float|None)->dict[str,Any]:
    base_hits=sum(int(r['actual']==r['pick']) for r in rows); executed=hits=draw_picks=draw_hits=abstain=0
    abstain_draw=abstain_correct=abstain_opposite=0; rescued=lost=0
    decisions=Counter()
    for r in rows:
        do_draw=bool(draw_active and r['draw_utility']>0.0)
        if do_draw:
            executed+=1; draw_picks+=1; decisions['draw']+=1
            ok=r['actual']=='draw'; hits+=int(ok); draw_hits+=int(ok)
            rescued+=int(r['actual']=='draw'); lost+=int(r['actual']==r['pick'])
            continue
        do_veto=bool(veto is not None and r['p_error_model']>=veto)
        if do_veto:
            abstain+=1; decisions['abstain']+=1
            abstain_draw+=int(r['actual']=='draw'); abstain_correct+=int(r['actual']==r['pick'])
            abstain_opposite+=int(r['actual'] not in {'draw',r['pick']})
            continue
        executed+=1; decisions[r['pick']]+=1; hits+=int(r['actual']==r['pick'])
    return {
        'count':len(rows),'base_hits':base_hits,'base_accuracy':base_hits/len(rows) if rows else None,
        'executed_count':executed,'executed_hits':hits,'executed_accuracy':hits/executed if executed else None,
        'retention_vs_base':executed/len(rows) if rows else None,'abstain_count':abstain,
        'draw_pick_count':draw_picks,'draw_hits':draw_hits,'draw_precision':draw_hits/draw_picks if draw_picks else None,
        'draw_override_net_hits':rescued-lost,'draw_rescued':rescued,'draw_lost_correct_ha':lost,
        'abstained_actual_draws':abstain_draw,'abstained_correct_ha':abstain_correct,'abstained_opposite_ha':abstain_opposite,
        'decision_counts':dict(decisions),
    }


def main()->int:
    freeze=json.loads(FREEZE.read_text(encoding='utf-8'))
    if freeze.get('status')!='FROZEN' or float(freeze.get('selector_threshold') or 0)!=0.55: raise RuntimeError('selector freeze drift')
    train,valid,target,data=build_records(freeze)
    models={
        'draw':fit_label(train,lambda r:r['actual']=='draw'),
        'correct':fit_label(train,lambda r:r['actual']==r['pick']),
        'error':fit_label(train,lambda r:r['actual']!=r['pick']),
    }
    score_rows(valid,models); score_rows(target,models)
    # DRAW activation is not tuned: Bayes utility >0. It must be validation-positive or is disabled wholesale.
    v_draw=eval_rule(valid,True,None)
    draw_active=bool(v_draw['draw_pick_count']>=20 and v_draw['draw_override_net_hits']>0 and (v_draw['draw_precision'] or 0)>0.5)
    # One-dimensional error veto threshold chosen on 2024 only. Retain at least 90% of base selected decisions.
    curve=[]
    for k in range(25,71):
        th=k/100.0; m=eval_rule(valid,draw_active,th); m['veto_threshold']=th
        m['eligible']=bool(m['abstain_count']>=20 and m['retention_vs_base']>=0.90 and m['executed_accuracy']>m['base_accuracy'] and m['abstained_actual_draws']>m['abstained_correct_ha'])
        curve.append(m)
    eligible=[m for m in curve if m['eligible']]
    chosen=max(eligible,key=lambda m:(m['executed_accuracy'],m['retention_vs_base'],-m['veto_threshold'])) if eligible else None
    veto=float(chosen['veto_threshold']) if chosen else None
    target_base=eval_rule(target,False,None); target_result=eval_rule(target,draw_active,veto) if veto is not None or draw_active else target_base
    errors=Counter('draw' if r['actual']=='draw' else 'opposite_home_away' for r in target if r['actual']!=r['pick'])
    payload={
        'schema_version':'V6.50.8-draw-triage-decision-r1','generated_at_utc':r7.now(),'formal_current_version':'V5.0.1',
        'status':'PASS_RESEARCH_CHALLENGE' if (draw_active or veto is not None) else 'REJECT_NO_VALIDATION_POSITIVE_TRIAGE',
        'classification':'STRICT_PRE2024_TRAIN_2024_VALIDATION_2025_TARGET_THREE_STATE_DRAW_TRIAGE_FORMAL_WEIGHT_0',
        'design':{
            'base_selector':'V6.47.5 frozen reliability threshold 0.55','train_end_exclusive':TRAIN_END,'validation_end_exclusive':VALID_END,
            'models':['P_DRAW','P_BASE_CORRECT','P_BASE_ERROR'],'draw_rule':'untuned P_DRAW > P_BASE_CORRECT, activated only if entire 2024 validation rule has positive net hits and >50% draw precision',
            'veto_rule':'P_BASE_ERROR >= validation-selected threshold','minimum_validation_retention':0.90,
            'same_day_policy':'predict all then update all','target_results_used_for_training_or_threshold':False,'market_probabilities_mutated':False,
        },
        'data':{**data,'train_n':len(train),'validation_n':len(valid),'target_n':len(target)},
        'validation':{'draw_rule_probe':v_draw,'draw_rule_active':draw_active,'veto_curve':curve,'chosen_veto':chosen},
        'target_2025':{'base':target_base,'candidate':target_result,'base_error_decomposition':dict(errors)},
        'gates':{
            'target_accuracy_nonworse':bool((target_result['executed_accuracy'] or 0)>=(target_base['base_accuracy'] or 0)),
            'target_70pct_reached':bool((target_result['executed_accuracy'] or 0)>=0.70),
            'target_retention_ge_90pct':bool((target_result['retention_vs_base'] or 0)>=0.90),
            'honest_draw_execution_active':draw_active,'formal_promotion_allowed':False,
        },
        'governance':{'research_only':True,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False,'formal_probability_change':False,'formal_selector_threshold_change':False,'historical_replay_cannot_promote':True},
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'validation_draw':v_draw,'chosen_veto':chosen,'target':target_result,'gates':payload['gates']},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
