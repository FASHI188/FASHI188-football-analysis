#!/usr/bin/env python3
"""V6.27.3: soft AH residual layer with prior-season-only training and untouched random100 test.

The V6.27.2 hard AH constraint improved some net-goal metrics but damaged joint log score and was
infeasible for most rows. This challenger keeps the useful residual idea while refusing to force the
full market AH moment.

Training (2024/25 only)
-----------------------
For every legal pre-match row, build the accepted baseline matrix:
  formal 1X2 locked + V6.26 market-updated direct total locked.
Let b be its signed AH settlement moment, m the normalized two-way market signed moment (2p-1), and y
the realized signed settlement. Fit ONE global residual strength alpha analytically by least squares:
  y ~= b + alpha*(m-b)
  alpha = clip(sum((m-b)*(y-b))/sum((m-b)^2), 0, 1)
No grid search, no test-season outcomes, no per-league tuning.

Test (2025/26 only)
-------------------
Freeze alpha before touching test outcomes. Enumerate legal 2025/26 rows outcome-blind, fixed-seed
shuffle, and evaluate the first 100 successful projections. The candidate target is
  soft_m = b + alpha*(m-b)
and is reconciled through the exact scalar-dual KL solver while preserving 1X2 and total marginals.

Research only. Historical odds lack original quote timestamps; no CURRENT/formal weight change.
"""
from __future__ import annotations
import json,math,random,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import three_stage_core_v6260 as core
import validate_architecture_order_v6190 as arch
import validate_decoupled_1x2_total_fusion_v6191 as dec
import validate_joint_market_ipf_crossseason_v6164 as base
import validate_market_ou_kl_projection_v6162 as ou
import validate_three_stage_ah_netgoal_random100_v6272 as ahm
import run_three_stage_ah_netgoal_random100_v6272_dual as dual
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals,read_processed_matches,settle_home_handicap

OUT=ROOT/'manifests'/'v6_three_stage_ah_soft_residual_random100_v6273_status.json'
TRAIN_SEASON='2024/25';TEST_SEASON='2025/26';SEED=627300;TARGET=100;ATTEMPT_POOL=180


def baseline_for(hist,cid,season,m,params,temp,mk):
    try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=params,use_team_effects=True)
    except Exception:return None,None
    if not p:return None,None
    prior=temperature_scale_matrix(p['probabilities']['score_matrix'],temp);formal_one=arch.one_vec(prior);marg=derive_score_marginals(prior);td=ou.project(marg['total_goals'],float(mk['p_over25']))
    if td is None:return None,None
    target_total=[float(td[k]) for k in ou.TOTAL_KEYS]
    try:q,a=core.reconcile(prior,formal_one,target_total)
    except Exception:return None,None
    return (q,formal_one,target_total) if q is not None and a.get('converged') else (None,None)


def fit_alpha(cfg):
    warmc=int(cfg['validation']['warmup_competition_matches']);warmt=int(cfg['validation']['warmup_team_matches']);rows=[];meta={}
    for cid in dec.COMPS:
        mk=base.market_lookup(cid,TRAIN_SEASON);ah=ahm.ah_lookup(cid,TRAIN_SEASON);params=ou.params_by_season(cid).get(TRAIN_SEASON)
        if not params:continue
        matches=[m for m in read_processed_matches(cid) if str(m.season)==TRAIN_SEASON];bydate=defaultdict(list)
        for m in matches:bydate[m.date].append(m)
        hist=[];hc=Counter();ac=Counter();n=0
        for dt in sorted(bydate):
            day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team));pending=[]
            for m in day:
                key=(m.date.isoformat(),m.home_team,m.away_team)
                if len(hist)>=warmc and hc[m.home_team]>=warmt and ac[m.away_team]>=warmt and key in mk and key in ah:
                    built=baseline_for(hist,cid,TRAIN_SEASON,m,params,ou.calibrator(cid,TRAIN_SEASON),mk[key])
                    if built[0] is not None:
                        q,_,_=built;line=float(ah[key]['line']);b=ahm.settlement_moment(q,line);market=2*float(ah[key]['home_side_share'])-1;obs=settle_home_handicap(m.home_goals,m.away_goals,line);y=float(obs['win']-obs['loss']);rows.append((market-b,y-b,cid));n+=1
            for m in day:hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
        meta[cid]=n
    den=sum(x*x for x,_,_ in rows);num=sum(x*z for x,z,_ in rows);raw=num/den if den>1e-15 else 0.0;alpha=min(1.0,max(0.0,raw))
    return alpha,{'training_rows':len(rows),'rows_by_competition':meta,'raw_alpha':raw,'selected_alpha':alpha,'objective':'signed_AH_settlement_MSE_closed_form','test_season_outcomes_used':False}


def main():
    cfg=load_config();alpha,training=fit_alpha(cfg);warmc=int(cfg['validation']['warmup_competition_matches']);warmt=int(cfg['validation']['warmup_team_matches'])
    candidates=[];packs={}
    for cid in dec.COMPS:
        mk=base.market_lookup(cid,TEST_SEASON);ah=ahm.ah_lookup(cid,TEST_SEASON);params=ou.params_by_season(cid).get(TEST_SEASON)
        if not params:continue
        matches=[m for m in read_processed_matches(cid) if str(m.season)==TEST_SEASON];bydate=defaultdict(list)
        for m in matches:bydate[m.date].append(m)
        hist=[];hc=Counter();ac=Counter();ids=[]
        for dt in sorted(bydate):
            day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
            for m in day:
                k=(m.date.isoformat(),m.home_team,m.away_team)
                if len(hist)>=warmc and hc[m.home_team]>=warmt and ac[m.away_team]>=warmt and k in mk and k in ah:
                    key=(TEST_SEASON,cid,*k);candidates.append(key);ids.append(key)
            for m in day:hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
        packs[cid]={'market':mk,'ah':ah,'params':params,'matches':matches,'candidate_ids':set(ids),'temperature':ou.calibrator(cid,TEST_SEASON)}
    order=list(candidates);random.Random(SEED).shuffle(order);frozen=order[:min(len(order),ATTEMPT_POOL)];wanted=set(frozen);rank={k:i for i,k in enumerate(frozen)}
    produced={};fail=Counter();max1=maxT=maxAH=maxMass=0.0
    for cid,pack in packs.items():
        if not wanted.intersection(pack['candidate_ids']):continue
        bydate=defaultdict(list)
        for m in pack['matches']:bydate[m.date].append(m)
        hist=[]
        for dt in sorted(bydate):
            day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
            for m in day:
                key=(TEST_SEASON,cid,m.date.isoformat(),m.home_team,m.away_team)
                if key not in wanted:continue
                k3=(m.date.isoformat(),m.home_team,m.away_team);built=baseline_for(hist,cid,TEST_SEASON,m,pack['params'],pack['temperature'],pack['market'][k3])
                if built[0] is None:fail['baseline']+=1;continue
                baseline,formal_one,target_total=built;ahi=pack['ah'][k3];line=float(ahi['line']);b=ahm.settlement_moment(baseline,line);market_m=2*float(ahi['home_side_share'])-1;soft_m=b+alpha*(market_m-b);soft_share=(soft_m+1)/2
                try:cand,ca=dual.reconcile_ah_dual(baseline,formal_one,target_total,line,soft_share)
                except Exception:cand,ca=None,{'converged':False}
                if cand is None or not ca.get('converged'):fail['soft_reconciliation']+=1;continue
                co=core.one_x_two_vector(cand);ct=core.total_goals_vector(cand);bset=ahm.settlement_vector(baseline,line);cset=ahm.settlement_vector(cand,line);aset=settle_home_handicap(m.home_goals,m.away_goals,line);obs=[float(aset[k]) for k in ('win','push','loss')];bs=bset[0]-bset[2];cs=cset[0]-cset[2];ys=obs[0]-obs[2];bgd=ahm.gd_vector(baseline);cgd=ahm.gd_vector(cand);di=max(-8,min(8,m.home_goals-m.away_goals))+8
                max1=max(max1,max(abs(x-y) for x,y in zip(co,formal_one)));maxT=max(maxT,max(abs(x-y) for x,y in zip(ct,target_total)));maxAH=max(maxAH,abs(cs-soft_m));maxMass=max(maxMass,abs(sum(float(c['probability']) for c in cand)-1))
                produced[key]={'date':m.date.isoformat(),'competition_id':cid,'home':m.home_team,'away':m.away_team,'actual_score':[m.home_goals,m.away_goals],'ah_line_home':line,'baseline_moment':b,'market_moment':market_m,'soft_target_moment':soft_m,'alpha':alpha,
                 'baseline_ah_brier':ahm.brier_frac(bset,obs),'candidate_ah_brier':ahm.brier_frac(cset,obs),'baseline_ah_signed_mse':(bs-ys)**2,'candidate_ah_signed_mse':(cs-ys)**2,'baseline_ah_direction_hit':ahm.direction_hit(bs,ys),'candidate_ah_direction_hit':ahm.direction_hit(cs,ys),'baseline_gd_rps':ahm.rps(bgd,di),'candidate_gd_rps':ahm.rps(cgd,di),'baseline_gd_logloss':-math.log(max(ahm.EPS,bgd[di])),'candidate_gd_logloss':-math.log(max(ahm.EPS,cgd[di])),'baseline_score_top1':arch.score_topk(baseline,1,m.home_goals,m.away_goals),'candidate_score_top1':arch.score_topk(cand,1,m.home_goals,m.away_goals),'baseline_score_top3':arch.score_topk(baseline,3,m.home_goals,m.away_goals),'candidate_score_top3':arch.score_topk(cand,3,m.home_goals,m.away_goals),'baseline_joint_log':ahm.joint_log(baseline,m.home_goals,m.away_goals),'candidate_joint_log':ahm.joint_log(cand,m.home_goals,m.away_goals)}
            for m in day:hist.append(m)
    rows=sorted(produced.values(),key=lambda r:rank[(TEST_SEASON,r['competition_id'],r['date'],r['home'],r['away'])])[:TARGET]
    summary={'count':len(rows)}
    for pre in ('baseline','candidate'):
        for met in ('ah_brier','ah_signed_mse','ah_direction_hit','gd_rps','gd_logloss','score_top1','score_top3','joint_log'):summary[f'{pre}_{met}']=ahm.avg(rows,f'{pre}_{met}')
    summary['delta_ah_direction_pp']=((summary.get('candidate_ah_direction_hit') or 0)-(summary.get('baseline_ah_direction_hit') or 0))*100;summary['delta_score_top1_pp']=((summary.get('candidate_score_top1') or 0)-(summary.get('baseline_score_top1') or 0))*100;summary['delta_score_top3_pp']=((summary.get('candidate_score_top3') or 0)-(summary.get('baseline_score_top3') or 0))*100
    checks={'sample_100':len(rows)==TARGET,'one_x_two_invariant':max1<=5e-9,'total_invariant':maxT<=5e-9,'soft_ah_target_fitted':maxAH<=5e-9,'ah_brier_improves':summary.get('candidate_ah_brier')<summary.get('baseline_ah_brier'),'ah_signed_mse_improves':summary.get('candidate_ah_signed_mse')<summary.get('baseline_ah_signed_mse'),'goal_difference_rps_improves':summary.get('candidate_gd_rps')<summary.get('baseline_gd_rps'),'goal_difference_logloss_nonworse':summary.get('candidate_gd_logloss')<=summary.get('baseline_gd_logloss')+1e-12,'ah_direction_noninferior':summary.get('candidate_ah_direction_hit')>=summary.get('baseline_ah_direction_hit')-1e-12,'joint_log_nonworse':summary.get('candidate_joint_log')<=summary.get('baseline_joint_log')+1e-12,'score_top3_noninferior':summary.get('candidate_score_top3')>=summary.get('baseline_score_top3')-1e-12}
    report={'schema_version':'V6.27.3-priorseason-soft-ah-residual-random100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if len(rows)==TARGET else 'PARTIAL','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_PRIORSEASON_TRAIN_UNTOUCHED_2025_26_RANDOM100_NO_ORIGINAL_MARKET_TIMESTAMP','train_season':TRAIN_SEASON,'test_season':TEST_SEASON,'seed':SEED,'target':TARGET,'candidate_population':len(candidates),'attempt_pool':len(frozen),'training':training,'failures':dict(fail),'audit':{'max_1x2_residual':max1,'max_total_residual':maxT,'max_soft_ah_residual':maxAH,'max_mass_residual':maxMass,'test_outcomes_used_for_training':False,'same_day_history_frozen':True},'summary':summary,'continuation_gate':{'checks':checks,'passed':all(checks.values()),'on_failure':'STOP_AH_RESIDUAL_LAYER_PENDING_BETTER_PUSH_ADJUSTED_OR_MULTI_LINE_AH_INFORMATION'},'sample':rows,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'historical_market_quotes_lack_original_timestamp':True,'random100_is_diagnostic_only':True,'automatic_promotion':False}}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':report['status'],'training':training,'failures':dict(fail),'audit':report['audit'],'summary':summary,'continuation_gate':report['continuation_gate']},ensure_ascii=False,indent=2));return 0 if len(rows)==TARGET else 2
if __name__=='__main__':raise SystemExit(main())
