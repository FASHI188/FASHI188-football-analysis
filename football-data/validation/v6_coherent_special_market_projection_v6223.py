#!/usr/bin/env python3
"""V6.22.3 deterministic maximal-coherent hard-constraint I-projection.

Core constraints are synchronized de-vigged 1X2 + every usable ordinary half-goal OU
line. Optional special-market information is admitted only when it can coexist with all
already-accepted constraints at strict residual tolerance. Fixed admission order is:
  1. Correct Score relative quoted-cell distribution (one block),
  2. Team Total half-lines, ordered by lower overround then side/line,
  3. BTTS,
  4. safe half-goal Asian Handicap lines, ordered by lower overround then line.

A rejected optional surface remains a cross-check and never forces a compromise rewrite.
This avoids arbitrary soft weights while exploiting the richest coherent subset.
Research only; no formal probability mutation.
"""
from __future__ import annotations
from typing import Any
from v6_multiline_market_matrix_projection_v682 import (
 rows,renorm,select_1x2,total_targets,outcome_group,total_group,scale_partition,marginal,
 max_residual,kl,total_distribution,score_diagnostics
)
from v6_unified_special_market_projection_v6221 import (
 btts_target,btts_group,team_total_constraints,team_total_group,ah_constraints,ah_group,
 correct_score_relative,apply_score_relative,score_relative_residual
)
TOL=1e-8;MAX=5000

def solve(prior,bundle,one,totals,score,team_lines,btts,ah_lines):
 c=renorm(prior)
 for it in range(1,MAX+1):
  c=scale_partition(c,outcome_group,one,'1x2')
  for line,t in totals:c=scale_partition(c,total_group(line),t,f'OU{line:g}')
  if score:c=apply_score_relative(c,score)
  for x in team_lines:c=scale_partition(c,team_total_group(x['side'],x['line']),x['target'],f"TT{x['side']}{x['line']:g}")
  if btts:c=scale_partition(c,btts_group,btts,'BTTS')
  for x in ah_lines:c=scale_partition(c,ah_group(x['home_line']),x['target'],f"AH{x['home_line']:+g}")
  r={'1x2':max_residual(marginal(c,outcome_group),one)}
  r.update({f'OU:{line:g}':max_residual(marginal(c,total_group(line)),t) for line,t in totals})
  if score:r['CorrectScore:relative']=score_relative_residual(c,score)
  for x in team_lines:r[f"TT:{x['side']}:{x['line']:g}"]=max_residual(marginal(c,team_total_group(x['side'],x['line'])),x['target'])
  if btts:r['BTTS']=max_residual(marginal(c,btts_group),btts)
  for x in ah_lines:r[f"AH:{x['home_line']:+g}"]=max_residual(marginal(c,ah_group(x['home_line'])),x['target'])
  worst=max(r.values())
  if worst<=TOL:return True,c,it,r,worst
 return False,c,MAX,r,worst

def project_coherent(prior:list[dict[str,Any]],bundle:dict[str,Any],raw:dict[str,Any],score_power:float=1.0)->dict[str,Any]:
 prior=renorm(prior);one=select_1x2(bundle);totals=total_targets(bundle)
 if len(totals)<2:return {'status':'INSUFFICIENT_MULTILINE_TOTAL_CONTEXT','total_half_line_count':len(totals)}
 score=correct_score_relative(raw,prior,score_power);teams=sorted(team_total_constraints(raw,bundle.get('home_team_source',''),bundle.get('away_team_source','')),key=lambda x:(x['overround'],x['side'],x['line']));bt=btts_target(raw);ahs=sorted(ah_constraints(bundle),key=lambda x:(x['overround'],x['home_line']))
 ok,c,it,r,worst=solve(prior,bundle,one,totals,None,[],None,[])
 if not ok:return {'status':'CORE_1X2_OU_INCONSISTENT','iterations':it,'constraint_residuals':r,'max_constraint_residual':worst}
 accepted={'correct_score_relative':False,'team_totals':[],'btts':False,'ah_half_lines':[]};rejected=[]
 if score:
  q=solve(prior,bundle,one,totals,score,accepted['team_totals'],bt if accepted['btts'] else None,accepted['ah_half_lines'])
  if q[0]:accepted['correct_score_relative']=True;c,it,r,worst=q[1:]
  else:rejected.append({'surface':'correct_score_relative','reason':'INCOHERENT_WITH_ACCEPTED_CORE','max_residual':q[4]})
 for x in teams:
  trial=accepted['team_totals']+[x];q=solve(prior,bundle,one,totals,score if accepted['correct_score_relative'] else None,trial,bt if accepted['btts'] else None,accepted['ah_half_lines'])
  if q[0]:accepted['team_totals']=trial;c,it,r,worst=q[1:]
  else:rejected.append({'surface':'team_total','side':x['side'],'line':x['line'],'offer_id':x['offer_id'],'overround':x['overround'],'reason':'INCOHERENT_WITH_ACCEPTED','max_residual':q[4]})
 if bt:
  q=solve(prior,bundle,one,totals,score if accepted['correct_score_relative'] else None,accepted['team_totals'],bt,accepted['ah_half_lines'])
  if q[0]:accepted['btts']=True;c,it,r,worst=q[1:]
  else:rejected.append({'surface':'btts','reason':'INCOHERENT_WITH_ACCEPTED','max_residual':q[4]})
 for x in ahs:
  trial=accepted['ah_half_lines']+[x];q=solve(prior,bundle,one,totals,score if accepted['correct_score_relative'] else None,accepted['team_totals'],bt if accepted['btts'] else None,trial)
  if q[0]:accepted['ah_half_lines']=trial;c,it,r,worst=q[1:]
  else:rejected.append({'surface':'asian_handicap_half','home_line':x['home_line'],'offer_id':x['offer_id'],'overround':x['overround'],'reason':'INCOHERENT_WITH_ACCEPTED','max_residual':q[4]})
 ps=sum(p for _h,_a,p in rows(c));score_meta=None
 if score:score_meta={'available':True,'accepted':accepted['correct_score_relative'],'supported_count':score['supported_count'],'quoted_count':score['quoted_count'],'unsupported':score['unsupported'],'power':score['power'],'absolute_probability_claim':False}
 return {'status':'COHERENT_SPECIAL_MARKET_MATRIX_READY','method':'minimum_KL_IPF_maximal_coherent_special_market_subset','objective':'exact I-projection onto core 1X2+OU plus deterministic optional constraints that pass strict joint-coherence gate','admission_order':['correct_score_relative','team_total_by_lowest_overround','btts','asian_handicap_half_by_lowest_overround'],'iterations_final':it,'converged':True,'constraint_residuals':r,'max_constraint_residual':worst,'probability_sum_residual':abs(ps-1.0),'kl_from_prior':kl(c,prior),'accepted':{'correct_score_relative':accepted['correct_score_relative'],'team_total_lines':[{'side':x['side'],'line':x['line'],'offer_id':x['offer_id'],'overround':x['overround']} for x in accepted['team_totals']],'btts':accepted['btts'],'asian_handicap_half_lines':[{'home_line':x['home_line'],'offer_id':x['offer_id'],'overround':x['overround']} for x in accepted['ah_half_lines']]},'rejected':rejected,'correct_score_semantics':score_meta,'candidate_matrix':c,'total_goals_distribution':total_distribution(c),'score_diagnostics':score_diagnostics(c),'governance':{'hard_core':['1x2','ordinary_half_goal_OU_ladder'],'optional_incoherent_surface_is_crosscheck_only':True,'arbitrary_soft_weights':False,'correct_score_exhaustive_probability_claim':False,'unquoted_score_mass_zeroed':False,'missing_tail_fabrication':False,'research_only':True,'formal_probability_change':False,'current_rule_change':False}}
