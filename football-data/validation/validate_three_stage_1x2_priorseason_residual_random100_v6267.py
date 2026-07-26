#!/usr/bin/env python3
"""V6.26.7 random100: prior-season-frozen 1X2 market-residual fusion.

For each competition/target season, fit exactly one scalar alpha using eligible rows from strictly
EARLIER seasons only. The head is
    q_i(alpha) proportional to p_market_i^(1-alpha) * p_formal_i^alpha, alpha in [0,1].
Alpha minimizes prior-season multiclass log loss. The objective is convex; alpha is found by
bisection on the analytic derivative, not by test-set grid selection. If no earlier-season training
rows exist, alpha=0.5 is the fixed ex-ante fallback.

The target-season alpha is frozen before that season starts. Current-season results never update it.
Total-goals head is unchanged and exact score is reconciled last. Evaluation uses the exact same
fixed-seed random100 order as V6.26.4. Historical odds lack original timestamps: research only.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import three_stage_core_v6260 as core  # noqa: E402
import validate_architecture_order_v6190 as arch  # noqa: E402
import validate_decoupled_1x2_total_fusion_v6191 as dec  # noqa: E402
import validate_market_ou_kl_projection_v6162 as ou  # noqa: E402
import validate_three_stage_random100_v6264 as r100  # noqa: E402
from football_v460_engine import load_config, predict_from_history  # noqa: E402
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import derive_score_marginals  # noqa: E402

OUT = ROOT / "manifests" / "v6_three_stage_1x2_priorseason_residual_random100_v6267_status.json"
EPS = 1e-15


def avg(rows: list[dict[str, Any]], key: str) -> float | None:
    return sum(float(r[key]) for r in rows) / len(rows) if rows else None


def logpool(formal: list[float], market: list[float], alpha: float) -> list[float]:
    logs = [
        (1.0-alpha)*math.log(max(EPS, float(pm))) + alpha*math.log(max(EPS, float(pf)))
        for pf, pm in zip(formal, market)
    ]
    m = max(logs)
    raw = [math.exp(x-m) for x in logs]
    z = sum(raw)
    return [x/z for x in raw]


def derivative(training: list[tuple[list[float], list[float], int]], alpha: float) -> float:
    if not training:
        return 0.0
    total = 0.0
    for formal, market, actual in training:
        q = logpool(formal, market, alpha)
        ratios = [math.log(max(EPS, pf)) - math.log(max(EPS, pm)) for pf, pm in zip(formal, market)]
        total += sum(q[i]*ratios[i] for i in range(3)) - ratios[actual]
    return total / len(training)


def fit_alpha(training: list[tuple[list[float], list[float], int]]) -> tuple[float, dict[str, Any]]:
    if not training:
        return 0.5, {"method": "fixed_fallback_no_prior_rows", "training_count": 0}
    d0 = derivative(training, 0.0)
    d1 = derivative(training, 1.0)
    if d0 >= 0.0:
        alpha = 0.0
        status = "boundary_market"
    elif d1 <= 0.0:
        alpha = 1.0
        status = "boundary_formal"
    else:
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo+hi)/2.0
            dm = derivative(training, mid)
            if dm > 0.0:
                hi = mid
            else:
                lo = mid
        alpha = (lo+hi)/2.0
        status = "interior_bisection"
    loss = 0.0
    for formal, market, actual in training:
        q = logpool(formal, market, alpha)
        loss += -math.log(max(EPS, q[actual]))
    return alpha, {
        "method": status,
        "training_count": len(training),
        "derivative_at_0": d0,
        "derivative_at_1": d1,
        "training_mean_logloss_at_alpha": loss/len(training),
    }


def main() -> int:
    cfg = load_config()
    candidates, packs = r100._enumerate_candidates(cfg)
    order = list(candidates)
    random.Random(r100.SEED).shuffle(order)
    frozen_order = order[: min(len(order), r100.ATTEMPT_POOL)]
    wanted = set(frozen_order)
    rank = {key:i for i,key in enumerate(frozen_order)}

    produced: dict[tuple[str,str,str,str,str],dict[str,Any]] = {}
    failures = Counter()
    alpha_audit: dict[str,Any] = {}
    max_one=max_total=max_mass=0.0

    for cid in dec.COMPS:
        prior_season_training: list[tuple[list[float],list[float],int]] = []
        alpha_audit[cid] = {}

        for season in dec.SEASONS:
            pack = packs.get((season,cid))
            if not pack:
                continue
            alpha, fit = fit_alpha(prior_season_training)
            alpha_audit[cid][season] = {"alpha_formal_residual": alpha, **fit}
            bydate = defaultdict(list)
            for m in pack["matches"]:
                bydate[m.date].append(m)
            hist=[]
            current_season_rows: list[tuple[list[float],list[float],int]] = []

            for dt in sorted(bydate):
                day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
                day_training=[]
                for m in day:
                    key=(season,cid,m.date.isoformat(),m.home_team,m.away_team)
                    if key not in pack["candidate_ids"]:
                        continue
                    mk=pack["lookup"].get((m.date.isoformat(),m.home_team,m.away_team))
                    try:
                        pred=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,
                                                  selected_parameters=pack["params"],use_team_effects=True)
                    except Exception:
                        pred=None
                    if not pred:
                        failures["formal_prior"]+=1
                        continue
                    prior=temperature_scale_matrix(pred["probabilities"]["score_matrix"],pack["temperature"])
                    formal_one=arch.one_vec(prior)
                    market_one=[float(x) for x in mk["one_x_two"]]
                    actual=arch.result_index(m.home_goals,m.away_goals)
                    day_training.append((formal_one,market_one,actual))

                    if key not in wanted:
                        continue
                    fused_one=logpool(formal_one,market_one,alpha)
                    marg=derive_score_marginals(prior)
                    total_dict=ou.project(marg["total_goals"],float(mk["p_over25"]))
                    if total_dict is None:
                        failures["total_projection"]+=1
                        continue
                    target_total=[float(total_dict[k]) for k in ou.TOTAL_KEYS]
                    try:
                        matrix,audit=core.reconcile(prior,fused_one,target_total)
                    except Exception:
                        matrix,audit=None,{"converged":False}
                    if matrix is None or not audit.get("converged"):
                        failures["reconciliation"]+=1
                        continue
                    final_one=core.one_x_two_vector(matrix)
                    formal_total=arch.total_vec(prior);new_total=core.total_goals_vector(matrix)
                    ti=min(7,m.home_goals+m.away_goals)
                    max_one=max(max_one,max(abs(a-b) for a,b in zip(final_one,fused_one)))
                    max_total=max(max_total,max(abs(a-b) for a,b in zip(new_total,target_total)))
                    max_mass=max(max_mass,abs(sum(float(c["probability"]) for c in matrix)-1.0))
                    produced[key]={
                        "date":m.date.isoformat(),"competition_id":cid,"season":season,
                        "home":m.home_team,"away":m.away_team,"actual_score":[m.home_goals,m.away_goals],
                        "alpha_formal_residual":alpha,"alpha_training_count":fit["training_count"],
                        "formal_1x2_top1":int(max(range(3),key=lambda i:formal_one[i])==actual),
                        "market_1x2_top1":int(max(range(3),key=lambda i:market_one[i])==actual),
                        "residual_1x2_top1":int(max(range(3),key=lambda i:final_one[i])==actual),
                        "formal_1x2_brier":arch.brier3(formal_one,actual),
                        "market_1x2_brier":arch.brier3(market_one,actual),
                        "residual_1x2_brier":arch.brier3(final_one,actual),
                        "formal_1x2_logloss":arch.logloss3(formal_one,actual),
                        "market_1x2_logloss":arch.logloss3(market_one,actual),
                        "residual_1x2_logloss":arch.logloss3(final_one,actual),
                        "formal_total_top1":int(max(range(8),key=lambda i:formal_total[i])==ti),
                        "residual_total_top1":int(max(range(8),key=lambda i:new_total[i])==ti),
                        "formal_total_rps":arch.rps8(formal_total,ti),"residual_total_rps":arch.rps8(new_total,ti),
                        "formal_score_top1":arch.score_topk(prior,1,m.home_goals,m.away_goals),
                        "residual_score_top1":arch.score_topk(matrix,1,m.home_goals,m.away_goals),
                        "formal_score_top3":arch.score_topk(prior,3,m.home_goals,m.away_goals),
                        "residual_score_top3":arch.score_topk(matrix,3,m.home_goals,m.away_goals),
                    }
                # Outcomes from this day are recorded only after all same-day predictions.
                current_season_rows.extend(day_training)
                for m in day: hist.append(m)

            # Target-season rows become training data only for future seasons.
            prior_season_training.extend(current_season_rows)

    rows=sorted(produced.values(),key=lambda r:rank[(r["season"],r["competition_id"],r["date"],r["home"],r["away"])])[:r100.TARGET]
    summary={"count":len(rows)}
    for prefix in ("formal","market","residual"):
        summary[f"{prefix}_1x2_top1"]=avg(rows,f"{prefix}_1x2_top1")
        summary[f"{prefix}_1x2_brier"]=avg(rows,f"{prefix}_1x2_brier")
        summary[f"{prefix}_1x2_logloss"]=avg(rows,f"{prefix}_1x2_logloss")
    for prefix in ("formal","residual"):
        summary[f"{prefix}_total_top1"]=avg(rows,f"{prefix}_total_top1")
        summary[f"{prefix}_total_rps"]=avg(rows,f"{prefix}_total_rps")
        summary[f"{prefix}_score_top1"]=avg(rows,f"{prefix}_score_top1")
        summary[f"{prefix}_score_top3"]=avg(rows,f"{prefix}_score_top3")
    summary["residual_vs_formal_1x2_pp"]=((summary["residual_1x2_top1"] or 0)-(summary["formal_1x2_top1"] or 0))*100
    summary["residual_vs_market_1x2_pp"]=((summary["residual_1x2_top1"] or 0)-(summary["market_1x2_top1"] or 0))*100
    summary["residual_vs_formal_total_pp"]=((summary["residual_total_top1"] or 0)-(summary["formal_total_top1"] or 0))*100
    summary["residual_vs_formal_score1_pp"]=((summary["residual_score_top1"] or 0)-(summary["formal_score_top1"] or 0))*100
    summary["mean_alpha_formal_residual_in_sample"]=avg(rows,"alpha_formal_residual")

    report={
        "schema_version":"V6.26.7-priorseason-frozen-1x2-market-residual-random100-r1",
        "generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status":"PASS" if len(rows)==r100.TARGET else "PARTIAL",
        "formal_current_version":"V5.0.1",
        "classification":"RETROSPECTIVE_FIXED_SEED_RANDOM100_PRIOR_SEASON_FROZEN_RESIDUAL_FUSION",
        "seed":r100.SEED,"target":r100.TARGET,"candidate_population":len(candidates),"failures":dict(failures),
        "audit":{"max_1x2_residual":max_one,"max_total_residual":max_total,"max_mass_residual":max_mass,
                 "target_season_results_used_to_fit_alpha":False,"same_day_history_frozen":True,"asian_handicap_primary_target":False},
        "summary":summary,"alpha_by_competition_season":alpha_audit,"sample":rows,
        "governance":{"research_only":True,"formal_weight":0,"current_rule_change":False,"random100_is_diagnostic_only":True,
                      "alpha_optimization_target":"prior_season_logloss","alpha_domain":"competition_specific","automatic_promotion":False},
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"failures":report["failures"],"audit":report["audit"],"summary":summary},ensure_ascii=False,indent=2))
    return 0 if len(rows)==r100.TARGET else 2


if __name__=="__main__":
    raise SystemExit(main())
