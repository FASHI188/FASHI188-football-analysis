#!/usr/bin/env python3
"""V6.27.0 Fast100 fallback architecture: preserve formal 1X2, improve total, reconcile score.

This is the fail-closed adjudication after V6.26.4-.9 1X2 fusion challengers failed to improve
Top1. Stage 1 therefore remains the existing formal 1X2 marginal exactly. Stage 2 uses the V6.26
independent total track (formal direct 0-7+ prior projected only to de-vigged O/U2.5). Stage 3
reconciles the score matrix while hard-preserving BOTH accepted marginals.

Therefore 1X2 cannot be sacrificed to improve total/score. Asian handicap remains auxiliary/derived.
Exact same fixed-seed random100 diagnostic contract; research only, no CURRENT promotion.
"""
from __future__ import annotations
import json,random,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import three_stage_core_v6260 as core
import validate_architecture_order_v6190 as arch
import validate_market_ou_kl_projection_v6162 as ou
import validate_three_stage_random100_v6264 as r100
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals
OUT=ROOT/'manifests'/'v6_three_stage_formal1x2_total_random100_v6270_status.json'
EPS=1e-15

def avg(rows,key):return sum(float(r[key]) for r in rows)/len(rows) if rows else None

def joint_log(matrix,hg,ag):
    p=0.0
    for c in matrix:
        if int(c['home_goals'])==int(hg) and int(c['away_goals'])==int(ag):p+=float(c['probability'])
    import math
    return -math.log(max(EPS,p))

def main():
    cfg=load_config();candidates,packs=r100._enumerate_candidates(cfg);order=list(candidates);random.Random(r100.SEED).shuffle(order);frozen=order[:min(len(order),r100.ATTEMPT_POOL)];wanted=set(frozen);rank={k:i for i,k in enumerate(frozen)}
    produced={};failures=Counter();max1=maxT=maxM=0.0
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
                mk=pack['lookup'].get((m.date.isoformat(),m.home_team,m.away_team))
                try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=pack['params'],use_team_effects=True)
                except Exception:p=None
                if not p:failures['formal_prior']+=1;continue
                prior=temperature_scale_matrix(p['probabilities']['score_matrix'],pack['temperature']);formal_one=arch.one_vec(prior);marg=derive_score_marginals(prior);td=ou.project(marg['total_goals'],float(mk['p_over25']))
                if td is None:failures['total_projection']+=1;continue
                targetT=[float(td[k]) for k in ou.TOTAL_KEYS]
                try:matrix,audit=core.reconcile(prior,formal_one,targetT)
                except Exception:matrix,audit=None,{'converged':False}
                if matrix is None or not audit.get('converged'):failures['reconciliation']+=1;continue
                one=core.one_x_two_vector(matrix);ft=arch.total_vec(prior);nt=core.total_goals_vector(matrix);ri=arch.result_index(m.home_goals,m.away_goals);ti=min(7,m.home_goals+m.away_goals)
                max1=max(max1,max(abs(a-b) for a,b in zip(one,formal_one)));maxT=max(maxT,max(abs(a-b) for a,b in zip(nt,targetT)));maxM=max(maxM,abs(sum(float(c['probability']) for c in matrix)-1.0))
                produced[key]={'date':m.date.isoformat(),'competition_id':cid,'season':season,'home':m.home_team,'away':m.away_team,'actual_score':[m.home_goals,m.away_goals],
                  'formal_1x2_top1':int(max(range(3),key=lambda i:formal_one[i])==ri),'anchored_1x2_top1':int(max(range(3),key=lambda i:one[i])==ri),'max_1x2_probability_change':max(abs(a-b) for a,b in zip(one,formal_one)),
                  'formal_1x2_brier':arch.brier3(formal_one,ri),'anchored_1x2_brier':arch.brier3(one,ri),'formal_1x2_logloss':arch.logloss3(formal_one,ri),'anchored_1x2_logloss':arch.logloss3(one,ri),
                  'formal_total_top1':int(max(range(8),key=lambda i:ft[i])==ti),'anchored_total_top1':int(max(range(8),key=lambda i:nt[i])==ti),'formal_total_rps':arch.rps8(ft,ti),'anchored_total_rps':arch.rps8(nt,ti),
                  'formal_score_top1':arch.score_topk(prior,1,m.home_goals,m.away_goals),'anchored_score_top1':arch.score_topk(matrix,1,m.home_goals,m.away_goals),'formal_score_top3':arch.score_topk(prior,3,m.home_goals,m.away_goals),'anchored_score_top3':arch.score_topk(matrix,3,m.home_goals,m.away_goals),
                  'formal_joint_log':joint_log(prior,m.home_goals,m.away_goals),'anchored_joint_log':joint_log(matrix,m.home_goals,m.away_goals)}
            for m in day:hist.append(m)
    rows=sorted(produced.values(),key=lambda r:rank[(r['season'],r['competition_id'],r['date'],r['home'],r['away'])])[:r100.TARGET]
    summary={'count':len(rows)}
    for prefix in ('formal','anchored'):
        for metric in ('1x2_top1','1x2_brier','1x2_logloss','total_top1','total_rps','score_top1','score_top3','joint_log'):summary[f'{prefix}_{metric}']=avg(rows,f'{prefix}_{metric}')
    summary['delta_1x2_top1_pp']=((summary['anchored_1x2_top1'] or 0)-(summary['formal_1x2_top1'] or 0))*100;summary['delta_total_top1_pp']=((summary['anchored_total_top1'] or 0)-(summary['formal_total_top1'] or 0))*100;summary['delta_score_top1_pp']=((summary['anchored_score_top1'] or 0)-(summary['formal_score_top1'] or 0))*100;summary['delta_score_top3_pp']=((summary['anchored_score_top3'] or 0)-(summary['formal_score_top3'] or 0))*100
    checks={'one_x_two_exactly_preserved':max1<=2e-10 and abs((summary['anchored_1x2_top1'] or 0)-(summary['formal_1x2_top1'] or 0))<=1e-12,'total_rps_improves':summary['anchored_total_rps']<summary['formal_total_rps'],'joint_log_nonworse':summary['anchored_joint_log']<=summary['formal_joint_log']+1e-12,'score_top1_nonworse':summary['anchored_score_top1']>=summary['formal_score_top1']-1e-12}
    report={'schema_version':'V6.27.0-formal-1x2-anchored-total-score-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if len(rows)==r100.TARGET else 'PARTIAL','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_FAST100_FAIL_CLOSED_THREE_STAGE_FALLBACK','seed':r100.SEED,'target':r100.TARGET,'candidate_population':len(candidates),'failures':dict(failures),'audit':{'max_1x2_invariant_residual':max1,'max_total_residual':maxT,'max_mass_residual':maxM,'asian_handicap_primary_target':False,'same_day_history_frozen':True},'summary':summary,'architecture_gate':{'checks':checks,'passed':all(checks.values())},'sample':rows,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'1x2_fusion_experimentation_stopped':True,'formal_1x2_is_accepted_head':True,'random100_is_diagnostic_only':True,'automatic_promotion':False}}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':report['status'],'failures':report['failures'],'audit':report['audit'],'summary':summary,'architecture_gate':report['architecture_gate']},ensure_ascii=False,indent=2));return 0 if len(rows)==r100.TARGET else 2
if __name__=='__main__':raise SystemExit(main())
