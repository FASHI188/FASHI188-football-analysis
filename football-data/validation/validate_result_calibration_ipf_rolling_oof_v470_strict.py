#!/usr/bin/env python3
"""Strict V2 entrypoint for 17-domain result-calibration/IPF rolling OOF.

Corrections versus the initial research runner:
- multinomial calibration is fit with damped Newton/IRLS rather than slow first-order descent;
- every participating calibration fold must converge;
- target folds with fewer than 100 strictly-prior PIT training rows are skipped and audited, never fitted with a relaxed sample gate;
- IPF convergence, 1X2 constraint residuals, every P(T) marginal and probability sum remain hard gates.

This remains unregistered research under CURRENT V4.7.0: formal weight 0 and no
production probability mutation.
"""
from __future__ import annotations

import math

import validate_result_calibration_ipf_rolling_oof_v470 as base


NEWTON_MAX_ITER = 80
GRAD_TOL = 1e-7
STEP_TOL = 1e-8
MIN_TRAINING_ROWS = 100


def _solve_system(matrix, vector):
    n = len(vector)
    augmented = [list(matrix[i]) + [float(vector[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            # Deterministic diagonal jitter for near-singular Hessians.
            augmented[col][col] += 1e-8
            pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-14:
            raise base.PlatformError("multinomial Newton Hessian is singular")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        scale = augmented[col][col]
        for j in range(col, n + 1):
            augmented[col][j] /= scale
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            if abs(factor) <= 1e-18:
                continue
            for j in range(col, n + 1):
                augmented[row][j] -= factor * augmented[col][j]
    return [augmented[i][n] for i in range(n)]


def _loss_gradient_hessian(beta, rows):
    d = 8
    gradient = [0.0] * d
    hessian = [[0.0] * d for _ in range(d)]
    loss = 0.0
    for row in rows:
        x = row["features"]
        probabilities = base._softmax(beta, x)
        ph = float(probabilities["home"])
        pd = float(probabilities["draw"])
        actual = row["actual"]
        loss -= math.log(max(1e-15, float(probabilities[actual])))
        yh = 1.0 if actual == "home" else 0.0
        yd = 1.0 if actual == "draw" else 0.0
        for j in range(4):
            gradient[j] += (ph - yh) * x[j]
            gradient[4 + j] += (pd - yd) * x[j]
            for k in range(4):
                xx = x[j] * x[k]
                hessian[j][k] += ph * (1.0 - ph) * xx
                hessian[4 + j][4 + k] += pd * (1.0 - pd) * xx
                cross = -ph * pd * xx
                hessian[j][4 + k] += cross
                hessian[4 + j][k] += cross
    n = max(1, len(rows))
    loss /= n
    gradient = [value / n for value in gradient]
    hessian = [[value / n for value in row] for row in hessian]
    penalty_scale = base.L2_TO_IDENTITY / n
    for j in range(d):
        deviation = beta[j] - base.IDENTITY_BETA[j]
        loss += 0.5 * penalty_scale * deviation * deviation
        gradient[j] += penalty_scale * deviation
        hessian[j][j] += penalty_scale + 1e-10
    return loss, gradient, hessian


def _fit_multinomial_newton(rows):
    if len(rows) < MIN_TRAINING_ROWS:
        raise base.PlatformError("insufficient multinomial calibration training rows")
    beta = list(base.IDENTITY_BETA)
    loss, gradient, hessian = _loss_gradient_hessian(beta, rows)
    converged = False
    final_grad_norm = math.sqrt(sum(value * value for value in gradient))
    for iteration in range(NEWTON_MAX_ITER):
        final_grad_norm = math.sqrt(sum(value * value for value in gradient))
        if final_grad_norm < GRAD_TOL:
            converged = True
            break
        direction = _solve_system(hessian, gradient)
        directional_decrease = sum(gradient[j] * direction[j] for j in range(8))
        if not math.isfinite(directional_decrease) or directional_decrease <= 0.0:
            raise base.PlatformError("multinomial Newton direction is not a descent direction")
        step = 1.0
        accepted = False
        chosen = None
        for _ in range(28):
            candidate = [beta[j] - step * direction[j] for j in range(8)]
            candidate_loss, candidate_gradient, candidate_hessian = _loss_gradient_hessian(candidate, rows)
            if candidate_loss <= loss - 1e-4 * step * directional_decrease:
                chosen = (candidate, candidate_loss, candidate_gradient, candidate_hessian)
                accepted = True
                break
            step *= 0.5
        if not accepted or chosen is None:
            break
        beta, loss, gradient, hessian = chosen
        max_step = max(abs(step * value) for value in direction)
        if max_step < STEP_TOL:
            final_grad_norm = math.sqrt(sum(value * value for value in gradient))
            converged = final_grad_norm < 1e-6
            break
    final_grad_norm = math.sqrt(sum(value * value for value in gradient))
    if final_grad_norm < 1e-6:
        converged = True
    return {
        "beta": beta,
        "training_rows": len(rows),
        "loss": loss,
        "iterations": iteration + 1,
        "converged": converged,
        "final_gradient_norm": final_grad_norm,
        "optimizer": "damped_newton_irls",
        "l2_to_identity": base.L2_TO_IDENTITY,
        "identity_anchor": base.IDENTITY_BETA,
    }


def _validate_domain_strict(cid, seed_offset):
    report = base.load_json(base.REPORT_ROOT / f"{cid}.json")
    all_matches = base.read_processed_matches(cid)
    seasons = base._completed_outer_seasons(cid, report)
    if len(seasons) < 2:
        raise base.PlatformError(f"insufficient completed outer seasons for {cid}")
    cache = {season: base._season_baseline_rows(cid, report, all_matches, season) for season in seasons}
    outer_reports = []
    pooled_rows = []
    max_outcome_residual = 0.0
    max_total_residual = 0.0
    max_sum_residual = 0.0
    nonconverged_projection_rows = 0
    skipped_training_folds = []
    calibration_nonconverged_folds = []

    for outer_index, target_season in enumerate(seasons[1:]):
        target_year = base.rolling._season_year(target_season)
        training_seasons = [season for season in seasons if base.rolling._season_year(season) < target_year]
        train_rows = [row for season in training_seasons for row in cache[season]["rows"]]
        if len(train_rows) < MIN_TRAINING_ROWS:
            skipped_training_folds.append({
                "target_season": target_season,
                "training_seasons": training_seasons,
                "training_rows": len(train_rows),
                "reason": f"strict minimum training rows is {MIN_TRAINING_ROWS}",
            })
            continue
        model = _fit_multinomial_newton(train_rows)
        if not model["converged"]:
            calibration_nonconverged_folds.append({
                "target_season": target_season,
                "training_rows": len(train_rows),
                "final_gradient_norm": model["final_gradient_norm"],
            })
            continue

        season_rows = []
        for item in cache[target_season]["rows"]:
            target_one = base._softmax(model["beta"], item["features"])
            candidate, audit = base._ipf_project(item["baseline"], target_one)
            max_outcome_residual = max(max_outcome_residual, float(audit["max_outcome_residual"]))
            max_total_residual = max(max_total_residual, float(audit["max_total_marginal_residual"]))
            max_sum_residual = max(max_sum_residual, float(audit["probability_sum_residual"]))
            if not audit["converged"]:
                nonconverged_projection_rows += 1
                continue
            metric_row = base.rolling._metric_row(item["baseline"], candidate, item["match"])
            metric_row["target_season"] = target_season
            season_rows.append(metric_row)
            pooled_rows.append(metric_row)
        if not season_rows:
            continue
        summary = base.rolling._aggregate(season_rows, seed_offset + outer_index * 100)
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
        raise base.PlatformError(f"no converged rolling OOF projection rows for {cid}")
    pooled = base.rolling._aggregate(pooled_rows, seed_offset + 900)
    ci = pooled["paired_block_bootstrap"]
    seasons_brier_improve = sum(1 for item in outer_reports if item["metrics"]["one_x_two_brier"]["candidate_minus_baseline"] < 0)
    seasons_draw_improve = sum(1 for item in outer_reports if item["metrics"]["draw_brier"]["candidate_minus_baseline"] < 0)
    seasons_joint_noncat = sum(1 for item in outer_reports if item["metrics"]["joint_log"]["candidate_minus_baseline"] <= 0.005)
    all_calibration_converged = not calibration_nonconverged_folds and all(item["calibration_model"]["converged"] for item in outer_reports)

    checks = {
        "multiple_outer_seasons": len(outer_reports) >= 2,
        "strict_prior_training_each_fold": all(all(base.rolling._season_year(season) < base.rolling._season_year(item["target_season"]) for season in item["training_seasons"]) for item in outer_reports),
        "minimum_training_rows_each_participating_fold": all(item["training_rows"] >= MIN_TRAINING_ROWS for item in outer_reports),
        "all_calibration_models_converged": all_calibration_converged,
        "all_projection_rows_converged": nonconverged_projection_rows == 0,
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
        "skipped_insufficient_training_folds": skipped_training_folds,
        "calibration_nonconverged_folds": calibration_nonconverged_folds,
        "projection_audit": {
            "nonconverged_rows": nonconverged_projection_rows,
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
        "governance_reason": "Strict Newton/IRLS result-calibration/IPF challenger remains unregistered research under CURRENT V4.7.0.",
    }


base._fit_multinomial = _fit_multinomial_newton
base.validate_domain = _validate_domain_strict
base.MIN_TRAINING_ROWS = MIN_TRAINING_ROWS


if __name__ == "__main__":
    raise SystemExit(base.main())
