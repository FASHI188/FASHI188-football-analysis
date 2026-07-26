#!/usr/bin/env python3
"""V6.28.0 fixed-seed random100 screen for cross-season dynamic team strength.

Purpose
-------
Test genuinely new football information for the 1X2 layer, not another market blend. The candidate
borrows a capped amount of prior-season team attack/defence statistics according to roster
continuity, manager continuity, promotion/relegation and structural-break features from the existing
V4.7 dynamic-strength research module.

Leakage contract
----------------
- Selection season: 2024/25 only.
- Target/test season: 2025/26 only.
- For each competition, choose the V4.7 candidate with minimum 2024/25 1X2 RPS, tie-broken by joint
  log score, requiring >=100 selection predictions.
- Freeze that candidate for the entire 2025/26 target season.
- Within a target match, current-season state and continuity features use only information strictly
  before the target date, as implemented by the existing V4.7 engine.
- Enumerate predictions without using target outcomes, fixed-seed shuffle, evaluate exactly 100 rows.

This is an incremental-signal diagnostic on the Transfermarkt public research route. It is NOT a
formal V5.0.1 replay and cannot be compared numerically as if it were the same random100 population.
Formal weight remains zero even on a pass.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from datetime import timedelta, timezone, datetime
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_architecture_order_v6190 as metrics  # noqa: E402
from dynamic_strength_oof_screen_v470 import (  # noqa: E402
    CANDIDATES,
    MODEL_ROOT,
    build_season_indexes,
    challenger_matrix,
    load_domain_data,
    score_metrics,
    team_features,
    to_match,
)
from dynamic_strength_second_stage_v470 import season_predictions  # noqa: E402
from football_v460_engine import (  # noqa: E402
    _merge_parameters,
    build_score_matrix,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import PlatformError, derive_score_marginals, load_json  # noqa: E402

OUT = ROOT / "manifests" / "v6_dynamic_strength_1x2_random100_v6280_status.json"
TRAIN_SEASON = "2024/25"
TEST_SEASON = "2025/26"
SEED = 628100
TARGET = 100
CACHE = Path("/tmp/football-v628-dynamic-cache")
COMPS = (
    "ENG_PremierLeague",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
    "ESP_LaLiga",
    "NED_Eredivisie",
    "POR_PrimeiraLiga",
    "SCO_Premiership",
)
EPS = 1e-15


def one_vec(matrix: list[dict[str, Any]]) -> list[float]:
    one = derive_score_marginals(matrix)["1x2"]
    return [float(one[k]) for k in ("home", "draw", "away")]


def actual_result(h: int, a: int) -> int:
    return 0 if h > a else 1 if h == a else 2


def rps3(p: list[float], actual: int) -> float:
    c0 = p[0]
    c1 = p[0] + p[1]
    y0 = 1.0 if actual == 0 else 0.0
    y1 = 1.0 if actual <= 1 else 0.0
    return ((c0 - y0) ** 2 + (c1 - y1) ** 2) / 2.0


def top_score(matrix: list[dict[str, Any]], k: int, h: int, a: int) -> int:
    ranked = sorted(matrix, key=lambda c: float(c["probability"]), reverse=True)[:k]
    return int(any(int(c["home_goals"]) == h and int(c["away_goals"]) == a for c in ranked))


def total8(matrix: list[dict[str, Any]]) -> list[float]:
    out = [0.0] * 8
    for c in matrix:
        out[min(7, int(c["home_goals"]) + int(c["away_goals"]))] += float(c["probability"])
    return out


def rps8(p: list[float], actual: int) -> float:
    cp = 0.0
    score = 0.0
    for i in range(7):
        cp += p[i]
        score += (cp - (1.0 if actual <= i else 0.0)) ** 2
    return score / 7.0


def joint_log(matrix: list[dict[str, Any]], h: int, a: int) -> float:
    p = next((float(c["probability"]) for c in matrix if int(c["home_goals"]) == h and int(c["away_goals"]) == a), EPS)
    return -math.log(max(EPS, p))


def choose_candidate(cid: str, params: dict[str, float], data: dict[str, Any], indexes: dict[str, Any]) -> dict[str, Any]:
    base, candidate_maps = season_predictions(cid, TRAIN_SEASON, params, data, indexes)
    ranking = []
    for candidate in CANDIDATES:
        cmap = candidate_maps[candidate["id"]]
        keys = [k for k in base if k in cmap]
        if len(keys) < 100:
            continue
        ranking.append({
            "candidate_id": candidate["id"],
            "count": len(keys),
            "mean_one_x_two_rps": mean(cmap[k]["one_x_two_rps"] for k in keys),
            "mean_joint_log": mean(cmap[k]["joint_log"] for k in keys),
        })
    if not ranking:
        raise PlatformError(f"{cid}: no candidate has >=100 selection rows")
    ranking.sort(key=lambda r: (r["mean_one_x_two_rps"], r["mean_joint_log"], r["candidate_id"]))
    selected_id = ranking[0]["candidate_id"]
    selected = next(c for c in CANDIDATES if c["id"] == selected_id)
    return {"selected": selected, "ranking": ranking}


def build_target_rows(cid: str, params: dict[str, float], data: dict[str, Any], indexes: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    config = load_config()
    games = indexes["by_season"].get(TEST_SEASON, [])
    previous = indexes["previous"].get(TEST_SEASON)
    if not games or previous != TRAIN_SEASON:
        return []
    prior_rows = [to_match(g, cid) for g in indexes["by_season"][TRAIN_SEASON]]
    prior_cutoff = max(g["date"] for g in indexes["by_season"][TRAIN_SEASON]) + timedelta(days=1)
    try:
        prior_state = fit_current_season_state(prior_rows, prior_cutoff, params, config)
    except PlatformError:
        prior_state = None
    rows = []
    for target in games:
        history_games = [g for g in games if g["date"] < target["date"]]
        history = [to_match(g, cid) for g in history_games]
        try:
            current_state = fit_current_season_state(history, target["date"], params, config)
            base_means = expected_goals(current_state, f"club_{target['home_id']}", f"club_{target['away_id']}", params, config)
            baseline = build_score_matrix(
                float(base_means["mu_home"]), float(base_means["mu_away"]), current_state["nb_dispersion_k"],
                params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), low_score_factors(current_state, params),
            )
        except PlatformError:
            continue
        hf = team_features(target["home_id"], TEST_SEASON, target["date"], indexes, data["transfers"])
        af = team_features(target["away_id"], TEST_SEASON, target["date"], indexes, data["transfers"])
        if not hf.get("feature_complete") or not af.get("feature_complete"):
            continue
        try:
            challenger, audit = challenger_matrix(current_state, prior_state, target["home_id"], target["away_id"], hf, af, candidate, params, config)
        except PlatformError:
            continue
        bp = one_vec(baseline)
        cp = one_vec(challenger)
        actual = actual_result(int(target["home_goals"]), int(target["away_goals"]))
        actual_total = min(7, int(target["home_goals"]) + int(target["away_goals"]))
        rows.append({
            "match_key": f"{cid}:{TEST_SEASON}:{target['game_id']}",
            "competition_id": cid,
            "date": target["date"].date().isoformat(),
            "home_id": int(target["home_id"]),
            "away_id": int(target["away_id"]),
            "actual_result": actual,
            "actual_score": [int(target["home_goals"]), int(target["away_goals"])],
            "baseline_1x2": bp,
            "candidate_1x2": cp,
            "baseline_top1": int(max(range(3), key=lambda i: bp[i]) == actual),
            "candidate_top1": int(max(range(3), key=lambda i: cp[i]) == actual),
            "baseline_brier": metrics.brier3(bp, actual),
            "candidate_brier": metrics.brier3(cp, actual),
            "baseline_logloss": metrics.logloss3(bp, actual),
            "candidate_logloss": metrics.logloss3(cp, actual),
            "baseline_rps": rps3(bp, actual),
            "candidate_rps": rps3(cp, actual),
            "baseline_score_top1": top_score(baseline, 1, int(target["home_goals"]), int(target["away_goals"])),
            "candidate_score_top1": top_score(challenger, 1, int(target["home_goals"]), int(target["away_goals"])),
            "baseline_score_top3": top_score(baseline, 3, int(target["home_goals"]), int(target["away_goals"])),
            "candidate_score_top3": top_score(challenger, 3, int(target["home_goals"]), int(target["away_goals"])),
            "baseline_joint_log": joint_log(baseline, int(target["home_goals"]), int(target["away_goals"])),
            "candidate_joint_log": joint_log(challenger, int(target["home_goals"]), int(target["away_goals"])),
            "baseline_total_rps": rps8(total8(baseline), actual_total),
            "candidate_total_rps": rps8(total8(challenger), actual_total),
            "home_borrowing_weight": float(audit.get("home_borrowing_weight") or 0.0),
            "away_borrowing_weight": float(audit.get("away_borrowing_weight") or 0.0),
            "max_prior_equivalent_matches": float(audit.get("max_prior_equivalent_matches") or 0.0),
        })
    return rows


def avg(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(r[key]) for r in rows) / len(rows)


def main() -> int:
    config = load_config()
    all_rows = []
    selections = {}
    failures = {}
    for cid in COMPS:
        try:
            data = load_domain_data(cid, CACHE)
            indexes = build_season_indexes(data)
            artifact = load_json(MODEL_ROOT / cid / "model.json")
            selected_params = artifact["point_in_time_parameters"].get(TEST_SEASON)
            if not selected_params:
                raise PlatformError("no target-season point-in-time parameter set")
            params = _merge_parameters(config, selected_params)
            selection = choose_candidate(cid, params, data, indexes)
            rows = build_target_rows(cid, params, data, indexes, selection["selected"])
            selections[cid] = {
                "selected_candidate": selection["selected"]["id"],
                "selection_ranking": selection["ranking"],
                "target_eligible_predictions": len(rows),
            }
            all_rows.extend(rows)
        except Exception as exc:
            failures[cid] = str(exc)

    ordered = sorted(all_rows, key=lambda r: (r["competition_id"], r["date"], r["match_key"]))
    random.Random(SEED).shuffle(ordered)
    sample = ordered[:TARGET]
    if not sample:
        raise RuntimeError("no dynamic-strength target predictions")
    summary = {
        "count": len(sample),
        "baseline_1x2_top1": avg(sample, "baseline_top1"),
        "candidate_1x2_top1": avg(sample, "candidate_top1"),
        "baseline_1x2_brier": avg(sample, "baseline_brier"),
        "candidate_1x2_brier": avg(sample, "candidate_brier"),
        "baseline_1x2_logloss": avg(sample, "baseline_logloss"),
        "candidate_1x2_logloss": avg(sample, "candidate_logloss"),
        "baseline_1x2_rps": avg(sample, "baseline_rps"),
        "candidate_1x2_rps": avg(sample, "candidate_rps"),
        "baseline_score_top1": avg(sample, "baseline_score_top1"),
        "candidate_score_top1": avg(sample, "candidate_score_top1"),
        "baseline_score_top3": avg(sample, "baseline_score_top3"),
        "candidate_score_top3": avg(sample, "candidate_score_top3"),
        "baseline_joint_log": avg(sample, "baseline_joint_log"),
        "candidate_joint_log": avg(sample, "candidate_joint_log"),
        "baseline_total_rps": avg(sample, "baseline_total_rps"),
        "candidate_total_rps": avg(sample, "candidate_total_rps"),
        "mean_home_borrowing_weight": avg(sample, "home_borrowing_weight"),
        "mean_away_borrowing_weight": avg(sample, "away_borrowing_weight"),
    }
    summary["delta_1x2_top1_pp"] = (summary["candidate_1x2_top1"] - summary["baseline_1x2_top1"]) * 100.0
    by_comp = {}
    for cid in COMPS:
        rs = [r for r in sample if r["competition_id"] == cid]
        if not rs:
            continue
        by_comp[cid] = {
            "count": len(rs),
            "baseline_top1": avg(rs, "baseline_top1"),
            "candidate_top1": avg(rs, "candidate_top1"),
            "delta_pp": (avg(rs, "candidate_top1") - avg(rs, "baseline_top1")) * 100.0,
            "baseline_rps": avg(rs, "baseline_rps"),
            "candidate_rps": avg(rs, "candidate_rps"),
        }
    checks = {
        "sample_100": len(sample) == TARGET,
        "one_x_two_top1_plus_5pp": summary["candidate_1x2_top1"] >= summary["baseline_1x2_top1"] + 0.05 - 1e-12,
        "one_x_two_brier_nonworse": summary["candidate_1x2_brier"] <= summary["baseline_1x2_brier"] + 1e-12,
        "one_x_two_logloss_nonworse": summary["candidate_1x2_logloss"] <= summary["baseline_1x2_logloss"] + 1e-12,
        "one_x_two_rps_nonworse": summary["candidate_1x2_rps"] <= summary["baseline_1x2_rps"] + 1e-12,
        "joint_log_nonworse": summary["candidate_joint_log"] <= summary["baseline_joint_log"] + 1e-12,
        "total_rps_nonworse": summary["candidate_total_rps"] <= summary["baseline_total_rps"] + 1e-12,
    }
    report = {
        "schema_version": "V6.28.0-dynamic-strength-1x2-fixed-seed-random100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(sample) == TARGET else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_PUBLIC_TRANSFERMARKT_DYNAMIC_STRENGTH_SIGNAL_SCREEN_RANDOM100",
        "train_selection_season": TRAIN_SEASON,
        "test_season": TEST_SEASON,
        "seed": SEED,
        "target": TARGET,
        "eligible_population": len(all_rows),
        "competition_failures": failures,
        "selections": selections,
        "summary": summary,
        "by_competition_in_sample": by_comp,
        "fast100_gate": {
            "checks": checks,
            "passed": all(checks.values()),
            "on_failure": "DO_NOT_BUILD_DIRECT_1X2_HEAD_FROM_THIS_DYNAMIC_SIGNAL_WITHOUT_NEW_INFORMATION",
        },
        "sample": sample,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "selection_candidate_frozen_from_2024_25": True,
            "test_2025_26_outcomes_used_for_selection": False,
            "random100_is_diagnostic_only": True,
            "same_population_as_v626_random100": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "eligible_population": len(all_rows), "failures": failures, "selections": {k:v["selected_candidate"] for k,v in selections.items()}, "summary": summary, "fast100_gate": report["fast100_gate"]}, ensure_ascii=False, indent=2))
    return 0 if len(sample) == TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
