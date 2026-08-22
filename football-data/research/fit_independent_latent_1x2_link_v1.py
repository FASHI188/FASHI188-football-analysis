#!/usr/bin/env python3
"""Deterministic train-only fitter for the frozen independent Gaussian ordered 1X2 link."""
from __future__ import annotations
import math
from typing import Any, Iterable
from independent_latent_1x2_v1 import Independent1X2LinkConfig, probabilities_from_latent_comparison
class LinkFitError(ValueError): pass
ALLOWED_KEYS=frozenset({"latent_margin","latent_margin_variance","outcome"}); OUTCOMES=frozenset({"HOME","DRAW","AWAY"})
BOUNDS={"home_advantage":(-2.0,2.0),"draw_boundary":(0.02,3.0),"match_noise_variance":(0.05,8.0)}

def _finite(v,field):
    try: x=float(v)
    except (TypeError,ValueError) as e: raise LinkFitError(f"{field} must be numeric") from e
    if not math.isfinite(x): raise LinkFitError(f"{field} must be finite")
    return x

def _rows(rows:Iterable[dict[str,Any]]):
    if isinstance(rows,(str,bytes,dict)) or not hasattr(rows,"__iter__"): raise LinkFitError("training_rows must be iterable of dict")
    out=[]
    for i,r in enumerate(rows):
        if not isinstance(r,dict): raise LinkFitError(f"row {i} must be dict")
        if set(r)!=ALLOWED_KEYS: raise LinkFitError(f"row {i} keys mismatch")
        m=_finite(r["latent_margin"],"latent_margin"); v=_finite(r["latent_margin_variance"],"latent_margin_variance")
        if v<=0: raise LinkFitError("latent_margin_variance must be > 0")
        o=r["outcome"]
        if o not in OUTCOMES: raise LinkFitError("outcome must be HOME/DRAW/AWAY exactly")
        out.append((m,v,o))
    if len(out)<12: raise LinkFitError("at least 12 train rows required")
    return out

def _loss(rows,p):
    cfg=Independent1X2LinkConfig(*p)
    total=0.0
    key={"HOME":"home","DRAW":"draw","AWAY":"away"}
    for m,v,o in rows:
        q=probabilities_from_latent_comparison({"latent_margin":m,"latent_margin_variance":v,"interpretation":"synthetic_latent_comparison_v1"},config=cfg)
        total-=math.log(max(q[key[o]],1e-15))
    return total/len(rows)

def _clip(p):
    return tuple(min(BOUNDS[k][1],max(BOUNDS[k][0],x)) for k,x in zip(BOUNDS,p))

def fit_independent_latent_1x2_link(training_rows:Iterable[dict[str,Any]])->dict[str,Any]:
    rows=_rows(training_rows)
    starts=[(0.0,0.35,1.0),(-0.25,0.2,0.5),(0.25,0.6,1.5),(0.0,1.0,3.0)]
    best=None
    for start in starts:
        p=_clip(start); val=_loss(rows,p); steps=[0.5,0.25,0.5]
        for _ in range(28):
            changed=False
            for j in range(3):
                candidates=[p]
                for s in (-steps[j],steps[j]):
                    c=list(p); c[j]+=s; candidates.append(_clip(tuple(c)))
                scored=sorted((_loss(rows,c),c) for c in candidates)
                if scored[0][0] < val-1e-12: val,p=scored[0]; changed=True
            if not changed: steps=[s/2 for s in steps]
            if max(steps)<1e-5: break
        item=(val,p)
        if best is None or item<best: best=item
    val,p=best
    return {"schema":"football3_independent_latent_1x2_link_fit_v1","objective":"train_logloss","train_logloss":val,"home_advantage":p[0],"draw_boundary":p[1],"match_noise_variance":p[2],"train_rows_consumed":len(rows),"test_rows_consumed":0,"market_rows_consumed":0,"deterministic":True,"research_only":True,"formal_weight":0.0}
