#!/usr/bin/env python3
"""Rolling OOF research for an online model-residual draw challenger.

Unlike the rejected raw team draw-rate challenger, this version uses only residuals
from earlier eligible predictions in the SAME season: actual_draw - base_P(draw).
Home-team home residual and away-team away residual are shrunk by the formal
team_prior_matches parameter. A logistic residual layer is trained on strictly
completed earlier seasons and evaluated on the next completed outer season.

The candidate operates after replay-safe OOF matrix calibration and tilts only
score-diagonal cells inside each fixed total T, preserving the full P(T) marginal.
It is unregistered research, formal weight 0, with no production effect.
"""
from __future__ import annotations

import json
import math
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

import screen_draw_residual_challenger_v470_strict as strict_entry
import validate_draw_residual_rolling_oof_v470 as rolling
from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from football_v460_engine import current_season_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, derive_score_marginals, load_json, normalize_team_token, read_processed_matches, score_matrix_rows

base = strict_entry.base
TARGETS = ("FRA_Ligue1", "NED_Eredivisie", "SWE_Allsvenskan")
OUT = ROOT / "manifests" / "draw_online_residual_rolling_oof_v470_status.json"


def _completed_target_seasons(report: dict[str, Any]) -> list[str]:
    # Reuse the completed-season cap from the strict rolling validator wrapper.
    import validate_draw_residual_rolling_oof_v470_strict as completed
    return completed._completed_target_outer_seasons(report)


def _expected_total(matrix) -> float:
    return sum((h + a) * p for h, a, p in score_matrix_rows(matrix))


def _online_season_rows(cid: str, report: dict[str, Any], all_matches, season: str) -> dict[str, Any]:
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"selected parameters missing for {cid} {season}")
    temperature, calibration_mode = _target_season_temperature(cid, season)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    prior = max(1e-9, float(params.get("team_prior_matches", 8.0)))
    home_resid_sum = defaultdict(float)
    home_resid_count = defaultdict(int)
    away_resid_sum = defaultdict(float)
    away_resid_count = defaultdict(int)
    overall_resid_sum = defaultdict(float)
    overall_resid_count = defaultdict(int)
    output = []
    skipped = 0
    for match in matches:
        try:
            baseline = _predict_from_loaded_matches(
                all_matches,
                match.home_team,
                match.away_team,
                match.date,
                season,
                params,
            )
        except PlatformError:
            skipped += 1
            continue
        if abs(temperature - 1.0) > 1e-15:
            baseline = temperature_scale_matrix(baseline, temperature)
        one = derive_score_marginals(baseline)["1x2"]
        home_key = normalize_team_token(match.home_team)
        away_key = normalize_team_token(match.away_team)
        home_resid = home_resid_sum[home_key] / (home_resid_count[home_key] + prior)
        away_resid = away_resid_sum[away_key] / (away_resid_count[away_key] + prior)
        home_all_resid = overall_resid_sum[home_key] / (overall_resid_count[home_key] + 2.0 * prior)
        away_all_resid = overall_resid_sum[away_key] / (overall_resid_count[away_key] + 2.0 * prior)
        balance = 1.0 - abs(float(one["home"]) - float(one["away"]))
        pair_venue_resid = 0.5 * (home_resid + away_resid)
        pair_all_resid = 0.5 * (home_all_resid + away_all_resid)
        features = [
            base._logit(float(one["draw"])),
            home_resid,
            away_resid,
            pair_venue_resid,
            pair_all_resid,
            balance,
            _expected_total(baseline),
            pair_venue_resid * balance,
        ]
        actual_draw = 1 if match.home_goals == match.away_goals else 0
        output.append({
            "features": features,
            "label": actual_draw,
            "match": match,
            "baseline": baseline,
        })
        residual = actual_draw - float(one["draw"])
        home_resid_sum[home_key] += residual
        home_resid_count[home_key] += 1
        away_resid_sum[away_key] += residual
        away_resid_count[away_key] += 1
        overall_resid_sum[home_key] += residual
        overall_resid_count[home_key] += 1
        overall_resid_sum[away_key] += residual
        overall_resid_count[away_key] += 1
    return {
        "season": season,
        "rows": output,
        "skipped": skipped,
        "temperature": temperature,
        "calibration_mode": calibration_mode,
    }


def validate_domain(cid: str, seed_offset: int) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    all_matches = read_processed_matches(cid)
    target_seasons = _completed_target_seasons(report)
    if not target_seasons:
        raise PlatformError(f"no completed rolling target seasons for {cid}")
    # Precompute each completed outer season once. Every feature row uses only
    # earlier eligible predictions in its own season.
    all_outer = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and season not in all_outer:
            all_outer.append(season)
    max_target_year = max(rolling._season_year(season) for season in target_seasons)
    completed_outer = sorted(
        [season for season in all_outer if rolling._season_year(season) <= max_target_year],
        key=rolling._season_year,
    )
    season_cache = {season: _online_season_rows(cid, report, all_matches, season) for season in completed_outer}

    outer_reports = []
    pooled_metric_rows = []
    max_total_residual = 0.0
    for outer_index, target_season in enumerate(target_seasons):
        target_year = rolling._season_year(target_season)
        training_seasons = [season for season in completed_outer if rolling._season_year(season) < target_year]
        train_rows = [row for season in training_seasons for row in season_cache[season]["rows"]]
        if len(train_rows) < 100:
            continue
        model = base._fit_logistic(
            [row["features"] for row in train_rows],
            [row["label"] for row in train_rows],
        )
        season_metric_rows = []
        for item in season_cache[target_season]["rows"]:
            target_draw = base._predict_logistic(model, item["features"])
            candidate, _, residual = strict_entry._safe_tilt_diagonal_to_target(item["baseline"], target_draw)
            max_total_residual = max(max_total_residual, residual)
            metric_row = rolling._metric_row(item["baseline"], candidate, item["match"])
            metric_row["target_season"] = target_season
            season_metric_rows.append(metric_row)
            pooled_metric_rows.append(metric_row)
        if not season_metric_rows:
            continue
        summary = rolling._aggregate(season_metric_rows, seed_offset + outer_index * 100)
        outer_reports.append({
            "target_season": target_season,
            "training_seasons": training_seasons,
            "training_rows": len(train_rows),
            "training_draw_rate": mean(row["label"] for row in train_rows),
            "logistic_converged": model.get("converged"),
            "feature_definition": [
                "logit_base_draw",
                "home_same_season_home_model_residual",
                "away_same_season_away_model_residual",
                "pair_venue_model_residual",
                "pair_all_venue_model_residual",
                "base_home_away_balance",
                "base_expected_total",
                "venue_residual_x_balance",
            ],
            "oof_calibration": {
                "temperature": season_cache[target_season]["temperature"],
                "mode": season_cache[target_season]["calibration_mode"],
            },
            **summary,
        })
    if not pooled_metric_rows:
        raise PlatformError(f"no online residual rolling rows for {cid}")
    pooled = rolling._aggregate(pooled_metric_rows, seed_offset + 900)
    ci = pooled["paired_block_bootstrap"]
    seasons_draw_brier_improve = sum(1 for item in outer_reports if item["metrics"]["draw_brier"]["candidate_minus_baseline"] < 0)
    seasons_joint_noncat = sum(1 for item in outer_reports if item["metrics"]["joint_log"]["candidate_minus_baseline"] <= 0.005)
    checks = {
        "multiple_outer_seasons": len(outer_reports) >= 2,
        "strict_prior_training_each_fold": all(all(rolling._season_year(season) < rolling._season_year(item["target_season"]) for season in item["training_seasons"]) for item in outer_reports),
        "same_season_online_residual_only": True,
        "draw_brier_mean_improves": pooled["metrics"]["draw_brier"]["candidate_minus_baseline"] < 0,
        "draw_brier_ci_upper_below_zero": ci["draw_brier"]["ci95_upper"] < 0,
        "one_x_two_brier_ci_upper_noninferior": ci["one_x_two_brier"]["ci95_upper"] <= 0.001,
        "one_x_two_rps_ci_upper_noninferior": ci["one_x_two_rps"]["ci95_upper"] <= 0.001,
        "joint_log_ci_upper_noninferior": ci["joint_log"]["ci95_upper"] <= 0.005,
        "majority_seasons_draw_brier_improve": seasons_draw_brier_improve >= math.ceil(len(outer_reports) / 2),
        "all_seasons_joint_log_noncatastrophic": seasons_joint_noncat == len(outer_reports),
        "total_marginal_preserved": max_total_residual <= 1e-10,
    }
    status = "ROLLING_OOF_RESEARCH_CANDIDATE" if all(checks.values()) else "KEEP_FORMAL_WEIGHT_0"
    return {
        "competition_id": cid,
        "status": status,
        "outer_season_count": len(outer_reports),
        "pooled_prediction_count": len(pooled_metric_rows),
        "max_total_marginal_residual": max_total_residual,
        "pooled": pooled,
        "outer_seasons": outer_reports,
        "checks": checks,
        "formal_weight": 0,
        "automatic_promotion": False,
        "probability_change": False,
        "governance_reason": "Online model-residual draw challenger is unregistered research under CURRENT V4.7.0.",
    }


def main() -> int:
    reports = {}
    failures = {}
    for index, cid in enumerate(TARGETS):
        try:
            reports[cid] = validate_domain(cid, 50000 + index * 10000)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    candidates = [cid for cid, report in reports.items() if report["status"] == "ROLLING_OOF_RESEARCH_CANDIDATE"]
    payload = {
        "schema_version": "V4.7.0-draw-online-residual-rolling-oof-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(TARGETS) and not failures else "PARTIAL",
        "competition_count_requested": len(TARGETS),
        "competition_count_completed": len(reports),
        "rolling_oof_research_candidates": candidates,
        "reports": reports,
        "failures": failures,
        "governance": {
            "registered_in_current": False,
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
            "formal_use_requires_complete_current_upgrade": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "rolling_oof_research_candidates": candidates, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
