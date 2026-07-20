#!/usr/bin/env python3
"""17-domain rolling OOF research for conditional draw structure P(D=0 | T, X).

This challenger targets the diagnosed draw-discrimination defect at the conditional
score-allocation layer rather than at the marginal 1X2 layer. For each eligible
prediction, and only for explicit even totals T in {2,4,6}, it estimates whether the
conditional diagonal cell D=0 should receive more or less mass. Every total-specific
vector is renormalized, preserving the complete formal direct-total marginal P(T)
exactly. T=0 and tail/unsupported totals are left unchanged.

For each target completed outer season, the binary calibration model is trained only
on strictly earlier completed outer seasons. The identity anchor is the baseline
conditional diagonal probability, so the regularized model shrinks toward no change.
Research only; formal weight 0 and no production mutation under CURRENT V4.7.0.
"""
from __future__ import annotations

import json
import math
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

OUT = ROOT / "manifests" / "conditional_draw_by_total_rolling_oof_v470_status.json"
TOTALS = (2, 4, 6)
MIN_TRAINING_ROWS = 100
L2_TO_IDENTITY = 2.0
IDENTITY_BETA = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
MAX_ITER = 80


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _logit(value: float) -> float:
    value = min(1.0 - 1e-9, max(1e-9, value))
    return math.log(value / (1.0 - value))


def _total_structure(matrix, total: int) -> dict[str, float] | None:
    cells = [(h, a, p) for h, a, p in score_matrix_rows(matrix) if h + a == total]
    total_mass = sum(p for _, _, p in cells)
    if total_mass <= 0.0:
        return None
    diagonal_mass = sum(p for h, a, p in cells if h == a)
    conditional_draw = diagonal_mass / total_mass
    conditional_abs_difference = sum(abs(h - a) * p for h, a, p in cells) / total_mass
    return {
        "total_mass": total_mass,
        "conditional_draw": conditional_draw,
        "conditional_abs_difference": conditional_abs_difference,
    }


def _features(matrix, total: int) -> list[float] | None:
    structure = _total_structure(matrix, total)
    if structure is None:
        return None
    one = derive_score_marginals(matrix)["1x2"]
    side_gap = abs(float(one["home"]) - float(one["away"]))
    normalized_abs_difference = structure["conditional_abs_difference"] / max(1.0, float(total))
    normalized_total = float(total) / 6.0
    return [
        1.0,
        _logit(structure["conditional_draw"]),
        side_gap,
        normalized_abs_difference,
        normalized_total,
        side_gap * normalized_total,
    ]


def _loss_gradient_hessian(beta, rows):
    d = len(beta)
    gradient = [0.0] * d
    hessian = [[0.0] * d for _ in range(d)]
    loss = 0.0
    for row in rows:
        x = row["features"]
        y = float(row["label"])
        eta = sum(beta[j] * x[j] for j in range(d))
        p = _sigmoid(eta)
        loss -= y * math.log(max(1e-15, p)) + (1.0 - y) * math.log(max(1e-15, 1.0 - p))
        weight = max(1e-10, p * (1.0 - p))
        for j in range(d):
            gradient[j] += (p - y) * x[j]
            for k in range(d):
                hessian[j][k] += weight * x[j] * x[k]
    n = max(1, len(rows))
    loss /= n
    gradient = [value / n for value in gradient]
    hessian = [[value / n for value in row] for row in hessian]
    penalty = L2_TO_IDENTITY / n
    for j in range(d):
        deviation = beta[j] - IDENTITY_BETA[j]
        loss += 0.5 * penalty * deviation * deviation
        gradient[j] += penalty * deviation
        hessian[j][j] += penalty + 1e-10
    return loss, gradient, hessian


def _solve(matrix, vector):
    n = len(vector)
    aug = [list(matrix[i]) + [float(vector[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-12:
            aug[col][col] += 1e-8
            pivot = max(range(col, n), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-14:
            raise PlatformError("conditional draw Hessian singular")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= scale
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            if abs(factor) <= 1e-18:
                continue
            for j in range(col, n + 1):
                aug[row][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def _fit(rows):
    if len(rows) < MIN_TRAINING_ROWS:
        raise PlatformError("insufficient conditional draw training rows")
    beta = list(IDENTITY_BETA)
    loss, gradient, hessian = _loss_gradient_hessian(beta, rows)
    converged = False
    for iteration in range(MAX_ITER):
        grad_norm = math.sqrt(sum(value * value for value in gradient))
        if grad_norm < 1e-7:
            converged = True
            break
        direction = _solve(hessian, gradient)
        descent = sum(gradient[j] * direction[j] for j in range(len(beta)))
        if not math.isfinite(descent) or descent <= 0.0:
            raise PlatformError("conditional draw Newton direction invalid")
        step = 1.0
        accepted = False
        for _ in range(28):
            candidate = [beta[j] - step * direction[j] for j in range(len(beta))]
            c_loss, c_gradient, c_hessian = _loss_gradient_hessian(candidate, rows)
            if c_loss <= loss - 1e-4 * step * descent:
                beta, loss, gradient, hessian = candidate, c_loss, c_gradient, c_hessian
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break
        if max(abs(step * value) for value in direction) < 1e-8:
            converged = math.sqrt(sum(value * value for value in gradient)) < 1e-6
            break
    final_gradient_norm = math.sqrt(sum(value * value for value in gradient))
    if final_gradient_norm < 1e-6:
        converged = True
    return {
        "beta": beta,
        "training_rows": len(rows),
        "loss": loss,
        "iterations": iteration + 1,
        "converged": converged,
        "final_gradient_norm": final_gradient_norm,
        "optimizer": "damped_newton_irls",
        "l2_to_identity": L2_TO_IDENTITY,
        "identity_anchor": IDENTITY_BETA,
    }


def _predict(model, features):
    return _sigmoid(sum(model["beta"][j] * features[j] for j in range(len(features))))


def _transform(matrix, model):
    rows = [(h, a, float(p)) for h, a, p in score_matrix_rows(matrix)]
    by_total: dict[int, list[tuple[int, int, float]]] = {}
    for h, a, p in rows:
        by_total.setdefault(h + a, []).append((h, a, p))
    output = []
    max_total_residual = 0.0
    applied = {}
    for total, cells in by_total.items():
        total_mass = sum(p for _, _, p in cells)
        if total not in TOTALS or total_mass <= 0.0:
            output.extend({"home_goals": h, "away_goals": a, "probability": p} for h, a, p in cells)
            continue
        features = _features(matrix, total)
        if features is None:
            output.extend({"home_goals": h, "away_goals": a, "probability": p} for h, a, p in cells)
            continue
        target_conditional_draw = min(1.0 - 1e-9, max(1e-9, _predict(model, features)))
        diag_indices = [index for index, (h, a, _) in enumerate(cells) if h == a]
        if len(diag_indices) != 1:
            output.extend({"home_goals": h, "away_goals": a, "probability": p} for h, a, p in cells)
            continue
        diag_index = diag_indices[0]
        base_diag_mass = cells[diag_index][2]
        base_conditional_draw = base_diag_mass / total_mass
        off_mass = total_mass - base_diag_mass
        transformed = []
        for index, (h, a, p) in enumerate(cells):
            if index == diag_index:
                new_p = total_mass * target_conditional_draw
            elif off_mass <= 0.0:
                new_p = 0.0
            else:
                new_p = p * total_mass * (1.0 - target_conditional_draw) / off_mass
            transformed.append((h, a, new_p))
        max_total_residual = max(max_total_residual, abs(sum(p for _, _, p in transformed) - total_mass))
        output.extend({"home_goals": h, "away_goals": a, "probability": p} for h, a, p in transformed)
        applied[str(total)] = {
            "base_conditional_draw": base_conditional_draw,
            "target_conditional_draw": target_conditional_draw,
        }
    return output, {"max_total_marginal_residual": max_total_residual, "applied_totals": applied}


def _completed_seasons(cid, report):
    max_year = rolling._season_year(_requested_last_complete_season(cid))
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and season not in seasons and rolling._season_year(season) <= max_year:
            seasons.append(season)
    seasons.sort(key=rolling._season_year)
    return seasons


def _season_rows(cid, report, all_matches, season):
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"missing parameters for {cid} {season}")
    temperature, mode = _target_season_temperature(cid, season)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    output = []
    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
        except PlatformError:
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        output.append({"match": match, "baseline": matrix})
    return {"rows": output, "temperature": temperature, "mode": mode}


def _training_examples(season_cache, training_seasons):
    examples = []
    for season in training_seasons:
        for item in season_cache[season]["rows"]:
            actual_total = int(item["match"].home_goals) + int(item["match"].away_goals)
            if actual_total not in TOTALS:
                continue
            features = _features(item["baseline"], actual_total)
            if features is None:
                continue
            examples.append({
                "features": features,
                "label": 1 if item["match"].home_goals == item["match"].away_goals else 0,
            })
    return examples


def validate_domain(cid, seed_offset):
    report = load_json(REPORT_ROOT / f"{cid}.json")
    all_matches = read_processed_matches(cid)
    seasons = _completed_seasons(cid, report)
    if len(seasons) < 2:
        raise PlatformError(f"insufficient completed seasons for {cid}")
    cache = {season: _season_rows(cid, report, all_matches, season) for season in seasons}
    outer_reports = []
    pooled_rows = []
    skipped_training_folds = []
    nonconverged_folds = []
    max_total_residual = 0.0
    for outer_index, target_season in enumerate(seasons[1:]):
        target_year = rolling._season_year(target_season)
        training_seasons = [season for season in seasons if rolling._season_year(season) < target_year]
        training = _training_examples(cache, training_seasons)
        if len(training) < MIN_TRAINING_ROWS:
            skipped_training_folds.append({"target_season": target_season, "training_seasons": training_seasons, "training_rows": len(training)})
            continue
        model = _fit(training)
        if not model["converged"]:
            nonconverged_folds.append({"target_season": target_season, "training_rows": len(training), "final_gradient_norm": model["final_gradient_norm"]})
            continue
        season_metric_rows = []
        for item in cache[target_season]["rows"]:
            candidate, audit = _transform(item["baseline"], model)
            max_total_residual = max(max_total_residual, audit["max_total_marginal_residual"])
            metric = rolling._metric_row(item["baseline"], candidate, item["match"])
            metric["target_season"] = target_season
            season_metric_rows.append(metric)
            pooled_rows.append(metric)
        if not season_metric_rows:
            continue
        outer_reports.append({
            "target_season": target_season,
            "training_seasons": training_seasons,
            "training_rows": len(training),
            "conditional_draw_model": model,
            "oof_matrix_temperature": cache[target_season]["temperature"],
            "oof_matrix_mode": cache[target_season]["mode"],
            **rolling._aggregate(season_metric_rows, seed_offset + outer_index * 100),
        })
    if not pooled_rows:
        raise PlatformError(f"no conditional draw rolling OOF rows for {cid}")
    pooled = rolling._aggregate(pooled_rows, seed_offset + 900)
    ci = pooled["paired_block_bootstrap"]
    seasons_draw_improve = sum(1 for item in outer_reports if item["metrics"]["draw_brier"]["candidate_minus_baseline"] < 0)
    seasons_joint_noncat = sum(1 for item in outer_reports if item["metrics"]["joint_log"]["candidate_minus_baseline"] <= 0.005)
    checks = {
        "multiple_outer_seasons": len(outer_reports) >= 2,
        "strict_prior_training_each_fold": all(all(rolling._season_year(season) < rolling._season_year(item["target_season"]) for season in item["training_seasons"]) for item in outer_reports),
        "minimum_training_rows_each_participating_fold": all(item["training_rows"] >= MIN_TRAINING_ROWS for item in outer_reports),
        "all_models_converged": not nonconverged_folds and all(item["conditional_draw_model"]["converged"] for item in outer_reports),
        "total_marginal_preserved": max_total_residual <= 1e-10,
        "draw_brier_mean_improves": pooled["metrics"]["draw_brier"]["candidate_minus_baseline"] < 0,
        "draw_brier_ci_upper_below_zero": ci["draw_brier"]["ci95_upper"] < 0,
        "draw_auc_improves": pooled["metrics"]["draw_auc"]["candidate_minus_baseline"] > 0,
        "one_x_two_brier_ci_upper_noninferior": ci["one_x_two_brier"]["ci95_upper"] <= 0.001,
        "one_x_two_rps_ci_upper_noninferior": ci["one_x_two_rps"]["ci95_upper"] <= 0.001,
        "joint_log_ci_upper_noninferior": ci["joint_log"]["ci95_upper"] <= 0.005,
        "score_top1_noninferior": pooled["metrics"]["score_top1_accuracy"]["candidate_minus_baseline"] >= -0.005,
        "score_top3_noninferior": pooled["metrics"]["score_top3_accuracy"]["candidate_minus_baseline"] >= -0.005,
        "majority_seasons_draw_brier_improve": seasons_draw_improve >= math.ceil(len(outer_reports) / 2),
        "all_seasons_joint_log_noncatastrophic": seasons_joint_noncat == len(outer_reports),
    }
    status = "ROLLING_OOF_RESEARCH_CANDIDATE" if all(checks.values()) else "KEEP_FORMAL_WEIGHT_0"
    return {
        "competition_id": cid,
        "status": status,
        "outer_season_count": len(outer_reports),
        "pooled_prediction_count": len(pooled_rows),
        "skipped_insufficient_training_folds": skipped_training_folds,
        "nonconverged_model_folds": nonconverged_folds,
        "max_total_marginal_residual": max_total_residual,
        "pooled": pooled,
        "outer_seasons": outer_reports,
        "checks": checks,
        "formal_weight": 0,
        "automatic_promotion": False,
        "probability_change": False,
        "governance_reason": "Conditional draw-by-total D|T research challenger has no formal execution right under CURRENT V4.7.0 until explicit registration/promotion governance is satisfied.",
    }


def main():
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports = {}
    failures = {}
    for index, cid in enumerate(competitions):
        try:
            reports[cid] = validate_domain(cid, 200000 + index * 10000)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    candidates = [cid for cid, report in reports.items() if report["status"] == "ROLLING_OOF_RESEARCH_CANDIDATE"]
    payload = {
        "schema_version": "V4.7.0-conditional-draw-by-total-rolling-oof-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "rolling_oof_research_candidates": candidates,
        "reports": reports,
        "failures": failures,
        "governance": {
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
            "direct_total_marginal_preserved_by_design": True,
            "formal_use_requires_current_governance_review": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "rolling_oof_research_candidates": candidates, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
