#!/usr/bin/env python3
"""E3c-1 research: soft-constrained joint score-matrix coordination.

This is a new research route, not an E3b-2 repair. For each match, the
Champion unified score matrix is the prior. Probability conservation, legal
score coordinates, and prior support are hard constraints. E3b-1 H/D/A and
Champion total-goal marginals are soft targets in one explicit convex
objective:

    KL(q || p_prior)
    + lambda_outcome * KL(Aq || r_e3b1)
    + lambda_total * KL(Bq || g_champion)

Penalty pairs are selected only from earlier-season OOS records. No target
season outcome is used for selection. Research-only; formal_weight=0.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
from scipy.optimize import minimize

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import big5_high_completeness_b100 as b100  # noqa: E402
import e3b2_min_kl_unified_matrix as e3b2  # noqa: E402
import market_joint_direct_outcome_e3b1 as e3b1  # noqa: E402
import matrix_draw_gate_e3a as e3a  # noqa: E402
from platform_core import ROOT  # noqa: E402

OUT = ROOT.parent / "artifacts/research/e3c1_soft_joint_matrix"
OUTCOMES = ("home", "draw", "away")
OUTCOME_INDEX = {name: index for index, name in enumerate(OUTCOMES)}
EPS = 1e-15
OPT_TOL = 1e-8
PROB_TOL = 1e-10
MAX_ITER = 300
MIN_SELECTION_ROWS = 250
GUARD_RELATIVE = 0.005
PENALTY_GRID = (
    (0.0, 0.0),
    (0.25, 4.0),
    (0.5, 2.0),
    (1.0, 1.0),
    (2.0, 0.5),
    (4.0, 0.25),
    (2.0, 2.0),
)


def repository_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT.parent,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def score_outcome(home: int, away: int) -> str:
    return "home" if home > away else "draw" if home == away else "away"


def normalize(values: dict[str, float]) -> dict[str, float]:
    total = sum(float(values[name]) for name in OUTCOMES)
    if total <= 0:
        raise RuntimeError("outcome target has no mass")
    return {name: float(values[name]) / total for name in OUTCOMES}


def stable_softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - float(np.max(values))
    exp = np.exp(np.clip(shifted, -700.0, 700.0))
    return exp / float(np.sum(exp))


def kl_divergence(candidate: np.ndarray, target: np.ndarray) -> float:
    positive = candidate > 0
    return float(np.sum(candidate[positive] * np.log(candidate[positive] / target[positive])))


def group_prior(record: dict[str, Any]) -> dict[str, Any]:
    cells = [
        {
            "home_goals": int(cell["home_goals"]),
            "away_goals": int(cell["away_goals"]),
            "probability": max(0.0, float(cell["probability"])),
        }
        for cell in record["matrix"]
    ]
    prior_sum = sum(cell["probability"] for cell in cells)
    if prior_sum <= 0:
        raise RuntimeError("prior matrix has no mass")
    for cell in cells:
        cell["probability"] /= prior_sum

    grouped: dict[tuple[int, str], list[int]] = defaultdict(list)
    for index, cell in enumerate(cells):
        total = cell["home_goals"] + cell["away_goals"]
        label = score_outcome(cell["home_goals"], cell["away_goals"])
        grouped[(total, label)].append(index)

    groups: list[dict[str, Any]] = []
    for (total, label), indices in sorted(
        grouped.items(), key=lambda item: (item[0][0], OUTCOME_INDEX[item[0][1]])
    ):
        mass = sum(cells[index]["probability"] for index in indices)
        if mass <= 0:
            continue
        groups.append(
            {
                "total": total,
                "outcome": label,
                "indices": indices,
                "prior_mass": mass,
            }
        )
    if not groups:
        raise RuntimeError("prior matrix has no positive support groups")
    return {"cells": cells, "groups": groups}


def objective_components(
    z: np.ndarray,
    p: np.ndarray,
    outcome_index: np.ndarray,
    total_index: np.ndarray,
    outcome_target: np.ndarray,
    total_target: np.ndarray,
    lambda_outcome: float,
    lambda_total: float,
) -> dict[str, float]:
    outcome_mass = np.bincount(outcome_index, weights=z, minlength=len(OUTCOMES))
    total_mass = np.bincount(total_index, weights=z, minlength=len(total_target))
    matrix_kl = kl_divergence(z, p)
    outcome_kl = kl_divergence(outcome_mass, outcome_target)
    total_kl = kl_divergence(total_mass, total_target)
    return {
        "matrix_kl": matrix_kl,
        "outcome_kl": outcome_kl,
        "total_kl": total_kl,
        "weighted_objective": (
            matrix_kl + lambda_outcome * outcome_kl + lambda_total * total_kl
        ),
    }


def project_record(
    record: dict[str, Any],
    lambda_outcome: float,
    lambda_total: float,
) -> dict[str, Any]:
    prepared = group_prior(record)
    cells = prepared["cells"]
    groups = prepared["groups"]
    totals = sorted({int(group["total"]) for group in groups})
    total_lookup = {total: index for index, total in enumerate(totals)}

    p = np.asarray([float(group["prior_mass"]) for group in groups], dtype=float)
    p /= p.sum()
    outcome_index = np.asarray(
        [OUTCOME_INDEX[str(group["outcome"])] for group in groups], dtype=int
    )
    total_index = np.asarray(
        [total_lookup[int(group["total"])] for group in groups], dtype=int
    )
    outcome_target_dict = normalize(record["e3b1_probs"])
    outcome_target = np.asarray(
        [outcome_target_dict[name] for name in OUTCOMES], dtype=float
    )
    total_target = np.bincount(total_index, weights=p, minlength=len(totals))
    y0 = np.log(np.maximum(EPS, p))

    def objective_and_gradient(y: np.ndarray) -> tuple[float, np.ndarray]:
        z = stable_softmax(y)
        outcome_mass = np.bincount(
            outcome_index, weights=z, minlength=len(OUTCOMES)
        )
        total_mass = np.bincount(
            total_index, weights=z, minlength=len(total_target)
        )
        components = objective_components(
            z,
            p,
            outcome_index,
            total_index,
            outcome_target,
            total_target,
            lambda_outcome,
            lambda_total,
        )
        derivative = np.log(np.maximum(EPS, z) / np.maximum(EPS, p)) + 1.0
        derivative += lambda_outcome * (
            np.log(
                np.maximum(EPS, outcome_mass[outcome_index])
                / np.maximum(EPS, outcome_target[outcome_index])
            )
            + 1.0
        )
        derivative += lambda_total * (
            np.log(
                np.maximum(EPS, total_mass[total_index])
                / np.maximum(EPS, total_target[total_index])
            )
            + 1.0
        )
        centered = derivative - float(np.dot(z, derivative))
        gradient = z * centered
        return float(components["weighted_objective"]), gradient

    if lambda_outcome == 0.0 and lambda_total == 0.0:
        z = p.copy()
        success = True
        status = 0
        message = "baseline prior selected"
        iterations = 0
        evaluations = 1
        _, gradient = objective_and_gradient(y0)
    else:
        result = minimize(
            objective_and_gradient,
            y0,
            method="L-BFGS-B",
            jac=True,
            options={
                "maxiter": MAX_ITER,
                "ftol": 1e-13,
                "gtol": OPT_TOL,
                "maxls": 30,
            },
        )
        z = stable_softmax(np.asarray(result.x, dtype=float))
        _, gradient = objective_and_gradient(np.asarray(result.x, dtype=float))
        success = bool(result.success) or float(np.max(np.abs(gradient))) <= OPT_TOL
        status = int(result.status)
        message = str(result.message)
        iterations = int(result.nit)
        evaluations = int(result.nfev)

    q_cells = [0.0] * len(cells)
    for group, mass in zip(groups, z):
        prior_group = float(group["prior_mass"])
        for index in group["indices"]:
            q_cells[index] = (
                float(mass) * float(cells[index]["probability"]) / prior_group
            )

    probability_sum = sum(q_cells)
    if probability_sum <= 0:
        raise RuntimeError("soft projection produced no probability mass")
    q_cells = [value / probability_sum for value in q_cells]

    outcome_after = {name: 0.0 for name in OUTCOMES}
    total_after: dict[int, float] = defaultdict(float)
    total_before: dict[int, float] = defaultdict(float)
    support_preserved = True
    matrix = []
    for cell, probability in zip(cells, q_cells):
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        label = score_outcome(home, away)
        total = home + away
        prior_probability = float(cell["probability"])
        if prior_probability <= 0 and probability > PROB_TOL:
            support_preserved = False
        outcome_after[label] += probability
        total_after[total] += probability
        total_before[total] += prior_probability
        matrix.append(
            {
                "home_goals": home,
                "away_goals": away,
                "probability": probability,
            }
        )

    group_components = objective_components(
        z,
        p,
        outcome_index,
        total_index,
        outcome_target,
        total_target,
        lambda_outcome,
        lambda_total,
    )
    outcome_residuals = {
        name: outcome_after[name] - outcome_target_dict[name] for name in OUTCOMES
    }
    total_residuals = {
        str(total): total_after[total] - total_before[total]
        for total in sorted(total_before)
    }
    btts_before = sum(
        float(cell["probability"])
        for cell in cells
        if int(cell["home_goals"]) > 0 and int(cell["away_goals"]) > 0
    )
    btts_after = sum(
        float(cell["probability"])
        for cell in matrix
        if int(cell["home_goals"]) > 0 and int(cell["away_goals"]) > 0
    )
    converged = (
        success
        and abs(sum(q_cells) - 1.0) <= PROB_TOL
        and support_preserved
        and float(np.max(np.abs(gradient))) <= max(OPT_TOL, 5e-8)
    )
    return {
        **record,
        "e3c1_status": "CONVERGED" if converged else "NOT_CONVERGED",
        "e3c1_probs": outcome_after,
        "e3c1_matrix": matrix,
        "e3c1_penalties": {
            "lambda_outcome": lambda_outcome,
            "lambda_total": lambda_total,
        },
        "e3c1_optimizer": {
            "success": success,
            "status": status,
            "message": message,
            "iterations": iterations,
            "evaluations": evaluations,
            "stationarity_residual": float(np.max(np.abs(gradient))),
        },
        "e3c1_objective": group_components,
        "e3c1_probability_residual": abs(sum(q_cells) - 1.0),
        "e3c1_outcome_residuals": outcome_residuals,
        "e3c1_total_residuals": total_residuals,
        "e3c1_max_outcome_residual": max(abs(value) for value in outcome_residuals.values()),
        "e3c1_max_total_residual": max(
            (abs(value) for value in total_residuals.values()), default=0.0
        ),
        "e3c1_support_preserved": support_preserved,
        "e3c1_btts_before": btts_before,
        "e3c1_btts_after": btts_after,
    }


def total_metrics(records: list[dict[str, Any]], matrix_field: str) -> dict[str, Any]:
    if not records:
        return {"count": 0}
    logloss_values = []
    brier_values = []
    under25_brier = []
    for record in records:
        distribution: dict[int, float] = defaultdict(float)
        for cell in record[matrix_field]:
            total = int(cell["home_goals"]) + int(cell["away_goals"])
            distribution[total] += float(cell["probability"])
        actual_total = int(record["actual_total"])
        actual_probability = float(distribution.get(actual_total, 0.0))
        logloss_values.append(-math.log(max(EPS, actual_probability)))
        support = set(distribution) | {actual_total}
        brier_values.append(
            sum(
                (
                    float(distribution.get(total, 0.0))
                    - (1.0 if total == actual_total else 0.0)
                )
                ** 2
                for total in support
            )
        )
        under_probability = sum(
            probability for total, probability in distribution.items() if total <= 2
        )
        under25_brier.append((under_probability - float(actual_total <= 2)) ** 2)
    return {
        "count": len(records),
        "total_logloss": mean(logloss_values),
        "total_brier": mean(brier_values),
        "under25_brier": mean(under25_brier),
    }


def candidate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(records),
        "outcome": e3b1.metrics(records, "e3c1_probs"),
        "score": e3b2.score_metrics(records, "e3c1_matrix"),
        "total": total_metrics(records, "e3c1_matrix"),
    }


def guard_limit(value: float) -> float:
    return value * (1.0 + GUARD_RELATIVE)


def choose_penalties(validation_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(validation_rows) < MIN_SELECTION_ROWS:
        return {
            "status": "BASELINE_INSUFFICIENT_PRIOR_VALIDATION",
            "lambda_outcome": 0.0,
            "lambda_total": 0.0,
            "validation_rows": len(validation_rows),
            "leaderboard": [],
        }

    leaderboard = []
    baseline = None
    for lambda_outcome, lambda_total in PENALTY_GRID:
        projected = [
            project_record(row, lambda_outcome, lambda_total)
            for row in validation_rows
        ]
        converged = all(row["e3c1_status"] == "CONVERGED" for row in projected)
        if not converged:
            leaderboard.append(
                {
                    "lambda_outcome": lambda_outcome,
                    "lambda_total": lambda_total,
                    "status": "NUMERICAL_FAILURE",
                    "failure_count": sum(
                        row["e3c1_status"] != "CONVERGED" for row in projected
                    ),
                }
            )
            continue
        metrics = candidate_metrics(projected)
        item = {
            "lambda_outcome": lambda_outcome,
            "lambda_total": lambda_total,
            "status": "EVALUATED",
            **metrics,
        }
        leaderboard.append(item)
        if lambda_outcome == 0.0 and lambda_total == 0.0:
            baseline = item

    if baseline is None:
        return {
            "status": "BASELINE_NUMERICAL_FAILURE",
            "lambda_outcome": 0.0,
            "lambda_total": 0.0,
            "validation_rows": len(validation_rows),
            "leaderboard": leaderboard,
        }

    baseline_outcome = float(baseline["outcome"]["logloss"])
    eligible = []
    for item in leaderboard:
        if item.get("status") != "EVALUATED":
            continue
        score_ok = (
            float(item["score"]["exact_score_logloss"])
            <= guard_limit(float(baseline["score"]["exact_score_logloss"]))
        )
        total_ok = (
            float(item["total"]["total_logloss"])
            <= guard_limit(float(baseline["total"]["total_logloss"]))
        )
        btts_ok = (
            float(item["score"]["btts_logloss"])
            <= guard_limit(float(baseline["score"]["btts_logloss"]))
        )
        outcome_improved = float(item["outcome"]["logloss"]) < baseline_outcome - 1e-8
        item["guards"] = {
            "exact_score_logloss": score_ok,
            "total_logloss": total_ok,
            "btts_logloss": btts_ok,
            "outcome_logloss_improved": outcome_improved,
        }
        if score_ok and total_ok and btts_ok and outcome_improved:
            eligible.append(item)

    if not eligible:
        chosen = baseline
        status = "BASELINE_NO_GUARDED_IMPROVEMENT"
    else:
        eligible.sort(
            key=lambda item: (
                float(item["outcome"]["logloss"]),
                float(item["score"]["exact_score_logloss"]),
                float(item["total"]["total_logloss"]),
                float(item["score"]["btts_logloss"]),
                float(item["lambda_outcome"]) + float(item["lambda_total"]),
            )
        )
        chosen = eligible[0]
        status = "SELECTED_PRIOR_ONLY_GUARDED"

    return {
        "status": status,
        "lambda_outcome": float(chosen["lambda_outcome"]),
        "lambda_total": float(chosen["lambda_total"]),
        "validation_rows": len(validation_rows),
        "guard_relative": GUARD_RELATIVE,
        "selection_primary": "minimize outcome logloss",
        "selection_guards": [
            "exact score logloss <= baseline * (1 + guard)",
            "total logloss <= baseline * (1 + guard)",
            "BTTS logloss <= baseline * (1 + guard)",
        ],
        "leaderboard": leaderboard,
    }


def expanding_soft_oos(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_year[int(row["season_start_year"])].append(row)
    years = sorted(by_year)
    output: list[dict[str, Any]] = []
    folds = []
    for target_year in years:
        prior_years = [year for year in years if year < target_year]
        validation_year = prior_years[-1] if prior_years else None
        validation_rows = by_year[validation_year] if validation_year is not None else []
        selection = choose_penalties(validation_rows)
        current = sorted(
            by_year[target_year],
            key=lambda row: (
                row["date"],
                row["competition_id"],
                row["match_key"],
            ),
        )
        projected = [
            project_record(
                row,
                float(selection["lambda_outcome"]),
                float(selection["lambda_total"]),
            )
            for row in current
        ]
        for item in projected:
            item["e3c1_modeled"] = (
                float(selection["lambda_outcome"]) != 0.0
                or float(selection["lambda_total"]) != 0.0
            )
        output.extend(projected)
        folds.append(
            {
                "target_year": target_year,
                "prior_years": prior_years,
                "validation_year": validation_year,
                "target_rows": len(current),
                "selection": selection,
                "projection_failures": sum(
                    item["e3c1_status"] != "CONVERGED" for item in projected
                ),
            }
        )
    return output, folds


def projection_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    converged = [row for row in records if row["e3c1_status"] == "CONVERGED"]
    failures = [row for row in records if row["e3c1_status"] != "CONVERGED"]
    pair_counts = Counter(
        (
            float(row["e3c1_penalties"]["lambda_outcome"]),
            float(row["e3c1_penalties"]["lambda_total"]),
        )
        for row in records
    )
    return {
        "count": len(records),
        "converged_count": len(converged),
        "failure_count": len(failures),
        "all_converged": not failures,
        "adjusted_count": sum(bool(row["e3c1_modeled"]) for row in records),
        "penalty_pair_counts": {
            f"{pair[0]:g},{pair[1]:g}": count for pair, count in sorted(pair_counts.items())
        },
        "max_probability_residual": max(
            (float(row["e3c1_probability_residual"]) for row in converged),
            default=None,
        ),
        "max_stationarity_residual": max(
            (
                float(row["e3c1_optimizer"]["stationarity_residual"])
                for row in converged
            ),
            default=None,
        ),
        "all_support_preserved": all(
            bool(row["e3c1_support_preserved"]) for row in converged
        ),
        "mean_kl_q_prior": mean(
            float(row["e3c1_objective"]["matrix_kl"]) for row in converged
        )
        if converged
        else None,
        "mean_outcome_kl_to_e3b1": mean(
            float(row["e3c1_objective"]["outcome_kl"]) for row in converged
        )
        if converged
        else None,
        "mean_total_kl_to_champion": mean(
            float(row["e3c1_objective"]["total_kl"]) for row in converged
        )
        if converged
        else None,
        "mean_max_outcome_residual": mean(
            float(row["e3c1_max_outcome_residual"]) for row in converged
        )
        if converged
        else None,
        "max_outcome_residual": max(
            (float(row["e3c1_max_outcome_residual"]) for row in converged),
            default=None,
        ),
        "mean_max_total_residual": mean(
            float(row["e3c1_max_total_residual"]) for row in converged
        )
        if converged
        else None,
        "max_total_residual": max(
            (float(row["e3c1_max_total_residual"]) for row in converged),
            default=None,
        ),
        "mean_absolute_btts_shift": mean(
            abs(float(row["e3c1_btts_after"]) - float(row["e3c1_btts_before"]))
            for row in converged
        )
        if converged
        else None,
        "failure_examples": [
            {
                "match_key": row.get("match_key"),
                "optimizer": row.get("e3c1_optimizer"),
            }
            for row in failures[:20]
        ],
    }


def section_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(records),
        "market_outcome": e3b1.metrics(records, "market_probs"),
        "champion_outcome": e3b1.metrics(records, "champion_probs"),
        "e3b1_outcome": e3b1.metrics(records, "e3b1_probs"),
        "e3c1_outcome": e3b1.metrics(records, "e3c1_probs"),
        "champion_score": e3b2.score_metrics(records, "matrix"),
        "e3c1_score": e3b2.score_metrics(records, "e3c1_matrix"),
        "champion_total": total_metrics(records, "matrix"),
        "e3c1_total": total_metrics(records, "e3c1_matrix"),
        "projection_audit": projection_audit(records),
    }


def numeric_deltas(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    return {
        key: float(candidate[key]) - float(baseline[key])
        for key in sorted(set(candidate) & set(baseline))
        if key != "count"
        and isinstance(candidate[key], (int, float))
        and isinstance(baseline[key], (int, float))
    }


def per_league(records: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for cid, name in b100.BIG5.items():
        subset = [row for row in records if row["competition_id"] == cid]
        metrics = section_metrics(subset)
        metrics["competition_zh"] = name
        metrics["delta_e3c1_minus_market_outcome"] = numeric_deltas(
            metrics["e3c1_outcome"], metrics["market_outcome"]
        )
        metrics["delta_e3c1_minus_champion_outcome"] = numeric_deltas(
            metrics["e3c1_outcome"], metrics["champion_outcome"]
        )
        metrics["delta_e3c1_minus_e3b1_outcome"] = numeric_deltas(
            metrics["e3c1_outcome"], metrics["e3b1_outcome"]
        )
        metrics["delta_e3c1_minus_champion_score"] = numeric_deltas(
            metrics["e3c1_score"], metrics["champion_score"]
        )
        metrics["delta_e3c1_minus_champion_total"] = numeric_deltas(
            metrics["e3c1_total"], metrics["champion_total"]
        )
        result[cid] = metrics
    return result


def markdown(report: dict[str, Any]) -> str:
    full = report["full_oos"]
    lines = [
        "# E3c-1 Soft-Constrained Joint Matrix Coordination",
        "",
        "New research route; not an E3b-2 repair; formal_weight=0.",
        "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Objective: `{report['optimization']['objective']}`",
        f"- Full OOS records: {full['count']}",
        f"- Adjusted records: {full['projection_audit']['adjusted_count']}",
        f"- Converged: {full['projection_audit']['converged_count']}/{full['count']}",
        f"- Fixed B100: {report['b100']['count']}",
        "",
        "## Full OOS outcome metrics",
        "",
        "| Model | Accuracy | Balanced | Macro-F1 | Draw P | Draw R | Draw F1 | LogLoss | Brier | RPS | ECE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, key in (
        ("Market", "market_outcome"),
        ("Champion", "champion_outcome"),
        ("E3b-1 target", "e3b1_outcome"),
        ("E3c-1 matrix", "e3c1_outcome"),
    ):
        m = full[key]
        lines.append(
            f"| {label} | {m['accuracy']:.4%} | {m['balanced_accuracy']:.4%} | "
            f"{m['macro_f1']:.4%} | {m['draw_precision']:.4%} | "
            f"{m['draw_recall']:.4%} | {m['draw_f1']:.4%} | "
            f"{m['logloss']:.6f} | {m['brier']:.6f} | {m['rps']:.6f} | "
            f"{m['confidence_ece_10bin']:.6f} |"
        )
    lines.extend(
        (
            "",
            "## Matrix and total metrics",
            "",
            "| Metric | Champion | E3c-1 | Delta |",
            "|---|---:|---:|---:|",
        )
    )
    for key, label in (
        ("exact_score_top1_accuracy", "Exact score Top-1"),
        ("exact_score_top3_coverage", "Exact score Top-3"),
        ("exact_score_logloss", "Exact score LogLoss"),
        ("btts_brier", "BTTS Brier"),
        ("btts_logloss", "BTTS LogLoss"),
    ):
        before = float(full["champion_score"][key])
        after = float(full["e3c1_score"][key])
        lines.append(f"| {label} | {before:.6f} | {after:.6f} | {after-before:+.6f} |")
    for key, label in (
        ("total_logloss", "Total LogLoss"),
        ("total_brier", "Total Brier"),
        ("under25_brier", "Under 2.5 Brier"),
    ):
        before = float(full["champion_total"][key])
        after = float(full["e3c1_total"][key])
        lines.append(f"| {label} | {before:.6f} | {after:.6f} | {after-before:+.6f} |")
    audit = full["projection_audit"]
    lines.extend(
        (
            "",
            "## Soft-residual audit",
            "",
            f"- Penalty pair counts: `{json.dumps(audit['penalty_pair_counts'], ensure_ascii=False)}`",
            f"- Max probability residual: {audit['max_probability_residual']:.3e}",
            f"- Max stationarity residual: {audit['max_stationarity_residual']:.3e}",
            f"- Mean/max outcome marginal residual: {audit['mean_max_outcome_residual']:.6f} / {audit['max_outcome_residual']:.6f}",
            f"- Mean/max total marginal residual: {audit['mean_max_total_residual']:.6f} / {audit['max_total_residual']:.6f}",
            f"- Mean KL(q||prior): {audit['mean_kl_q_prior']:.6f}",
            "",
            "## Fixed interpretation",
            "",
            "- Soft targets are not described as exactly preserved.",
            "- All outcome and total residuals are reported.",
            "- Penalties are selected only from earlier-season OOS validation rows.",
            "- No automatic promotion; formal_weight=0.",
            "",
        )
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        evaluated, lineage = e3b2.build_records()
        projected, soft_folds = expanding_soft_oos(evaluated)
        failures = [
            {
                "match_key": row.get("match_key"),
                "status": row.get("e3c1_status"),
                "optimizer": row.get("e3c1_optimizer"),
            }
            for row in projected
            if row.get("e3c1_status") != "CONVERGED"
        ]
        if failures:
            raise RuntimeError(f"soft projection failures: {failures[:20]}")

        by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in projected:
            by_competition[row["competition_id"]].append(row)
        b100_rows, selection = e3a.fixed_b100(by_competition)
        full = section_metrics(projected)
        fixed = section_metrics(b100_rows)
        full["per_league"] = per_league(projected)
        fixed["per_league"] = per_league(b100_rows)
        fixed["selection"] = selection

        expected_b100 = b100.TARGET_PER_LEAGUE * len(b100.BIG5)
        audit = full["projection_audit"]
        implementation_pass = (
            len(b100_rows) == expected_b100
            and bool(audit["all_converged"])
            and bool(audit["all_support_preserved"])
            and float(audit["max_probability_residual"]) <= PROB_TOL
            and float(audit["max_stationarity_residual"]) <= max(OPT_TOL, 5e-8)
        )
        effect_checks = {
            "outcome_logloss_better_than_e3b1": (
                full["e3c1_outcome"]["logloss"] < full["e3b1_outcome"]["logloss"]
            ),
            "outcome_logloss_better_than_market": (
                full["e3c1_outcome"]["logloss"] < full["market_outcome"]["logloss"]
            ),
            "exact_score_logloss_not_worse_than_champion": (
                full["e3c1_score"]["exact_score_logloss"]
                <= full["champion_score"]["exact_score_logloss"]
            ),
            "total_logloss_not_worse_than_champion": (
                full["e3c1_total"]["total_logloss"]
                <= full["champion_total"]["total_logloss"]
            ),
            "draw_f1_better_than_e3b1": (
                full["e3c1_outcome"]["draw_f1"] > full["e3b1_outcome"]["draw_f1"]
            ),
        }
        report = {
            "schema_version": "1.0",
            "research_status": "PASS" if implementation_pass else "FAIL",
            "repository_head": repository_head(),
            "scope": "90_minutes_including_stoppage",
            "experiment": "E3C1_SOFT_CONSTRAINED_JOINT_MATRIX",
            "relationship_to_e3b2": "NEW_ROUTE_NOT_REPAIR",
            "optimization": {
                "prior": "Champion unified score matrix",
                "objective": (
                    "KL(q||p_prior) + lambda_outcome*KL(Aq||r_e3b1) "
                    "+ lambda_total*KL(Bq||g_champion)"
                ),
                "hard_constraints": [
                    "sum(q)=1",
                    "q>=0",
                    "prior zero support preserved",
                    "score cells retain legal nonnegative integer coordinates",
                ],
                "soft_targets": [
                    "H/D/A marginal toward E3b-1 direct outcome target",
                    "total-goal marginal toward Champion direct total target",
                ],
                "penalty_grid": [list(pair) for pair in PENALTY_GRID],
                "selection": (
                    "latest earlier-season OOS validation; minimize outcome logloss "
                    "subject to 0.5% relative guards on exact-score, total and BTTS logloss"
                ),
                "algorithm": "group-mass convex optimization with L-BFGS-B",
                "maximum_iterations": MAX_ITER,
                "stationarity_tolerance": OPT_TOL,
                "manual_probability_clipping": False,
                "hidden_slack": False,
            },
            "full_oos": full,
            "b100": fixed,
            "soft_selection_folds": soft_folds,
            "lineage": lineage,
            "audit": {
                "implementation_pass": implementation_pass,
                "b100_count_contract": (
                    "PASS" if len(b100_rows) == expected_b100 else "FAIL"
                ),
                "probability_conservation": (
                    "PASS"
                    if float(audit["max_probability_residual"]) <= PROB_TOL
                    else "FAIL"
                ),
                "support_preservation": (
                    "PASS" if audit["all_support_preserved"] else "FAIL"
                ),
                "optimizer_stationarity": (
                    "PASS"
                    if float(audit["max_stationarity_residual"])
                    <= max(OPT_TOL, 5e-8)
                    else "FAIL"
                ),
                "soft_residuals_reported": True,
                "effect_checks": effect_checks,
            },
            "promotion": {
                "automatic_promotion": False,
                "formal_weight": 0,
                "status": "CHALLENGE_LAYER_ONLY",
                "promotion_candidate": bool(
                    implementation_pass and all(effect_checks.values())
                ),
                "per_domain_forward_validation": "NOT_EVALUATED",
            },
            "formal_mutation": {
                "model": 0,
                "data": 0,
                "config": 0,
                "current": 0,
                "formal_weight": 0,
            },
            "failures": [],
        }
    except Exception as exc:
        report = {
            "schema_version": "1.0",
            "research_status": "FAIL",
            "repository_head": repository_head(),
            "experiment": "E3C1_SOFT_CONSTRAINED_JOINT_MATRIX",
            "relationship_to_e3b2": "NEW_ROUTE_NOT_REPAIR",
            "failures": [{"error": f"{type(exc).__name__}: {exc}"}],
            "promotion": {
                "automatic_promotion": False,
                "formal_weight": 0,
                "status": "CHALLENGE_LAYER_ONLY",
            },
            "formal_mutation": {
                "model": 0,
                "data": 0,
                "config": 0,
                "current": 0,
                "formal_weight": 0,
            },
        }

    json_path = output_dir / "e3c1_soft_joint_matrix.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if report["research_status"] == "PASS":
        (output_dir / "e3c1_soft_joint_matrix.md").write_text(
            markdown(report), encoding="utf-8"
        )
    if args.print_summary:
        print(
            json.dumps(
                {
                    "research_status": report["research_status"],
                    "repository_head": report.get("repository_head"),
                    "full_oos": {
                        key: report.get("full_oos", {}).get(key)
                        for key in (
                            "count",
                            "market_outcome",
                            "champion_outcome",
                            "e3b1_outcome",
                            "e3c1_outcome",
                            "champion_score",
                            "e3c1_score",
                            "champion_total",
                            "e3c1_total",
                            "projection_audit",
                        )
                    },
                    "audit": report.get("audit"),
                    "promotion": report.get("promotion"),
                    "failures": report.get("failures"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0 if report["research_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
