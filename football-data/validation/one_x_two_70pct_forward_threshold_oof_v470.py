#!/usr/bin/env python3
"""Forward-frozen selective 1X2 threshold validation for domain-level 70% research candidates.

For each target season, the gap threshold is chosen using strictly earlier completed
seasons only. The chosen threshold is then frozen and evaluated on the unseen target
season. Target-season outcomes never participate in threshold selection.

Research only. No threshold is promoted to runtime.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _actual_result,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, derive_score_marginals, load_json, read_processed_matches

TARGETS = ("ESP_LaLiga", "NED_Eredivisie", "POR_PrimeiraLiga", "SCO_Premiership")
THRESHOLDS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
OUT = ROOT / "manifests" / "one_x_two_70pct_forward_threshold_oof_v470_status.json"


def _season_year(season: str) -> int:
    return int(str(season)[:4])


def _wilson(hits: int, n: int, z: float = 1.959963984540054) -> dict[str, float | None]:
    if n <= 0:
        return {"lower": None, "upper": None}
    p = hits / n
    denom = 1.0 + z*z/n
    center = (p + z*z/(2*n))/denom
    margin = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/denom
    return {"lower": max(0.0, center-margin), "upper": min(1.0, center+margin)}


def _completed_seasons(cid: str, report: dict[str, Any]) -> list[str]:
    max_year = _season_year(_requested_last_complete_season(cid))
    seasons=[]
    for fold in report.get("folds") or []:
        s=str(fold.get("outer_season") or "")
        if s and _season_year(s)<=max_year and s not in seasons:
            seasons.append(s)
    seasons.sort(key=_season_year)
    return seasons


def _season_rows(cid: str, report: dict[str, Any], all_matches, season: str) -> list[dict[str, Any]]:
    fold=_fold_for_season(report,season)
    params=fold.get("selected_parameters")
    if not isinstance(params,dict): raise PlatformError(f"missing parameters {cid} {season}")
    temp,mode=_target_season_temperature(cid,season)
    matches=sorted([m for m in all_matches if str(m.season)==season],key=lambda m:(m.date,m.home_team,m.away_team))
    rows=[]
    for match in matches:
        try: matrix=_predict_from_loaded_matches(all_matches,match.home_team,match.away_team,match.date,season,params)
        except PlatformError: continue
        if abs(temp-1.0)>1e-15: matrix=temperature_scale_matrix(matrix,temp)
        one=derive_score_marginals(matrix)["1x2"]
        rank=sorted(((k,float(one[k])) for k in ("home","draw","away")),key=lambda kv:(-kv[1],kv[0]))
        actual=_actual_result(int(match.home_goals),int(match.away_goals))
        rows.append({"gap":rank[0][1]-rank[1][1],"hit":int(rank[0][0]==actual)})
    return rows


def _threshold_stats(rows_by_season: dict[str,list[dict[str,Any]]], threshold: float) -> dict[str,Any]:
    season=[]; pooled_n=pooled_hits=0
    for s,rows in rows_by_season.items():
        sel=[r for r in rows if float(r["gap"])>=threshold]
        n=len(sel); hits=sum(int(r["hit"]) for r in sel)
        pooled_n+=n; pooled_hits+=hits
        season.append({"season":s,"selected":n,"accuracy":hits/n if n else None})
    acc=[float(x["accuracy"]) for x in season if x["accuracy"] is not None]
    return {"threshold":threshold,"selected":pooled_n,"accuracy":pooled_hits/pooled_n if pooled_n else None,
            "min_season_selected":min((x["selected"] for x in season),default=0),
            "min_season_accuracy":min(acc) if acc else None,
            "season_accuracy_std":pstdev(acc) if len(acc)>1 else 0.0 if acc else None}


def _select_threshold(prior_rows: dict[str,list[dict[str,Any]]]) -> dict[str,Any] | None:
    candidates=[]
    for t in THRESHOLDS:
        s=_threshold_stats(prior_rows,t)
        checks=(s["selected"]>=60 and s["min_season_selected"]>=15 and s["accuracy"] is not None and s["accuracy"]>=0.68
                and s["min_season_accuracy"] is not None and s["min_season_accuracy"]>=0.58
                and s["season_accuracy_std"] is not None and s["season_accuracy_std"]<=0.12)
        if checks: candidates.append(s)
    if not candidates: return None
    # Prefer the broadest qualifying threshold to reduce selection variance; break ties by higher accuracy.
    candidates.sort(key=lambda x:(x["threshold"],-x["accuracy"]))
    return candidates[0]


def _validate_domain(cid: str) -> dict[str,Any]:
    report=load_json(REPORT_ROOT/f"{cid}.json"); seasons=_completed_seasons(cid,report)
    if len(seasons)<3: raise PlatformError(f"need at least 3 completed seasons for {cid}")
    all_matches=read_processed_matches(cid)
    cache={s:_season_rows(cid,report,all_matches,s) for s in seasons}
    folds=[]
    for idx in range(2,len(seasons)):
        target=seasons[idx]; prior=seasons[:idx]
        selected=_select_threshold({s:cache[s] for s in prior})
        if selected is None:
            folds.append({"target_season":target,"training_seasons":prior,"selection_status":"NO_PRIOR_QUALIFYING_THRESHOLD","selected_threshold":None,"selected_count":0,"accuracy":None})
            continue
        threshold=float(selected["threshold"])
        target_sel=[r for r in cache[target] if float(r["gap"])>=threshold]
        hits=sum(int(r["hit"]) for r in target_sel); n=len(target_sel)
        folds.append({"target_season":target,"training_seasons":prior,"selection_status":"FROZEN_FROM_PRIOR_SEASONS","selected_threshold":threshold,
                      "prior_selection_stats":selected,"selected_count":n,"eligible_predictions":len(cache[target]),"coverage":n/len(cache[target]) if cache[target] else None,
                      "hit_count":hits,"accuracy":hits/n if n else None,"ci95_wilson":_wilson(hits,n)})
    evaluated=[f for f in folds if f.get("accuracy") is not None]
    pooled_n=sum(int(f["selected_count"]) for f in evaluated); pooled_hits=sum(int(f["hit_count"]) for f in evaluated)
    acc=[float(f["accuracy"]) for f in evaluated]
    checks={"at_least_two_forward_folds":len(evaluated)>=2,"pooled_selected_at_least_60":pooled_n>=60,
            "pooled_accuracy_at_least_70pct":pooled_n>0 and pooled_hits/pooled_n>=0.70,
            "minimum_forward_season_accuracy_at_least_60pct":bool(acc) and min(acc)>=0.60,
            "forward_accuracy_std_at_most_10pp":len(acc)>1 and pstdev(acc)<=0.10}
    return {"competition_id":cid,"status":"FORWARD_OOF_RESEARCH_CANDIDATE" if all(checks.values()) else "KEEP_FORMAL_WEIGHT_0",
            "forward_folds":folds,"evaluated_forward_fold_count":len(evaluated),"pooled_selected_count":pooled_n,"pooled_hit_count":pooled_hits,
            "pooled_accuracy":pooled_hits/pooled_n if pooled_n else None,"pooled_ci95_wilson":_wilson(pooled_hits,pooled_n),
            "forward_accuracy_min":min(acc) if acc else None,"forward_accuracy_std":pstdev(acc) if len(acc)>1 else None,"checks":checks,
            "formal_weight":0,"automatic_promotion":False,"probability_change":False}


def main()->int:
    reports={}; failures={}; candidates=[]
    for cid in TARGETS:
        try:
            item=_validate_domain(cid); reports[cid]=item
            if item["status"]=="FORWARD_OOF_RESEARCH_CANDIDATE": candidates.append(cid)
        except Exception as exc: failures[cid]=f"{type(exc).__name__}: {exc}"
    payload={"schema_version":"V4.7.0-1x2-70pct-forward-threshold-oof-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
             "status":"PASS" if len(reports)==len(TARGETS) and not failures else "PARTIAL","competition_count_requested":len(TARGETS),"competition_count_completed":len(reports),
             "forward_oof_research_candidates":candidates,"reports":reports,"failures":failures,
             "governance":{"research_only":True,"threshold_selected_from_strictly_prior_seasons":True,"formal_threshold_selected":False,"runtime_change":False,"formal_weight_change":False}}
    atomic_write_json(OUT,payload); print(json.dumps({"status":payload["status"],"candidates":candidates,"failures":failures},ensure_ascii=False,indent=2)); return 0 if payload["status"]=="PASS" else 1

if __name__=="__main__": raise SystemExit(main())
