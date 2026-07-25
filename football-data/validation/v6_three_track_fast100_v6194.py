#!/usr/bin/env python3
"""V6.19.4 fixed-seed Fast100 three-track isolation diagnostic.

Exactly the same random-100 sampling contract as V6.19.3. Outputs are evaluated separately:
A) 1X2 = de-vigged market 1X2 only;
B) exact score = unmodified strict-PIT formal score model only;
C) total goals = independent O/U2.5-updated P(T) only.
No track is allowed to modify another track. Research only; historical market quotes lack original timestamps.
"""
from __future__ import annotations
import json, math, random, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import validate_architecture_order_v6190 as arch
import validate_joint_market_ipf_crossseason_v6164 as base
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals,read_processed_matches

OUT=ROOT/'manifests'/'v6_three_track_fast100_v6194_status.json'
SEED=20260725+6193
N=100
SEASONS=arch.SEASONS
COMPS=arch.COMPS
EPS=1e-15


def result_idx(h,a): return 0 if h>a else 1 if h==a else 2

def score_topk(matrix,h,a,k):
    z=sorted(matrix,key=lambda c:(-float(c['probability']),int(c['home_goals']),int(c['away_goals'])))[:k]
    return int(any(int(c['home_goals'])==h and int(c['away_goals'])==a for c in z))

def score_mode(matrix):
    c=min(matrix,key=lambda c:(-float(c['probability']),int(c['home_goals']),int(c['away_goals'])))
    return int(c['home_goals']),int(c['away_goals'])

def total_topk(p,actual,k):
    idx=sorted(range(8),key=lambda i:(-p[i],i))[:k]
    return int(actual in idx)

def brier3(p,y): return sum((p[i]-(1.0 if i==y else 0.0))**2 for i in range(3))
def logloss3(p,y): return -math.log(max(EPS,p[y]))

def band(rows,lo):
    sub=[r for r in rows if r['one_maxp']>=lo]
    return {'count':len(sub),'coverage':len(sub)/len(rows) if rows else None,'hits':sum(r['one_hit'] for r in sub),'accuracy':sum(r['one_hit'] for r in sub)/len(sub) if sub else None}


def main():
    cfg=load_config();warmc=int(cfg['validation']['warmup_competition_matches']);warmt=int(cfg['validation']['warmup_team_matches'])
    contexts={};candidates=[]
    for s in SEASONS:
      for cid in COMPS:
        params=ou.params_by_season(cid).get(s)
        if not params: continue
        lookup=base.market_lookup(cid,s)
        matches=[m for m in read_processed_matches(cid) if str(m.season)==s]
        matches.sort(key=lambda m:(m.date,m.home_team,m.away_team))
        contexts[(cid,s)]={'matches':matches,'lookup':lookup,'params':params,'temp':ou.calibrator(cid,s)}
        bydate=defaultdict(list)
        for m in matches: bydate[m.date].append(m)
        histn=0;hc=Counter();ac=Counter()
        for dt in sorted(bydate):
          day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
          for m in day:
            if histn>=warmc and hc[m.home_team]>=warmt and ac[m.away_team]>=warmt and (m.date.isoformat(),m.home_team,m.away_team) in lookup:
              candidates.append((cid,s,m.date.isoformat(),m.home_team,m.away_team))
          for m in day: histn+=1;hc[m.home_team]+=1;ac[m.away_team]+=1
    if len(candidates)<N: raise RuntimeError('not enough eligible candidates')
    selected=random.Random(SEED).sample(candidates,N)
    rows=[];fails=[]
    for cid,s,di,home,away in selected:
      ctx=contexts[(cid,s)]
      target=next(m for m in ctx['matches'] if m.date.isoformat()==di and m.home_team==home and m.away_team==away)
      hist=[m for m in ctx['matches'] if m.date<target.date]
      try:
        pred=predict_from_history(hist,cid,s,home,away,target.date,selected_parameters=ctx['params'],use_team_effects=True)
      except Exception as e:
        fails.append({'id':[cid,s,di,home,away],'stage':'formal','error':str(e)});continue
      matrix=temperature_scale_matrix(pred['probabilities']['score_matrix'],ctx['temp'])
      marg=derive_score_marginals(matrix);mk=ctx['lookup'][(di,home,away)]
      one=[float(x) for x in mk['one_x_two']]
      tdict=ou.project(marg['total_goals'],float(mk['p_over25']))
      if tdict is None:
        fails.append({'id':[cid,s,di,home,away],'stage':'ou'});continue
      tp=[float(tdict[k]) for k in ou.TOTAL_KEYS]
      y=result_idx(target.home_goals,target.away_goals);one_pick=max(range(3),key=lambda i:one[i])
      actual_t=min(7,target.home_goals+target.away_goals);tmode=max(range(8),key=lambda i:tp[i])
      smh,sma=score_mode(matrix);score_result=result_idx(smh,sma);score_total=min(7,smh+sma)
      actual_score_prob=next((float(c['probability']) for c in matrix if int(c['home_goals'])==target.home_goals and int(c['away_goals'])==target.away_goals),0.0)
      rows.append({
        'competition_id':cid,'season':s,'date':di,'home':home,'away':away,
        'actual_result':y,'actual_total':actual_t,'actual_score':[target.home_goals,target.away_goals],
        'one_pick':one_pick,'one_maxp':max(one),'one_hit':int(one_pick==y),'one_brier':brier3(one,y),'one_logloss':logloss3(one,y),
        'score_mode':[smh,sma],'score_top1':score_topk(matrix,target.home_goals,target.away_goals,1),'score_top3':score_topk(matrix,target.home_goals,target.away_goals,3),'score_logloss':-math.log(max(EPS,actual_score_prob)),
        'total_mode':tmode,'total_top1':int(tmode==actual_t),'total_top2':total_topk(tp,actual_t,2),'total_rps':arch.rps8(tp,actual_t),
        'score_result_agrees_1x2':int(score_result==one_pick),'score_total_agrees_total':int(score_total==tmode),
      })
    if len(rows)!=N: raise RuntimeError(f'expected 100 valid rows, got {len(rows)} failures={fails}')
    summary={
      'one_x_two':{'count':N,'hits':sum(r['one_hit'] for r in rows),'accuracy':sum(r['one_hit'] for r in rows)/N,'brier':sum(r['one_brier'] for r in rows)/N,'logloss':sum(r['one_logloss'] for r in rows)/N,'p_ge_0_58':band(rows,.58),'p_ge_0_60':band(rows,.60)},
      'score':{'count':N,'top1_hits':sum(r['score_top1'] for r in rows),'top1_accuracy':sum(r['score_top1'] for r in rows)/N,'top3_hits':sum(r['score_top3'] for r in rows),'top3_accuracy':sum(r['score_top3'] for r in rows)/N,'logloss':sum(r['score_logloss'] for r in rows)/N,'mode_counts':dict(Counter(f"{r['score_mode'][0]}-{r['score_mode'][1]}" for r in rows))},
      'total_goals':{'count':N,'top1_hits':sum(r['total_top1'] for r in rows),'top1_accuracy':sum(r['total_top1'] for r in rows)/N,'top2_hits':sum(r['total_top2'] for r in rows),'top2_accuracy':sum(r['total_top2'] for r in rows)/N,'rps':sum(r['total_rps'] for r in rows)/N,'mode_counts':dict(Counter(str(r['total_mode']) for r in rows))},
      'cross_track_audit':{'score_result_agreement_with_independent_1x2':sum(r['score_result_agrees_1x2'] for r in rows)/N,'score_total_agreement_with_independent_total':sum(r['score_total_agrees_total'] for r in rows)/N,'no_cross_track_probability_modification':True}
    }
    payload={'schema_version':'V6.19.4-three-track-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_FAST100_RESEARCH','seed':SEED,'candidate_count':len(candidates),'sample_count':N,'design':{'same_random100_as_v6193':True,'sample_before_model_execution':True,'strict_daily_pit':True,'tracks':{'1x2':'de-vigged 1X2 only','score':'unmodified strict-PIT score model only','total':'OU2.5-updated P(T) only'},'cross_track_probability_feedback':False},'summary':summary,'rows':rows,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'no_threshold_tuning':True,'historical_market_quotes_lack_original_timestamp':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2));return 0

if __name__=='__main__': raise SystemExit(main())
