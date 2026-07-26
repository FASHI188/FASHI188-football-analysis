#!/usr/bin/env python3
"""V6.27.1 Fast100: prior-season conditional exact-score structure challenge.

Stage 1 is frozen formal 1X2. Stage 2 is frozen V6.26 total 0-7+ track. Only Stage 3 is challenged.
For each competition/target season, actual scores from STRICTLY EARLIER seasons build counts inside
(result class, exact total bucket) groups. For each target fixture, the current formal score prior
supplies a fixture-specific Dirichlet base distribution within each group. Prior strength equals the
number of score cells in that group (one equivalent pseudo-observation per supported cell):
    posterior_cell ∝ observed_prior_season_count_cell + K_group * formal_conditional_cell
No shrinkage coefficient is fitted. Current-season results never enter the score-structure prior.
The adjusted prior is finally reconciled to the unchanged formal 1X2 marginal and V6.26 total
marginal. Thus the challenge can change ONLY exact-score structure, not upstream marginals.

Predeclared Fast100 continuation gate:
  * exact-score Top1 >= formal +5 percentage points;
  * final joint score log loss no worse than formal;
  * total RPS no worse than formal;
  * 1X2 marginal exactly preserved.
Research only; historical market prices lack original timestamps.
"""
from __future__ import annotations
import json,random,sys,math
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
OUT=ROOT/'manifests'/'v6_three_stage_score_structure_random100_v6271_status.json'
EPS=1e-15

def avg(rows,key):return sum(float(r[key]) for r in rows)/len(rows) if rows else None

def rclass(h,a):return 0 if h>a else 1 if h==a else 2

def groupkey(h,a):return (min(7,h+a),rclass(h,a))

def joint_log(matrix,hg,ag):
    p=0.0
    for c in matrix:
        if int(c['home_goals'])==int(hg) and int(c['away_goals'])==int(ag):p+=float(c['probability'])
    return -math.log(max(EPS,p))

def adjust_score_prior(prior,counts):
    groups=defaultdict(list)
    for i,c in enumerate(prior):groups[groupkey(int(c['home_goals']),int(c['away_goals']))].append(i)
    out=[{'home_goals':int(c['home_goals']),'away_goals':int(c['away_goals']),'probability':float(c['probability'])} for c in prior]
    changed_groups=0
    for g,idxs in groups.items():
        mass=sum(float(prior[i]['probability']) for i in idxs)
        if mass<=0:continue
        k=len(idxs);obs=counts.get(g) or Counter();n=sum(int(v) for v in obs.values())
        if n>0:changed_groups+=1
        weights=[]
        for i in idxs:
            c=prior[i];cell=(int(c['home_goals']),int(c['away_goals']));formal_cond=float(c['probability'])/mass
            weights.append(float(obs.get(cell,0))+k*formal_cond)
        z=sum(weights)
        for i,w in zip(idxs,weights):out[i]['probability']=mass*w/max(EPS,z)
    z=sum(float(c['probability']) for c in out)
    for c in out:c['probability']=float(c['probability'])/z
    return out,changed_groups

def main():
    cfg=load_config();candidates,packs=r100._enumerate_candidates(cfg);order=list(candidates);random.Random(r100.SEED).shuffle(order);frozen=order[:min(len(order),r100.ATTEMPT_POOL)];wanted=set(frozen);rank={k:i for i,k in enumerate(frozen)}
    produced={};failures=Counter();score_audit={};max1=maxT=maxM=0.0
    for cid in sorted({k[1] for k in packs}):
        prior_counts=defaultdict(Counter);score_audit[cid]={}
        seasons=[s for s in r100.dec.SEASONS if (s,cid) in packs]
        for season in seasons:
            pack=packs[(season,cid)];score_audit[cid][season]={'prior_observed_score_count':sum(sum(c.values()) for c in prior_counts.values()),'prior_group_count':sum(1 for c in prior_counts.values() if sum(c.values())>0)}
            bydate=defaultdict(list)
            for m in pack['matches']:bydate[m.date].append(m)
            hist=[];season_observed=[]
            for dt in sorted(bydate):
                day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
                for m in day:
                    key=(season,cid,m.date.isoformat(),m.home_team,m.away_team)
                    # Accumulate all completed target-season scores only for FUTURE seasons, after season end.
                    season_observed.append((int(m.home_goals),int(m.away_goals)))
                    if key not in wanted:continue
                    mk=pack['lookup'].get((m.date.isoformat(),m.home_team,m.away_team))
                    try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=pack['params'],use_team_effects=True)
                    except Exception:p=None
                    if not p:failures['formal_prior']+=1;continue
                    prior=temperature_scale_matrix(p['probabilities']['score_matrix'],pack['temperature']);formal_one=arch.one_vec(prior);marg=derive_score_marginals(prior);td=ou.project(marg['total_goals'],float(mk['p_over25']))
                    if td is None:failures['total_projection']+=1;continue
                    targetT=[float(td[k]) for k in ou.TOTAL_KEYS];score_prior,changed=adjust_score_prior(prior,prior_counts)
                    try:matrix,audit=core.reconcile(score_prior,formal_one,targetT)
                    except Exception:matrix,audit=None,{'converged':False}
                    if matrix is None or not audit.get('converged'):failures['reconciliation']+=1;continue
                    one=core.one_x_two_vector(matrix);ft=arch.total_vec(prior);nt=core.total_goals_vector(matrix);ri=arch.result_index(m.home_goals,m.away_goals);ti=min(7,m.home_goals+m.away_goals)
                    max1=max(max1,max(abs(a-b) for a,b in zip(one,formal_one)));maxT=max(maxT,max(abs(a-b) for a,b in zip(nt,targetT)));maxM=max(maxM,abs(sum(float(c['probability']) for c in matrix)-1.0))
                    produced[key]={'date':m.date.isoformat(),'competition_id':cid,'season':season,'home':m.home_team,'away':m.away_team,'actual_score':[m.home_goals,m.away_goals],'changed_prior_groups':changed,'prior_score_training_count':score_audit[cid][season]['prior_observed_score_count'],
                      'formal_1x2_top1':int(max(range(3),key=lambda i:formal_one[i])==ri),'scorestruct_1x2_top1':int(max(range(3),key=lambda i:one[i])==ri),'formal_1x2_brier':arch.brier3(formal_one,ri),'scorestruct_1x2_brier':arch.brier3(one,ri),'formal_1x2_logloss':arch.logloss3(formal_one,ri),'scorestruct_1x2_logloss':arch.logloss3(one,ri),
                      'formal_total_top1':int(max(range(8),key=lambda i:ft[i])==ti),'scorestruct_total_top1':int(max(range(8),key=lambda i:nt[i])==ti),'formal_total_rps':arch.rps8(ft,ti),'scorestruct_total_rps':arch.rps8(nt,ti),
                      'formal_score_top1':arch.score_topk(prior,1,m.home_goals,m.away_goals),'scorestruct_score_top1':arch.score_topk(matrix,1,m.home_goals,m.away_goals),'formal_score_top3':arch.score_topk(prior,3,m.home_goals,m.away_goals),'scorestruct_score_top3':arch.score_topk(matrix,3,m.home_goals,m.away_goals),'formal_joint_log':joint_log(prior,m.home_goals,m.away_goals),'scorestruct_joint_log':joint_log(matrix,m.home_goals,m.away_goals)}
                for m in day:hist.append(m)
            # Entire season becomes available only for later seasons.
            for h,a in season_observed:
                if h+a<=7:prior_counts[groupkey(h,a)][(h,a)]+=1
    rows=sorted(produced.values(),key=lambda r:rank[(r['season'],r['competition_id'],r['date'],r['home'],r['away'])])[:r100.TARGET]
    summary={'count':len(rows)}
    for prefix in ('formal','scorestruct'):
        for metric in ('1x2_top1','1x2_brier','1x2_logloss','total_top1','total_rps','score_top1','score_top3','joint_log'):summary[f'{prefix}_{metric}']=avg(rows,f'{prefix}_{metric}')
    summary['delta_1x2_top1_pp']=((summary['scorestruct_1x2_top1'] or 0)-(summary['formal_1x2_top1'] or 0))*100;summary['delta_total_top1_pp']=((summary['scorestruct_total_top1'] or 0)-(summary['formal_total_top1'] or 0))*100;summary['delta_score_top1_pp']=((summary['scorestruct_score_top1'] or 0)-(summary['formal_score_top1'] or 0))*100;summary['delta_score_top3_pp']=((summary['scorestruct_score_top3'] or 0)-(summary['formal_score_top3'] or 0))*100
    checks={'score_top1_plus_5pp':summary['scorestruct_score_top1']>=summary['formal_score_top1']+0.05-1e-12,'joint_log_nonworse':summary['scorestruct_joint_log']<=summary['formal_joint_log']+1e-12,'total_rps_nonworse':summary['scorestruct_total_rps']<=summary['formal_total_rps']+1e-12,'one_x_two_exactly_preserved':abs(summary['scorestruct_1x2_top1']-summary['formal_1x2_top1'])<=1e-12 and max1<=2e-10}
    report={'schema_version':'V6.27.1-priorseason-conditional-score-structure-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if len(rows)==r100.TARGET else 'PARTIAL','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_FAST100_SCORE_STRUCTURE_ONLY','seed':r100.SEED,'target':r100.TARGET,'candidate_population':len(candidates),'failures':dict(failures),'audit':{'max_1x2_invariant_residual':max1,'max_total_residual':maxT,'max_mass_residual':maxM,'target_season_scores_used_for_prior':False,'same_day_history_frozen':True,'asian_handicap_primary_target':False},'summary':summary,'score_training_audit':score_audit,'fast100_gate':{'checks':checks,'continue_score_research':all(checks.values()),'on_failure':'STOP_SCORE_STRUCTURE_EXPERIMENT_RETAIN_FORMAL_SCORE_PRIOR_PENDING_NEW_INFORMATION'},'sample':rows,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'upstream_1x2_locked':True,'upstream_total_locked':True,'no_fitted_shrinkage_weight':True,'random100_is_diagnostic_only':True,'automatic_promotion':False}}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':report['status'],'failures':report['failures'],'audit':report['audit'],'summary':summary,'fast100_gate':report['fast100_gate']},ensure_ascii=False,indent=2));return 0 if len(rows)==r100.TARGET else 2
if __name__=='__main__':raise SystemExit(main())
