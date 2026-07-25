#!/usr/bin/env python3
from __future__ import annotations
import json,math,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
from v6_unified_special_market_projection_v6221 import btts_target,team_total_constraints,correct_score_relative
from v6_unified_special_market_soft_projection_v6222 import project_soft
LAD=ROOT/'evidence'/'market_ladders_v680'/'kambi_full_time_ladders.json'
OUT=ROOT/'manifests'/'v6_unified_special_market_soft_projection_v6222_status.json'
def pois(k,l):return math.exp(-l)*l**k/math.factorial(k)
def prior():
 r=[{'home_goals':h,'away_goals':a,'probability':pois(h,1.55)*pois(a,1.15)} for h in range(10) for a in range(10)];s=sum(x['probability'] for x in r)
 for x in r:x['probability']/=s
 return r
def main():
 data=json.loads(LAD.read_text(encoding='utf-8'));chosen=raw=None
 for b in data.get('bundles') or []:
  p=ROOT/str(b.get('raw_path') or '')
  if not p.exists():continue
  try:x=json.loads(p.read_text(encoding='utf-8'))
  except Exception:continue
  if btts_target(x) and team_total_constraints(x,b.get('home_team_source',''),b.get('away_team_source','')) and correct_score_relative(x,prior(),1.0):chosen=b;raw=x;break
 if chosen is None:raise SystemExit('no qualifying frozen raw bundle')
 result=project_soft(prior(),chosen,raw,1.0)
 payload={'schema_version':'V6.22.2-soft-KL-unified-special-market-selftest-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if result.get('status')=='UNIFIED_SPECIAL_MARKET_SOFT_MATRIX_READY' else 'FAIL','real_event_id':chosen.get('event_id'),'real_raw_path':chosen.get('raw_path'),'real_observed_at_utc':chosen.get('observed_at_utc'),'projection_status':result.get('status'),'optimizer_success':result.get('optimizer_success'),'optimizer_message':result.get('optimizer_message'),'iterations':result.get('iterations'),'function_evaluations':result.get('function_evaluations'),'objective_value':result.get('objective_value'),'objective_terms':result.get('objective_terms'),'constraint_residuals':result.get('constraint_residuals'),'max_constraint_residual':result.get('max_constraint_residual'),'probability_sum_residual':result.get('probability_sum_residual'),'kl_from_prior':result.get('kl_from_prior'),'input_surface_counts':result.get('input_surface_counts'),'score_diagnostics':result.get('score_diagnostics'),'total_goals_distribution':result.get('total_goals_distribution'),'correct_score_semantics':result.get('correct_score_semantics'),'governance':{'engineering_only':True,'real_frozen_market_snapshot':True,'synthetic_positive_prior_only_for_solver_test':True,'accuracy_claim':False,'promotion_claim':False,'formal_weight':0,'current_rule_change':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0 if payload['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
