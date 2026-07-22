#!/usr/bin/env python3
"""V5.5.36 diagonal Dirichlet 1X2 calibration with KL score-matrix projection.

This is a one-time, leakage-safe challenge experiment. It learns separate probability
slopes and intercepts for home/draw/away from strictly prior outer seasons, selects
regularization on the penultimate completed season, and evaluates once on the same
4,786-match last-complete-season holdout as the formal baseline.

Calibrated 1X2 margins are projected to the unified score matrix with the exact
minimum-KL result-class tilt. CURRENT, formal weights and runtime probabilities are
never changed by this script.
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

import draw_recalibration_kl_v5535 as base
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, derive_score_marginals, load_json, score_matrix_rows

OUT = ROOT / "manifests" / "dirichlet_draw_kl_v5536_status.json"
CLASSES = ("home", "draw", "away")
IDENTITY = (0.0, 0.0, 1.0, 1.0, 1.0)
L2_GRID = (0.1, 1.0, 10.0, 50.0, 200.0)
BLEND_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
EPS = 1e-15


def _features(prob: dict[str, float]) -> dict[str, tuple[float, ...]]:
    lh = math.log(max(EPS, float(prob["home"])))
    ld = math.log(max(EPS, float(prob["draw"])))
    la = math.log(max(EPS, float(prob["away"])))
    return {
        "home": (1.0, 0.0, lh, 0.0, 0.0),
        "draw": (0.0, 1.0, 0.0, ld, 0.0),
        "away": (0.0, 0.0, 0.0, 0.0, la),
    }


def _dot(theta: tuple[float, ...] | list[float], vector: tuple[float, ...]) -> float:
    return sum(float(a) * float(b) for a, b in zip(theta, vector))


def _calibrate(prob: dict[str, float], theta: tuple[float, ...] | list[float]) -> dict[str, float]:
    feats = _features(prob)
    logits = {key: _dot(theta, feats[key]) for key in CLASSES}
    maximum = max(logits.values())
    weights = {key: math.exp(value - maximum) for key, value in logits.items()}
    denominator = sum(weights.values())
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise PlatformError("Dirichlet calibration normalization failed")
    return {key: value / denominator for key, value in weights.items()}


def _objective(rows: list[dict[str, Any]], theta: list[float], l2: float) -> float:
    total = 0.5 * float(l2) * sum((theta[i] - IDENTITY[i]) ** 2 for i in range(5))
    for row in rows:
        q = _calibrate(row["prob"], theta)
        total -= math.log(max(EPS, q[str(row["actual_result"])]))
    return total


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    augmented = [list(matrix[i]) + [float(vector[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) <= 1e-12:
            raise PlatformError("Dirichlet calibration Hessian is singular")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        for j in range(col, n + 1):
            augmented[col][j] /= divisor
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            if factor == 0.0:
                continue
            for j in range(col, n + 1):
                augmented[row][j] -= factor * augmented[col][j]
    return [augmented[i][n] for i in range(n)]


def _fit(rows: list[dict[str, Any]], l2: float) -> dict[str, Any]:
    if not rows:
        raise PlatformError("cannot fit Dirichlet calibration without rows")
    theta = list(IDENTITY)
    objective = _objective(rows, theta, l2)
    converged = False
    iterations = 0
    gradient_norm = None
    for iteration in range(1, 81):
        iterations = iteration
        gradient = [float(l2) * (theta[i] - IDENTITY[i]) for i in range(5)]
        hessian = [[0.0 for _ in range(5)] for _ in range(5)]
        for i in range(5):
            hessian[i][i] = float(l2)
        for row in rows:
            feats = _features(row["prob"])
            q = _calibrate(row["prob"], theta)
            truth = str(row["actual_result"])
            mean_feature = [sum(q[key] * feats[key][i] for key in CLASSES) for i in range(5)]
            for i in range(5):
                gradient[i] += mean_feature[i] - feats[truth][i]
            for i in range(5):
                for j in range(5):
                    second = sum(q[key] * feats[key][i] * feats[key][j] for key in CLASSES)
                    hessian[i][j] += second - mean_feature[i] * mean_feature[j]
        gradient_norm = max(abs(value) for value in gradient)
        if gradient_norm < 1e-8:
            converged = True
            break
        step = _solve(hessian, gradient)
        max_step = max(abs(value) for value in step)
        scale = 1.0
        accepted = False
        for _ in range(30):
            candidate = [theta[i] - scale * step[i] for i in range(5)]
            candidate_objective = _objective(rows, candidate, l2)
            if math.isfinite(candidate_objective) and candidate_objective <= objective + 1e-10:
                theta = candidate
                objective = candidate_objective
                accepted = True
                break
            scale *= 0.5
        if not accepted:
            raise PlatformError("Dirichlet Newton line search failed")
        if max_step * scale < 1e-8:
            converged = True
            break
    if not converged:
        raise PlatformError("Dirichlet optimizer did not converge")
    return {
        "theta": theta,
        "intercept_home": theta[0],
        "intercept_draw": theta[1],
        "slope_home": theta[2],
        "slope_draw": theta[3],
        "slope_away": theta[4],
        "l2": float(l2),
        "iterations": iterations,
        "converged": True,
        "objective": objective,
        "max_abs_gradient": gradient_norm,
        "training_count": len(rows),
    }


def _blend(global_fit: dict[str, Any], domain_fit: dict[str, Any], weight: float) -> tuple[float, ...]:
    global_theta = [float(value) for value in global_fit["theta"]]
    domain_theta = [float(value) for value in domain_fit["theta"]]
    return tuple((1.0 - weight) * global_theta[i] + weight * domain_theta[i] for i in range(5))


def _fit_maps(rows: list[dict[str, Any]], domains: list[str], l2: float, blend: float) -> tuple[dict[str, tuple[float, ...]], dict[str, Any]]:
    global_fit = _fit(rows, l2)
    domain_fits: dict[str, Any] = {}
    maps: dict[str, tuple[float, ...]] = {}
    for cid in domains:
        subset = [row for row in rows if row["competition_id"] == cid]
        domain_fit = _fit(subset, l2)
        domain_fits[cid] = domain_fit
        maps[cid] = _blend(global_fit, domain_fit, blend)
    return maps, {"global_fit": global_fit, "domain_fits": domain_fits, "l2": l2, "blend": blend}


def _metrics(rows: list[dict[str, Any]], maps: dict[str, tuple[float, ...]] | None) -> dict[str, Any]:
    count = hits = draw_hits = 0
    brier = rps = logloss = 0.0
    predicted = Counter()
    actual = Counter()
    by_competition: dict[str, Counter] = {}
    for row in rows:
        q = row["prob"] if maps is None else _calibrate(row["prob"], maps[row["competition_id"]])
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
        truth_vec = {"home": (1.0, 0.0, 0.0), "draw": (0.0, 1.0, 0.0), "away": (0.0, 0.0, 1.0)}[truth]
        c1 = q["home"] - truth_vec[0]
        c2 = q["home"] + q["draw"] - truth_vec[0] - truth_vec[1]
        rps += (c1 * c1 + c2 * c2) / 2.0
        logloss -= math.log(max(EPS, float(q[truth])))
        bucket = by_competition.setdefault(row["competition_id"], Counter())
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
                "count": int(bucket["count"]),
                "hits": int(bucket["hits"]),
                "accuracy": bucket["hits"] / bucket["count"] if bucket["count"] else None,
                "predicted_home": int(bucket["predicted_home"]),
                "predicted_draw": int(bucket["predicted_draw"]),
                "predicted_away": int(bucket["predicted_away"]),
                "actual_draw": int(bucket["actual_draw"]),
            }
            for cid, bucket in sorted(by_competition.items())
        },
    }


def _project(matrix: list[dict[str, Any]], target_1x2: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = derive_score_marginals(matrix)
    factors = {key: float(target_1x2[key]) / max(EPS, float(raw["1x2"][key])) for key in CLASSES}
    projected = []
    total = 0.0
    for home, away, probability in score_matrix_rows(matrix):
        result = base._result_for_score(home, away)
        value = float(probability) * factors[result]
        projected.append({"home_goals": home, "away_goals": away, "probability": value})
        total += value
    if total <= 0.0 or not math.isfinite(total):
        raise PlatformError("Dirichlet KL projection normalization failed")
    for row in projected:
        row["probability"] = float(row["probability"]) / total
    margins = derive_score_marginals(projected)
    margin_residual = max(abs(float(margins["1x2"][key]) - float(target_1x2[key])) for key in CLASSES)
    sum_residual = abs(float(margins["probability_sum"]) - 1.0)
    if margin_residual > 1e-10 or sum_residual > 1e-10:
        raise PlatformError("Dirichlet KL projection residual exceeded tolerance")
    return projected, {"max_1x2_margin_residual": margin_residual, "probability_sum_residual": sum_residual}


def main() -> int:
    formal = load_json(base.FORMAL_STATUS)
    domains = sorted((formal.get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 domains, got {len(domains)}")
    cache: dict[str, dict[str, list[dict[str, Any]]]] = {}
    failures: dict[str, str] = {}
    roles: dict[str, Any] = {}
    for cid in domains:
        try:
            report = load_json(base.REPORT_ROOT / f"{cid}.json")
            seasons = _completed_outer_seasons_last_complete_only(report)[-4:]
            if len(seasons) != 4:
                raise PlatformError(f"need four completed outer seasons for {cid}")
            roles[cid] = {"fit_seasons": seasons[:2], "selection_validation_season": seasons[2], "untouched_holdout_season": seasons[3]}
            cache[cid] = {
                season: base._build_season_rows(cid, season, keep_matrix=(season == seasons[3]))
                for season in seasons
            }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if failures:
        payload = {"schema_version": "V5.5.36-dirichlet-draw-kl-r1", "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "status": "FAIL_DATA_BUILD", "failures": failures, "formal_weight_change": False, "runtime_probability_change": False}
        atomic_write_json(OUT, payload)
        return 1

    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    for cid in domains:
        role = roles[cid]
        for season in role["fit_seasons"]:
            train.extend(cache[cid][season])
        validation.extend(cache[cid][role["selection_validation_season"]])
        holdout.extend(cache[cid][role["untouched_holdout_season"]])
    if len(holdout) != 4786:
        raise PlatformError(f"holdout scope mismatch: expected 4786, got {len(holdout)}")

    baseline_validation = _metrics(validation, None)
    candidates = []
    for l2 in L2_GRID:
        for blend in BLEND_GRID:
            try:
                maps, fit_audit = _fit_maps(train, domains, l2, blend)
                metrics = _metrics(validation, maps)
                proper_safe = (
                    float(metrics["mean_log_loss"]) <= float(baseline_validation["mean_log_loss"]) + 1e-12
                    and float(metrics["mean_brier"]) <= float(baseline_validation["mean_brier"]) + 1e-12
                    and float(metrics["mean_rps"]) <= float(baseline_validation["mean_rps"]) + 1e-12
                )
                candidates.append({"l2": l2, "blend": blend, "validation": metrics, "proper_scores_nonworse": proper_safe, "fit_audit": fit_audit, "status": "OK"})
            except Exception as exc:
                candidates.append({"l2": l2, "blend": blend, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}", "proper_scores_nonworse": False})
    eligible = [item for item in candidates if item.get("proper_scores_nonworse") and item.get("validation")]
    eligible.sort(key=lambda item: (-float(item["validation"]["accuracy"]), float(item["validation"]["mean_log_loss"]), float(item["validation"]["mean_rps"]), float(item["blend"]), float(item["l2"])))

    baseline_holdout = _metrics(holdout, None)
    if not eligible:
        result = {"status": "NO_PROPER_SCORE_SAFE_CANDIDATE", "challenge_gate_passed": False, "challenge_gate_fail_reasons": ["no validation-safe Dirichlet candidate"]}
    else:
        selected = eligible[0]
        refit_rows = train + validation
        maps, refit_audit = _fit_maps(refit_rows, domains, float(selected["l2"]), float(selected["blend"]))
        calibrated_holdout = _metrics(holdout, maps)
        max_margin_residual = 0.0
        max_sum_residual = 0.0
        joint_log_delta = 0.0
        total_goal_l1 = 0.0
        projection_count = 0
        for row in holdout:
            matrix = row.get("matrix")
            if not isinstance(matrix, list) or not matrix:
                raise PlatformError("holdout score matrix missing")
            target = _calibrate(row["prob"], maps[row["competition_id"]])
            projected, audit = _project(matrix, target)
            max_margin_residual = max(max_margin_residual, float(audit["max_1x2_margin_residual"]))
            max_sum_residual = max(max_sum_residual, float(audit["probability_sum_residual"]))
            raw_actual = projected_actual = 0.0
            raw_total = derive_score_marginals(matrix)["total_goals"]
            projected_total = derive_score_marginals(projected)["total_goals"]
            total_goal_l1 += sum(abs(float(raw_total.get(key, 0.0)) - float(projected_total.get(key, 0.0))) for key in set(raw_total) | set(projected_total))
            for home, away, probability in score_matrix_rows(matrix):
                if home == row["actual_home_goals"] and away == row["actual_away_goals"]:
                    raw_actual += float(probability)
            for home, away, probability in score_matrix_rows(projected):
                if home == row["actual_home_goals"] and away == row["actual_away_goals"]:
                    projected_actual += float(probability)
            joint_log_delta += -math.log(max(EPS, projected_actual)) + math.log(max(EPS, raw_actual))
            projection_count += 1
        gain_pp = 100.0 * (float(calibrated_holdout["accuracy"]) - float(baseline_holdout["accuracy"]))
        fail_reasons = []
        if gain_pp < 1.0:
            fail_reasons.append("holdout accuracy gain below 1 percentage point")
        if float(calibrated_holdout["mean_log_loss"]) >= float(baseline_holdout["mean_log_loss"]):
            fail_reasons.append("holdout log loss did not improve")
        if float(calibrated_holdout["mean_brier"]) >= float(baseline_holdout["mean_brier"]):
            fail_reasons.append("holdout Brier did not improve")
        if float(calibrated_holdout["mean_rps"]) >= float(baseline_holdout["mean_rps"]):
            fail_reasons.append("holdout RPS did not improve")
        if int(calibrated_holdout["draw_prediction_count"]) < 100:
            fail_reasons.append("draw prediction count below 100")
        if projection_count and joint_log_delta / projection_count >= 0.0:
            fail_reasons.append("joint score log loss did not improve")
        if max_margin_residual > 1e-10 or max_sum_residual > 1e-10:
            fail_reasons.append("KL projection audit failed")
        result = {
            "status": "CHALLENGE_GATE_PASS" if not fail_reasons else "CHALLENGE_GATE_FAIL",
            "selected_candidate": {"l2": selected["l2"], "blend": selected["blend"], "selection_validation": selected["validation"], "selection_fit_audit": selected["fit_audit"], "refit_audit": refit_audit},
            "holdout": calibrated_holdout,
            "accuracy_gain_pp": gain_pp,
            "mean_joint_log_loss_delta_calibrated_minus_raw": joint_log_delta / projection_count if projection_count else None,
            "mean_total_goals_distribution_l1_change": total_goal_l1 / projection_count if projection_count else None,
            "kl_projection_audit": {"prior": "OOF-temperature-calibrated unified score matrix", "objective": "minimum KL projection to learned Dirichlet 1X2 margins", "constraint_form": "three 1X2 class margins plus probability conservation", "convergence_status": "CONVERGED_CLOSED_FORM", "iterations": 1, "projection_count": projection_count, "max_1x2_margin_residual": max_margin_residual, "max_probability_sum_residual": max_sum_residual},
            "challenge_gate_passed": not fail_reasons,
            "challenge_gate_fail_reasons": fail_reasons,
        }

    payload = {
        "schema_version": "V5.5.36-dirichlet-draw-kl-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_count_requested": 17,
        "competition_count_completed": 17,
        "failures": {},
        "season_roles": roles,
        "method": {"calibrator": "diagonal Dirichlet calibration", "parameters": ["home intercept", "draw intercept", "home log-probability slope", "draw log-probability slope", "away log-probability slope"], "selection": "earliest two outer seasons fit; penultimate season candidate selection; last complete season untouched holdout", "candidate_l2": list(L2_GRID), "candidate_global_domain_blends": list(BLEND_GRID), "manual_draw_probability": False, "historical_odds_used": False, "target_holdout_used_for_selection": False, "matrix_projection": "closed-form minimum-KL result-class tilt"},
        "row_counts": {"initial_fit": len(train), "selection_validation": len(validation), "untouched_holdout": len(holdout)},
        "baseline": {"selection_validation": baseline_validation, "untouched_holdout": baseline_holdout},
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "candidate_summary": [{"l2": item.get("l2"), "blend": item.get("blend"), "status": item.get("status"), "error": item.get("error"), "proper_scores_nonworse": item.get("proper_scores_nonworse"), "validation_accuracy": (item.get("validation") or {}).get("accuracy"), "validation_draw_prediction_count": (item.get("validation") or {}).get("draw_prediction_count"), "validation_log_loss": (item.get("validation") or {}).get("mean_log_loss"), "validation_brier": (item.get("validation") or {}).get("mean_brier"), "validation_rps": (item.get("validation") or {}).get("mean_rps")} for item in candidates],
        "result": result,
        "governance": {"research_challenge_only": True, "formal_weight_change": False, "runtime_probability_change": False, "current_rule_change": False, "automatic_promotion": False, "promotion_requires_competition_level_rolling_validation": True},
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({"status": payload["status"], "result_status": result.get("status"), "baseline_accuracy": baseline_holdout.get("accuracy"), "calibrated_accuracy": (result.get("holdout") or {}).get("accuracy"), "accuracy_gain_pp": result.get("accuracy_gain_pp"), "baseline_draw_predictions": baseline_holdout.get("draw_prediction_count"), "calibrated_draw_predictions": (result.get("holdout") or {}).get("draw_prediction_count"), "challenge_gate_passed": result.get("challenge_gate_passed"), "fail_reasons": result.get("challenge_gate_fail_reasons")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
