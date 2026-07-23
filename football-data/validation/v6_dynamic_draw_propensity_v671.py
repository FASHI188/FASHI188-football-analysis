#!/usr/bin/env python3
"""V6.7.1 time-decayed team draw-propensity residual challenger.

Tests an orthogonal hypothesis not covered by prior draw-band or static multimarket models:
for the same market-implied draw probability, some teams may exhibit persistent short-lived
over/under draw tendency. State uses only matches completed before the current match and resets
at each season boundary. The market remains the base prediction; the specialist is decision-only.

Fit 2022/23+2023/24, select 2024/25, holdout 2025/26. Research only.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/'validation'; ENGINE=ROOT/'engine'
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import v6_direct_outcome_mvp_v600 as base
import v6_market_residual_fusion_v620 as mkt
import v6_multimarket_draw_side_v643 as mm
from platform_core import PlatformError, normalize_team_token

OUT=ROOT/'manifests'/'v6_dynamic_draw_propensity_v671_status.json'
SEASONS=("2022/23","2023/24","2024/25","2025/26")
SEASON_CODES={"2022/23":"2223","2023/24":"2324","2024/25":"2425","2025/26":"2526"}
HALF_LIVES=(5.0,10.0,20.0)
L2_GRID=(1.0,10.0,100.0,1000.0)
THRESHOLDS=tuple(round(0.24+i*0.01,2) for i in range(39)) # .24-.62
MIN_OVERRIDES=40


def logit(p):
    p=min(1-1e-8,max(1e-8,p)); return math.log(p/(1-p))


def raw_rows():
    out={s:[] for s in SEASONS}; audit={}
    for cid,code in mm.LEAGUES.items():
        built=mkt._build_domain_rows_with_identity(cid,list(SEASONS));audit[cid]={}
        for season in SEASONS:
            raw,url=mkt._download_csv(code,SEASON_CODES[season]);matched,stats=mm.match_rows(cid,built[season],raw)
            for r in matched:
                x=dict(r);x['competition_id']=cid;x['season']=season;out[season].append(x)
            audit[cid][season]={'url':url,'matched':len(matched),'stats':stats}
    return out,audit


def with_state(rows_by_season,half_life):
    alpha=1-math.exp(math.log(0.5)/half_life)
    out={s:[] for s in SEASONS}
    for season in SEASONS:
        by_comp=defaultdict(list)
        for r in rows_by_season[season]: by_comp[r['competition_id']].append(r)
        for cid,rows in by_comp.items():
            rows=sorted(rows,key=lambda r:(r['date'],r['home_team'],r['away_team']))
            st=defaultdict(float); n=defaultdict(int); league=0.0; league_n=0
            for r in rows:
                q=r['surface']['one']; market_pick=max(('home','draw','away'),key=lambda k:q[k])
                h=normalize_team_token(r['home_team']);a=normalize_team_token(r['away_team'])
                hs=st[h]*(n[h]/(n[h]+5.0)) if n[h] else 0.0
                as_=st[a]*(n[a]/(n[a]+5.0)) if n[a] else 0.0
                ls=league*(league_n/(league_n+20.0)) if league_n else 0.0
                side_gap=abs(q['home']-q['away']);under=r['surface']['under_prob'];ahbal=abs(r['surface']['ah_home_prob']-.5)
                z=dict(r);z['market_pick']=market_pick;z['draw_y']=1 if r['actual_result']=='draw' else 0
                z['draw_state_x']=[1.0,logit(q['draw']),q['draw'],side_gap,under-.5,abs(r['surface']['ah_line']),ahbal,hs,as_,(hs+as_)/2,abs(hs-as_),ls,q['draw']*((hs+as_)/2)]
                out[season].append(z)
                residual=z['draw_y']-q['draw']
                st[h]=(1-alpha)*st[h]+alpha*residual;st[a]=(1-alpha)*st[a]+alpha*residual;n[h]+=1;n[a]+=1
                league=(1-alpha)*league+alpha*residual;league_n+=1
    return out


def fit(rows,l2):
    return base._fit_binary([r for r in rows if r['market_pick']!='draw'],'draw_state_x','draw_y',l2)


def score(rows,model=None,threshold=None):
    total=hits=market_hits=overrides=draw_hits=lost=captured=0; actual_draw=0
    for r in rows:
        mp=r['market_pick'];t=r['actual_result'];mh=int(mp==t);market_hits+=mh;pick=mp;actual_draw+=int(t=='draw')
        if model is not None and threshold is not None and mp!='draw' and base._predict_binary(model,r['draw_state_x'])>=threshold:
            overrides+=1;pick='draw'
            if t=='draw':draw_hits+=1;captured+=1
            elif mh:lost+=1
        hits+=int(pick==t);total+=1
    return {'count':total,'hits':hits,'accuracy':hits/total if total else None,'market_hits':market_hits,'market_accuracy':market_hits/total if total else None,'accuracy_gain_pp':100*(hits-market_hits)/total if total else None,'override_count':overrides,'draw_hits':draw_hits,'draw_precision':draw_hits/overrides if overrides else None,'draw_recall':draw_hits/actual_draw if actual_draw else None,'actual_draw_count':actual_draw,'original_wrong_draws_captured':captured,'original_correct_picks_lost':lost,'paired_net_hits':captured-lost}


def main():
    raw,audit=raw_rows();candidates=[];baselines={}
    selected=None
    for hl in HALF_LIVES:
        data=with_state(raw,hl);tr=data['2022/23']+data['2023/24'];va=data['2024/25'];ho=data['2025/26']
        if min(len(tr),len(va),len(ho))<700: raise PlatformError('insufficient state rows')
        if not baselines:baselines={'validation':score(va),'holdout':score(ho)}
        for l2 in L2_GRID:
            model=fit(tr,l2)
            for th in THRESHOLDS:
                s=score(va,model,th);c={'half_life':hl,'l2':l2,'threshold':th,'eligible':s['override_count']>=MIN_OVERRIDES,'validation':s};candidates.append(c)
    elig=[c for c in candidates if c['eligible']]
    elig.sort(key=lambda c:(-c['validation']['accuracy'],-c['validation']['paired_net_hits'],-(c['validation']['draw_precision'] or 0),c['validation']['override_count'],c['half_life'],c['l2'],c['threshold']))
    selected=elig[0] if elig else None;hold=None;gate=False
    if selected and selected['validation']['accuracy_gain_pp']>0:
        data=with_state(raw,float(selected['half_life']));refit=fit(data['2022/23']+data['2023/24']+data['2024/25'],float(selected['l2']));hold=score(data['2025/26'],refit,float(selected['threshold']));gate=bool(hold['accuracy_gain_pp']>0 and hold['paired_net_hits']>0 and hold['override_count']>=40)
    out={'schema_version':'V6.7.1-dynamic-draw-propensity-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','design':{'state':'EWMA of prior actual_draw - market_draw_probability','season_reset':True,'strictly_prior_matches_only':True,'half_lives':list(HALF_LIVES),'probability_rewrite':False,'holdout_used_for_selection':False},'source_audit':audit,'baseline':baselines,'selected_candidate':selected,'holdout_result':hold,'research_gate_passed':gate,'interpretation':'PASS_DYNAMIC_DRAW_SIGNAL' if gate else 'REJECT_NO_STABLE_DYNAMIC_DRAW_INCREMENT','governance':{'research_only':True,'automatic_promotion':False,'fresh_forward_required':True,'current_rule_change':False,'formal_weight_change':False,'runtime_probability_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
