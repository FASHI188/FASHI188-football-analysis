#!/usr/bin/env python3
"""V6.26.8 random100: prior-season conditional 1X2 disagreement gate.

This is a head-specific residual gate, NOT a full-matrix MoE.
When formal and market Top-1 agree, use the equal log pool. When they disagree, classify the
pre-match regime by (formal_pick, market_pick, whether formal confidence margin >= market margin).
For each competition/target season, regime counts are frozen from strictly earlier seasons only.
A Beta(1,1) posterior mean estimates the formal-expert weight:
    w=(formal_only_correct+1)/(formal_only_correct+market_only_correct+2)
Rows where neither expert's Top-1 is correct do not manufacture a winner. The weight is then used
in the geometric opinion pool q_i proportional p_formal_i^w * p_market_i^(1-w).

No numeric blend weight is tuned on the random100 sample. Total head and final score reconciliation
are unchanged. Historical odds lack original timestamps, so this remains research only.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import three_stage_core_v6260 as core  # noqa: E402
import validate_architecture_order_v6190 as arch  # noqa: E402
import validate_decoupled_1x2_total_fusion_v6191 as dec  # noqa: E402
import validate_market_ou_kl_projection_v6162 as ou  # noqa: E402
import validate_three_stage_random100_v6264 as r100  # noqa: E402
from football_v460_engine import load_config,predict_from_history  # noqa: E402
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import derive_score_marginals  # noqa: E402

OUT=ROOT/'manifests'/'v6_three_stage_1x2_conditional_gate_random100_v6268_status.json'
EPS=1e-15
CLASSES=('home','draw','away')


def avg(rows,key): return sum(float(r[key]) for r in rows)/len(rows) if rows else None

def pick_margin(p):
    order=sorted(range(3),key=lambda i:p[i],reverse=True)
    return order[0],float(p[order[0]])-float(p[order[1]])

def regime(formal,market):
    fp,fm=pick_margin(formal);mp,mm=pick_margin(market)
    return (fp,mp,'formal_margin_ge' if fm>=mm else 'market_margin_gt')

def geometric_pool(formal,market,w):
    logs=[w*math.log(max(EPS,float(pf)))+(1-w)*math.log(max(EPS,float(pm))) for pf,pm in zip(formal,market)]
    m=max(logs);raw=[math.exp(x-m) for x in logs];z=sum(raw)
    return [x/z for x in raw]

def gate_weight(counts,key,agree):
    if agree:return 0.5,{'formal_only':0,'market_only':0,'neither':0,'weight_source':'AGREEMENT_EQUAL_LOGPOOL'}
    c=counts.get(key) or {'formal_only':0,'market_only':0,'neither':0}
    w=(float(c['formal_only'])+1.0)/(float(c['formal_only'])+float(c['market_only'])+2.0)
    return w,{**c,'weight_source':'PRIOR_SEASON_BETA11_REGIME'}


def main():
    cfg=load_config();candidates,packs=r100._enumerate_candidates(cfg);order=list(candidates);random.Random(r100.SEED).shuffle(order)
    frozen=order[:min(len(order),r100.ATTEMPT_POOL)];wanted=set(frozen);rank={k:i for i,k in enumerate(frozen)}
    produced={};failures=Counter();gate_audit={};max1=maxT=maxM=0.0

    for cid in dec.COMPS:
        prior_counts=defaultdict(lambda:{'formal_only':0,'market_only':0,'neither':0})
        gate_audit[cid]={}
        for season in dec.SEASONS:
            pack=packs.get((season,cid))
            if not pack:continue
            frozen_counts={k:dict(v) for k,v in prior_counts.items()}
            gate_audit[cid][season]={'prior_regime_count':len(frozen_counts),'prior_informative_rows':sum(v['formal_only']+v['market_only'] for v in frozen_counts.values())}
            bydate=defaultdict(list)
            for m in pack['matches']:bydate[m.date].append(m)
            hist=[];season_updates=[]
            for dt in sorted(bydate):
                day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team));day_updates=[]
                for m in day:
                    key=(season,cid,m.date.isoformat(),m.home_team,m.away_team)
                    if key not in pack['candidate_ids']:continue
                    mk=pack['lookup'].get((m.date.isoformat(),m.home_team,m.away_team))
                    try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=pack['params'],use_team_effects=True)
                    except Exception:p=None
                    if not p:failures['formal_prior']+=1;continue
                    prior=temperature_scale_matrix(p['probabilities']['score_matrix'],pack['temperature']);formal=arch.one_vec(prior);market=[float(x) for x in mk['one_x_two']]
                    actual=arch.result_index(m.home_goals,m.away_goals);fp,fmargin=pick_margin(formal);mp,mmargin=pick_margin(market);rkey=regime(formal,market)
                    if fp!=mp:
                        if fp==actual: outcome='formal_only'
                        elif mp==actual: outcome='market_only'
                        else: outcome='neither'
                        day_updates.append((rkey,outcome))
                    if key not in wanted:continue
                    w,stats=gate_weight(frozen_counts,rkey,fp==mp);fused=geometric_pool(formal,market,w)
                    marg=derive_score_marginals(prior);td=ou.project(marg['total_goals'],float(mk['p_over25']))
                    if td is None:failures['total_projection']+=1;continue
                    targetT=[float(td[k]) for k in ou.TOTAL_KEYS]
                    try:matrix,audit=core.reconcile(prior,fused,targetT)
                    except Exception:matrix,audit=None,{'converged':False}
                    if matrix is None or not audit.get('converged'):failures['reconciliation']+=1;continue
                    one=core.one_x_two_vector(matrix);ft=arch.total_vec(prior);nt=core.total_goals_vector(matrix);ti=min(7,m.home_goals+m.away_goals)
                    max1=max(max1,max(abs(a-b) for a,b in zip(one,fused)));maxT=max(maxT,max(abs(a-b) for a,b in zip(nt,targetT)));maxM=max(maxM,abs(sum(float(c['probability']) for c in matrix)-1.0))
                    produced[key]={
                      'date':m.date.isoformat(),'competition_id':cid,'season':season,'home':m.home_team,'away':m.away_team,'actual_score':[m.home_goals,m.away_goals],
                      'formal_pick':CLASSES[fp],'market_pick':CLASSES[mp],'formal_margin':fmargin,'market_margin':mmargin,'gate_weight_formal':w,'regime':list(rkey),
                      'regime_formal_only_prior':stats['formal_only'],'regime_market_only_prior':stats['market_only'],'regime_neither_prior':stats['neither'],
                      'formal_1x2_top1':int(fp==actual),'market_1x2_top1':int(mp==actual),'gate_1x2_top1':int(max(range(3),key=lambda i:one[i])==actual),
                      'formal_1x2_brier':arch.brier3(formal,actual),'market_1x2_brier':arch.brier3(market,actual),'gate_1x2_brier':arch.brier3(one,actual),
                      'formal_1x2_logloss':arch.logloss3(formal,actual),'market_1x2_logloss':arch.logloss3(market,actual),'gate_1x2_logloss':arch.logloss3(one,actual),
                      'formal_total_top1':int(max(range(8),key=lambda i:ft[i])==ti),'gate_total_top1':int(max(range(8),key=lambda i:nt[i])==ti),
                      'formal_total_rps':arch.rps8(ft,ti),'gate_total_rps':arch.rps8(nt,ti),
                      'formal_score_top1':arch.score_topk(prior,1,m.home_goals,m.away_goals),'gate_score_top1':arch.score_topk(matrix,1,m.home_goals,m.away_goals),
                      'formal_score_top3':arch.score_topk(prior,3,m.home_goals,m.away_goals),'gate_score_top3':arch.score_topk(matrix,3,m.home_goals,m.away_goals)}
                season_updates.extend(day_updates)
                for m in day:hist.append(m)
            # Freeze contract: current-season outcomes enter only future seasons' gate table.
            for rkey,outcome in season_updates:prior_counts[rkey][outcome]+=1

    rows=sorted(produced.values(),key=lambda r:rank[(r['season'],r['competition_id'],r['date'],r['home'],r['away'])])[:r100.TARGET]
    summary={'count':len(rows)}
    for p in ('formal','market','gate'):
        summary[f'{p}_1x2_top1']=avg(rows,f'{p}_1x2_top1');summary[f'{p}_1x2_brier']=avg(rows,f'{p}_1x2_brier');summary[f'{p}_1x2_logloss']=avg(rows,f'{p}_1x2_logloss')
    for p in ('formal','gate'):
        summary[f'{p}_total_top1']=avg(rows,f'{p}_total_top1');summary[f'{p}_total_rps']=avg(rows,f'{p}_total_rps');summary[f'{p}_score_top1']=avg(rows,f'{p}_score_top1');summary[f'{p}_score_top3']=avg(rows,f'{p}_score_top3')
    summary['gate_vs_formal_1x2_pp']=((summary['gate_1x2_top1'] or 0)-(summary['formal_1x2_top1'] or 0))*100
    summary['gate_vs_market_1x2_pp']=((summary['gate_1x2_top1'] or 0)-(summary['market_1x2_top1'] or 0))*100
    summary['gate_vs_formal_total_pp']=((summary['gate_total_top1'] or 0)-(summary['formal_total_top1'] or 0))*100
    summary['gate_vs_formal_score1_pp']=((summary['gate_score_top1'] or 0)-(summary['formal_score_top1'] or 0))*100
    summary['mean_gate_weight_formal']=avg(rows,'gate_weight_formal')
    report={'schema_version':'V6.26.8-priorseason-conditional-disagreement-gate-random100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
      'status':'PASS' if len(rows)==r100.TARGET else 'PARTIAL','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_RANDOM100_PRIOR_SEASON_CONDITIONAL_1X2_GATE',
      'seed':r100.SEED,'target':r100.TARGET,'candidate_population':len(candidates),'failures':dict(failures),'audit':{'max_1x2_residual':max1,'max_total_residual':maxT,'max_mass_residual':maxM,'target_season_results_used_for_gate':False,'same_day_history_frozen':True,'asian_handicap_primary_target':False},
      'summary':summary,'gate_training_audit':gate_audit,'sample':rows,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'full_matrix_moe':False,'head_specific_gate':True,'beta_prior':[1,1],'random100_is_diagnostic_only':True,'automatic_promotion':False}}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':report['status'],'failures':report['failures'],'audit':report['audit'],'summary':summary},ensure_ascii=False,indent=2));return 0 if len(rows)==r100.TARGET else 2
if __name__=='__main__':raise SystemExit(main())
