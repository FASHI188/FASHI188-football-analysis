#!/usr/bin/env python3
"""Fast exact-dual runner for V6.27.2.

With 1X2 and total marginals fixed, the KL solution with one additional AH expectation constraint has
form q(h,a) proportional p(h,a)*exp(alpha_result + beta_total + lambda*g_AH). For any fixed lambda,
the existing V6.26 IPF solves alpha/beta exactly. Therefore only the scalar lambda remains. This
runner brackets and bisects lambda, replacing the much slower three-set alternating projection while
preserving the same constraints and research contract.
"""
from __future__ import annotations
import math,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import three_stage_core_v6260 as core
import validate_three_stage_ah_netgoal_random100_v6272 as ahm
import run_three_stage_ah_netgoal_random100_v6272 as runner


def tilted(prior:list[dict[str,Any]],line:float,lam:float):
    logs=[]
    for c in prior:
        p=max(ahm.EPS,float(c['probability']));g=ahm.settlement_score(int(c['home_goals']),int(c['away_goals']),line);logs.append(math.log(p)+lam*g)
    m=max(logs);ws=[math.exp(x-m) for x in logs];z=sum(ws)
    return [{'home_goals':int(c['home_goals']),'away_goals':int(c['away_goals']),'probability':w/z} for c,w in zip(prior,ws)]


def reconcile_ah_dual(prior,target_one,target_total,line,home_share):
    target=2.0*float(home_share)-1.0
    cache={}
    def evaluate(lam):
        key=round(float(lam),14)
        if key in cache:return cache[key]
        tp=tilted(prior,float(line),float(lam));q,a=core.reconcile(tp,target_one,target_total,tolerance=5e-11,max_iter=1200)
        if q is None or not a.get('converged'):raise RuntimeError('partition IPF failed inside AH dual')
        moment=ahm.settlement_moment(q,float(line));cache[key]=(moment,q,a);return cache[key]
    m0,q0,a0=evaluate(0.0)
    if abs(m0-target)<=ahm.TOL:
        return q0,{'converged':True,'iterations':1,'max_residual':max(float(a0.get('max_residual') or 0.0),abs(m0-target)),'target_signed_settlement':target,'final_signed_settlement':m0,'solver':'SCALAR_DUAL_IPF'}
    lo,hi=-1.0,1.0;mlo,_,_=evaluate(lo);mhi,_,_=evaluate(hi)
    for _ in range(24):
        if mlo<=target<=mhi:break
        if target<mlo:hi=lo;mhi=mlo;lo*=2.0;mlo,_,_=evaluate(lo)
        else:lo=hi;mlo=mhi;hi*=2.0;mhi,_,_=evaluate(hi)
    if not (mlo<=target<=mhi):raise RuntimeError('AH moment infeasible under locked 1X2+total marginals')
    best=None
    for it in range(1,61):
        mid=(lo+hi)/2.0;mm,q,a=evaluate(mid);best=(mm,q,a,it)
        if abs(mm-target)<=ahm.TOL:break
        if mm<target:lo=mid
        else:hi=mid
    mm,q,a,it=best
    one=core.one_x_two_vector(q);tot=core.total_goals_vector(q)
    r=max(float(a.get('max_residual') or 0.0),max(abs(x-y) for x,y in zip(one,[float(v)/sum(target_one) for v in target_one])),max(abs(x-y) for x,y in zip(tot,[float(v)/sum(target_total) for v in target_total])),abs(mm-target))
    return q,{'converged':r<=max(ahm.TOL,5e-9),'iterations':it,'max_residual':r,'target_signed_settlement':target,'final_signed_settlement':mm,'solver':'SCALAR_DUAL_IPF','dual_evaluations':len(cache)}

ahm.reconcile_ah=reconcile_ah_dual

if __name__=='__main__':raise SystemExit(runner.main())
