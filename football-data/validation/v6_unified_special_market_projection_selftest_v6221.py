#!/usr/bin/env python3
from __future__ import annotations
import json,math,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
from v6_unified_special_market_projection_v6221 import project_unified,btts_target,team_total_constraints,correct_score_relative,ah_constraints
LAD=ROOT/'evidence'/'market_ladders_v680'/'kambi_full_time_ladders.json'
OUT=ROOT/'manifests'/'v6_unified_special_market_projection_v6221_status.json'

def poisson(k,l):return math.exp(-l)*l**k/math.factorial(k)
def prior():
 rows=[]
 for h in range(10):
  for a in range(10):rows.append({'home_goals':h,'away_goals':a,'probability':poisson(h,1.55)*poisson(a,1.15)})
 s=sum(x['probability'] for x in rows)
 for x in rows:x['probability']/=s
 return rows

def main():
 data=json.loads(LAD.read_text(encoding='utf-8'));chosen=None;raw=None
 for b in data.get('bundles') or []:
  p=ROOT/str(b.get('raw_path') or '')
  if not p.exists():continue
  try:r=json.loads(p.read_text(encoding='utf-8'))
  except Exception:continue
  if btts_target(r) and team_total_constraints(r,b.get('home_team_source',''),b.get('away_team_source','')) and correct_score_relative(r,prior(),1.0):
   chosen=b;raw=r;break
 if chosen is None:raise SystemExit('no real frozen bundle with special-market surfaces')
 result=project_unified(prior(),chosen,raw,1.0)
 payload={'schema_version':'V6.22.1-unified-special-market-projection-selftest-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if result.get('status')=='UNIFIED_SPECIAL_MARKET_MATRIX_READY' else 'FAIL','real_event_id':chosen.get('event_id'),'real_raw_path':chosen.get('raw_path'),'real_observed_at_utc':chosen.get('observed_at_utc'),'input_surface_counts':{'total_lines':len(chosen.get('total_goal_ladder') or []),'ah_lines':len(chosen.get('asian_handicap_ladder') or []),'team_total_half_lines':len(team_total_constraints(raw,chosen.get('home_team_source',''),chosen.get('away_team_source',''))),'btts':bool(btts_target(raw)),'correct_score_relative':bool(correct_score_relative(raw,prior(),1.0)),'safe_ah_half_lines':len(ah_constraints(chosen))},'projection_status':result.get('status'),'iterations':result.get('iterations'),'max_constraint_residual':result.get('max_constraint_residual'),'probability_sum_residual':result.get('probability_sum_residual'),'kl_from_prior':result.get('kl_from_prior'),'constraint_residuals':result.get('constraint_residuals'),'score_diagnostics':result.get('score_diagnostics'),'total_goals_distribution':result.get('total_goals_distribution'),'available_constraints':result.get('available_constraints'),'governance':{'engineering_only':True,'real_frozen_market_snapshot':True,'synthetic_positive_prior_only_for_solver_test':True,'accuracy_claim':False,'promotion_claim':False,'formal_weight':0,'current_rule_change':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0 if payload['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
