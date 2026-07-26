#!/usr/bin/env python3
"""Efficient runner for V6.27.2.

Uses the exact V6.27.2 model/metrics but computes only the first 220 candidates in the frozen
seed-6260100 outcome-blind order, then reports the first 100 successful reconciliations. This changes
no prediction rule or candidate ordering; it only avoids evaluating thousands of candidates that can
never enter the random100 receipt.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))

import validate_three_stage_ah_netgoal_random100_v6272 as ahm
import validate_decoupled_1x2_total_fusion_v6191 as dec
import validate_joint_market_ipf_crossseason_v6164 as base
import validate_market_ou_kl_projection_v6162 as ou
import validate_architecture_order_v6190 as arch
import three_stage_core_v6260 as core
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals,read_processed_matches,settle_home_handicap

ATTEMPT_POOL=220


def main()->int:
    cfg=load_config();warmc=int(cfg['validation']['warmup_competition_matches']);warmt=int(cfg['validation']['warmup_team_matches'])
    candidates=[];packs={};ah_sources=Counter()
    for season in dec.SEASONS:
        for cid in dec.COMPS:
            mk=base.market_lookup(cid,season); ah=ahm.ah_lookup(cid,season); params=ou.params_by_season(cid).get(season)
            if not params:continue
            matches=[m for m in read_processed_matches(cid) if str(m.season)==season];bydate=defaultdict(list)
            for m in matches:bydate[m.date].append(m)
            hist=[];hc=Counter();ac=Counter();ids=[]
            for dt in sorted(bydate):
                day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
                for m in day:
                    key3=(m.date.isoformat(),m.home_team,m.away_team)
                    if len(hist)>=warmc and hc[m.home_team]>=warmt and ac[m.away_team]>=warmt and key3 in mk and key3 in ah:
                        key=(season,cid,*key3);candidates.append(key);ids.append(key);ah_sources[ah[key3]['source']]+=1
                for m in day:hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
            packs[(season,cid)]={'market':mk,'ah':ah,'params':params,'matches':matches,'candidate_ids':set(ids),'temperature':ou.calibrator(cid,season)}

    order=list(candidates);random.Random(ahm.SEED).shuffle(order);frozen=order[:min(len(order),ATTEMPT_POOL)];wanted=set(frozen);rank={k:i for i,k in enumerate(frozen)}
    produced={};failures=Counter();max1=maxT=maxAH=maxMass=0.0
    for (season,cid),pack in packs.items():
        if not wanted.intersection(pack['candidate_ids']):continue
        bydate=defaultdict(list)
        for m in pack['matches']:bydate[m.date].append(m)
        hist=[]
        for dt in sorted(bydate):
            day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
            for m in day:
                key=(season,cid,m.date.isoformat(),m.home_team,m.away_team)
                if key not in wanted:continue
                key3=(m.date.isoformat(),m.home_team,m.away_team);mk=pack['market'].get(key3);ah=pack['ah'].get(key3)
                try:pred=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=pack['params'],use_team_effects=True)
                except Exception:pred=None
                if not pred:failures['formal_prior']+=1;continue
                prior=temperature_scale_matrix(pred['probabilities']['score_matrix'],pack['temperature']);formal_one=arch.one_vec(prior);marg=derive_score_marginals(prior);td=ou.project(marg['total_goals'],float(mk['p_over25']))
                if td is None:failures['total_projection']+=1;continue
                target_total=[float(td[k]) for k in ou.TOTAL_KEYS]
                try:baseline,ba=core.reconcile(prior,formal_one,target_total)
                except Exception:baseline,ba=None,{'converged':False}
                if baseline is None or not ba.get('converged'):failures['baseline_reconciliation']+=1;continue
                try:candidate,ca=ahm.reconcile_ah(baseline,formal_one,target_total,float(ah['line']),float(ah['home_side_share']))
                except Exception:candidate,ca=None,{'converged':False}
                if candidate is None or not ca.get('converged'):failures['ah_reconciliation']+=1;continue
                co=core.one_x_two_vector(candidate);ct=core.total_goals_vector(candidate);line=float(ah['line']);bset=ahm.settlement_vector(baseline,line);cset=ahm.settlement_vector(candidate,line);aset=settle_home_handicap(m.home_goals,m.away_goals,line);obs=[float(aset[k]) for k in ('win','push','loss')]
                bs=bset[0]-bset[2];cs=cset[0]-cset[2];actual_signed=obs[0]-obs[2];bgd=ahm.gd_vector(baseline);cgd=ahm.gd_vector(candidate);actual_d=max(-8,min(8,m.home_goals-m.away_goals))+8
                max1=max(max1,max(abs(a-b) for a,b in zip(co,formal_one)));maxT=max(maxT,max(abs(a-b) for a,b in zip(ct,target_total)));maxAH=max(maxAH,abs(cs-(2*float(ah['home_side_share'])-1)));maxMass=max(maxMass,abs(sum(float(c['probability']) for c in candidate)-1.0))
                produced[key]={
                  'date':m.date.isoformat(),'competition_id':cid,'season':season,'home':m.home_team,'away':m.away_team,'actual_score':[m.home_goals,m.away_goals],'ah_line_home':line,'ah_home_side_share':float(ah['home_side_share']),'ah_source':ah['source'],
                  'baseline_ah_brier':ahm.brier_frac(bset,obs),'candidate_ah_brier':ahm.brier_frac(cset,obs),'baseline_ah_signed_mse':(bs-actual_signed)**2,'candidate_ah_signed_mse':(cs-actual_signed)**2,'baseline_ah_direction_hit':ahm.direction_hit(bs,actual_signed),'candidate_ah_direction_hit':ahm.direction_hit(cs,actual_signed),
                  'baseline_gd_rps':ahm.rps(bgd,actual_d),'candidate_gd_rps':ahm.rps(cgd,actual_d),'baseline_gd_logloss':-math.log(max(ahm.EPS,bgd[actual_d])),'candidate_gd_logloss':-math.log(max(ahm.EPS,cgd[actual_d])),
                  'baseline_score_top1':arch.score_topk(baseline,1,m.home_goals,m.away_goals),'candidate_score_top1':arch.score_topk(candidate,1,m.home_goals,m.away_goals),'baseline_score_top3':arch.score_topk(baseline,3,m.home_goals,m.away_goals),'candidate_score_top3':arch.score_topk(candidate,3,m.home_goals,m.away_goals),'baseline_joint_log':ahm.joint_log(baseline,m.home_goals,m.away_goals),'candidate_joint_log':ahm.joint_log(candidate,m.home_goals,m.away_goals),'iterations':int(ca.get('iterations') or 0),'max_residual':float(ca.get('max_residual') or 0.0)}
            for m in day:hist.append(m)

    rows=sorted(produced.values(),key=lambda r:rank[(r['season'],r['competition_id'],r['date'],r['home'],r['away'])])[:ahm.TARGET]
    summary={'count':len(rows)}
    for prefix in ('baseline','candidate'):
        for metric in ('ah_brier','ah_signed_mse','ah_direction_hit','gd_rps','gd_logloss','score_top1','score_top3','joint_log'):summary[f'{prefix}_{metric}']=ahm.avg(rows,f'{prefix}_{metric}')
    summary['delta_ah_direction_pp']=((summary['candidate_ah_direction_hit'] or 0)-(summary['baseline_ah_direction_hit'] or 0))*100;summary['delta_score_top1_pp']=((summary['candidate_score_top1'] or 0)-(summary['baseline_score_top1'] or 0))*100;summary['delta_score_top3_pp']=((summary['candidate_score_top3'] or 0)-(summary['baseline_score_top3'] or 0))*100
    checks={'sample_100':len(rows)==ahm.TARGET,'one_x_two_invariant':max1<=ahm.TOL,'total_invariant':maxT<=ahm.TOL,'ah_moment_fitted':maxAH<=ahm.TOL,'ah_brier_improves':summary.get('candidate_ah_brier') is not None and summary['candidate_ah_brier']<summary['baseline_ah_brier'],'ah_signed_mse_improves':summary.get('candidate_ah_signed_mse') is not None and summary['candidate_ah_signed_mse']<summary['baseline_ah_signed_mse'],'goal_difference_rps_improves':summary.get('candidate_gd_rps') is not None and summary['candidate_gd_rps']<summary['baseline_gd_rps'],'ah_direction_noninferior':summary.get('candidate_ah_direction_hit') is not None and summary['candidate_ah_direction_hit']>=summary['baseline_ah_direction_hit']-1e-12,'joint_log_nonworse':summary.get('candidate_joint_log') is not None and summary['candidate_joint_log']<=summary['baseline_joint_log']+1e-12}
    report={'schema_version':'V6.27.2-ah-netgoal-fixed-seed-random100-r2-efficient','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if len(rows)==ahm.TARGET else 'PARTIAL','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_RANDOM100_AH_NETGOAL_NO_ORIGINAL_MARKET_TIMESTAMP','seed':ahm.SEED,'target':ahm.TARGET,'candidate_population':len(candidates),'attempt_pool':len(frozen),'failures':dict(failures),'ah_source_candidate_counts':dict(ah_sources),'audit':{'max_1x2_invariant_residual':max1,'max_total_invariant_residual':maxT,'max_ah_moment_residual':maxAH,'max_probability_mass_residual':maxMass,'same_day_history_frozen':True,'ah_role':'DEDICATED_NET_GOAL_LAYER_RESEARCH_ONLY','normalized_two_way_ah_share_is_not_claimed_exact_push_adjusted_probability':True},'summary':summary,'continuation_gate':{'checks':checks,'passed':all(checks.values()),'on_failure':'DO_NOT_PROMOTE_AH_AS_HARD_NETGOAL_CONSTRAINT'},'sample':rows,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'historical_market_quotes_lack_original_timestamp':True,'random100_is_diagnostic_only':True,'fixed_attempt_pool_before_evaluation':True,'automatic_promotion':False,'one_x_two_locked':True,'total_goals_locked':True}}
    ahm.OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':report['status'],'candidate_population':len(candidates),'attempt_pool':len(frozen),'failures':dict(failures),'audit':report['audit'],'summary':summary,'continuation_gate':report['continuation_gate']},ensure_ascii=False,indent=2));return 0 if len(rows)==ahm.TARGET else 2

if __name__=='__main__':raise SystemExit(main())
