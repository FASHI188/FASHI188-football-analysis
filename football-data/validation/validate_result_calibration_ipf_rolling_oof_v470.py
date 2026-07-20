#!/usr/bin/env python3
"""17-domain rolling OOF research: multinomial 1X2 calibration + KL/IPF score projection.

Motivation: the formal core has near-correct marginal draw frequency but weak draw
fixture discrimination. This challenger does not hand-boost draws. For every target
outer season it fits a domain-specific multinomial calibration map using only
strictly earlier completed outer seasons. Inputs are log base 1X2 probabilities.
The calibrated 1X2 target is projected back to the unified score matrix using
iterative proportional fitting while preserving every total-goals marginal P(T).

Research only: unregistered in CURRENT V4.7.0, formal weight 0, no runtime mutation.
"""
from __future__ import annotations

import json
import math
import random
import sys
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

import validate_draw_residual_rolling_oof_v470 as rolling
from backtest_last_complete_season_all_domains_v470 import (
    FORMAL_STATUS,
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, derive_score_marginals, load_json, read_processed_matches, score_matrix_rows

OUT = ROOT / "manifests" / "result_calibration_ipf_rolling_oof_v470_status.json"
IDENTITY_BETA = [0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 1.0, -1.0]
L2_TO_IDENTITY = 2.0
MAX_FIT_ITER = 800
MAX_IPF_ITER = 1000
IPF_TOLERANCE = 1e-10


def _softmax(beta: list[float], features: list[float]) -> dict[str, float]:
    zh = sum(beta[j] * features[j] for j in range(4))
    zd = sum(beta[4 + j] * features[j] for j in range(4))
    maximum = max(0.0, zh, zd)
    eh = math.exp(zh - maximum)
    ed = math.exp(zd - maximum)
    ea = math.exp(-maximum)
    total = eh + ed + ea
    return {"home": eh / total, "draw": ed / total, "away": ea / total}


def _features(one: dict[str, float]) -> list[float]:
    return [
        1.0,
        math.log(max(1e-12, float(one["home"]))),
        math.log(max(1e-12, float(one["draw"]))),
        math.log(max(1e-12, float(one["away"]))),
    ]


def _loss_and_gradient(beta: list[float], rows: list[dict[str, Any]]) -> tuple[float, list[float]]:
    gradient = [0.0] * 8
    loss = 0.0
    for row in rows:
        probabilities = _softmax(beta, row["features"])
        actual = row["actual"]
        loss -= math.log(max(1e-15, probabilities[actual]))
        yh = 1.0 if actual == "home" else 0.0
        yd = 1.0 if actual == "draw" else 0.0
        for j in range(4):
            gradient[j] += (probabilities["home"] - yh) * row["features"][j]
            gradient[4 + j] += (probabilities["draw"] - yd) * row["features"][j]
    n = max(1, len(rows))
    loss /= n
    gradient = [value / n for value in gradient]
    penalty_scale = L2_TO_IDENTITY / n
    for j in range(8):
        deviation = beta[j] - IDENTITY_BETA[j]
        loss += 0.5 * penalty_scale * deviation * deviation
        gradient[j] += penalty_scale * deviation
    return loss, gradient


def _fit_multinomial(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 100:
        raise PlatformError("insufficient multinomial calibration training rows")
    beta = list(IDENTITY_BETA)
    loss, gradient = _loss_and_gradient(beta, rows)
    converged = False
    for iteration in range(MAX_FIT_ITER):
        grad_norm = math.sqrt(sum(value * value for value in gradient))
        if grad_norm < 1e-7:
            converged = True
            break
        step = 1.0
        accepted = False
        for _ in range(24):
            candidate = [beta[j] - step * gradient[j] for j in range(8)]
            candidate_loss, candidate_gradient = _loss_and_gradient(candidate, rows)
            if candidate_loss <= loss - 1e-4 * step * grad_norm * grad_norm:
                beta = candidate
                loss = candidate_loss
                gradient = candidate_gradient
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break
    return {
        "beta": beta,
        "training_rows": len(rows),
        "loss": loss,
        "iterations": iteration + 1,
        "converged": converged or math.sqrt(sum(value * value for value in gradient)) < 1e-6,
        "l2_to_identity": L2_TO_IDENTITY,
        "identity_anchor": IDENTITY_BETA,
    }


def _outcome(home: int, away: int) -> str:
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def _ipf_project(matrix, target: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [(h, a, float(p)) for h, a, p in score_matrix_rows(matrix)]
    total_targets: dict[int, float] = {}
    for h, a, p in rows:
        total_targets[h + a] = total_targets.get(h + a, 0.0) + p
    q = [p for _, _, p in rows]
    converged = False
    outcome_residual = float("inf")
    total_residual = float("inf")
    for iteration in range(MAX_IPF_ITER):
        current = {"home": 0.0, "draw": 0.0, "away": 0.0}
        for (h, a, _), value in zip(rows, q):
            current[_outcome(h, a)] += value
        factors = {}
        for key in current:
            if current[key] <= 0.0:
                if target[key] > 1e-12:
                    raise PlatformError("IPF outcome support infeasible")
                factors[key] = 1.0
            else:
                factors[key] = target[key] / current[key]
        q = [value * factors[_outcome(h, a)] for (h, a, _), value in zip(rows, q)]

        sums_by_total: dict[int, float] = {}
        for (h, a, _), value in zip(rows, q):
            sums_by_total[h + a] = sums_by_total.get(h + a, 0.0) + value
        scaled = []
        for (h, a, _), value in zip(rows, q):
            denom = sums_by_total[h + a]
            target_total = total_targets[h + a]
            if target_total <= 0.0:
                scaled.append(0.0)
            elif denom <= 0.0:
                raise PlatformError("IPF total support infeasible")
            else:
                scaled.append(value * target_total / denom)
        q = scaled

        current = {"home": 0.0, "draw": 0.0, "away": 0.0}
        sums_by_total = {}
        for (h, a, _), value in zip(rows, q):
            current[_outcome(h, a)] += value
            sums_by_total[h + a] = sums_by_total.get(h + a, 0.0) + value
        outcome_residual = max(abs(current[key] - target[key]) for key in current)
        total_residual = max(abs(sums_by_total[total] - target_total) for total, target_total in total_targets.items())
        if outcome_residual <= IPF_TOLERANCE and total_residual <= IPF_TOLERANCE:
            converged = True
            break
    output = [
        {"home_goals": h, "away_goals": a, "probability": value}
        for (h, a, _), value in zip(rows, q)
    ]
    probability_sum = sum(q)
    return output, {
        "converged": converged,
        "iterations": iteration + 1,
        "max_outcome_residual": outcome_residual,
        "max_total_marginal_residual": total_residual,
        "probability_sum_residual": abs(probability_sum - 1.0),
        "target_one_x_two": target,
    }


def _season_baseline_rows(cid: str, report: dict[str, Any], all_matches, season: str) -> dict[str, Any]:
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"missing selected parameters for {cid} {season}")
    temperature, mode = _target_season_temperature(cid, season)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    output = []
    skipped = 0
    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
        except PlatformError:
            skipped += 1
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        one = derive_score_marginals(matrix)["1x2"]
        actual = "home" if match.home_goals > match.away_goals else "away" if match.home_goals < match.away_goals else "draw"
        output.append({
            "features": _features(one),
            "actual": actual,
            "match": match,
            "baseline": matrix,
        })
    return {"season": season, "rows": output, "skipped": skipped, "temperature": temperature, "mode": mode}


def _completed_outer_seasons(cid: str, report: dict[str, Any]) -> list[str]:
    max_year = rolling._season_year(_requested_last_complete_season(cid))
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and season not in seasons and rolling._season_year(season) <= max_year:
            seasons.append(season)
    seasons.sort(key=rolling._season_year)
    return seasons


def validate_domain(cid: str, seed_offset: int) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    all_matches = read_processed_matches(cid)
    seasons = _completed_outer_seasons(cid, report)
    if len(seasons) < 2:
        raise PlatformError(f"insufficient completed outer seasons for {cid}")
    cache = {season: _season_baseline_rows(cid, report, all_matches, season) for season in seasons}
    outer_reports = []
    pooled_rows = []
    max_outcome_residual = 0.0
    max_total_residual = 0.0
    max_sum_residual = 0.0
    nonconverged = 0
    for outer_index, target_season in enumerate(seasons[1:]):
        target_year = rolling._season_year(target_season)
        training_seasons = [season for season in seasons if rolling._season_year(season) < target_year]
        train_rows = [row for season in training_seasons for row in cache[season]["rows"]]
        model = _fit_multinomial(train_rows)
        season_rows = []
        for item in cache[target_season]["rows"]:
            target_one = _softmax(model["beta"], item["features"])
            candidate, audit = _ipf_project(item["baseline"], target_one)
            max_outcome_residual = max(max_outcome_residual, float(audit["max_outcome_residual"]))
            max_total_residual = max(max_total_residual, float(audit["max_total_marginal_residual"]))
            max_sum_residual = max(max_sum_residual, float(audit["probability_sum_residual"]))
            if not audit["converged"]:
                nonconverged += 1
                continue
            metric_row = rolling._metric_row(item["baseline"], candidate, item["match"])
            metric_row["target_season"] = target_season
            season_rows.append(metric_row)
            pooled_rows.append(metric_row)
        if not season_rows:
            continue
        summary = rolling._aggregate(season_rows, seed_offset + outer_index * 100)
        outer_reports.append({
            "target_season": target_season,
            "training_seasons": training_seasons,
            "training_rows": len(train_rows),
            "calibration_model": model,
            "oof_matrix_temperature": cache[target_season]["temperature"],
            "oof_matrix_mode": cache[target_season]["mode"],
            **summary,
        })
    if not pooled_rows:
        raise PlatformError(f"no converged rolling OOF projection rows for {cid}")
    pooled = rolling._aggregate(pooled_rows, seed_offset + 900)
    ci = pooled["paired_block_bootstrap"]
    seasons_brier_improve = sum(1 for item in outer_reports if item["metrics"]["one_x_two_brier"]["candidate_minus_baseline"] < 0)
    seasons_draw_improve = sum(1 for item in outer_reports if item["metrics"]["draw_brier"]["candidate_minus_baseline"] < 0)
    seasons_joint_noncat = sum(1 for item in outer_reports if item["metrics"]["joint_log"]["candidate_minus_baseline"] <= 0.005)
    checks = {
        "multiple_outer_seasons": len(outer_reports) >= 2,
        "strict_prior_training_each_fold": all(all(rolling._season_year(season) < rolling._season_year(item["target_season"]) for season in item["training_seasons"]) for item in outer_reports),
        "all_projection_rows_converged": nonconverged == 0,
        "outcome_constraint_residual": max_outcome_residual <= 1e-8,
        "total_marginal_preserved": max_total_residual <= 1e-10,
        "probability_sum_preserved": max_sum_residual <= 1e-10,
        "one_x_two_brier_mean_improves": pooled["metrics"]["one_x_two_brier"]["candidate_minus_baseline"] < 0,
        "one_x_two_brier_ci_upper_below_zero": ci["one_x_two_brier"]["ci95_upper"] < 0,
        "one_x_two_rps_ci_upper_noninferior": ci["one_x_two_rps"]["ci95_upper"] <= 0.001,
        "draw_brier_ci_upper_noninferior": ci["draw_brier"]["ci95_upper"] <= 0.001,
        "joint_log_ci_upper_noninferior": ci["joint_log"]["ci95_upper"] <= 0.005,
        "majority_seasons_one_x_two_brier_improve": seasons_brier_improve >= math.ceil(len(outer_reports) / 2),
        "majority_seasons_draw_brier_improve": seasons_draw_improve >= math.ceil(len(outer_reports) / 2),
        "all_seasons_joint_log_noncatastrophic": seasons_joint_noncat == len(outer_reports),
    }
    status = "ROLLING_OOF_RESEARCH_CANDIDATE" if all(checks.values()) else "KEEP_FORMAL_WEIGHT_0"
    return {
        "competition_id": cid,
        "status": status,
        "outer_season_count": len(outer_reports),
        "pooled_prediction_count": len(pooled_rows),
        "projection_audit": {
            "nonconverged_rows": nonconverged,
            "max_outcome_residual": max_outcome_residual,
            "max_total_marginal_residual": max_total_residual,
            "max_probability_sum_residual": max_sum_residual,
        },
        "pooled": pooled,
        "outer_seasons": outer_reports,
        "checks": checks,
        "formal_weight": 0,
        "automatic_promotion": False,
        "probability_change": False,
        "governance_reason": "Unregistered multinomial result-calibration/IPF challenger under CURRENT V4.7.0.",
    }


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports = {}
    failures = {}
    for index, cid in enumerate(competitions):
        try:
            reports[cid] = validate_domain(cid, 100000 + index * 10000)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    candidates = [cid for cid, report in reports.items() if report["status"] == "ROLLING_OOF_RESEARCH_CANDIDATE"]
    payload = {
        "schema_version": "V4.7.0-result-calibration-ipf-rolling-oof-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
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
