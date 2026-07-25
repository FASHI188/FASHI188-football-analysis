#!/usr/bin/env python3
"""V6.22.1 unified special-market constrained joint-score-matrix projector.

Research-only extension of V6.8.2.  It keeps ONE explicit joint-score prior and projects
that same matrix against synchronized full-time prematch market surfaces from ONE frozen
Kambi event response:
  * 1X2;
  * ordinary half-goal Total Goals lines;
  * full-time BTTS;
  * full-time Home/Away Team Total half-goal lines;
  * safe half-goal Asian Handicap lines;
  * Correct Score RELATIVE quoted-cell probabilities.

Correct Score is intentionally conditional/relative because V6.22.0 proved Kambi exposes
only numeric score cells and no priced aggregate catch-all tail.  Therefore this module
NEVER normalizes the quoted score cells to probability 1.  It preserves their current
joint mass and only changes the distribution WITHIN the supported quoted subset according
to normalized inverse prices.  Unquoted/tail mass remains supplied by the explicit prior
and the other complete market surfaces.

All constraints are frozen-input only.  No formal/current/runtime probability is mutated.
"""
from __future__ import annotations
import json,math,re
from collections import defaultdict
from pathlib import Path
from typing import Any,Callable

from v6_multiline_market_matrix_projection_v682 import (
    EPS,TOL,MAX_ITER,rows,renorm,devig,half_line,outcome_group,total_group,
    scale_partition,marginal,max_residual,kl,total_distribution,score_diagnostics,
    select_1x2,total_targets,
)

SCORE_RE=re.compile(r'^\s*(\d+)\s*[-:]\s*(\d+)\s*$')

def text(v:Any)->str:return str(v or '').strip()
def norm_team(v:Any)->str:return re.sub(r'[^a-z0-9]+','',text(v).casefold())
def english(o:dict[str,Any])->str:return text(o.get('englishLabel') or o.get('englishName') or o.get('label') or o.get('name'))
def is_prematch(off:dict[str,Any])->bool:
    tags={text(x).upper() for x in (off.get('tags') or [])};return not tags or 'OFFERED_PREMATCH' in tags
def full_time(off:dict[str,Any])->bool:
    life=text((off.get('criterion') or {}).get('lifetime')).upper();lab=english(off.get('criterion') or {}).casefold()
    return life in {'','FULL_TIME','MATCH'} and '1st half' not in lab and '2nd half' not in lab and 'half time' not in lab

def decimal(raw:Any)->float|None:
    try:v=float(raw)/1000.0
    except Exception:return None
    return v if math.isfinite(v) and v>1.0 else None

def open_outcomes(off:dict[str,Any])->list[dict[str,Any]]:
    return [x for x in (off.get('outcomes') or []) if isinstance(x,dict) and text(x.get('status')).upper()=='OPEN']

def choose_lower_margin(existing:dict[str,Any]|None,candidate:dict[str,Any])->dict[str,Any]:
    if existing is None:return candidate
    return candidate if float(candidate.get('overround',1e9))<float(existing.get('overround',1e9)) else existing

def btts_target(raw:dict[str,Any])->dict[str,float]|None:
    for off in (raw.get('payload') or {}).get('betOffers') or []:
        if not isinstance(off,dict) or not is_prematch(off) or not full_time(off):continue
        if english(off.get('criterion') or {}).casefold()!='both teams to score':continue
        vals={}
        for x in open_outcomes(off):
            od=decimal(x.get('odds'));lab=english(x).casefold()
            if od is None:continue
            if lab in {'yes','ja'}:vals['yes']=od
            elif lab in {'no','nee'}:vals['no']=od
        if set(vals)=={'yes','no'}:return devig(vals)
    return None

def btts_group(h:int,a:int)->str:return 'yes' if h>0 and a>0 else 'no'

def team_total_constraints(raw:dict[str,Any],home_name:str,away_name:str)->list[dict[str,Any]]:
    targets:dict[tuple[str,float],dict[str,Any]]={};hn,an=norm_team(home_name),norm_team(away_name)
    for off in (raw.get('payload') or {}).get('betOffers') or []:
        if not isinstance(off,dict) or not is_prematch(off) or not full_time(off):continue
        lab=english(off.get('criterion') or {});low=lab.casefold()
        if not low.startswith('total goals by '):continue
        team=lab[len('Total Goals by '):].strip();tn=norm_team(team)
        side='home' if tn==hn else 'away' if tn==an else None
        if side is None:continue
        oo=uu=None;line=None
        for x in open_outcomes(off):
            od=decimal(x.get('odds'))
            try:ln=float(x.get('line'))/1000.0
            except Exception:ln=None
            if od is None or ln is None:continue
            if text(x.get('type'))=='OT_OVER':oo=od;line=ln
            elif text(x.get('type'))=='OT_UNDER':uu=od;line=ln
        if oo is None or uu is None or line is None or not half_line(line):continue
        inv=1/oo+1/uu;c={'side':side,'line':line,'target':devig({'over':oo,'under':uu}),'overround':inv-1,'offer_id':off.get('id')}
        targets[(side,line)]=choose_lower_margin(targets.get((side,line)),c)
    return [targets[k] for k in sorted(targets,key=lambda z:(z[0],z[1]))]

def team_total_group(side:str,line:float)->Callable[[int,int],str]:
    threshold=math.floor(line)
    return (lambda h,a:'under' if h<=threshold else 'over') if side=='home' else (lambda h,a:'under' if a<=threshold else 'over')

def ah_constraints(bundle:dict[str,Any])->list[dict[str,Any]]:
    home_ref=norm_team(bundle.get('home_team_source'));away_ref=norm_team(bundle.get('away_team_source'));by_line={}
    for off in bundle.get('asian_handicap_ladder') or []:
        outs=off.get('outcomes') or []
        if len(outs)!=2:continue
        hr=next((x for x in outs if norm_team(x.get('participant'))==home_ref),None);ar=next((x for x in outs if norm_team(x.get('participant'))==away_ref),None)
        if not hr or not ar:continue
        line=float(hr['line'])
        if not half_line(abs(line)):continue
        # Half-handicap lines have no push; opposite side should be -line.
        if abs(float(ar['line'])+line)>1e-9:continue
        hp,ap=float(hr['odds']),float(ar['odds']);target=devig({'home':hp,'away':ap});overround=1/hp+1/ap-1
        c={'home_line':line,'target':target,'overround':overround,'offer_id':off.get('offer_id')}
        if line not in by_line or overround<by_line[line]['overround']:by_line[line]=c
    return [by_line[k] for k in sorted(by_line)]

def ah_group(home_line:float)->Callable[[int,int],str]:
    def g(h:int,a:int)->str:
        adjusted=(h-a)+home_line
        if abs(adjusted)<=1e-12:raise ValueError('half-line AH unexpectedly produced push')
        return 'home' if adjusted>0 else 'away'
    return g

def correct_score_relative(raw:dict[str,Any],prior:list[dict[str,Any]],power:float=1.0)->dict[str,Any]|None:
    support={(h,a) for h,a,_p in rows(prior)};best=None
    for off in (raw.get('payload') or {}).get('betOffers') or []:
        if not isinstance(off,dict) or not is_prematch(off) or not full_time(off):continue
        if english(off.get('criterion') or {}).casefold()!='correct score':continue
        priced=[];unsupported=[]
        for x in open_outcomes(off):
            m=SCORE_RE.match(english(x));od=decimal(x.get('odds'))
            if not m or od is None:continue
            sc=(int(m.group(1)),int(m.group(2)))
            if sc in support:priced.append((sc,od,x.get('changedDate')))
            else:unsupported.append({'score':list(sc),'odds':od,'changedDate':x.get('changedDate')})
        if len(priced)<3:continue
        inv=[(1/od)**power for _sc,od,_cd in priced];z=sum(inv);target={sc:w/z for (sc,_od,_cd),w in zip(priced,inv)}
        cand={'offer_id':off.get('id'),'target':target,'supported_count':len(priced),'unsupported':unsupported,'quoted_count':len(priced)+len(unsupported),'power':power}
        if best is None or cand['supported_count']>best['supported_count']:best=cand
    return best

def apply_score_relative(matrix:list[dict[str,Any]],constraint:dict[str,Any])->list[dict[str,Any]]:
    target=constraint['target'];cur={(h,a):p for h,a,p in rows(matrix)};subset=sum(cur.get(sc,0.0) for sc in target)
    if subset<=0:raise ValueError('Correct Score quoted subset has no prior support')
    out=[]
    for h,a,p in rows(matrix):
        sc=(h,a)
        q=subset*float(target[sc]) if sc in target else p
        out.append({'home_goals':h,'away_goals':a,'probability':q})
    return renorm(out)

def score_relative_residual(matrix:list[dict[str,Any]],constraint:dict[str,Any])->float:
    target=constraint['target'];cur={(h,a):p for h,a,p in rows(matrix)};subset=sum(cur.get(sc,0.0) for sc in target)
    if subset<=0:return 1.0
    return max(abs(cur.get(sc,0.0)/subset-float(w)) for sc,w in target.items())

def project_unified(prior:list[dict[str,Any]],bundle:dict[str,Any],raw:dict[str,Any],score_power:float=1.0)->dict[str,Any]:
    prior=renorm(prior);one=select_1x2(bundle);totals=total_targets(bundle);bt=btts_target(raw);teams=team_total_constraints(raw,bundle.get('home_team_source',''),bundle.get('away_team_source',''));ahs=ah_constraints(bundle);score=correct_score_relative(raw,prior,score_power)
    if len(totals)<2:return {'status':'INSUFFICIENT_MULTILINE_TOTAL_CONTEXT','total_half_line_count':len(totals)}
    candidate=prior
    for iteration in range(1,MAX_ITER+1):
        candidate=scale_partition(candidate,outcome_group,one,'1x2')
        for line,target in totals:candidate=scale_partition(candidate,total_group(line),target,f'OU{line:g}')
        if bt:candidate=scale_partition(candidate,btts_group,bt,'BTTS')
        for c in teams:candidate=scale_partition(candidate,team_total_group(c['side'],c['line']),c['target'],f"{c['side']}_TT{c['line']:g}")
        for c in ahs:candidate=scale_partition(candidate,ah_group(c['home_line']),c['target'],f"AH{c['home_line']:+g}")
        if score:candidate=apply_score_relative(candidate,score)
        residuals={'1x2':max_residual(marginal(candidate,outcome_group),one)}
        residuals.update({f'OU:{line:g}':max_residual(marginal(candidate,total_group(line)),target) for line,target in totals})
        if bt:residuals['BTTS']=max_residual(marginal(candidate,btts_group),bt)
        for c in teams:residuals[f"TT:{c['side']}:{c['line']:g}"]=max_residual(marginal(candidate,team_total_group(c['side'],c['line'])),c['target'])
        for c in ahs:residuals[f"AH:{c['home_line']:+g}"]=max_residual(marginal(candidate,ah_group(c['home_line'])),c['target'])
        if score:residuals['CorrectScore:relative']=score_relative_residual(candidate,score)
        worst=max(residuals.values()) if residuals else 0.0
        if worst<=1e-8:
            ps=sum(p for _h,_a,p in rows(candidate));return {'status':'UNIFIED_SPECIAL_MARKET_MATRIX_READY','method':'cyclic_minimum_KL_IPF_with_relative_correct_score_constraint','objective':'minimum KL from explicit prior subject to complete-market marginals and conditional quoted-score ratios','iterations':iteration,'converged':True,'constraint_residuals':residuals,'max_constraint_residual':worst,'probability_sum_residual':abs(ps-1.0),'kl_from_prior':kl(candidate,prior),'constraints':{'one_x_two':one,'total_half_lines':{str(l):t for l,t in totals},'btts':bt,'team_totals':teams,'asian_handicap_half_lines':ahs,'correct_score_relative':None if not score else {'offer_id':score['offer_id'],'supported_count':score['supported_count'],'quoted_count':score['quoted_count'],'unsupported':score['unsupported'],'power':score['power'],'probability_mass_claim':'NONE_RELATIVE_ONLY'}},'candidate_matrix':candidate,'total_goals_distribution':total_distribution(candidate),'score_diagnostics':score_diagnostics(candidate),'governance':{'correct_score_exhaustive_probability_claim':False,'unquoted_score_mass_zeroed':False,'missing_tail_fabrication':False,'research_only':True,'formal_probability_change':False,'current_rule_change':False}}
    return {'status':'SPECIAL_MARKET_CONSTRAINT_NONCONVERGENCE_OR_INCONSISTENCY','iterations':MAX_ITER,'constraint_residuals':residuals,'max_constraint_residual':worst,'available_constraints':{'total_half_lines':len(totals),'btts':bool(bt),'team_total_lines':len(teams),'ah_half_lines':len(ahs),'correct_score_relative':bool(score)}}
