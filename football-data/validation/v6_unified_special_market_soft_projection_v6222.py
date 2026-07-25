#!/usr/bin/env python3
"""V6.22.2 soft-KL unified special-market score-matrix projection.

V6.22.1 proved that individually de-vigged Kambi surfaces are not exactly mutually
consistent.  This solver therefore minimizes one explicit reproducible objective instead
of forcing contradictory hard equalities:

  J(P) = KL(P || prior)
       + KL(1X2_target || 1X2(P))
       + mean KL(OU_target_l || OU_l(P))
       + KL(BTTS_target || BTTS(P))                     [when available]
       + mean KL(TeamTotal_target || TeamTotal(P))     [when available]
       + mean KL(AH_half_target || AH_half(P))          [when available]
       + KL(CS_relative || P(score | quoted CS cells)) [when available]

Each MARKET TYPE receives weight 1; multiple lines within a type are averaged, so simply
having more quoted lines does not mechanically give that surface more influence.  There is
no fitted eta and no outcome-driven parameter choice.  Correct Score remains RELATIVE ONLY
because V6.22.0 found no aggregate catch-all tail outcomes.  Zero prior support stays zero.

Research only; never mutates V5.0.1 formal probabilities.
"""
from __future__ import annotations
import math
from typing import Any
import numpy as np
from scipy.optimize import minimize

from v6_multiline_market_matrix_projection_v682 import rows,renorm,select_1x2,total_targets,total_distribution,score_diagnostics,kl
from v6_unified_special_market_projection_v6221 import (
    btts_target,btts_group,team_total_constraints,team_total_group,
    ah_constraints,ah_group,correct_score_relative,
)
EPS=1e-14

def kld(target:np.ndarray,current:np.ndarray)->float:
    t=np.clip(target,EPS,1.0);c=np.clip(current,EPS,1.0)
    return float(np.sum(t*np.log(t/c)))

def binary_from_mask(p:np.ndarray,mask:np.ndarray)->np.ndarray:
    x=float(p[mask].sum());return np.array([x,1.0-x],dtype=float)

def prepare(prior:list[dict[str,Any]],bundle:dict[str,Any],raw:dict[str,Any],score_power:float=1.0)->dict[str,Any]:
    prior=renorm(prior);cells=[(h,a) for h,a,_p in rows(prior)];p0=np.array([p for _h,_a,p in rows(prior)],dtype=float);active=p0>0
    one=select_1x2(bundle);totals=total_targets(bundle);bt=btts_target(raw);teams=team_total_constraints(raw,bundle.get('home_team_source',''),bundle.get('away_team_source',''));ahs=ah_constraints(bundle);score=correct_score_relative(raw,prior,score_power)
    if len(totals)<2:raise ValueError('need at least two ordinary half-goal total lines')
    # Masks and target arrays. Outcome order is explicit in every block.
    one_masks=[np.array([h>a for h,a in cells]),np.array([h==a for h,a in cells]),np.array([h<a for h,a in cells])];one_t=np.array([one['home'],one['draw'],one['away']],dtype=float)
    ou=[]
    for line,t in totals:
        under=np.array([h+a<=math.floor(line) for h,a in cells]);ou.append((line,under,np.array([t['under'],t['over']],dtype=float)))
    bt_block=None
    if bt:
        yes=np.array([h>0 and a>0 for h,a in cells]);bt_block=(yes,np.array([bt['yes'],bt['no']],dtype=float))
    tt=[]
    for c in teams:
        g=team_total_group(c['side'],c['line']);under=np.array([g(h,a)=='under' for h,a in cells]);tt.append((c,under,np.array([c['target']['under'],c['target']['over']],dtype=float)))
    ah=[]
    for c in ahs:
        g=ah_group(c['home_line']);hm=np.array([g(h,a)=='home' for h,a in cells]);ah.append((c,hm,np.array([c['target']['home'],c['target']['away']],dtype=float)))
    cs=None
    if score:
        index={sc:i for i,sc in enumerate(cells)};pairs=[(sc,index[sc],float(w)) for sc,w in score['target'].items() if sc in index]
        if len(pairs)>=3:cs={'offer_id':score['offer_id'],'indices':np.array([i for _s,i,_w in pairs],dtype=int),'target':np.array([w for _s,_i,w in pairs],dtype=float),'scores':[list(s) for s,_i,_w in pairs],'unsupported':score['unsupported'],'power':score['power']}
    return {'prior':prior,'cells':cells,'p0':p0,'active':active,'one_target':one,'one_masks':one_masks,'one_t':one_t,'ou':ou,'btts':bt_block,'team_totals':tt,'ah':ah,'correct_score':cs,'metadata':{'total_half_lines':len(ou),'btts':bt_block is not None,'team_total_lines':len(tt),'ah_half_lines':len(ah),'correct_score_supported_cells':0 if cs is None else len(cs['scores'])}}

def probabilities(theta:np.ndarray,p0:np.ndarray,active:np.ndarray)->np.ndarray:
    z=np.full_like(p0,-np.inf,dtype=float);base=np.log(np.clip(p0[active],EPS,1.0));u=base+theta;u-=np.max(u);e=np.exp(u);z[active]=e/e.sum();z[~active]=0.0;return z

def terms(p:np.ndarray,d:dict[str,Any])->dict[str,float]:
    p0=d['p0'];active=d['active'];out={}
    out['prior']=float(np.sum(p[active]*np.log(np.clip(p[active],EPS,1)/np.clip(p0[active],EPS,1))))
    cur=np.array([float(p[m].sum()) for m in d['one_masks']],dtype=float);out['1x2']=kld(d['one_t'],cur)
    ou_terms=[kld(t,binary_from_mask(p,m)) for _line,m,t in d['ou']];out['ou']=float(np.mean(ou_terms)) if ou_terms else 0.0
    if d['btts'] is not None:
        m,t=d['btts'];out['btts']=kld(t,binary_from_mask(p,m))
    if d['team_totals']:
        out['team_totals']=float(np.mean([kld(t,binary_from_mask(p,m)) for _c,m,t in d['team_totals']]))
    if d['ah']:
        out['ah_half']=float(np.mean([kld(t,binary_from_mask(p,m)) for _c,m,t in d['ah']]))
    cs=d['correct_score']
    if cs is not None:
        mass=float(p[cs['indices']].sum());cur=p[cs['indices']]/max(EPS,mass);out['correct_score_relative']=kld(cs['target'],cur)
    return out

def objective(theta:np.ndarray,d:dict[str,Any])->float:
    p=probabilities(theta,d['p0'],d['active']);t=terms(p,d);return float(sum(t.values()))

def residuals(p:np.ndarray,d:dict[str,Any])->dict[str,float]:
    r={};cur=np.array([float(p[m].sum()) for m in d['one_masks']]);r['1x2']=float(np.max(np.abs(cur-d['one_t'])))
    for line,m,t in d['ou']:r[f'OU:{line:g}']=float(np.max(np.abs(binary_from_mask(p,m)-t)))
    if d['btts'] is not None:
        m,t=d['btts'];r['BTTS']=float(np.max(np.abs(binary_from_mask(p,m)-t)))
    for c,m,t in d['team_totals']:r[f"TT:{c['side']}:{c['line']:g}"]=float(np.max(np.abs(binary_from_mask(p,m)-t)))
    for c,m,t in d['ah']:r[f"AH:{c['home_line']:+g}"]=float(np.max(np.abs(binary_from_mask(p,m)-t)))
    cs=d['correct_score']
    if cs is not None:
        mass=float(p[cs['indices']].sum());cur=p[cs['indices']]/max(EPS,mass);r['CorrectScore:relative']=float(np.max(np.abs(cur-cs['target'])))
    return r

def project_soft(prior:list[dict[str,Any]],bundle:dict[str,Any],raw:dict[str,Any],score_power:float=1.0)->dict[str,Any]:
    d=prepare(prior,bundle,raw,score_power);n=int(d['active'].sum());x0=np.zeros(n,dtype=float)
    opt=minimize(lambda x:objective(x,d),x0,method='L-BFGS-B',options={'maxiter':1200,'ftol':1e-12,'gtol':1e-9,'maxls':50})
    p=probabilities(np.asarray(opt.x,dtype=float),d['p0'],d['active']);candidate=[{'home_goals':h,'away_goals':a,'probability':float(q)} for (h,a),q in zip(d['cells'],p)];tm=terms(p,d);rs=residuals(p,d);ps=float(p.sum())
    return {'status':'UNIFIED_SPECIAL_MARKET_SOFT_MATRIX_READY' if bool(opt.success) and abs(ps-1.0)<=1e-10 else 'SOFT_PROJECTION_OPTIMIZATION_FAILED','method':'penalized_KL_single_joint_matrix_equal_surface_type_weights','objective':'KL(P||prior)+KL_1x2+mean_KL_OU+KL_BTTS+mean_KL_team_totals+mean_KL_half_AH+KL_relative_correct_score','optimizer':'scipy_L-BFGS-B','optimizer_success':bool(opt.success),'optimizer_status':int(opt.status),'optimizer_message':str(opt.message),'iterations':int(getattr(opt,'nit',0)),'function_evaluations':int(getattr(opt,'nfev',0)),'objective_value':float(opt.fun),'objective_terms':tm,'constraint_residuals':rs,'max_constraint_residual':max(rs.values()) if rs else 0.0,'probability_sum_residual':abs(ps-1.0),'kl_from_prior':kl(candidate,d['prior']),'input_surface_counts':d['metadata'],'candidate_matrix':candidate,'total_goals_distribution':total_distribution(candidate),'score_diagnostics':score_diagnostics(candidate),'correct_score_semantics':None if d['correct_score'] is None else {'quoted_supported_scores':d['correct_score']['scores'],'unsupported_quoted_scores':d['correct_score']['unsupported'],'power':d['correct_score']['power'],'absolute_probability_claim':False,'relative_conditional_only':True},'governance':{'surface_type_weights':'EQUAL_ONE_EACH_LINES_AVERAGED_WITHIN_TYPE','outcome_tuned_hyperparameters':False,'correct_score_exhaustive_probability_claim':False,'unquoted_score_mass_zeroed':False,'missing_tail_fabrication':False,'research_only':True,'formal_probability_change':False,'current_rule_change':False}}
