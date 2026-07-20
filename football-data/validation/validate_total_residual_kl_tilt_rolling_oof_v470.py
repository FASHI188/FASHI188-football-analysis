#!/usr/bin/env python3
"""Strict rolling OOF total-residual KL-tilt challenger across 17 domains.

The baseline unified score matrix is replayed point-in-time. A ridge model trained only
on strictly earlier completed seasons predicts the residual between actual total goals
and the baseline matrix expected total, using same-season pre-match team residual
history. The baseline P(T) is then minimally KL-tilted to a target expected total; all
score cells inside a fixed T layer are scaled equally, so P(D|T) is preserved.

Research only. No formal runtime mutation or promotion is created.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    FORMAL_STATUS,
    REPORT_ROOT,
    _actual_result,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, derive_score_marginals, load_json, read_processed_matches, score_matrix_rows, top_scores

OUT = ROOT / "manifests" / "total_residual_kl_tilt_rolling_oof_v470_status.json"
RIDGE = 10.0
MIN_TRAIN_ROWS = 200
BLOCK_SIZE = 20
BOOTSTRAP_DRAWS = 3000
SEED = 470271


def _season_year(season: str) -> int:
    return int(str(season)[:4])


def _completed_seasons(cid: str, report: dict[str, Any]) -> list[str]:
    max_year = _season_year(_requested_last_complete_season(cid))
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and _season_year(season) <= max_year and season not in seasons:
            seasons.append(season)
    seasons.sort(key=_season_year)
    return seasons


def _total_probs(matrix) -> dict[int, float]:
    out: dict[int, float] = {}
    for h, a, p in score_matrix_rows(matrix):
        out[h + a] = out.get(h + a, 0.0) + float(p)
    return out


def _expected_total(matrix) -> float:
    return sum(t * p for t, p in _total_probs(matrix).items())


def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _feature_vector(history: dict[str, list[float]], home: str, away: str, base_mean: float) -> list[float]:
    hv = history.get(home, [])
    av = history.get(away, [])
    return [
        1.0,
        _safe_mean(hv),
        _safe_mean(av),
        _safe_mean(hv[-5:]),
        _safe_mean(av[-5:]),
        math.log1p(len(hv)),
        math.log1p(len(av)),
        base_mean,
    ]


def _season_rows(cid: str, report: dict[str, Any], all_matches, season: str) -> list[dict[str, Any]]:
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"invalid selected parameters for {cid} {season}")
    temperature, mode = _target_season_temperature(cid, season)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    team_residual_history: dict[str, list[float]] = defaultdict(list)
    rows = []
    for match in matches:
        try:
            baseline = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
        except PlatformError:
            continue
        if abs(temperature - 1.0) > 1e-15:
            baseline = temperature_scale_matrix(baseline, temperature)
        base_mean = _expected_total(baseline)
        x = _feature_vector(team_residual_history, match.home_team, match.away_team, base_mean)
        actual_total = int(match.home_goals) + int(match.away_goals)
        residual = actual_total - base_mean
        rows.append({"features": x, "target_residual": residual, "baseline": baseline, "match": match, "base_mean": base_mean})
        team_residual_history[match.home_team].append(residual)
        team_residual_history[match.away_team].append(residual)
    return rows


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    aug = [list(a[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise PlatformError("singular ridge system")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        aug[col] = [v / div for v in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            if abs(factor) <= 1e-18:
                continue
            aug[row] = [aug[row][j] - factor * aug[col][j] for j in range(n + 1)]
    return [aug[i][-1] for i in range(n)]


def _fit_ridge(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < MIN_TRAIN_ROWS:
        raise PlatformError("insufficient total-residual training rows")
    p = len(rows[0]["features"])
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for row in rows:
        x = [float(v) for v in row["features"]]
        y = float(row["target_residual"])
        for i in range(p):
            xty[i] += x[i] * y
            for j in range(p):
                xtx[i][j] += x[i] * x[j]
    for i in range(1, p):
        xtx[i][i] += RIDGE
    beta = _solve_linear(xtx, xty)
    return {"beta": beta, "training_rows": len(rows), "ridge": RIDGE}


def _predict_residual(model: dict[str, Any], features: list[float]) -> float:
    return sum(float(b) * float(x) for b, x in zip(model["beta"], features))


def _tilted_total_probs(prior: dict[int, float], target_mean: float) -> dict[int, float]:
    support = sorted(t for t, p in prior.items() if p > 0)
    if not support:
        raise PlatformError("empty total support")
    low_support, high_support = float(min(support)), float(max(support))
    target = min(high_support - 1e-8, max(low_support + 1e-8, float(target_mean)))
    base_mean = sum(t * prior[t] for t in support)
    if abs(target - base_mean) < 1e-12:
        return dict(prior)

    def expectation(lam: float) -> tuple[float, dict[int, float]]:
        max_log = max(lam * t for t in support)
        weights = {t: prior[t] * math.exp(lam * t - max_log) for t in support}
        z = sum(weights.values())
        q = {t: weights[t] / z for t in support}
        return sum(t * q[t] for t in support), q

    lo, hi = -20.0, 20.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        em, _ = expectation(mid)
        if em < target:
            lo = mid
        else:
            hi = mid
    _, q = expectation((lo + hi) / 2.0)
    return q


def _apply_total_tilt(matrix, target_mean: float) -> tuple[list[dict[str, Any]], float]:
    prior = _total_probs(matrix)
    q = _tilted_total_probs(prior, target_mean)
    out = []
    for h, a, p in score_matrix_rows(matrix):
        t = h + a
        denom = prior.get(t, 0.0)
        new_p = 0.0 if denom <= 0 else float(p) * q.get(t, 0.0) / denom
        out.append({"home_goals": h, "away_goals": a, "probability": new_p})
    residual = max(abs(_total_probs(out).get(t, 0.0) - q.get(t, 0.0)) for t in set(prior) | set(q))
    return out, residual


def _total_rps(probs: dict[int, float], actual: int) -> float:
    support = sorted(set(probs) | {actual})
    if not support:
        return 0.0
    max_t = max(support)
    score = 0.0
    for threshold in range(max_t):
        pred_cdf = sum(p for t, p in probs.items() if t <= threshold)
        actual_cdf = 1.0 if actual <= threshold else 0.0
        score += (pred_cdf - actual_cdf) ** 2
    return score / max(1, max_t)


def _brier(one: dict[str, float], actual: str) -> float:
    return sum((float(one[k]) - (1.0 if k == actual else 0.0)) ** 2 for k in ("home", "draw", "away"))


def _rps(one: dict[str, float], actual: str) -> float:
    actual_vec = {"home": (1,0,0), "draw": (0,1,0), "away": (0,0,1)}[actual]
    p = (one["home"], one["draw"], one["away"])
    return ((p[0]-actual_vec[0])**2 + ((p[0]+p[1])-(actual_vec[0]+actual_vec[1]))**2)/2.0


def _joint_log(matrix, hg: int, ag: int) -> float:
    p = sum(prob for h, a, prob in score_matrix_rows(matrix) if h == hg and a == ag)
    return -math.log(max(1e-15, p))


def _metric_row(base, cand, match) -> dict[str, float]:
    bm = derive_score_marginals(base)["1x2"]
    cm = derive_score_marginals(cand)["1x2"]
    actual = _actual_result(int(match.home_goals), int(match.away_goals))
    actual_score = f"{int(match.home_goals)}-{int(match.away_goals)}"
    brank = top_scores(base, 3)
    crank = top_scores(cand, 3)
    bt = sorted(_total_probs(base).items(), key=lambda kv:(-kv[1],kv[0]))
    ct = sorted(_total_probs(cand).items(), key=lambda kv:(-kv[1],kv[0]))
    at = int(match.home_goals)+int(match.away_goals)
    return {
        "base_1x2_acc": float(max(bm,key=bm.get)==actual), "cand_1x2_acc": float(max(cm,key=cm.get)==actual),
        "base_1x2_brier": _brier(bm,actual), "cand_1x2_brier": _brier(cm,actual),
        "base_1x2_rps": _rps(bm,actual), "cand_1x2_rps": _rps(cm,actual),
        "base_score1": float(brank[0]["score"]==actual_score), "cand_score1": float(crank[0]["score"]==actual_score),
        "base_score3": float(any(x["score"]==actual_score for x in brank)), "cand_score3": float(any(x["score"]==actual_score for x in crank)),
        "base_total1": float(bt[0][0]==at), "cand_total1": float(ct[0][0]==at),
        "base_total2": float(at in {x[0] for x in bt[:2]}), "cand_total2": float(at in {x[0] for x in ct[:2]}),
        "base_total_rps": _total_rps(_total_probs(base),at), "cand_total_rps": _total_rps(_total_probs(cand),at),
        "base_log": _joint_log(base,int(match.home_goals),int(match.away_goals)), "cand_log": _joint_log(cand,int(match.home_goals),int(match.away_goals)),
    }


def _bootstrap(rows: list[dict[str,float]], key: str, seed: int) -> dict[str,Any]:
    blocks=[rows[i:i+BLOCK_SIZE] for i in range(0,len(rows),BLOCK_SIZE)]
    if not blocks: return {"mean_difference":None,"ci95_lower":None,"ci95_upper":None}
    diffs=lambda r: r[f"cand_{key}"]-r[f"base_{key}"]
    point=mean(diffs(r) for r in rows)
    rng=random.Random(seed); samples=[]
    for _ in range(BOOTSTRAP_DRAWS):
        sample=[]
        for _ in blocks: sample.extend(rng.choice(blocks))
        samples.append(mean(diffs(r) for r in sample))
    samples.sort()
    return {"mean_difference":point,"ci95_lower":samples[int(.025*(len(samples)-1))],"ci95_upper":samples[int(.975*(len(samples)-1))],"blocks":len(blocks),"draws":BOOTSTRAP_DRAWS}


def _aggregate(rows: list[dict[str,float]], seed: int) -> dict[str,Any]:
    metrics={}
    for key in ("1x2_acc","1x2_brier","1x2_rps","score1","score3","total1","total2","total_rps","log"):
        b=mean(r[f"base_{key}"] for r in rows); c=mean(r[f"cand_{key}"] for r in rows)
        metrics[key]={"baseline":b,"candidate":c,"candidate_minus_baseline":c-b}
    ci={key:_bootstrap(rows,key,seed+i) for i,key in enumerate(("1x2_brier","1x2_rps","total_rps","log"),1)}
    return {"count":len(rows),"metrics":metrics,"paired_block_bootstrap":ci}


def _validate_domain(cid: str, seed: int) -> dict[str,Any]:
    report=load_json(REPORT_ROOT/f"{cid}.json"); seasons=_completed_seasons(cid,report)
    all_matches=read_processed_matches(cid)
    cache={s:_season_rows(cid,report,all_matches,s) for s in seasons}
    outer=[]; pooled=[]; skipped=[]; max_t_res=0.0
    for idx,target in enumerate(seasons[1:]):
        prior=[s for s in seasons if _season_year(s)<_season_year(target)]
        train=[r for s in prior for r in cache[s]]
        if len(train)<MIN_TRAIN_ROWS:
            skipped.append({"target_season":target,"training_rows":len(train)})
            continue
        model=_fit_ridge(train); season_rows=[]
        for item in cache[target]:
            residual_hat=_predict_residual(model,item["features"])
            cand,tres=_apply_total_tilt(item["baseline"],item["base_mean"]+residual_hat)
            max_t_res=max(max_t_res,tres)
            row=_metric_row(item["baseline"],cand,item["match"]); season_rows.append(row); pooled.append(row)
        if season_rows:
            outer.append({"target_season":target,"training_seasons":prior,"model":model,**_aggregate(season_rows,seed+idx*100)})
    if not pooled: raise PlatformError(f"no rolling OOF rows for {cid}")
    agg=_aggregate(pooled,seed+900); m=agg["metrics"]; ci=agg["paired_block_bootstrap"]
    checks={
        "multiple_outer_seasons":len(outer)>=2,
        "strict_prior_training_each_fold":True,
        "total_kl_tilt_preserves_conditional_D_given_T":True,
        "total_projection_residual":max_t_res<=1e-10,
        "total_rps_mean_improves":m["total_rps"]["candidate_minus_baseline"]<0,
        "total_rps_ci_upper_below_zero":ci["total_rps"]["ci95_upper"] is not None and ci["total_rps"]["ci95_upper"]<0,
        "total_top1_noninferior":m["total1"]["candidate_minus_baseline"]>=0,
        "total_top2_noninferior":m["total2"]["candidate_minus_baseline"]>=0,
        "score_top1_noninferior":m["score1"]["candidate_minus_baseline"]>=0,
        "score_top3_noninferior":m["score3"]["candidate_minus_baseline"]>=0,
        "one_x_two_accuracy_noninferior":m["1x2_acc"]["candidate_minus_baseline"]>=0,
        "one_x_two_brier_noninferior":m["1x2_brier"]["candidate_minus_baseline"]<=0,
        "one_x_two_rps_noninferior":m["1x2_rps"]["candidate_minus_baseline"]<=0,
        "joint_log_noninferior":m["log"]["candidate_minus_baseline"]<=0,
    }
    candidate=all(checks.values())
    return {"competition_id":cid,"status":"ROLLING_OOF_RESEARCH_CANDIDATE" if candidate else "KEEP_FORMAL_WEIGHT_0","outer_season_count":len(outer),"pooled_prediction_count":len(pooled),"skipped_insufficient_training_folds":skipped,"max_total_projection_residual":max_t_res,"pooled":agg,"outer_seasons":outer,"checks":checks,"formal_weight":0,"automatic_promotion":False,"probability_change":False}


def main()->int:
    status=load_json(FORMAL_STATUS); comps=sorted((status.get("reports") or {}).keys()); reports={}; failures={}; candidates=[]
    for i,cid in enumerate(comps):
        try:
            item=_validate_domain(cid,SEED+i*10000); reports[cid]=item
            if item["status"]=="ROLLING_OOF_RESEARCH_CANDIDATE": candidates.append(cid)
        except Exception as exc: failures[cid]=f"{type(exc).__name__}: {exc}"
    payload={"schema_version":"V4.7.0-total-residual-kl-tilt-rolling-oof-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS" if len(reports)==len(comps) and not failures else "PARTIAL","competition_count_requested":len(comps),"competition_count_completed":len(reports),"rolling_oof_research_candidates":candidates,"reports":reports,"failures":failures,"governance":{"research_only":True,"formal_weight_change":False,"probability_change":False,"automatic_promotion":False,"same_final_matrix_metrics":True}}
    atomic_write_json(OUT,payload); print(json.dumps({"status":payload["status"],"candidates":candidates,"failures":failures},ensure_ascii=False,indent=2)); return 0 if payload["status"]=="PASS" else 1

if __name__=="__main__": raise SystemExit(main())
