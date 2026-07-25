#!/usr/bin/env python3
"""V6.19.1 decoupled 1X2 + total fusion challenge.

Proposed final ordering:
  A) independent de-vigged 1X2 market marginal;
  B) independent total-goal track: formal direct P(T) updated ONLY by O/U2.5;
  C) score matrix reconciliation last, hard-preserving A and B.

This is strict daily-PIT retrospective research. Historical prices have no original quote
timestamps; formal weight stays zero.
"""
from __future__ import annotations
import json,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import validate_architecture_order_v6190 as arch
import validate_joint_market_ipf_crossseason_v6164 as base
import validate_joint_market_ipf_v6163 as old_joint
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals,read_processed_matches
OUT=ROOT/'manifests'/'v6_decoupled_1x2_total_fusion_v6191_status.json'
SEASONS=arch.SEASONS;COMPS=arch.COMPS;TOL=arch.TOL


def summarize(rows:list[dict[str,Any]])->dict[str,Any]:
    if not rows:return {'count':0}
    n=len(rows);mean=lambda k:sum(float(r[k]) for r in rows)/n
    return {
      'count':n,
      'formal_1x2_top1':mean('formal_1x2_top1'),'old_joint_1x2_top1':mean('old_1x2_top1'),'new_1x2_top1':mean('new_1x2_top1'),
      'formal_1x2_brier':mean('formal_1x2_brier'),'old_joint_1x2_brier':mean('old_1x2_brier'),'new_1x2_brier':mean('new_1x2_brier'),
      'formal_1x2_logloss':mean('formal_1x2_logloss'),'old_joint_1x2_logloss':mean('old_1x2_logloss'),'new_1x2_logloss':mean('new_1x2_logloss'),
      'formal_total_top1':mean('formal_total_top1'),'ou_only_total_top1':mean('ou_total_top1'),'old_joint_total_top1':mean('old_total_top1'),'new_total_top1':mean('new_total_top1'),
      'formal_total_rps':mean('formal_total_rps'),'ou_only_total_rps':mean('ou_total_rps'),'old_joint_total_rps':mean('old_total_rps'),'new_total_rps':mean('new_total_rps'),
      'formal_score_top1':mean('formal_score_top1'),'old_joint_score_top1':mean('old_score_top1'),'new_score_top1':mean('new_score_top1'),
      'formal_score_top3':mean('formal_score_top3'),'old_joint_score_top3':mean('old_score_top3'),'new_score_top3':mean('new_score_top3'),
      'new_vs_formal_1x2_pp':(mean('new_1x2_top1')-mean('formal_1x2_top1'))*100,
      'new_vs_formal_total_pp':(mean('new_total_top1')-mean('formal_total_top1'))*100,
      'new_vs_ou_only_total_pp':(mean('new_total_top1')-mean('ou_total_top1'))*100,
      'new_vs_old_joint_total_pp':(mean('new_total_top1')-mean('old_total_top1'))*100,
      'new_vs_formal_score_top1_pp':(mean('new_score_top1')-mean('formal_score_top1'))*100,
      'new_vs_old_joint_score_top1_pp':(mean('new_score_top1')-mean('old_score_top1'))*100,
      'fixed_selective_market_primary':arch.fixed_selective(rows),
    }


def eval_comp_season(cid,season,config):
    lookup=base.market_lookup(cid,season);params=ou.params_by_season(cid).get(season)
    if not params:return [],{'reason':'NO_FORMAL_PARAMS','market_rows':len(lookup)}
    matches=[m for m in read_processed_matches(cid) if str(m.season)==season];bydate=defaultdict(list)
    for m in matches:bydate[m.date].append(m)
    hist=[];hc=Counter();ac=Counter();temp=ou.calibrator(cid,season);rows=[]
    warmc=int(config['validation']['warmup_competition_matches']);warmt=int(config['validation']['warmup_team_matches'])
    attempted=oldconv=newconv=0;maxres=max1=maxT=0.0
    for dt in sorted(bydate):
      day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
      for m in day:
        mk=lookup.get((m.date.isoformat(),m.home_team,m.away_team))
        if len(hist)<warmc or hc[m.home_team]<warmt or ac[m.away_team]<warmt or not mk:continue
        try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=params,use_team_effects=True)
        except Exception:continue
        prior=temperature_scale_matrix(p['probabilities']['score_matrix'],temp);marg=derive_score_marginals(prior)
        target_dict=ou.project(marg['total_goals'],float(mk['p_over25']))
        if target_dict is None:continue
        target_total=[float(target_dict[k]) for k in ou.TOTAL_KEYS];target_one=[float(x) for x in mk['one_x_two']];attempted+=1
        old,oa=old_joint.ipf(prior,target_one,float(mk['p_over25']))
        if old is None or not oa.get('converged'):continue
        oldconv+=1
        new,na=arch.reconcile(prior,target_one,target_total)
        if new is None or not na.get('converged'):continue
        newconv+=1;maxres=max(maxres,float(na.get('max_residual') or 0.0))
        fp=arch.one_vec(prior);op=arch.one_vec(old);np=arch.one_vec(new);ft=arch.total_vec(prior);ot=arch.total_vec(old);nt=arch.total_vec(new)
        max1=max(max1,max(abs(a-b) for a,b in zip(np,target_one)));maxT=max(maxT,max(abs(a-b) for a,b in zip(nt,target_total)))
        ri=arch.result_index(m.home_goals,m.away_goals);ti=min(7,m.home_goals+m.away_goals);rank=sorted(target_one,reverse=True)
        rows.append({
          'date':m.date.isoformat(),'competition_id':cid,'season':season,
          'formal_1x2_top1':int(max(range(3),key=lambda i:fp[i])==ri),'old_1x2_top1':int(max(range(3),key=lambda i:op[i])==ri),'new_1x2_top1':int(max(range(3),key=lambda i:np[i])==ri),
          'formal_1x2_brier':arch.brier3(fp,ri),'old_1x2_brier':arch.brier3(op,ri),'new_1x2_brier':arch.brier3(np,ri),
          'formal_1x2_logloss':arch.logloss3(fp,ri),'old_1x2_logloss':arch.logloss3(op,ri),'new_1x2_logloss':arch.logloss3(np,ri),
          'formal_total_top1':int(max(range(8),key=lambda i:ft[i])==ti),'ou_total_top1':int(max(range(8),key=lambda i:target_total[i])==ti),'old_total_top1':int(max(range(8),key=lambda i:ot[i])==ti),'new_total_top1':int(max(range(8),key=lambda i:nt[i])==ti),
          'formal_total_rps':arch.rps8(ft,ti),'ou_total_rps':arch.rps8(target_total,ti),'old_total_rps':arch.rps8(ot,ti),'new_total_rps':arch.rps8(nt,ti),
          'formal_score_top1':arch.score_topk(prior,1,m.home_goals,m.away_goals),'old_score_top1':arch.score_topk(old,1,m.home_goals,m.away_goals),'new_score_top1':arch.score_topk(new,1,m.home_goals,m.away_goals),
          'formal_score_top3':arch.score_topk(prior,3,m.home_goals,m.away_goals),'old_score_top3':arch.score_topk(old,3,m.home_goals,m.away_goals),'new_score_top3':arch.score_topk(new,3,m.home_goals,m.away_goals),
          'market_maxp':rank[0],'market_margin':rank[0]-rank[1],
        })
      for m in day:hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
    return rows,{'market_rows':len(lookup),'matches':len(matches),'attempted':attempted,'old_converged':oldconv,'new_converged':newconv,'max_new_residual':maxres,'max_1x2_constraint_residual':max1,'max_total_constraint_residual':maxT,'same_date_history_frozen':True}


def main():
    cfg=load_config();by={};meta={};allrows=[]
    for s in SEASONS:
      sr=[];meta[s]={}
      for cid in COMPS:
        r,m=eval_comp_season(cid,s,cfg);sr+=r;meta[s][cid]=m
      by[s]=summarize(sr);allrows+=sr
    agg=summarize(allrows);attempt=sum(int(m.get('attempted') or 0) for sm in meta.values() for m in sm.values());conv=sum(int(m.get('new_converged') or 0) for sm in meta.values() for m in sm.values())
    max1=max((float(m.get('max_1x2_constraint_residual') or 0) for sm in meta.values() for m in sm.values()),default=0);maxT=max((float(m.get('max_total_constraint_residual') or 0) for sm in meta.values() for m in sm.values()),default=0)
    status='PASS' if attempt>0 and conv==attempt and max1<=2*TOL and maxT<=2*TOL else 'WARN'
    payload={'schema_version':'V6.19.1-decoupled-1x2-total-fusion-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':status,'formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'strict_daily_pit':True,'seasons':list(SEASONS),'competitions':list(COMPS),'step_1_1x2':'independent de-vigged 1X2 marginal','step_2_total':'formal direct P(T) projected ONLY to de-vigged O/U2.5, preserving within <=2 and >=3 relative mass','step_3_score':'KL/IPF-style reconciliation hard-preserving both upstream marginals','no_selector_tuning':True,'tolerance':TOL},'audit':{'attempted':attempt,'new_converged':conv,'convergence_rate':conv/attempt if attempt else None,'max_1x2_constraint_residual':max1,'max_total_constraint_residual':maxT},'season_results':by,'aggregate':agg,'meta':meta,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'historical_market_quotes_lack_original_timestamp':True,'no_same_day_result_leakage':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':status,'audit':payload['audit'],'season_results':by,'aggregate':agg},ensure_ascii=False,indent=2));return 0 if status=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
