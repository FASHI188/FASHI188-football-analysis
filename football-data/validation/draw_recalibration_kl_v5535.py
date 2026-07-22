#!/usr/bin/env python3
"""Leakage-safe V5.5.35 draw recalibration and KL matrix projection experiment.

Goal
----
Repair the formal core's severe draw under-selection without manually assigning a
draw rate and without breaking the single unified score matrix.

Method
------
1. Rebuild chronological outer-fold predictions with the frozen formal engine.
2. Fit multinomial intercept recalibration on strictly earlier outer seasons only.
   The optimizer minimizes penalized 1X2 log loss; away is the reference class.
3. Select global/domain blending and L2 strength on the penultimate outer season.
4. Refit with every season strictly before the untouched last-complete-season holdout.
5. Project the calibrated 1X2 margins back to the joint score matrix by the exact
   minimum-KL result-class tilt q(h,a) proportional to p(h,a)*exp(alpha_result).

The challenge layer does not mutate CURRENT, formal weights or runtime probabilities.
It reports accuracy and proper scores and fails closed on convergence, probability
conservation or marginal-consistency errors.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
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
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    atomic_write_json,
    derive_score_marginals,
    load_json,
    read_processed_matches,
    score_matrix_rows,
)

OUT = ROOT / "manifests" / "draw_recalibration_kl_v5535_status.json"
CLASSES = ("home", "draw", "away")
L2_GRID = (0.0, 0.1, 1.0, 10.0, 50.0, 200.0)
BLEND_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
EPS = 1e-15


def _season_key(season: str) -> tuple[int, str]:
    text = str(season)
    try:
        return int(text[:4]), text
    except ValueError:
        return 0, text


def _completed_outer_seasons(report: dict[str, Any]) -> list[str]:
    seasons: list[str] = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and season not in seasons:
            seasons.append(season)
    seasons.sort(key=_season_key)
    return seasons


def _softmax_with_offsets(prob: dict[str, float], alpha: tuple[float, float]) -> dict[str, float]:
    logits = {
        "home": math.log(max(EPS, float(prob["home"]))) + float(alpha[0]),
        "draw": math.log(max(EPS, float(prob["draw"]))) + float(alpha[1]),
        "away": math.log(max(EPS, float(prob["away"]))),
    }
    maximum = max(logits.values())
    weights = {key: math.exp(value - maximum) for key, value in logits.items()}
    denominator = sum(weights.values())
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise PlatformError("multinomial intercept calibration normalization failed")
    return {key: value / denominator for key, value in weights.items()}


def _objective(rows: list[dict[str, Any]], alpha: tuple[float, float], l2: float) -> float:
    total = 0.5 * float(l2) * (alpha[0] * alpha[0] + alpha[1] * alpha[1])
    for row in rows:
        calibrated = _softmax_with_offsets(row["prob"], alpha)
        total -= math.log(max(EPS, calibrated[str(row["actual_result"])]))
    return total


def _fit_intercepts(rows: list[dict[str, Any]], l2: float) -> dict[str, Any]:
    if not rows:
        raise PlatformError("cannot fit draw recalibration without rows")
    alpha = [0.0, 0.0]
    converged = False
    iterations = 0
    last_objective = _objective(rows, (alpha[0], alpha[1]), l2)

    for iteration in range(1, 101):
        iterations = iteration
        g0 = float(l2) * alpha[0]
        g1 = float(l2) * alpha[1]
        h00 = float(l2)
        h11 = float(l2)
        h01 = 0.0
        for row in rows:
            q = _softmax_with_offsets(row["prob"], (alpha[0], alpha[1]))
            y0 = 1.0 if row["actual_result"] == "home" else 0.0
            y1 = 1.0 if row["actual_result"] == "draw" else 0.0
            q0 = q["home"]
            q1 = q["draw"]
            g0 += q0 - y0
            g1 += q1 - y1
            h00 += q0 * (1.0 - q0)
            h11 += q1 * (1.0 - q1)
            h01 -= q0 * q1

        determinant = h00 * h11 - h01 * h01
        if not math.isfinite(determinant) or determinant <= 1e-12:
            raise PlatformError("draw recalibration Hessian is singular")
        step0 = (h11 * g0 - h01 * g1) / determinant
        step1 = (-h01 * g0 + h00 * g1) / determinant
        max_step = max(abs(step0), abs(step1))
        if max_step < 1e-10:
            converged = True
            break

        accepted = False
        scale = 1.0
        for _ in range(25):
            candidate = (alpha[0] - scale * step0, alpha[1] - scale * step1)
            objective = _objective(rows, candidate, l2)
            if math.isfinite(objective) and objective <= last_objective + 1e-12:
                alpha[0], alpha[1] = candidate
                last_objective = objective
                accepted = True
                break
            scale *= 0.5
        if not accepted:
            raise PlatformError("draw recalibration Newton line search failed")
        if max_step * scale < 1e-9:
            converged = True
            break

    if not converged:
        raise PlatformError("draw recalibration optimizer did not converge")
    gradient_norm = 0.0
    # Finite and compact convergence residual for audit.
    for key_index, key in enumerate(("home", "draw")):
        grad = float(l2) * alpha[key_index]
        for row in rows:
            q = _softmax_with_offsets(row["prob"], (alpha[0], alpha[1]))
            grad += q[key] - (1.0 if row["actual_result"] == key else 0.0)
        gradient_norm = max(gradient_norm, abs(grad))
    return {
        "alpha_home": alpha[0],
        "alpha_draw": alpha[1],
        "alpha_away": 0.0,
        "l2": float(l2),
        "iterations": iterations,
        "converged": True,
        "objective": last_objective,
        "max_abs_gradient": gradient_norm,
        "training_count": len(rows),
    }


def _blend(global_fit: dict[str, Any], domain_fit: dict[str, Any], blend: float) -> tuple[float, float]:
    weight = float(blend)
    return (
        (1.0 - weight) * float(global_fit["alpha_home"]) + weight * float(domain_fit["alpha_home"]),
        (1.0 - weight) * float(global_fit["alpha_draw"]) + weight * float(domain_fit["alpha_draw"]),
    )


def _result_for_score(home: int, away: int) -> str:
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def _project_matrix(matrix: list[dict[str, Any]], alpha: tuple[float, float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = derive_score_marginals(matrix)
    calibrated_1x2 = _softmax_with_offsets(raw["1x2"], alpha)
    factors = {
        key: calibrated_1x2[key] / max(EPS, float(raw["1x2"][key]))
        for key in CLASSES
    }
    projected: list[dict[str, Any]] = []
    total = 0.0
    for home, away, probability in score_matrix_rows(matrix):
        value = float(probability) * factors[_result_for_score(home, away)]
        projected.append({"home_goals": home, "away_goals": away, "probability": value})
        total += value
    if total <= 0.0 or not math.isfinite(total):
        raise PlatformError("KL projection normalization failed")
    for row in projected:
        row["probability"] = float(row["probability"]) / total
    projected_marginals = derive_score_marginals(projected)
    residual = max(
        abs(float(projected_marginals["1x2"][key]) - float(calibrated_1x2[key]))
        for key in CLASSES
    )
    conservation = abs(float(projected_marginals["probability_sum"]) - 1.0)
    if residual > 1e-10 or conservation > 1e-10:
        raise PlatformError(
            f"KL projection audit failed: marginal_residual={residual} conservation={conservation}"
        )
    return projected, {
        "objective": "minimize KL(q||p) subject to calibrated 1X2 margins",
        "constraint_form": "sum of score cells in each result class equals calibrated 1X2 marginal",
        "closed_form": "q_cell proportional to p_cell * exp(alpha_result)",
        "converged": True,
        "iterations": 1,
        "probability_sum_residual": conservation,
        "max_1x2_margin_residual": residual,
        "raw_1x2": raw["1x2"],
        "calibrated_1x2": calibrated_1x2,
        "result_class_factors": factors,
    }


def _one_x_two_metrics(rows: list[dict[str, Any]], alpha_by_domain: dict[str, tuple[float, float]] | None) -> dict[str, Any]:
    count = hits = 0
    brier = rps = logloss = 0.0
    predicted = Counter()
    actual = Counter()
    draw_hits = 0
    by_domain: dict[str, Counter] = {}
    for row in rows:
        alpha = (0.0, 0.0) if alpha_by_domain is None else alpha_by_domain[row["competition_id"]]
        q = _softmax_with_offsets(row["prob"], alpha)
        pick = max(CLASSES, key=lambda key: float(q[key]))
        truth = str(row["actual_result"])
        hit = int(pick == truth)
        count += 1
        hits += hit
        predicted[pick] += 1
        actual[truth] += 1
        if pick == "draw" and truth == "draw":
            draw_hits += 1
        brier += sum((float(q[key]) - (1.0 if truth == key else 0.0)) ** 2 for key in CLASSES)
        actual_vec = {"home": (1.0, 0.0, 0.0), "draw": (0.0, 1.0, 0.0), "away": (0.0, 0.0, 1.0)}[truth]
        c1 = q["home"] - actual_vec[0]
        c2 = (q["home"] + q["draw"]) - (actual_vec[0] + actual_vec[1])
        rps += (c1 * c1 + c2 * c2) / 2.0
        logloss -= math.log(max(EPS, q[truth]))
        bucket = by_domain.setdefault(row["competition_id"], Counter())
        bucket["count"] += 1
        bucket["hits"] += hit
        bucket[f"predicted_{pick}"] += 1
        bucket[f"actual_{truth}"] += 1
    return {
        "count": count,
        "hit_count": hits,
        "accuracy": hits / count if count else None,
        "mean_brier": brier / count if count else None,
        "mean_rps": rps / count if count else None,
        "mean_log_loss": logloss / count if count else None,
        "predicted_direction_counts": dict(predicted),
        "actual_direction_counts": dict(actual),
        "draw_prediction_count": int(predicted["draw"]),
        "draw_hit_count": draw_hits,
        "draw_precision": draw_hits / predicted["draw"] if predicted["draw"] else None,
        "by_competition": {
            cid: {
                "count": int(values["count"]),
                "hits": int(values["hits"]),
                "accuracy": values["hits"] / values["count"] if values["count"] else None,
                "predicted_home": int(values["predicted_home"]),
                "predicted_draw": int(values["predicted_draw"]),
                "predicted_away": int(values["predicted_away"]),
                "actual_draw": int(values["actual_draw"]),
            }
            for cid, values in sorted(by_domain.items())
        },
    }


def _build_season_rows(cid: str, season: str, keep_matrix: bool) -> list[dict[str, Any]]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"invalid selected parameters for {cid} {season}")
    all_matches = read_processed_matches(cid)
    matches = sorted(
        [match for match in all_matches if str(match.season) == season],
        key=lambda match: (match.date, match.home_team, match.away_team),
    )
    temperature, mode = _target_season_temperature(cid, season)
    rows: list[dict[str, Any]] = []
    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(
                all_matches,
                match.home_team,
                match.away_team,
                match.date,
                season,
                params,
            )
        except PlatformError:
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        margins = derive_score_marginals(matrix)
        rows.append({
            "competition_id": cid,
            "season": season,
            "prob": {key: float(margins["1x2"][key]) for key in CLASSES},
            "actual_result": _actual_result(int(match.home_goals), int(match.away_goals)),
            "actual_home_goals": int(match.home_goals),
            "actual_away_goals": int(match.away_goals),
            "matrix": matrix if keep_matrix else None,
            "temperature": temperature,
            "calibration_mode": mode,
        })
    return rows


def _candidate_alpha_maps(
    train_rows: list[dict[str, Any]],
    domains: list[str],
    l2: float,
    blend: float,
) -> tuple[dict[str, tuple[float, float]], dict[str, Any]]:
    global_fit = _fit_intercepts(train_rows, l2)
    maps: dict[str, tuple[float, float]] = {}
    domain_fits: dict[str, Any] = {}
    for cid in domains:
        subset = [row for row in train_rows if row["competition_id"] == cid]
        fit = _fit_intercepts(subset, l2)
        domain_fits[cid] = fit
        maps[cid] = _blend(global_fit, fit, blend)
    return maps, {"global_fit": global_fit, "domain_fits": domain_fits, "blend": blend, "l2": l2}


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    domains = sorted((formal.get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 formal domains, found {len(domains)}")

    rows_by_domain_season: dict[str, dict[str, list[dict[str, Any]]]] = {}
    failures: dict[str, str] = {}
    for cid in domains:
        try:
            report = load_json(REPORT_ROOT / f"{cid}.json")
            seasons = _completed_outer_seasons(report)
            if len(seasons) < 4:
                raise PlatformError(f"need at least four outer seasons for {cid}")
            selected = seasons[-4:]
            rows_by_domain_season[cid] = {
                season: _build_season_rows(cid, season, keep_matrix=(season == selected[-1]))
                for season in selected
            }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if failures:
        payload = {
            "schema_version": "V5.5.35-draw-recalibration-kl-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "FAIL_DATA_BUILD",
            "failures": failures,
            "formal_weight_change": False,
            "probability_change": False,
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    train_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    holdout_rows: list[dict[str, Any]] = []
    season_roles: dict[str, Any] = {}
    for cid in domains:
        seasons = sorted(rows_by_domain_season[cid], key=_season_key)
        season_roles[cid] = {
            "fit_seasons": seasons[:2],
            "selection_validation_season": seasons[2],
            "untouched_holdout_season": seasons[3],
        }
        for season in seasons[:2]:
            train_rows.extend(rows_by_domain_season[cid][season])
        validation_rows.extend(rows_by_domain_season[cid][seasons[2]])
        holdout_rows.extend(rows_by_domain_season[cid][seasons[3]])

    baseline_validation = _one_x_two_metrics(validation_rows, None)
    candidates: list[dict[str, Any]] = []
    for l2 in L2_GRID:
        for blend in BLEND_GRID:
            try:
                alpha_map, fit_audit = _candidate_alpha_maps(train_rows, domains, l2, blend)
                metrics = _one_x_two_metrics(validation_rows, alpha_map)
                proper_scores_nonworse = (
                    float(metrics["mean_log_loss"]) <= float(baseline_validation["mean_log_loss"]) + 1e-12
                    and float(metrics["mean_brier"]) <= float(baseline_validation["mean_brier"]) + 1e-12
                    and float(metrics["mean_rps"]) <= float(baseline_validation["mean_rps"]) + 1e-12
                )
                candidates.append({
                    "l2": l2,
                    "blend": blend,
                    "validation": metrics,
                    "proper_scores_nonworse": proper_scores_nonworse,
                    "fit_audit": fit_audit,
                })
            except Exception as exc:
                candidates.append({
                    "l2": l2,
                    "blend": blend,
                    "status": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                    "proper_scores_nonworse": False,
                })

    eligible = [item for item in candidates if item.get("proper_scores_nonworse") and item.get("validation")]
    if not eligible:
        selected = None
    else:
        eligible.sort(
            key=lambda item: (
                -float(item["validation"]["accuracy"]),
                float(item["validation"]["mean_log_loss"]),
                float(item["validation"]["mean_brier"]),
                float(item["blend"]),
                float(item["l2"]),
            )
        )
        selected = eligible[0]

    baseline_holdout = _one_x_two_metrics(holdout_rows, None)
    result: dict[str, Any]
    if selected is None:
        result = {
            "status": "NO_PROPER_SCORE_SAFE_CANDIDATE",
            "selected_candidate": None,
            "holdout": None,
            "challenge_gate_passed": False,
            "challenge_gate_fail_reasons": ["no candidate improved or preserved all validation proper scores"],
        }
    else:
        refit_rows = train_rows + validation_rows
        alpha_map, refit_audit = _candidate_alpha_maps(
            refit_rows,
            domains,
            float(selected["l2"]),
            float(selected["blend"]),
        )
        calibrated_holdout = _one_x_two_metrics(holdout_rows, alpha_map)
        max_margin_residual = 0.0
        max_sum_residual = 0.0
        joint_log_delta = 0.0
        total_goal_probability_l1_sum = 0.0
        projection_count = 0
        for row in holdout_rows:
            matrix = row.get("matrix")
            if not isinstance(matrix, list) or not matrix:
                raise PlatformError("holdout unified score matrix missing")
            projected, audit = _project_matrix(matrix, alpha_map[row["competition_id"]])
            max_margin_residual = max(max_margin_residual, float(audit["max_1x2_margin_residual"]))
            max_sum_residual = max(max_sum_residual, float(audit["probability_sum_residual"]))
            raw_actual = 0.0
            projected_actual = 0.0
            raw_total = derive_score_marginals(matrix)["total_goals"]
            projected_total = derive_score_marginals(projected)["total_goals"]
            total_keys = set(raw_total) | set(projected_total)
            total_goal_probability_l1_sum += sum(
                abs(float(raw_total.get(key, 0.0)) - float(projected_total.get(key, 0.0)))
                for key in total_keys
            )
            for home, away, probability in score_matrix_rows(matrix):
                if home == row["actual_home_goals"] and away == row["actual_away_goals"]:
                    raw_actual += float(probability)
            for home, away, probability in score_matrix_rows(projected):
                if home == row["actual_home_goals"] and away == row["actual_away_goals"]:
                    projected_actual += float(probability)
            joint_log_delta += -math.log(max(EPS, projected_actual)) + math.log(max(EPS, raw_actual))
            projection_count += 1

        accuracy_gain_pp = 100.0 * (
            float(calibrated_holdout["accuracy"]) - float(baseline_holdout["accuracy"])
        )
        fail_reasons: list[str] = []
        if accuracy_gain_pp < 1.0:
            fail_reasons.append("holdout 1X2 accuracy gain below 1 percentage point")
        if float(calibrated_holdout["mean_log_loss"]) >= float(baseline_holdout["mean_log_loss"]):
            fail_reasons.append("holdout 1X2 log loss did not improve")
        if float(calibrated_holdout["mean_brier"]) >= float(baseline_holdout["mean_brier"]):
            fail_reasons.append("holdout Brier score did not improve")
        if float(calibrated_holdout["mean_rps"]) >= float(baseline_holdout["mean_rps"]):
            fail_reasons.append("holdout RPS did not improve")
        if int(calibrated_holdout["draw_prediction_count"]) < 50:
            fail_reasons.append("draw prediction count remains below 50")
        if max_margin_residual > 1e-10 or max_sum_residual > 1e-10:
            fail_reasons.append("KL projection audit residual exceeded tolerance")
        if projection_count and joint_log_delta / projection_count >= 0.0:
            fail_reasons.append("mean joint score log loss did not improve")
        result = {
            "status": "CHALLENGE_GATE_PASS" if not fail_reasons else "CHALLENGE_GATE_FAIL",
            "selected_candidate": {
                "l2": selected["l2"],
                "blend": selected["blend"],
                "selection_validation": selected["validation"],
                "selection_fit_audit": selected["fit_audit"],
                "refit_audit": refit_audit,
            },
            "holdout": calibrated_holdout,
            "accuracy_gain_pp": accuracy_gain_pp,
            "mean_joint_log_loss_delta_calibrated_minus_raw": joint_log_delta / projection_count if projection_count else None,
            "mean_total_goals_distribution_l1_change": total_goal_probability_l1_sum / projection_count if projection_count else None,
            "kl_projection_audit": {
                "prior": "OOF-temperature-calibrated formal unified score matrix",
                "market_constraints": [],
                "objective": "minimum KL result-class tilt to learned 1X2 margins",
                "constraint_form": "three 1X2 class margins plus probability conservation",
                "closed_form_solution": True,
                "convergence_status": "CONVERGED_CLOSED_FORM",
                "iterations": 1,
                "max_1x2_margin_residual": max_margin_residual,
                "max_probability_sum_residual": max_sum_residual,
                "projection_count": projection_count,
            },
            "challenge_gate_passed": not fail_reasons,
            "challenge_gate_fail_reasons": fail_reasons,
        }

    payload = {
        "schema_version": "V5.5.35-draw-recalibration-kl-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "competition_count_requested": len(domains),
        "competition_count_completed": len(domains) - len(failures),
        "failures": failures,
        "season_roles": season_roles,
        "method": {
            "calibrator": "penalized multinomial intercept recalibration",
            "reference_class": "away",
            "hyperparameter_selection": "fit on earliest two outer seasons; select on penultimate season only",
            "holdout": "last complete season untouched until final evaluation",
            "candidate_l2": list(L2_GRID),
            "candidate_global_domain_blends": list(BLEND_GRID),
            "matrix_projection": "closed-form minimum-KL result-class tilt",
            "manual_draw_probability": False,
            "historical_odds_used": False,
            "target_holdout_used_for_selection": False,
        },
        "row_counts": {
            "initial_fit": len(train_rows),
            "selection_validation": len(validation_rows),
            "untouched_holdout": len(holdout_rows),
        },
        "baseline": {
            "selection_validation": baseline_validation,
            "untouched_holdout": baseline_holdout,
        },
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "candidate_summary": [
            {
                "l2": item.get("l2"),
                "blend": item.get("blend"),
                "proper_scores_nonworse": item.get("proper_scores_nonworse"),
                "status": item.get("status", "OK"),
                "error": item.get("error"),
                "validation_accuracy": (item.get("validation") or {}).get("accuracy"),
                "validation_draw_prediction_count": (item.get("validation") or {}).get("draw_prediction_count"),
                "validation_log_loss": (item.get("validation") or {}).get("mean_log_loss"),
                "validation_brier": (item.get("validation") or {}).get("mean_brier"),
                "validation_rps": (item.get("validation") or {}).get("mean_rps"),
            }
            for item in candidates
        ],
        "result": result,
        "governance": {
            "research_challenge_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "promotion_requires_separate_competition_level_rolling_validation": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": payload["status"],
        "result_status": result["status"],
        "baseline_holdout_accuracy": baseline_holdout["accuracy"],
        "calibrated_holdout_accuracy": (result.get("holdout") or {}).get("accuracy"),
        "accuracy_gain_pp": result.get("accuracy_gain_pp"),
        "baseline_draw_predictions": baseline_holdout["draw_prediction_count"],
        "calibrated_draw_predictions": (result.get("holdout") or {}).get("draw_prediction_count"),
        "challenge_gate_passed": result.get("challenge_gate_passed"),
        "fail_reasons": result.get("challenge_gate_fail_reasons"),
    }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
