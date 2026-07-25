#!/usr/bin/env python3
"""V6.20.3 final three-track Fast100 audit.

Builds three independent tracks on the same fixed 2025/26 random100:
1) de-vigged 1X2;
2) hierarchical direct total P(T=0..6,7+);
3) independent exact score P(T_score)*P(H|T_score,X).
Then applies the non-mutating conflict gate. No track rewrites another track.
"""
from __future__ import annotations
import json, math, random, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; V=ROOT/'validation'; E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))

import v6_total_shot_residual_v6181 as shot
import v6_conditional_score_shot_challenge_v6184 as oldscore
import v6_independent_total_hierarchical_fast100_v6200 as total
import v6_independent_score_hierarchical_fast100_v6201 as score
from three_track_conflict_gate_v6202 import audit_three_tracks

OUT=ROOT/'manifests'/'v6_three_track_final_fast100_v6203_status.json'
SEED=20260725+6203
EPS=1e-15


def choose_total(train,valid,shot_names,comps):
    base=total.metric(valid,lambda r:r['ou']); board=[]
    for C in total.CS:
        for alpha in total.ALPHAS:
            m=total.fit_hier(train,C,alpha,shot_names,comps); met=total.metric(valid,lambda r,m=m:total.probs(m,r,shot_names,comps)); met.update({'C':C,'alpha':alpha,'proper_noninferior':met['rps']<=base['rps'] and met['logloss']<=base['logloss']}); board.append(met)
    ok=[x for x in board if x['proper_noninferior']]
    sel=max(ok,key=lambda x:(x['top1'],-x['rps'],-x['logloss'])) if ok else min(board,key=lambda x:(x['rps'],x['logloss']))
    return base,board,sel


def choose_score(train,valid,total_model,shot_names,comps):
    baseline=score.metrics(valid,score.formal_probs); board=[]
    for C in score.COND_CS:
        for alpha in score.COND_ALPHAS:
            cm,q7=score.fit_cond(train,C,alpha,shot_names,comps); met=score.metrics(valid,lambda r,cm=cm,q7=q7:score.score_probs(r,total_model,cm,q7,shot_names,comps)); met.update({'C':C,'alpha':alpha,'logloss_noninferior':met['logloss']<=baseline['logloss']}); board.append(met)
    ok=[x for x in board if x['logloss_noninferior']]
    sel=max(ok,key=lambda x:(x['top1'],x['top3'],-x['logloss'])) if ok else min(board,key=lambda x:x['logloss'])
    return baseline,board,sel


def one_metrics(rows):
    n=len(rows);hits=0;brier=ll=0.0;bands={0.58:[0,0],0.60:[0,0]}
    for r in rows:
        p=r['one']; y=0 if r['home_goals']>r['away_goals'] else 1 if r['home_goals']==r['away_goals'] else 2; pick=max(range(3),key=lambda i:p[i]);hit=int(pick==y);hits+=hit;brier+=sum((p[i]-(1 if i==y else 0))**2 for i in range(3));ll+=-math.log(max(EPS,p[y]));mx=max(p)
        for t in bands:
            if mx>=t:bands[t][0]+=1;bands[t][1]+=hit
    return {'count':n,'hits':hits,'accuracy':hits/n,'brier':brier/n,'logloss':ll/n,'p58':{'count':bands[0.58][0],'hits':bands[0.58][1],'accuracy':bands[0.58][1]/bands[0.58][0] if bands[0.58][0] else None},'p60':{'count':bands[0.60][0],'hits':bands[0.60][1],'accuracy':bands[0.60][1]/bands[0.60][0] if bands[0.60][0] else None}}


def main():
    raw,_=shot.raw_stat_matches();lookup,_=shot.lagged_shot_lookup(raw);base,_=oldscore.build_rows(lookup);rows=score.enrich(base)
    shot_names=sorted(rows[0]['shots']);comps=sorted({r['competition_id'] for r in rows})
    train=[r for r in rows if r['season'] in {'2022/23','2023/24'}];valid=[r for r in rows if r['season']=='2024/25'];test=[r for r in rows if r['season']=='2025/26']
    tbase,tboard,tsel=choose_total(train,valid,shot_names,comps); tm_train=total.fit_hier(train,tsel['C'],tsel['alpha'],shot_names,comps)
    sbase,sboard,ssel=choose_score(train,valid,tm_train,shot_names,comps)
    tm=total.fit_hier(train+valid,tsel['C'],tsel['alpha'],shot_names,comps);cm,q7=score.fit_cond(train+valid,ssel['C'],ssel['alpha'],shot_names,comps)
    sample=random.Random(SEED).sample(test,100)
    one=one_metrics(sample); tot=total.metric(sample,lambda r:total.probs(tm,r,shot_names,comps)); scr=score.metrics(sample,lambda r:score.score_probs(r,tm,cm,q7,shot_names,comps))
    reason_counts=Counter();allowed=hits=0;cross_res=cross_tot=0
    for r in sample:
        tp=total.probs(tm,r,shot_names,comps);sp=score.score_probs(r,tm,cm,q7,shot_names,comps);ranked=sorted(sp,key=lambda c:(-sp[c],c[0],c[1]));sr=[{'home_goals':h,'away_goals':a,'probability':sp[(h,a)]} for h,a in ranked]
        gate=audit_three_tracks(r['one'],tp,sr,score_model_passed=bool(ssel.get('logloss_noninferior')))
        reason_counts.update(gate['reasons'])
        top=ranked[0];op=max(range(3),key=lambda i:r['one'][i]);cross_res+=int((0 if top[0]>top[1] else 1 if top[0]==top[1] else 2)==op);cross_tot+=int(min(7,sum(top))==max(range(8),key=lambda i:tp[i]))
        if gate['score_exact_allowed']:
            allowed+=1;hits+=int(top==(int(r['home_goals']),int(r['away_goals'])))
    gate_summary={'score_allowed_count':allowed,'coverage':allowed/100,'allowed_top1_hits':hits,'allowed_top1_accuracy':hits/allowed if allowed else None,'reason_counts':dict(reason_counts),'raw_score_result_agreement_1x2':cross_res/100,'raw_score_total_top1_agreement':cross_tot/100}
    payload={'schema_version':'V6.20.3-three-track-final-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_2025_26_FAST100_RESEARCH','design':{'same_sample_all_tracks':True,'sample_before_evaluation':True,'cross_track_probability_feedback':False,'train':['2022/23','2023/24'],'validation':'2024/25','test':'2025/26 fixed-seed random100'},'selected_total':tsel,'selected_score':ssel,'one_x_two':one,'total_goals':tot,'score':scr,'conflict_gate':gate_summary,'screening':{'total_fast100_plus5pp_vs_ou':None,'score_fast100_plus5pp_vs_formal':None,'note':'screen decisions are reported, never used to retune this test sample'},'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'test_used_for_selection':False,'no_cross_track_mutation':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'one_x_two':one,'total_goals':tot,'score':scr,'conflict_gate':gate_summary,'selected_total':tsel,'selected_score':ssel},ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
