#!/usr/bin/env python3
"""Allocation-only V4.7 dynamic-strength research screen.

This variant tests whether prior-season continuity improves the home/away allocation
conditional on the CURRENT Champion direct total-goals marginal.  It deliberately
preserves mu_total and the NB total track so a 1X2/handicap improvement cannot be
credited to silently changing the validated direct-total model.  Research only.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from dynamic_strength_challenger_v470 import commensurability_score
from dynamic_strength_oof_screen_v470 import (
    CANDIDATES,
    EVIDENCE_CONFIG,
    MODEL_ROOT,
    bootstrap_diff,
    build_season_indexes,
    date_windows,
    load_domain_data,
    score_metrics,
    team_features,
    to_match,
    blended_rate,
    write_json,
    utc_now,
)
from football_v460_engine import _merge_parameters, _shrunk_rate, build_score_matrix, expected_goals, fit_current_season_state, load_config, low_score_factors
from platform_core import PlatformError, load_json, normalize_team_token

ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "manifests" / "dynamic_strength_allocation_only_oof_v470"


def allocation_only_matrix(current_state: dict[str, Any], prior_state: dict[str, Any] | None, home_id: int, away_id: int, home_feat: dict[str, Any], away_feat: dict[str, Any], candidate: dict[str, Any], params: dict[str, float], config: dict[str, Any], champion_mu_total: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    home_key = normalize_team_token(f"club_{home_id}"); away_key = normalize_team_token(f"club_{away_id}")
    if home_key not in current_state["team"] or away_key not in current_state["team"]:
        raise PlatformError("current team state unavailable")
    current_home = current_state["team"][home_key]; current_away = current_state["team"][away_key]
    if current_home["home_raw_matches"] < int(config["minimum_team_raw_matches"]) or current_away["away_raw_matches"] < int(config["minimum_team_raw_matches"]):
        raise PlatformError("current-season venue sample below minimum")
    prior_home = prior_state["team"].get(home_key) if prior_state else None; prior_away = prior_state["team"].get(away_key) if prior_state else None
    max_prior = float(candidate["max_prior_equivalent_matches"]); coeffs = candidate["coefficients"]
    keys = ("roster_continuity", "coach_continuity", "promoted_or_relegated", "structural_break_score")
    home_weight = 0.0 if home_feat.get("promoted_or_relegated") or prior_home is None else commensurability_score(**{k: home_feat[k] for k in keys}, coefficients=coeffs)
    away_weight = 0.0 if away_feat.get("promoted_or_relegated") or prior_away is None else commensurability_score(**{k: away_feat[k] for k in keys}, coefficients=coeffs)

    def rate(cur: dict[str, Any], prv: dict[str, Any] | None, num_key: str, den_key: str, weight: float) -> tuple[float, float]:
        return blended_rate(float(cur[num_key]), float(cur[den_key]), float(prv[num_key]) if prv else 0.0, float(prv[den_key]) if prv else 0.0, weight, max_prior)

    hgf, hgf_n = rate(current_home, prior_home, "home_gf", "home_matches", home_weight); hga, hga_n = rate(current_home, prior_home, "home_ga", "home_matches", home_weight)
    agf, agf_n = rate(current_away, prior_away, "away_gf", "away_matches", away_weight); aga, aga_n = rate(current_away, prior_away, "away_ga", "away_matches", away_weight)
    league_home = current_state["league_home_goals"]; league_away = current_state["league_away_goals"]; team_prior = params["team_prior_matches"]
    home_gf_rate = _shrunk_rate(hgf * hgf_n, hgf_n, league_home, team_prior); home_ga_rate = _shrunk_rate(hga * hga_n, hga_n, league_away, team_prior)
    away_gf_rate = _shrunk_rate(agf * agf_n, agf_n, league_away, team_prior); away_ga_rate = _shrunk_rate(aga * aga_n, aga_n, league_home, team_prior)
    home_signal = home_gf_rate * away_ga_rate / max(1e-15, league_home); away_signal = away_gf_rate * home_ga_rate / max(1e-15, league_away)
    minimum_mu = params["minimum_goal_mean"]; maximum_mu = params["maximum_goal_mean"]
    home_signal = min(maximum_mu, max(minimum_mu, home_signal)); away_signal = min(maximum_mu, max(minimum_mu, away_signal))
    share = home_signal / max(1e-15, home_signal + away_signal)
    matrix = build_score_matrix(champion_mu_total * share, champion_mu_total * (1.0 - share), current_state["nb_dispersion_k"], params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), low_score_factors(current_state, params))
    return matrix, {"home_borrowing_weight": home_weight, "away_borrowing_weight": away_weight, "max_prior_equivalent_matches": max_prior, "mu_total_preserved": champion_mu_total, "home_share": share}


def compute(competition_id: str, cache: Path) -> dict[str, Any]:
    evidence = load_json(EVIDENCE_CONFIG); route = evidence["competition_mapping"][competition_id]
    if route["validation_route"] not in {"standard", "standard_regular_league_only"}: raise PlatformError(f"stage adapter required: {route['validation_route']}")
    data = load_domain_data(competition_id, cache); indexes = build_season_indexes(data); by_season = indexes["by_season"]
    artifact = load_json(MODEL_ROOT / competition_id / "model.json"); parameter_map = artifact["point_in_time_parameters"]; config = load_config()
    baseline = {}; candidates = {c["id"]: [] for c in CANDIDATES}
    for season, selected in parameter_map.items():
        games = by_season.get(season, []); previous = indexes["previous"].get(season)
        if not games or not previous or previous not in by_season: continue
        params = _merge_parameters(config, selected); prior_rows = [to_match(g, competition_id) for g in by_season[previous]]; prior_cutoff = max(g["date"] for g in by_season[previous]) + timedelta(days=1)
        try: prior_state = fit_current_season_state(prior_rows, prior_cutoff, params, config)
        except PlatformError: prior_state = None
        for target in games:
            history = [to_match(g, competition_id) for g in games if g["date"] < target["date"]]
            try:
                current_state = fit_current_season_state(history, target["date"], params, config)
                base_means = expected_goals(current_state, f"club_{target['home_id']}", f"club_{target['away_id']}", params, config)
                base_matrix = build_score_matrix(float(base_means["mu_home"]), float(base_means["mu_away"]), current_state["nb_dispersion_k"], params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), low_score_factors(current_state, params))
            except PlatformError: continue
            home_feat = team_features(target["home_id"], season, target["date"], indexes, data["transfers"]); away_feat = team_features(target["away_id"], season, target["date"], indexes, data["transfers"])
            if not home_feat.get("feature_complete") or not away_feat.get("feature_complete"): continue
            key = f"{competition_id}:{season}:{target['game_id']}"; block = f"{season}:{target['date'].year}-{target['date'].month:02d}"
            baseline[key] = {"match_key": key, "date": target["date"].date().isoformat(), "season": season, "block_id": block, **score_metrics(base_matrix, target["home_goals"], target["away_goals"])}
            for candidate in CANDIDATES:
                matrix, audit = allocation_only_matrix(current_state, prior_state, target["home_id"], target["away_id"], home_feat, away_feat, candidate, params, config, float(base_means["mu_total"]))
                candidates[candidate["id"]].append({"match_key": key, "date": target["date"].date().isoformat(), "season": season, "block_id": block, "candidate_id": candidate["id"], **score_metrics(matrix, target["home_goals"], target["away_goals"]), **audit})

    baseline_by_season = defaultdict(list)
    for r in baseline.values(): baseline_by_season[r["season"]].append(r)
    maps = {cid: {r["match_key"]: r for r in rows} for cid, rows in candidates.items()}; selected_model = []; selected_base = []; folds = []; seen = set()
    ordered = sorted(baseline_by_season, key=lambda s: min(r["date"] for r in baseline_by_season[s]))
    for season in ordered:
        records = sorted(baseline_by_season[season], key=lambda r: (r["date"], r["match_key"]))
        for wi, dates in enumerate(date_windows(records, 4), start=1):
            start = min(dates); prior = {k for k, r in baseline.items() if r["date"] < start}
            if not prior: continue
            scored = []
            for candidate in CANDIDATES:
                cmap = maps[candidate["id"]]; keys = [k for k in prior if k in cmap]
                if len(keys) < 100: continue
                scored.append((mean(cmap[k]["one_x_two_rps"] for k in keys), mean(cmap[k]["joint_log"] for k in keys), candidate["id"], len(keys)))
            if not scored: continue
            scored.sort(); selected_id = scored[0][2]; cmap = maps[selected_id]; test_keys = [r["match_key"] for r in records if r["date"] in dates and r["match_key"] in cmap]
            if seen.intersection(test_keys): raise PlatformError("overlapping OOF test windows")
            seen.update(test_keys)
            for key in test_keys: selected_model.append(cmap[key]); selected_base.append(baseline[key])
            folds.append({"fold_id": f"{season}:RW{wi}", "season": season, "test_start": start, "test_end": max(dates), "selected_candidate": selected_id, "selection_predictions": scored[0][3], "outer_predictions": len(test_keys)})
    pairs = list(zip(selected_model, selected_base))
    if not pairs: raise PlatformError("no paired allocation-only OOF predictions")
    cis = {metric: bootstrap_diff(pairs, metric) for metric in ("joint_log", "one_x_two_brier", "one_x_two_rps", "total_goals_rps")}
    def avg(rows, key): return mean(r[key] for r in rows)
    coverage = {key: {"current": avg(selected_base, key), "candidate": avg(selected_model, key)} for key in ("top1", "top3", "top5", "score80", "score90")}
    selected_counts = Counter(f["selected_candidate"] for f in folds)
    checks = {
        "minimum_outer_predictions": len(pairs) >= 200,
        "minimum_rolling_time_folds": len(folds) >= 8,
        "one_x_two_rps_ci_improves": cis["one_x_two_rps"]["ci95_upper"] < 0.0,
        "joint_log_noninferior": cis["joint_log"]["ci95_upper"] <= 0.002,
        "one_x_two_brier_noninferior": cis["one_x_two_brier"]["ci95_upper"] <= 0.002,
        "total_goals_rps_preserved": abs(cis["total_goals_rps"]["mean_difference"]) <= 1e-12 and abs(cis["total_goals_rps"]["ci95_upper"]) <= 1e-12,
        "top1_nonworse": coverage["top1"]["candidate"] + 1e-12 >= coverage["top1"]["current"],
        "top3_nonworse": coverage["top3"]["candidate"] + 1e-12 >= coverage["top3"]["current"],
        "top5_nonworse": coverage["top5"]["candidate"] + 1e-12 >= coverage["top5"]["current"],
        "score80_calibrated": 0.76 <= coverage["score80"]["candidate"] <= 0.84,
        "score90_calibrated": 0.86 <= coverage["score90"]["candidate"] <= 0.94,
        "probability_conservation": max(r["probability_sum_error"] for r in selected_model) <= 1e-8,
        "non_identity_selected": sum(v for k, v in selected_counts.items() if k != "identity_no_borrow") > 0,
    }
    status = "ALLOCATION_ONLY_DYNAMIC_STRENGTH_REVIEW_CANDIDATE" if all(checks.values()) else "KEEP_RESEARCH_WEIGHT_0"
    report = {"schema_version": "V4.7.0-dynamic-strength-allocation-only-oof-r1", "generated_at_utc": utc_now(), "competition_id": competition_id, "status": status, "formal_weight": 0, "automatic_promotion": False, "probability_change": False, "total_goals_marginal_policy": "preserve_current_champion_mu_total_and_NB_track", "outer_predictions": len(pairs), "rolling_time_folds": len(folds), "selected_candidate_counts": dict(selected_counts), "confidence_intervals": cis, "coverage": coverage, "checks": checks, "folds": folds, "policy": "Research only. Passing creates a second-stage interaction review candidate; it never changes V4.7 formal probabilities or weights."}
    write_json(REPORT_ROOT / f"{competition_id}.json", report); return report


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--competition", required=True); parser.add_argument("--cache-dir", default="/tmp/football-dynamic-strength-allocation-cache"); args = parser.parse_args()
    try: report = compute(args.competition, Path(args.cache_dir))
    except Exception as exc:
        report = {"schema_version": "V4.7.0-dynamic-strength-allocation-only-oof-r1", "generated_at_utc": utc_now(), "competition_id": args.competition, "status": "FAILED", "formal_weight": 0, "automatic_promotion": False, "probability_change": False, "reason": str(exc)}; write_json(REPORT_ROOT / f"{args.competition}.json", report); print(json.dumps(report, ensure_ascii=False, indent=2)); return 1
    print(json.dumps({"competition_id": args.competition, "status": report["status"], "outer_predictions": report["outer_predictions"], "rolling_time_folds": report["rolling_time_folds"]}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
