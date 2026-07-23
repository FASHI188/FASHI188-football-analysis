#!/usr/bin/env python3
"""V6.7.2 market-anchored zero-modified Skellam draw challenger.

The model treats a draw structurally as goal difference == 0 instead of as a generic third
class. Total scoring intensity is inferred from synchronized O/U 2.5 prices. The home/away
split is inferred from the market's conditional decisive-outcome ratio. A draw calibration
layer then learns whether the structural zero mass contains incremental information beyond
the 1X2 market draw price. Home/Away conditional ratio is never relearned.

Fit: 2022/23 + 2023/24; select: 2024/25; holdout: 2025/26.
Research challenger only; no formal/CURRENT mutation.
"""
from __future__ import annotations

import json, math, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];VALIDATION=ROOT/'validation';ENGINE=ROOT/'engine'
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import v6_direct_outcome_mvp_v600 as base
import v6_market_residual_fusion_v620 as mkt
import v6_multimarket_draw_side_v643 as mm
from platform_core import PlatformError

OUT=ROOT/'manifests'/'v6_zero_modified_skellam_draw_v672_status.json'
SEASONS=("2022/23","2023/24","2024/25","2025/26")
SEASON_CODES={"2022/23":"2223","2023/24":"2324","2024/25":"2425","2025/26":"2526"}
L2_GRID=(1.0,10.0,100.0,1000.0)
ALPHAS=(0.25,0.5,0.75,1.0)
EPS=1e-12
MAX_GOALS=16


def logit(p):
    p=min(1-1e-8,max(1e-8,p));return math.log(p/(1-p))

def poisson_probs(lam):
    p=math.exp(-lam);arr=[p]
    for k in range(1,MAX_GOALS+1):
        p*=lam/k;arr.append(p)
    return arr

def under25_from_lambda(lam):
    return math.exp(-lam)*(1+lam+lam*lam/2)

def solve_total_lambda(under_prob):
    lo,hi=0.05,8.0
    for _ in range(70):
        mid=(lo+hi)/2
        if under25_from_lambda(mid)>under_prob:lo=mid
        else:hi=mid
    return (lo+hi)/2

def outcome_probs(lh,la):
    hp=poisson_probs(lh);ap=poisson_probs(la);h=d=a=0.0
    for i,pi in enumerate(hp):
        for j,pj in enumerate(ap):
            z=pi*pj
            if i>j:h+=z
            elif i==j:d+=z
            else:a+=z
    s=h+d+a
    return {'home':h/s,'draw':d/s,'away':a/s}

def solve_split(total,target_home_decisive):
    lo,hi=0.03,0.97
    for _ in range(55):
        s=(lo+hi)/2;q=outcome_probs(total*s,total*(1-s));r=q['home']/max(EPS,q['home']+q['away'])
        if r<target_home_decisive:lo=s
        else:hi=s
    s=(lo+hi)/2;return total*s,total*(1-s)

def augment(r):
    surf=r['surface'];market=surf['one'];total=solve_total_lambda(surf['under_prob']);target=market['home']/max(EPS,market['home']+market['away']);lh,la=solve_split(total,target);struct=outcome_probs(lh,la)
    side_gap=abs(market['home']-market['away']);struct_gap=struct['draw']-market['draw']
    z=dict(r);z['structural']={'lambda_total':total,'lambda_home':lh,'lambda_away':la,'p':struct,'draw_gap_vs_market':struct_gap};z['draw_y']=1 if r['actual_result']=='draw' else 0
    z['zms_draw_x']=[1.0,logit(market['draw']),logit(struct['draw']),struct_gap,surf['under_prob']-.5,side_gap,abs(surf['ah_line']),abs(surf['ah_home_prob']-.5),struct_gap*(surf['under_prob']-.5)]
    return z

def build():
    out={s:[] for s in SEASONS};audit={}
    for cid,code in mm.LEAGUES.items():
        built=mkt._build_domain_rows_with_identity(cid,list(SEASONS));audit[cid]={}
        for season in SEASONS:
            raw,url=mkt._download_csv(code,SEASON_CODES[season]);matched,stats=mm.match_rows(cid,built[season],raw);rows=[augment(r) for r in matched];out[season]+=rows;audit[cid][season]={'url':url,'matched':len(rows),'stats':stats}
    return out,audit

def fit(rows,l2):return base._fit_binary(rows,'zms_draw_x','draw_y',l2)
def prob(r,model,alpha):
    market=r['surface']['one'];pd_model=min(1-1e-6,max(1e-6,base._predict_binary(model,r['zms_draw_x'])));pd=(1-alpha)*market['draw']+alpha*pd_model;side=market['home']/max(EPS,market['home']+market['away']);rem=1-pd;return {'home':rem*side,'draw':pd,'away':rem*(1-side)}
def score(rows,model=None,alpha=0.0):
    n=h=0;b=rps=ll=0.;draw_pred=draw_hit=actual_draw=0
    for r in rows:
        q=r['surface']['one'] if model is None else prob(r,model,alpha);t=r['actual_result'];p=max(('home','draw','away'),key=lambda k:q[k]);n+=1;h+=int(p==t);draw_pred+=int(p=='draw');draw_hit+=int(p=='draw' and t=='draw');actual_draw+=int(t=='draw');
        for k in ('home','draw','away'):b+=(q[k]-(1 if t==k else 0))**2
        th=1 if t=='home' else 0;td=1 if t=='draw' else 0;c1=q['home']-th;c2=q['home']+q['draw']-th-td;rps+=(c1*c1+c2*c2)/2;ll-=math.log(max(EPS,q[t]))
    return {'count':n,'hits':h,'accuracy':h/n,'mean_brier':b/n,'mean_rps':rps/n,'mean_log_loss':ll/n,'draw_prediction_count':draw_pred,'draw_hits':draw_hit,'draw_precision':draw_hit/draw_pred if draw_pred else None,'draw_recall':draw_hit/actual_draw if actual_draw else None,'actual_draw_count':actual_draw}

def nonworse(a,b):return a['accuracy']>=b['accuracy']-1e-12 and a['mean_brier']<=b['mean_brier']+1e-12 and a['mean_rps']<=b['mean_rps']+1e-12 and a['mean_log_loss']<=b['mean_log_loss']+1e-12

def main():
    data,audit=build();tr=data['2022/23']+data['2023/24'];va=data['2024/25'];ho=data['2025/26']
    if min(len(tr),len(va),len(ho))<700:raise PlatformError('insufficient rows')
    bv=score(va);bh=score(ho);cands=[]
    for l2 in L2_GRID:
        model=fit(tr,l2)
        for alpha in ALPHAS:
            s=score(va,model,alpha);cands.append({'l2':l2,'alpha':alpha,'proper_and_accuracy_nonworse':nonworse(s,bv),'validation':s})
    elig=[c for c in cands if c['proper_and_accuracy_nonworse']]
    elig.sort(key=lambda c:(c['validation']['mean_log_loss'],c['validation']['mean_brier'],-c['validation']['accuracy'],-(c['validation']['draw_recall'] or 0),c['alpha'],c['l2']))
    sel=elig[0] if elig else None;mh=None;gate=False
    if sel:
        refit=fit(tr+va,float(sel['l2']));mh=score(ho,refit,float(sel['alpha']));gate=bool(nonworse(mh,bh) and (mh['accuracy']>bh['accuracy'] or mh['mean_log_loss']<bh['mean_log_loss']))
    gap_stats={}
    for name,rows in [('validation',va),('holdout',ho)]:
        gaps=[r['structural']['draw_gap_vs_market'] for r in rows];gap_stats[name]={'mean_structural_minus_market_draw':sum(gaps)/len(gaps),'positive_fraction':sum(g>0 for g in gaps)/len(gaps)}
    out={'schema_version':'V6.7.2-market-anchored-zero-modified-skellam-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','design':{'draw_definition':'goal_difference_zero','lambda_total_source':'synchronized OU2.5 de-vigged probability inversion','lambda_split_source':'1X2 conditional home-vs-away decisive ratio','draw_calibration':'binary zero-mass calibration','holdout_used_for_selection':False},'row_counts':{'fit':len(tr),'validation':len(va),'holdout':len(ho)},'source_audit':audit,'structural_gap_audit':gap_stats,'baseline_market_validation':bv,'selected_candidate':sel,'baseline_market_holdout':bh,'challenger_holdout':mh,'research_gate_passed':gate,'interpretation':'PASS_STRUCTURAL_ZERO_MASS_SIGNAL' if gate else 'REJECT_NO_PROPER_SCORE_SAFE_STRUCTURAL_DRAW_INCREMENT','governance':{'research_only':True,'challenge_model':True,'no_poisson_or_skellam_formal_weight':True,'automatic_promotion':False,'fresh_forward_required':True,'current_rule_change':False,'formal_weight_change':False,'runtime_probability_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
