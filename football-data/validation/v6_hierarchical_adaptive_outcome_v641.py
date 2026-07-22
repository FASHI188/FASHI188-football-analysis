#!/usr/bin/env python3
"""V6.4.1 hierarchical adaptive 1X2 challenger.

Addresses four diagnosed failure modes without changing the frozen V6.0.1 backbone:
1. draw under/over-classification -> dedicated residual draw calibrator;
2. away-side instability -> dedicated home-v-away conditional calibrator;
3. league heterogeneity -> partially pooled competition intercepts (ridge-shrunk);
4. season drift -> prequential competition offsets using only earlier matches in that season.

Selection discipline:
- source probabilities are the corrected V6.2.5-r4 pooled V6.0.1 cache;
- within the older 850, each competition is split chronologically 60/40;
- hyperparameters are selected only on the older-season validation tail;
- selected parameters are refit on all older 850;
- newer 850 is reported as development evidence only, never used for selection;
- no CURRENT/formal/runtime mutation.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import v6_direct_outcome_mvp_v600 as base
from platform_core import PlatformError

CACHE = ROOT / "manifests" / "v6_sampled_17domain_pooled_scored_cache_v625_r4.json"
OUT = ROOT / "manifests" / "v6_hierarchical_adaptive_outcome_v641_status.json"
CLASSES = ("home", "draw", "away")
EPS = 1e-12
CORE_L2_GRID = (1.0, 10.0, 100.0)
DOMAIN_L2_GRID = (10.0, 50.0, 200.0)
ONLINE_L2_GRID = (5.0, 20.0, 80.0)
DRAW_RATIO_GRID = (0.75, 0.80, 0.85, 0.90, 0.95, 1.00)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _clip(x: float, lo: float = 1e-6, hi: float = 1.0 - 1e-6) -> float:
    return min(hi, max(lo, x))


def _logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-min(700.0, x))
        return 1.0 / (1.0 + z)
    z = math.exp(max(-700.0, x))
    return z / (1.0 + z)


def _entropy(q: dict[str, float]) -> float:
    return -sum(float(q[k]) * math.log(max(EPS, float(q[k]))) for k in CLASSES)


def _domain_names(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(r["competition_id"]) for r in rows})


def _draw_features(r: dict[str, Any], domains: list[str]) -> list[float]:
    q, f = r["q"], r["formal"]
    side_gap = abs(math.log(max(EPS, q["home"])) - math.log(max(EPS, q["away"])))
    core = [
        1.0,
        _logit(float(q["draw"])),
        _logit(float(f["draw"])),
        side_gap,
        _entropy(q),
        float(q["draw"]) - float(f["draw"]),
    ]
    cid = str(r["competition_id"])
    return core + [1.0 if cid == d else 0.0 for d in domains]


def _side_features(r: dict[str, Any], domains: list[str]) -> list[float]:
    q, f = r["q"], r["formal"]
    qh = float(q["home"]) / max(EPS, float(q["home"]) + float(q["away"]))
    fh = float(f["home"]) / max(EPS, float(f["home"]) + float(f["away"]))
    core = [
        1.0,
        _logit(qh),
        _logit(fh),
        _logit(qh) - _logit(fh),
        float(r["confidence"]),
        float(q["draw"]),
    ]
    cid = str(r["competition_id"])
    return core + [1.0 if cid == d else 0.0 for d in domains]


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return base._solve(matrix, vector)


def _fit_penalized(xs: list[list[float]], ys: list[int], penalties: list[float]) -> dict[str, Any]:
    if not xs or len(xs) != len(ys):
        raise PlatformError("invalid logistic training rows")
    d = len(xs[0])
    if any(len(x) != d for x in xs) or len(penalties) != d:
        raise PlatformError("logistic dimension mismatch")
    theta = [0.0] * d
    prevalence = sum(ys) / len(ys)
    theta[0] = _logit(min(.9999, max(.0001, prevalence)))
    objective = None
    grad_norm = None
    for iteration in range(1, 61):
        grad = [0.0] * d
        hess = [[0.0] * d for _ in range(d)]
        obj = 0.5 * sum(penalties[j] * theta[j] * theta[j] for j in range(d))
        for x, y in zip(xs, ys):
            eta = sum(theta[j] * x[j] for j in range(d))
            p = _sigmoid(eta)
            obj -= y * math.log(max(EPS, p)) + (1-y) * math.log(max(EPS, 1-p))
            w = p * (1-p)
            for j in range(d):
                grad[j] += (p-y) * x[j]
                for k in range(d):
                    hess[j][k] += w * x[j] * x[k]
        for j in range(d):
            grad[j] += penalties[j] * theta[j]
            hess[j][j] += penalties[j]
        hess[0][0] += 1e-8
        grad_norm = max(abs(v) for v in grad)
        objective = obj
        if grad_norm < 1e-7:
            break
        step = _solve(hess, grad)
        scale = 1.0
        accepted = False
        for _ in range(24):
            cand = [theta[j] - scale * step[j] for j in range(d)]
            cand_obj = 0.5 * sum(penalties[j] * cand[j] * cand[j] for j in range(d))
            for x, y in zip(xs, ys):
                p = _sigmoid(sum(cand[j]*x[j] for j in range(d)))
                cand_obj -= y * math.log(max(EPS,p)) + (1-y)*math.log(max(EPS,1-p))
            if math.isfinite(cand_obj) and cand_obj <= obj + 1e-10:
                theta, objective, accepted = cand, cand_obj, True
                break
            scale *= .5
        if not accepted:
            raise PlatformError("hierarchical logistic line search failed")
        if max(abs(scale*v) for v in step) < 1e-8:
            break
    return {"theta": theta, "objective": objective, "max_abs_gradient": grad_norm, "iterations": iteration, "training_count": len(xs)}


def _predict(model: dict[str, Any], x: list[float]) -> float:
    return _sigmoid(sum(float(a)*float(b) for a,b in zip(model["theta"], x)))


def _fit_models(rows: list[dict[str, Any]], domains: list[str], core_l2: float, domain_l2: float) -> dict[str, Any]:
    draw_x = [_draw_features(r, domains) for r in rows]
    draw_y = [1 if r["actual_result"] == "draw" else 0 for r in rows]
    core_dim = 6
    penalties = [0.0] + [core_l2]*(core_dim-1) + [domain_l2]*len(domains)
    draw_model = _fit_penalized(draw_x, draw_y, penalties)

    decisive = [r for r in rows if r["actual_result"] != "draw"]
    side_x = [_side_features(r, domains) for r in decisive]
    side_y = [1 if r["actual_result"] == "home" else 0 for r in decisive]
    side_model = _fit_penalized(side_x, side_y, penalties)
    return {"draw_model": draw_model, "side_model": side_model, "core_l2": core_l2, "domain_l2": domain_l2}


def _static_probs(r: dict[str, Any], models: dict[str, Any], domains: list[str]) -> tuple[float,float]:
    return _clip(_predict(models["draw_model"], _draw_features(r,domains))), _clip(_predict(models["side_model"], _side_features(r,domains)))


def _offset(history: list[tuple[float,int]], l2: float) -> float:
    if not history:
        return 0.0
    b = 0.0
    for _ in range(12):
        g = l2*b
        h = l2
        for p,y in history:
            z = _sigmoid(_logit(p)+b)
            g += z-y
            h += z*(1-z)
        if h <= 1e-12:
            break
        step = g/h
        b -= step
        if abs(step) < 1e-8:
            break
    return max(-1.0, min(1.0, b))


def _probability(r: dict[str, Any], models: dict[str, Any], domains: list[str], online_l2: float, draw_hist: dict[str,list[tuple[float,int]]], side_hist: dict[str,list[tuple[float,int]]]) -> tuple[dict[str,float],tuple[float,float]]:
    cid = str(r["competition_id"])
    pd0, ph0 = _static_probs(r,models,domains)
    pd = _sigmoid(_logit(pd0)+_offset(draw_hist[cid],online_l2))
    ph = _sigmoid(_logit(ph0)+_offset(side_hist[cid],online_l2))
    rem = 1-pd
    q = {"home": rem*ph, "draw": pd, "away": rem*(1-ph)}
    return q,(pd0,ph0)


def _pick(q: dict[str,float], draw_ratio: float) -> str:
    side = "home" if q["home"] >= q["away"] else "away"
    return "draw" if q["draw"] >= draw_ratio*q[side] else side


def _score_prequential(rows: list[dict[str,Any]], models: dict[str,Any], domains: list[str], online_l2: float, draw_ratio: float, seed_history: list[dict[str,Any]] | None = None) -> dict[str,Any]:
    draw_hist: dict[str,list[tuple[float,int]]] = defaultdict(list)
    side_hist: dict[str,list[tuple[float,int]]] = defaultdict(list)
    if seed_history:
        for r in sorted(seed_history,key=lambda x:(str(x["competition_id"]),str(x["date"]),str(x["identity"]))):
            cid=str(r["competition_id"])
            pd0,ph0=_static_probs(r,models,domains)
            draw_hist[cid].append((pd0,1 if r["actual_result"]=="draw" else 0))
            if r["actual_result"]!="draw": side_hist[cid].append((ph0,1 if r["actual_result"]=="home" else 0))

    count=hits=0; brier=rps=ll=0.0
    pred=Counter(); actual=Counter(); matrix={p:{t:0 for t in CLASSES} for p in CLASSES}
    by_domain: dict[str,Counter]=defaultdict(Counter)
    details=[]
    for r in sorted(rows,key=lambda x:(str(x["date"]),str(x["competition_id"]),str(x["identity"]))):
        cid=str(r["competition_id"])
        q,(pd0,ph0)=_probability(r,models,domains,online_l2,draw_hist,side_hist)
        p=_pick(q,draw_ratio); t=str(r["actual_result"]); hit=int(p==t)
        count+=1; hits+=hit; pred[p]+=1; actual[t]+=1; matrix[p][t]+=1
        brier += sum((q[k]-(1.0 if t==k else 0.0))**2 for k in CLASSES)
        tv={"home":(1,0,0),"draw":(0,1,0),"away":(0,0,1)}[t]
        c1=q["home"]-tv[0]; c2=q["home"]+q["draw"]-tv[0]-tv[1]
        rps += (c1*c1+c2*c2)/2
        ll -= math.log(max(EPS,q[t]))
        by_domain[cid]["count"]+=1; by_domain[cid]["hits"]+=hit
        details.append({"identity":r["identity"],"competition_id":cid,"pick":p,"truth":t,"hit":hit,"q":q})
        draw_hist[cid].append((pd0,1 if t=="draw" else 0))
        if t!="draw": side_hist[cid].append((ph0,1 if t=="home" else 0))
    draw_actual=actual["draw"]; draw_hits=matrix["draw"]["draw"]; draw_pred=sum(matrix["draw"].values())
    return {
        "count":count,"hits":hits,"accuracy":hits/count if count else None,
        "mean_brier":brier/count if count else None,"mean_rps":rps/count if count else None,"mean_log_loss":ll/count if count else None,
        "predicted_direction_counts":dict(pred),"actual_direction_counts":dict(actual),
        "draw_recall":draw_hits/draw_actual if draw_actual else None,"draw_precision":draw_hits/draw_pred if draw_pred else None,
        "confusion":matrix,
        "by_domain":{cid:{"count":c["count"],"hits":c["hits"],"accuracy":c["hits"]/c["count"] if c["count"] else None} for cid,c in sorted(by_domain.items())},
        "details":details,
    }


def _baseline(rows: list[dict[str,Any]]) -> dict[str,Any]:
    count=hits=0;brier=rps=ll=0.0;pred=Counter();actual=Counter();matrix={p:{t:0 for t in CLASSES} for p in CLASSES}
    for r in rows:
        q=r["q"];p=str(r["pick"]);t=str(r["actual_result"]);hit=int(p==t)
        count+=1;hits+=hit;pred[p]+=1;actual[t]+=1;matrix[p][t]+=1
        brier+=sum((float(q[k])-(1 if t==k else 0))**2 for k in CLASSES)
        tv={"home":(1,0,0),"draw":(0,1,0),"away":(0,0,1)}[t]
        c1=float(q["home"])-tv[0];c2=float(q["home"])+float(q["draw"])-tv[0]-tv[1]
        rps+=(c1*c1+c2*c2)/2;ll-=math.log(max(EPS,float(q[t])))
    da=actual["draw"];dh=matrix["draw"]["draw"];dp=sum(matrix["draw"].values())
    return {"count":count,"hits":hits,"accuracy":hits/count,"mean_brier":brier/count,"mean_rps":rps/count,"mean_log_loss":ll/count,"predicted_direction_counts":dict(pred),"actual_direction_counts":dict(actual),"draw_recall":dh/da if da else None,"draw_precision":dh/dp if dp else None,"confusion":matrix}


def _split_older(rows: list[dict[str,Any]]) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    by=defaultdict(list)
    for r in rows: by[str(r["competition_id"])].append(r)
    train=[];valid=[]
    for cid,items in sorted(by.items()):
        items=sorted(items,key=lambda x:(str(x["date"]),str(x["identity"])))
        cut=max(1,int(round(.60*len(items))))
        train.extend(items[:cut]);valid.extend(items[cut:])
    return train,valid


def _strip(m: dict[str,Any]) -> dict[str,Any]:
    x=dict(m);x.pop("details",None);return x


def main() -> int:
    cache=_load(CACHE);rows=list(cache["rows"]);older=[r for r in rows if r["role"]=="older"];newer=[r for r in rows if r["role"]=="newer"]
    domains=_domain_names(rows);train,valid=_split_older(older)
    baseline_valid=_baseline(valid);baseline_newer=_baseline(newer)
    candidates=[]
    for core_l2 in CORE_L2_GRID:
        for domain_l2 in DOMAIN_L2_GRID:
            models=_fit_models(train,domains,core_l2,domain_l2)
            for online_l2 in ONLINE_L2_GRID:
                for draw_ratio in DRAW_RATIO_GRID:
                    m=_score_prequential(valid,models,domains,online_l2,draw_ratio,seed_history=train)
                    proper_nonworse=(m["mean_brier"]<=baseline_valid["mean_brier"]+1e-12 and m["mean_rps"]<=baseline_valid["mean_rps"]+1e-12 and m["mean_log_loss"]<=baseline_valid["mean_log_loss"]+1e-12)
                    candidates.append({"core_l2":core_l2,"domain_l2":domain_l2,"online_l2":online_l2,"draw_ratio":draw_ratio,"proper_scores_nonworse":proper_nonworse,"validation":_strip(m)})
    eligible=[c for c in candidates if c["proper_scores_nonworse"]]
    pool=eligible if eligible else candidates
    pool.sort(key=lambda c:(-float(c["validation"]["accuracy"]),-float(c["validation"]["draw_recall"] or 0),float(c["validation"]["mean_log_loss"])))
    selected=pool[0]
    refit=_fit_models(older,domains,float(selected["core_l2"]),float(selected["domain_l2"]))
    newer_metric=_score_prequential(newer,refit,domains,float(selected["online_l2"]),float(selected["draw_ratio"]),seed_history=None)
    proper_newer={
        "brier_nonworse":newer_metric["mean_brier"]<=baseline_newer["mean_brier"]+1e-12,
        "rps_nonworse":newer_metric["mean_rps"]<=baseline_newer["mean_rps"]+1e-12,
        "log_loss_nonworse":newer_metric["mean_log_loss"]<=baseline_newer["mean_log_loss"]+1e-12,
    }
    payload={
        "schema_version":"V6.4.1-hierarchical-adaptive-outcome-r1",
        "generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status":"PASS",
        "design":{
            "source_cache":CACHE.name,"domains":len(domains),"older_train_count":len(train),"older_validation_count":len(valid),"older_refit_count":len(older),"newer_development_count":len(newer),
            "architecture":["global residual draw expert","global residual decisive-side expert","ridge-shrunk competition intercepts","prequential current-season competition offsets"],
            "newer_used_for_selection":False,
        },
        "baseline_validation":baseline_valid,
        "selected_candidate":selected,
        "baseline_newer":baseline_newer,
        "challenger_newer":_strip(newer_metric),
        "newer_accuracy_gain_pp":100*(float(newer_metric["accuracy"])-float(baseline_newer["accuracy"])),
        "newer_draw_recall_gain_pp":100*(float(newer_metric["draw_recall"] or 0)-float(baseline_newer["draw_recall"] or 0)),
        "newer_proper_score_guard":proper_newer,
        "research_gate_passed":bool(float(newer_metric["accuracy"])>float(baseline_newer["accuracy"]) and all(proper_newer.values())),
        "governance":{
            "development_only":True,"newer_850_not_pristine":True,"automatic_promotion":False,"fresh_forward_required":True,
            "formal_weight_change":False,"runtime_probability_change":False,"current_rule_change":False,"v610_v613_untouched":True,
        },
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
