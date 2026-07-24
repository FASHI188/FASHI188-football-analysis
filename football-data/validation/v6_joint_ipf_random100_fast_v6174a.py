#!/usr/bin/env python3
"""V6.17.4a fast fixed-seed random-100 audit of V6.16.4.

Two-pass design: enumerate legal pre-match candidates without running the model, fixed-seed shuffle,
then run formal prior + the frozen V6.16.4 1X2/O-U2.5 IPF only for candidates in shuffled order
until 100 successful predictions are obtained. No outcome-based selection or tuning.
"""
from __future__ import annotations
import json, random, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; V=ROOT/'validation'; E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import validate_joint_market_ipf_crossseason_v6164 as base
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals,read_processed_matches
OUT=ROOT/'manifests'/'v6_joint_ipf_random100_fast_v6174a_status.json'
SEED=6174001; TARGET=100

def one_vec(m):
    x=derive_score_marginals(m)['1x2']; return [float(x[k]) for k in ('home','draw','away')]
def total_vec(m):
    x=derive_score_marginals(m)['total_goals']; return [float(x[k]) for k in base.TOTAL_KEYS]
def rid(h,a): return 0 if h>a else 1 if h==a else 2
def avg(rows,key): return sum(float(r[key]) for r in rows)/len(rows) if rows else None

def enumerate_candidates(cfg):
    candidates=[]; packs={}; warmc=int(cfg['validation']['warmup_competition_matches']); warmt=int(cfg['validation']['warmup_team_matches'])
    for season in base.SEASONS:
      for cid in base.COMPS:
        lookup=base.market_lookup(cid,season); params=ou.params_by_season(cid).get(season)
        if not params: continue
        matches=[m for m in read_processed_matches(cid) if str(m.season)==season]; bydate=defaultdict(list)
        for m in matches: bydate[m.date].append(m)
        hist=[];hc=Counter();ac=Counter(); ids=[]
        for dt in sorted(bydate):
          for m in sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team)):
            mk=lookup.get((m.date.isoformat(),m.home_team,m.away_team))
            if len(hist)>=warmc and hc[m.home_team]>=warmt and ac[m.away_team]>=warmt and mk:
              key=(season,cid,m.date.isoformat(),m.home_team,m.away_team); candidates.append(key);ids.append(key)
            hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
        packs[(season,cid)]={'lookup':lookup,'params':params,'matches':matches,'candidate_ids':set(ids),'temp':ou.calibrator(cid,season)}
    return candidates,packs

def main():
    cfg=load_config(); candidates,packs=enumerate_candidates(cfg); order=list(candidates); random.Random(SEED).shuffle(order); rank={k:i for i,k in enumerate(order)}
    # Try first 140 candidates to tolerate rare model/IPF failures without outcome filtering.
    wanted=set(order[:min(len(order),140)]); produced={}; failures=Counter()
    for (season,cid),pack in packs.items():
      if not any(k in wanted for k in pack['candidate_ids']): continue
      matches=pack['matches'];bydate=defaultdict(list)
      for m in matches: bydate[m.date].append(m)
      hist=[];hc=Counter();ac=Counter()
      for dt in sorted(bydate):
        for m in sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team)):
          key=(season,cid,m.date.isoformat(),m.home_team,m.away_team)
          if key in wanted:
            mk=pack['lookup'].get((m.date.isoformat(),m.home_team,m.away_team))
            try: p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=pack['params'],use_team_effects=True)
            except Exception: p=None
            if not p: failures['formal_prior']+=1
            else:
              prior=temperature_scale_matrix(p['probabilities']['score_matrix'],pack['temp']); adj,audit=base.joint.ipf(prior,mk['one_x_two'],float(mk['p_over25']))
              if adj is None or not audit.get('converged'): failures['ipf']+=1
              else:
                pt=total_vec(prior);qt=total_vec(adj);po=one_vec(prior);qo=one_vec(adj);tb=min(7,m.home_goals+m.away_goals);r=rid(m.home_goals,m.away_goals)
                produced[key]={'date':m.date.isoformat(),'competition_id':cid,'season':season,'home':m.home_team,'away':m.away_team,'actual_score':[m.home_goals,m.away_goals],'prior_total_rps':ou.rps(pt,tb),'ipf_total_rps':ou.rps(qt,tb),'prior_total_top1':int(max(range(8),key=lambda i:pt[i])==tb),'ipf_total_top1':int(max(range(8),key=lambda i:qt[i])==tb),'prior_score_top1':ou.top_score(prior,1,m.home_goals,m.away_goals),'ipf_score_top1':ou.top_score(adj,1,m.home_goals,m.away_goals),'prior_score_top3':ou.top_score(prior,3,m.home_goals,m.away_goals),'ipf_score_top3':ou.top_score(adj,3,m.home_goals,m.away_goals),'prior_1x2_top1':int(max(range(3),key=lambda i:po[i])==r),'ipf_1x2_top1':int(max(range(3),key=lambda i:qo[i])==r),'iterations':int(audit['iterations']),'max_residual':float(audit['max_residual'])}
          hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
    rows=sorted(produced.values(),key=lambda r:rank[(r['season'],r['competition_id'],r['date'],r['home'],r['away'])])[:TARGET]
    summary={'count':len(rows),'prior_exact_total_top1':avg(rows,'prior_total_top1'),'ipf_exact_total_top1':avg(rows,'ipf_total_top1'),'prior_score_top1':avg(rows,'prior_score_top1'),'ipf_score_top1':avg(rows,'ipf_score_top1'),'prior_score_top3':avg(rows,'prior_score_top3'),'ipf_score_top3':avg(rows,'ipf_score_top3'),'prior_1x2_top1':avg(rows,'prior_1x2_top1'),'ipf_1x2_top1':avg(rows,'ipf_1x2_top1'),'prior_total_rps':avg(rows,'prior_total_rps'),'ipf_total_rps':avg(rows,'ipf_total_rps')}
    report={'schema_version':'V6.17.4a-v6164-fast-random100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if len(rows)==TARGET else 'PARTIAL','classification':'RETROSPECTIVE_FIXED_SEED_RANDOM100_FROM_LEGAL_V6164_CANDIDATES','seed':SEED,'candidate_population':len(candidates),'attempt_pool':min(len(order),140),'failures':dict(failures),'summary':summary,'sample':rows,'governance':{'research_only':True,'candidate_enumeration_uses_no_outcomes':True,'no_test_tuning':True,'formal_weight_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'candidate_population':len(candidates),'failures':dict(failures),'summary':summary},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
