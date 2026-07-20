#!/usr/bin/env python3
"""Strict second-stage forward-season review for V4.7 dynamic strength.

For each target season, the borrowing candidate is selected using only the fully
completed immediately preceding season.  The selected candidate is then frozen
for the entire unseen target season.  Each target season is split into two
reporting folds, yielding at least eight forward time folds across four seasons.
This is still research: passing only permits final-chain calibration interaction
review and never auto-promotes a formal weight.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from dynamic_strength_oof_screen_v470 import (
    CANDIDATES,
    EVIDENCE_CONFIG,
    MODEL_ROOT,
    bootstrap_diff,
    build_season_indexes,
    challenger_matrix,
    date_windows,
    load_domain_data,
    score_metrics,
    team_features,
    to_match,
    utc_now,
    write_json,
)
from football_v460_engine import _merge_parameters, build_score_matrix, expected_goals, fit_current_season_state, load_config, low_score_factors
from platform_core import PlatformError, load_json

ROOT = Path(__file__).resolve().parents[1]
STAGE1_ROOT = ROOT / "manifests" / "dynamic_strength_oof_screen_v470"
OUT_ROOT = ROOT / "manifests" / "dynamic_strength_second_stage_v470"


def season_predictions(competition_id: str, season: str, params: dict[str, float], data: dict[str, Any], indexes: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    config = load_config(); by_season = indexes["by_season"]; games = by_season.get(season, []); previous = indexes["previous"].get(season)
    if not games or not previous or previous not in by_season: return {}, {c["id"]: {} for c in CANDIDATES}
    prior_rows = [to_match(g, competition_id) for g in by_season[previous]]; prior_cutoff = max(g["date"] for g in by_season[previous]) + timedelta(days=1)
    try: prior_state = fit_current_season_state(prior_rows, prior_cutoff, params, config)
    except PlatformError: prior_state = None
    baseline = {}; candidate_maps = {c["id"]: {} for c in CANDIDATES}
    for target in games:
        history = [to_match(g, competition_id) for g in games if g["date"] < target["date"]]
        try:
            current_state = fit_current_season_state(history, target["date"], params, config)
            base_means = expected_goals(current_state, f"club_{target['home_id']}", f"club_{target['away_id']}", params, config)
            base_matrix = build_score_matrix(float(base_means["mu_home"]), float(base_means["mu_away"]), current_state["nb_dispersion_k"], params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), low_score_factors(current_state, params))
        except PlatformError:
            continue
        home_feat = team_features(target["home_id"], season, target["date"], indexes, data["transfers"]); away_feat = team_features(target["away_id"], season, target["date"], indexes, data["transfers"])
        if not home_feat.get("feature_complete") or not away_feat.get("feature_complete"): continue
        key = f"{competition_id}:{season}:{target['game_id']}"; block = f"{season}:{target['date'].year}-{target['date'].month:02d}"
        baseline[key] = {"match_key": key, "date": target["date"].date().isoformat(), "season": season, "block_id": block, **score_metrics(base_matrix, target["home_goals"], target["away_goals"])}
        for candidate in CANDIDATES:
            try: matrix, audit = challenger_matrix(current_state, prior_state, target["home_id"], target["away_id"], home_feat, away_feat, candidate, params, config)
            except PlatformError: continue
            candidate_maps[candidate["id"]][key] = {"match_key": key, "date": target["date"].date().isoformat(), "season": season, "block_id": block, "candidate_id": candidate["id"], **score_metrics(matrix, target["home_goals"], target["away_goals"]), **audit}
    return baseline, candidate_maps


def validate(competition_id: str, cache: Path) -> dict[str, Any]:
    stage1_path = STAGE1_ROOT / f"{competition_id}.json"
    if not stage1_path.exists() or load_json(stage1_path).get("status") != "DYNAMIC_STRENGTH_REVIEW_CANDIDATE":
        raise PlatformError("second stage requires a passing stage-1 dynamic-strength receipt")
    evidence = load_json(EVIDENCE_CONFIG); route = evidence["competition_mapping"][competition_id]
    if route["validation_route"] not in {"standard", "standard_regular_league_only"}: raise PlatformError("stage adapter required before second stage")
    data = load_domain_data(competition_id, cache); indexes = build_season_indexes(data); artifact = load_json(MODEL_ROOT / competition_id / "model.json"); parameter_map = artifact["point_in_time_parameters"]
    config = load_config(); selected_model=[]; selected_base=[]; folds=[]; season_selections=[]
    for target_season, selected_params in parameter_map.items():
        selection_season = indexes["previous"].get(target_season)
        if not selection_season: continue
        params = _merge_parameters(config, selected_params)
        selection_baseline, selection_candidates = season_predictions(competition_id, selection_season, params, data, indexes)
        scored=[]
        for candidate in CANDIDATES:
            cmap=selection_candidates[candidate["id"]]; keys=[k for k in selection_baseline if k in cmap]
            if len(keys)<100: continue
            scored.append((mean(cmap[k]["one_x_two_rps"] for k in keys), mean(cmap[k]["joint_log"] for k in keys), candidate["id"], len(keys)))
        if not scored: continue
        scored.sort(); selected_id=scored[0][2]; selection_count=scored[0][3]
        target_baseline, target_candidates = season_predictions(competition_id, target_season, params, data, indexes); cmap=target_candidates[selected_id]
        paired_keys=[k for k in target_baseline if k in cmap]
        if not paired_keys: continue
        target_records=sorted((target_baseline[k] for k in paired_keys),key=lambda r:(r["date"],r["match_key"]))
        season_selections.append({"target_season":target_season,"selection_season":selection_season,"selected_candidate":selected_id,"selection_predictions":selection_count,"selection_mean_one_x_two_rps":scored[0][0],"selection_mean_joint_log":scored[0][1]})
        for wi,dates in enumerate(date_windows(target_records,2),start=1):
            keys=[r["match_key"] for r in target_records if r["date"] in dates]
            for key in keys: selected_model.append(cmap[key]); selected_base.append(target_baseline[key])
            folds.append({"fold_id":f"{target_season}:OUTER{wi}","target_season":target_season,"selection_season":selection_season,"candidate_frozen_before_target_season":selected_id,"test_start":min(dates),"test_end":max(dates),"outer_predictions":len(keys)})
    pairs=list(zip(selected_model,selected_base))
    if not pairs: raise PlatformError("no paired second-stage predictions")
    cis={metric:bootstrap_diff(pairs,metric) for metric in ("joint_log","one_x_two_brier","one_x_two_rps","total_goals_rps")}
    def avg(rows,key): return mean(r[key] for r in rows)
    coverage={key:{"current":avg(selected_base,key),"candidate":avg(selected_model,key)} for key in ("top1","top3","top5","score80","score90")}
    selections=Counter(item["selected_candidate"] for item in season_selections)
    checks={
        "minimum_outer_predictions":len(pairs)>=200,
        "minimum_independent_forward_time_folds":len(folds)>=8,
        "candidate_frozen_before_each_target_season":all(f["selection_season"]!=f["target_season"] for f in folds),
        "one_x_two_rps_ci_improves":cis["one_x_two_rps"]["ci95_upper"]<0.0,
        "joint_log_ci_nonworse":cis["joint_log"]["ci95_upper"]<=0.0,
        "one_x_two_brier_ci_nonworse":cis["one_x_two_brier"]["ci95_upper"]<=0.0,
        "total_goals_rps_ci_nonworse":cis["total_goals_rps"]["ci95_upper"]<=0.0,
        "top1_nonworse":coverage["top1"]["candidate"]+1e-12>=coverage["top1"]["current"],
        "top3_nonworse":coverage["top3"]["candidate"]+1e-12>=coverage["top3"]["current"],
        "top5_nonworse":coverage["top5"]["candidate"]+1e-12>=coverage["top5"]["current"],
        "score80_calibrated":0.76<=coverage["score80"]["candidate"]<=0.84,
        "score90_calibrated":0.86<=coverage["score90"]["candidate"]<=0.94,
        "probability_conservation":max(r["probability_sum_error"] for r in selected_model)<=1e-8,
        "non_identity_selected":sum(v for k,v in selections.items() if k!="identity_no_borrow")>0,
    }
    status="SECOND_STAGE_FINAL_CHAIN_REVIEW_CANDIDATE" if all(checks.values()) else "SECOND_STAGE_NOT_PROMOTED"
    report={"schema_version":"V4.7.0-dynamic-strength-second-stage-r1","generated_at_utc":utc_now(),"competition_id":competition_id,"status":status,"formal_weight":0,"automatic_promotion":False,"probability_change":False,"outer_predictions":len(pairs),"independent_forward_time_folds":len(folds),"season_candidate_selections":season_selections,"selected_candidate_counts":dict(selections),"confidence_intervals":cis,"coverage":coverage,"checks":checks,"folds":folds,"policy":"Strict forward-season review only. Passing still requires replay through the formal OOF calibration/final matrix chain and a CURRENT-compliant competition-specific promotion receipt."
    }
    write_json(OUT_ROOT/f"{competition_id}.json",report); return report


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--competition",required=True); parser.add_argument("--cache-dir",default="/tmp/football-dynamic-strength-second-stage-cache"); args=parser.parse_args()
    try: report=validate(args.competition,Path(args.cache_dir))
    except Exception as exc:
        report={"schema_version":"V4.7.0-dynamic-strength-second-stage-r1","generated_at_utc":utc_now(),"competition_id":args.competition,"status":"FAILED","formal_weight":0,"automatic_promotion":False,"probability_change":False,"reason":str(exc)}; write_json(OUT_ROOT/f"{args.competition}.json",report); print(json.dumps(report,ensure_ascii=False,indent=2)); return 1
    print(json.dumps({"competition_id":args.competition,"status":report["status"],"outer_predictions":report["outer_predictions"],"folds":report["independent_forward_time_folds"]},ensure_ascii=False,indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
