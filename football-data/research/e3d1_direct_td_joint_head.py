#!/usr/bin/env python3
"""E3d-1 research: direct legal (T, D) joint score-matrix head.

This route does not predict H/D/A first and then repair it. It trains two
compatible components from match outcomes:

1. a direct total-goal distribution P(T=t | X);
2. a conditional legal home-goal / goal-difference allocation
   P(H=h | T=t, X), where A=t-h and D=2h-t.

The final score matrix is their product. Therefore score, H/D/A, total goals,
BTTS and every downstream market are derived from one legal matrix by
construction. Champion probabilities are used only as logarithmic offsets for
regularisation; E3b-1 and E3c-1 outputs are not training targets.

All model and regularisation choices are selected from earlier-season OOS
records only. Research-only; formal_weight=0.
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
import e3c1_soft_joint_matrix as e3c1  # noqa: E402
import market_joint_direct_outcome_e3b1 as e3b1  # noqa: E402
import matrix_draw_gate_e3a as e3a  # noqa: E402
from platform_core import ROOT  # noqa: E402

OUT = ROOT.parent / "artifacts/research/e3d1_direct_td_joint_head"
OUTCOMES = ("home", "draw", "away")
EPS = 1e-12
PROB_TOL = 1e-10
OPT_GRAD_TOL = 2e-6
MAX_ITER = 350
MIN_TRAIN_ROWS = 900
MIN_VALID_ROWS = 250
GUARD_RELATIVE = 0.005
L2_GRID = (1.0, 3.0, 10.0)

MARKET_FEATURES = (
    "close_h", "close_d", "close_a",
    "move_h", "move_d", "move_a",
    "close_entropy", "close_margin",
    "close_under25", "under25_move", "ou_available",
    "close_ah", "ah_line_move", "close_ah_home", "ah_home_move", "ah_available",
    "book_std_h", "book_std_d", "book_std_a",
    "book_vote_h", "book_vote_d", "book_vote_a", "book_count_log",
)
TEAM_FEATURES = (
    "mu_total", "allocation_home_share", "ess",
    "home_score_signal", "away_score_signal",
    "home_direct_total_rate", "away_direct_total_rate",
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


def stable_softmax(logits: np.ndarray, axis: int = 1) -> np.ndarray:
    maximum = np.max(logits, axis=axis, keepdims=True)
    exponent = np.exp(np.clip(logits - maximum, -700.0, 700.0))
    denominator = np.sum(exponent, axis=axis, keepdims=True)
    return exponent / np.maximum(EPS, denominator)


def score_outcome(home: int, away: int) -> str:
    return "home" if home > away else "draw" if home == away else "away"


def match_vector(record: dict[str, Any]) -> tuple[list[float], list[str]]:
    market_map = {
        str(name): float(value)
        for name, value in zip(record["market_names"], record["market_x"])
    }
    values: list[float] = []
    names: list[str] = []
    for name in MARKET_FEATURES:
        values.append(float(market_map.get(name, 0.0)))
        names.append(f"market_{name}")

    champion = [float(record["champion_probs"][name]) for name in OUTCOMES]
    market = [float(record["market_probs"][name]) for name in OUTCOMES]
    for index, label in enumerate(OUTCOMES):
        values.append(champion[index])
        names.append(f"champion_{label}")
    for index, label in enumerate(OUTCOMES):
        values.append(champion[index] - market[index])
        names.append(f"champion_minus_market_{label}")

    values.extend((float(record["strength_gap"]), float(record["allocation_gap"])))
    names.extend(("strength_gap", "allocation_gap"))

    sample = dict(record.get("team_sample", {}))
    for key in TEAM_FEATURES:
        raw = e3b1.number(sample.get(key))
        if key == "ess" and raw is not None:
            raw = math.log1p(max(0.0, raw))
        values.extend((0.0 if raw is None else float(raw), 0.0 if raw is None else 1.0))
        names.extend((f"team_{key}", f"team_{key}_available"))

    for competition_id in b100.BIG5:
        values.append(1.0 if record["competition_id"] == competition_id else 0.0)
        names.append(f"league_{competition_id}")

    if len(values) != len(names):
        raise RuntimeError("match feature schema length mismatch")
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError("non-finite match feature")
    return values, names


def total_bases(total: int, maximum_total: int) -> tuple[np.ndarray, np.ndarray]:
    capped = min(total, 7)
    scaled = capped / 7.0
    base = np.asarray(
        [
            1.0,
            scaled,
            scaled * scaled,
            float(total == 0),
            float(total == 1),
            float(total == 2),
            float(total == 3),
            float(total == 4),
            float(total == 5),
            float(total == 6),
            float(total >= 7),
            float(total == maximum_total),
        ],
        dtype=float,
    )
    interaction = np.asarray(
        [
            scaled,
            scaled * scaled,
            float(total <= 2),
            float(total % 2 == 0),
            float(total >= 7),
            float(total == maximum_total),
        ],
        dtype=float,
    )
    return base, interaction


def conditional_bases(total: int, home: int) -> tuple[np.ndarray, np.ndarray]:
    away = total - home
    denominator = max(1, total)
    signed = (home - away) / denominator
    absolute = abs(home - away) / denominator
    draw = float(home == away)
    btts = float(home > 0 and away > 0)
    home_win = float(home > away)
    away_win = float(home < away)
    base = np.asarray(
        [
            1.0,
            signed,
            absolute,
            signed * signed,
            draw,
            btts,
            home_win,
            away_win,
            draw * float(total == 0),
            draw * float(total == 2),
            draw * float(total == 4),
            draw * float(total == 6),
            draw * float(total >= 8),
            float(abs(home - away) >= 3),
        ],
        dtype=float,
    )
    interaction = np.asarray(
        [
            signed,
            absolute,
            draw,
            btts,
            home_win - away_win,
            draw * float(total <= 4),
        ],
        dtype=float,
    )
    return base, interaction


def matrix_structure(record: dict[str, Any]) -> dict[str, Any]:
    by_total: dict[int, list[dict[str, Any]]] = defaultdict(list)
    total_mass: dict[int, float] = defaultdict(float)
    cells = []
    for cell in record["matrix"]:
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        probability = max(0.0, float(cell["probability"]))
        item = {"home_goals": home, "away_goals": away, "probability": probability}
        cells.append(item)
        by_total[home + away].append(item)
        total_mass[home + away] += probability
    probability_sum = sum(total_mass.values())
    if probability_sum <= 0:
        raise RuntimeError("matrix has no probability mass")
    totals = sorted(by_total)
    expected = list(range(min(totals), max(totals) + 1))
    if totals != expected or totals[0] != 0:
        raise RuntimeError("matrix total support is not contiguous from zero")
    for total in totals:
        ordered = sorted(by_total[total], key=lambda item: item["home_goals"])
        if [item["home_goals"] for item in ordered] != list(range(total + 1)):
            raise RuntimeError(f"illegal conditional support for total={total}")
        by_total[total] = ordered
    total_probability = np.asarray([total_mass[total] / probability_sum for total in totals])
    conditional = {}
    positive_total_mask = []
    for total in totals:
        mass = total_mass[total]
        positive = mass > EPS
        positive_total_mask.append(positive)
        if positive:
            conditional[total] = np.asarray(
                [float(item["probability"]) / mass for item in by_total[total]], dtype=float
            )
        else:
            # Structural cells exist, but the Champion tail can be exactly zero
            # after floating-point truncation. Keep that total at zero probability;
            # this placeholder conditional is never allowed to create mass.
            conditional[total] = np.full(total + 1, 1.0 / (total + 1), dtype=float)
    return {
        "cells": cells,
        "by_total": dict(by_total),
        "totals": totals,
        "total_probability": total_probability,
        "positive_total_mask": positive_total_mask,
        "conditional": conditional,
    }


def prepare_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise RuntimeError("no rows")
    raw_features = []
    feature_names = None
    structures = []
    total_support = None
    for record in rows:
        values, names = match_vector(record)
        if feature_names is None:
            feature_names = names
        elif names != feature_names:
            raise RuntimeError("match feature schema drift")
        structure = matrix_structure(record)
        if total_support is None:
            total_support = structure["totals"]
        elif structure["totals"] != total_support:
            raise RuntimeError("matrix total support drift")
        actual_total = int(record["actual_total"])
        actual_home = int(str(record["actual_score"]).split("-", 1)[0])
        if actual_total not in structure["by_total"]:
            raise RuntimeError(f"actual total outside matrix support: {actual_total}")
        actual_total_index = structure["totals"].index(actual_total)
        if not structure["positive_total_mask"][actual_total_index]:
            raise RuntimeError(f"actual total has zero prior support: {actual_total}")
        if not 0 <= actual_home <= actual_total:
            raise RuntimeError("actual home score is illegal for total")
        raw_features.append(values)
        structures.append(structure)
    return {
        "raw_features": np.asarray(raw_features, dtype=float),
        "feature_names": list(feature_names or []),
        "structures": structures,
        "totals": list(total_support or []),
    }


def design_metadata(totals: list[int], feature_count: int) -> dict[str, Any]:
    total_base_rows = []
    total_interaction_rows = []
    maximum = max(totals)
    for total in totals:
        base, interaction = total_bases(total, maximum)
        total_base_rows.append(base)
        total_interaction_rows.append(interaction)
    first_cond_base, first_cond_interaction = conditional_bases(0, 0)
    return {
        "total_base": np.asarray(total_base_rows, dtype=float),
        "total_interaction": np.asarray(total_interaction_rows, dtype=float),
        "total_base_dim": len(total_base_rows[0]),
        "total_interaction_dim": len(total_interaction_rows[0]),
        "conditional_base_dim": len(first_cond_base),
        "conditional_interaction_dim": len(first_cond_interaction),
        "feature_count": feature_count,
    }


def standardize_fit(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    means = raw.mean(axis=0)
    scales = raw.std(axis=0)
    scales = np.where(scales > 1e-10, scales, 1.0)
    return (raw - means) / scales, means, scales


def total_prior_matrix(structures: list[dict[str, Any]]) -> np.ndarray:
    prior = np.asarray([item["total_probability"] for item in structures], dtype=float)
    prior = np.maximum(0.0, prior)
    row_sum = prior.sum(axis=1, keepdims=True)
    if np.any(row_sum <= 0):
        raise RuntimeError("total prior row has no mass")
    prior /= row_sum
    return prior


def fit_total_head(
    x: np.ndarray,
    rows: list[dict[str, Any]],
    structures: list[dict[str, Any]],
    design: dict[str, Any],
    l2: float,
) -> dict[str, Any]:
    n = len(rows)
    total_base = design["total_base"]
    total_interaction = design["total_interaction"]
    base_dim = int(design["total_base_dim"])
    interaction_dim = int(design["total_interaction_dim"])
    feature_count = x.shape[1]
    totals = structures[0]["totals"]
    lookup = {total: index for index, total in enumerate(totals)}
    actual = np.asarray([lookup[int(row["actual_total"])] for row in rows], dtype=int)
    prior = total_prior_matrix(structures)
    offset = np.where(prior > 0.0, np.log(np.maximum(prior, EPS)), -1e30)
    onehot = np.zeros_like(prior)
    onehot[np.arange(n), actual] = 1.0

    def unpack(flat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        base_weights = flat[:base_dim]
        interaction_weights = flat[base_dim:].reshape(feature_count, interaction_dim)
        return base_weights, interaction_weights

    def objective(flat: np.ndarray) -> tuple[float, np.ndarray]:
        base_weights, interaction_weights = unpack(flat)
        logits = (
            offset
            + total_base @ base_weights
            + (x @ interaction_weights) @ total_interaction.T
        )
        probability = stable_softmax(logits)
        nll = -np.log(np.clip(probability[np.arange(n), actual], EPS, 1.0)).mean()
        penalty = 0.5 * l2 * (
            np.square(base_weights[1:]).sum() + np.square(interaction_weights).sum()
        ) / n
        error = probability - onehot
        grad_base = error.sum(axis=0) @ total_base / n
        grad_interaction = x.T @ (error @ total_interaction) / n
        grad_base[1:] += l2 * base_weights[1:] / n
        grad_interaction += l2 * interaction_weights / n
        gradient = np.concatenate((grad_base, grad_interaction.ravel()))
        return float(nll + penalty), gradient

    initial = np.zeros(base_dim + feature_count * interaction_dim)
    result = minimize(
        objective,
        initial,
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": MAX_ITER, "ftol": 1e-11, "gtol": 1e-7, "maxls": 30},
    )
    value, gradient = objective(np.asarray(result.x, dtype=float))
    base_weights, interaction_weights = unpack(np.asarray(result.x, dtype=float))
    success = bool(result.success) or float(np.linalg.norm(gradient)) <= OPT_GRAD_TOL
    return {
        "status": "TRAINED" if success else "FIT_FAILED",
        "base_weights": base_weights.tolist(),
        "interaction_weights": interaction_weights.tolist(),
        "optimizer": {
            "success": success,
            "scipy_success": bool(result.success),
            "status": int(result.status),
            "message": str(result.message),
            "iterations": int(result.nit),
            "evaluations": int(result.nfev),
            "objective": value,
            "gradient_norm": float(np.linalg.norm(gradient)),
        },
    }


def conditional_design(total: int) -> tuple[np.ndarray, np.ndarray]:
    base_rows = []
    interaction_rows = []
    for home in range(total + 1):
        base, interaction = conditional_bases(total, home)
        base_rows.append(base)
        interaction_rows.append(interaction)
    return np.asarray(base_rows, dtype=float), np.asarray(interaction_rows, dtype=float)


def fit_conditional_head(
    x: np.ndarray,
    rows: list[dict[str, Any]],
    structures: list[dict[str, Any]],
    design: dict[str, Any],
    l2: float,
) -> dict[str, Any]:
    n = len(rows)
    base_dim = int(design["conditional_base_dim"])
    interaction_dim = int(design["conditional_interaction_dim"])
    feature_count = x.shape[1]
    groups: dict[int, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[int(row["actual_total"])].append(index)

    def unpack(flat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        base_weights = flat[:base_dim]
        interaction_weights = flat[base_dim:].reshape(feature_count, interaction_dim)
        return base_weights, interaction_weights

    def objective(flat: np.ndarray) -> tuple[float, np.ndarray]:
        base_weights, interaction_weights = unpack(flat)
        total_loss = 0.0
        grad_base = np.zeros(base_dim)
        grad_interaction = np.zeros((feature_count, interaction_dim))
        for total, indices_list in groups.items():
            indices = np.asarray(indices_list, dtype=int)
            base_matrix, interaction_matrix = conditional_design(total)
            prior = np.asarray(
                [structures[index]["conditional"][total] for index in indices], dtype=float
            )
            prior = np.clip(prior, EPS, 1.0)
            prior /= prior.sum(axis=1, keepdims=True)
            logits = (
                np.log(prior)
                + base_matrix @ base_weights
                + (x[indices] @ interaction_weights) @ interaction_matrix.T
            )
            probability = stable_softmax(logits)
            actual_home = np.asarray(
                [int(str(rows[index]["actual_score"]).split("-", 1)[0]) for index in indices],
                dtype=int,
            )
            total_loss += float(
                -np.log(
                    np.clip(probability[np.arange(len(indices)), actual_home], EPS, 1.0)
                ).sum()
            )
            error = probability
            error[np.arange(len(indices)), actual_home] -= 1.0
            grad_base += error.sum(axis=0) @ base_matrix
            grad_interaction += x[indices].T @ (error @ interaction_matrix)

        penalty = 0.5 * l2 * (
            np.square(base_weights[1:]).sum() + np.square(interaction_weights).sum()
        )
        total_loss = (total_loss + penalty) / n
        grad_base[1:] += l2 * base_weights[1:]
        grad_interaction += l2 * interaction_weights
        gradient = np.concatenate((grad_base / n, grad_interaction.ravel() / n))
        return float(total_loss), gradient

    initial = np.zeros(base_dim + feature_count * interaction_dim)
    result = minimize(
        objective,
        initial,
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": MAX_ITER, "ftol": 1e-11, "gtol": 1e-7, "maxls": 30},
    )
    value, gradient = objective(np.asarray(result.x, dtype=float))
    base_weights, interaction_weights = unpack(np.asarray(result.x, dtype=float))
    success = bool(result.success) or float(np.linalg.norm(gradient)) <= OPT_GRAD_TOL
    return {
        "status": "TRAINED" if success else "FIT_FAILED",
        "base_weights": base_weights.tolist(),
        "interaction_weights": interaction_weights.tolist(),
        "optimizer": {
            "success": success,
            "scipy_success": bool(result.success),
            "status": int(result.status),
            "message": str(result.message),
            "iterations": int(result.nit),
            "evaluations": int(result.nfev),
            "objective": value,
            "gradient_norm": float(np.linalg.norm(gradient)),
        },
    }


def fit_model(rows: list[dict[str, Any]], l2: float) -> dict[str, Any]:
    counts = Counter(row["actual_outcome"] for row in rows)
    if len(rows) < MIN_TRAIN_ROWS:
        return {
            "status": "BASELINE_INSUFFICIENT_TRAIN",
            "rows": len(rows),
            "counts": dict(counts),
            "l2": l2,
        }
    prepared = prepare_rows(rows)
    x, means, scales = standardize_fit(prepared["raw_features"])
    design = design_metadata(prepared["totals"], x.shape[1])
    total_head = fit_total_head(x, rows, prepared["structures"], design, l2)
    conditional_head = fit_conditional_head(x, rows, prepared["structures"], design, l2)
    trained = (
        total_head["status"] == "TRAINED"
        and conditional_head["status"] == "TRAINED"
    )
    return {
        "status": "TRAINED" if trained else "FIT_FAILED",
        "rows": len(rows),
        "counts": dict(counts),
        "l2": l2,
        "feature_names": prepared["feature_names"],
        "means": means.tolist(),
        "scales": scales.tolist(),
        "totals": prepared["totals"],
        "design": {
            key: value.tolist() if isinstance(value, np.ndarray) else value
            for key, value in design.items()
        },
        "total_head": total_head,
        "conditional_head": conditional_head,
    }


def predict_model(model: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if model["status"] != "TRAINED":
        output = []
        for record in rows:
            item = dict(record)
            item["e3d1_matrix"] = [dict(cell) for cell in record["matrix"]]
            item["e3d1_probs"] = dict(record["champion_probs"])
            item["e3d1_status"] = "BASELINE_CHAMPION"
            item["e3d1_modeled"] = False
            output.append(item)
        return output

    prepared = prepare_rows(rows)
    if prepared["feature_names"] != model["feature_names"]:
        raise RuntimeError("prediction feature schema mismatch")
    if prepared["totals"] != model["totals"]:
        raise RuntimeError("prediction total support mismatch")
    raw = prepared["raw_features"]
    x = (raw - np.asarray(model["means"])) / np.asarray(model["scales"])
    design = {
        key: np.asarray(value, dtype=float)
        if key in ("total_base", "total_interaction")
        else value
        for key, value in model["design"].items()
    }
    total_base = design["total_base"]
    total_interaction = design["total_interaction"]
    total_base_weights = np.asarray(model["total_head"]["base_weights"])
    total_interaction_weights = np.asarray(model["total_head"]["interaction_weights"])
    prior_total = total_prior_matrix(prepared["structures"])
    total_logits = (
        np.where(prior_total > 0.0, np.log(np.maximum(prior_total, EPS)), -1e30)
        + total_base @ total_base_weights
        + (x @ total_interaction_weights) @ total_interaction.T
    )
    total_probability = stable_softmax(total_logits)

    cond_base_weights = np.asarray(model["conditional_head"]["base_weights"])
    cond_interaction_weights = np.asarray(model["conditional_head"]["interaction_weights"])
    output = []
    for row_index, (record, structure) in enumerate(zip(rows, prepared["structures"])):
        matrix = []
        outcome_probability = {name: 0.0 for name in OUTCOMES}
        for total_index, total in enumerate(structure["totals"]):
            base_matrix, interaction_matrix = conditional_design(total)
            prior_conditional = np.clip(structure["conditional"][total], EPS, 1.0)
            prior_conditional /= prior_conditional.sum()
            logits = (
                np.log(prior_conditional)
                + base_matrix @ cond_base_weights
                + (x[row_index] @ cond_interaction_weights) @ interaction_matrix.T
            )
            conditional_probability = stable_softmax(logits.reshape(1, -1))[0]
            for home, conditional_mass in enumerate(conditional_probability):
                away = total - home
                probability = float(total_probability[row_index, total_index] * conditional_mass)
                matrix.append(
                    {
                        "home_goals": int(home),
                        "away_goals": int(away),
                        "probability": probability,
                    }
                )
                outcome_probability[score_outcome(home, away)] += probability
        probability_sum = sum(float(cell["probability"]) for cell in matrix)
        if probability_sum <= 0:
            raise RuntimeError("prediction matrix has no mass")
        for cell in matrix:
            cell["probability"] = float(cell["probability"]) / probability_sum
        outcome_sum = sum(outcome_probability.values())
        outcome_probability = {
            name: value / outcome_sum for name, value in outcome_probability.items()
        }
        item = dict(record)
        item["e3d1_matrix"] = matrix
        item["e3d1_probs"] = outcome_probability
        item["e3d1_status"] = "MODELED"
        item["e3d1_modeled"] = True
        output.append(item)
    return output


def baseline_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "outcome": e3b1.metrics(rows, "champion_probs"),
        "score": e3b2.score_metrics(rows, "matrix"),
        "total": e3c1.total_metrics(rows, "matrix"),
    }


def candidate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "outcome": e3b1.metrics(rows, "e3d1_probs"),
        "score": e3b2.score_metrics(rows, "e3d1_matrix"),
        "total": e3c1.total_metrics(rows, "e3d1_matrix"),
    }


def guard_limit(value: float) -> float:
    return value * (1.0 + GUARD_RELATIVE)


def select_l2(inner_train: list[dict[str, Any]], validation: list[dict[str, Any]]) -> dict[str, Any]:
    if len(inner_train) < MIN_TRAIN_ROWS or len(validation) < MIN_VALID_ROWS:
        return {
            "status": "BASELINE_INSUFFICIENT_INNER_DATA",
            "l2": None,
            "inner_train_rows": len(inner_train),
            "validation_rows": len(validation),
            "leaderboard": [],
        }
    baseline = baseline_metrics(validation)
    leaderboard = []
    eligible = []
    for l2 in L2_GRID:
        model = fit_model(inner_train, l2)
        if model["status"] != "TRAINED":
            leaderboard.append(
                {
                    "l2": l2,
                    "status": model["status"],
                    "model": model,
                }
            )
            continue
        predicted = predict_model(model, validation)
        metrics = candidate_metrics(predicted)
        score_ok = (
            float(metrics["score"]["exact_score_logloss"])
            <= guard_limit(float(baseline["score"]["exact_score_logloss"]))
        )
        total_ok = (
            float(metrics["total"]["total_logloss"])
            <= guard_limit(float(baseline["total"]["total_logloss"]))
        )
        btts_ok = (
            float(metrics["score"]["btts_logloss"])
            <= guard_limit(float(baseline["score"]["btts_logloss"]))
        )
        outcome_improved = (
            float(metrics["outcome"]["logloss"])
            < float(baseline["outcome"]["logloss"]) - 1e-8
        )
        item = {
            "l2": l2,
            "status": "EVALUATED",
            "metrics": metrics,
            "guards": {
                "exact_score_logloss": score_ok,
                "total_logloss": total_ok,
                "btts_logloss": btts_ok,
                "outcome_logloss_improved": outcome_improved,
            },
            "model_optimizer": {
                "total": model["total_head"]["optimizer"],
                "conditional": model["conditional_head"]["optimizer"],
            },
        }
        leaderboard.append(item)
        if score_ok and total_ok and btts_ok and outcome_improved:
            eligible.append(item)
    if not eligible:
        return {
            "status": "BASELINE_NO_GUARDED_IMPROVEMENT",
            "l2": None,
            "inner_train_rows": len(inner_train),
            "validation_rows": len(validation),
            "guard_relative": GUARD_RELATIVE,
            "baseline": baseline,
            "leaderboard": leaderboard,
        }
    eligible.sort(
        key=lambda item: (
            float(item["metrics"]["outcome"]["logloss"]),
            -float(item["metrics"]["outcome"]["draw_f1"]),
            float(item["metrics"]["score"]["exact_score_logloss"]),
            float(item["metrics"]["total"]["total_logloss"]),
            float(item["l2"]),
        )
    )
    chosen = eligible[0]
    return {
        "status": "SELECTED_PRIOR_ONLY_GUARDED",
        "l2": float(chosen["l2"]),
        "inner_train_rows": len(inner_train),
        "validation_rows": len(validation),
        "guard_relative": GUARD_RELATIVE,
        "baseline": baseline,
        "leaderboard": leaderboard,
    }


def expanding_oos(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_year[int(row["season_start_year"])].append(row)
    years = sorted(by_year)
    output = []
    folds = []
    for target_year in years:
        prior_years = [year for year in years if year < target_year]
        validation_year = prior_years[-1] if prior_years else None
        inner_train_years = [
            year for year in prior_years if validation_year is not None and year < validation_year
        ]
        inner_train = [row for year in inner_train_years for row in by_year[year]]
        validation = by_year[validation_year] if validation_year is not None else []
        selection = select_l2(inner_train, validation)
        prior_rows = [row for year in prior_years for row in by_year[year]]
        if selection["l2"] is None:
            model = {
                "status": "BASELINE_SELECTION",
                "rows": len(prior_rows),
                "l2": None,
            }
        else:
            model = fit_model(prior_rows, float(selection["l2"]))
        current = sorted(
            by_year[target_year],
            key=lambda row: (row["date"], row["competition_id"], row["match_key"]),
        )
        predicted = predict_model(model, current)
        output.extend(predicted)
        folds.append(
            {
                "target_year": target_year,
                "prior_years": prior_years,
                "inner_train_years": inner_train_years,
                "validation_year": validation_year,
                "target_rows": len(current),
                "selection": selection,
                "final_model": model,
                "modeled_rows": sum(bool(row["e3d1_modeled"]) for row in predicted),
            }
        )
    return output, folds


def matrix_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    probability_residuals = []
    outcome_residuals = []
    total_conditional_residuals = []
    legal = True
    finite = True
    support_identity = True
    zero_probability_support_preserved = True
    for row in rows:
        matrix = row["e3d1_matrix"]
        total = sum(float(cell["probability"]) for cell in matrix)
        probability_residuals.append(abs(total - 1.0))
        finite = finite and all(
            math.isfinite(float(cell["probability"])) and float(cell["probability"]) >= 0.0
            for cell in matrix
        )
        legal = legal and all(
            int(cell["home_goals"]) >= 0 and int(cell["away_goals"]) >= 0
            for cell in matrix
        )
        prior_support = {
            (int(cell["home_goals"]), int(cell["away_goals"])) for cell in row["matrix"]
        }
        candidate_support = {
            (int(cell["home_goals"]), int(cell["away_goals"])) for cell in matrix
        }
        support_identity = support_identity and prior_support == candidate_support
        prior_probability = {
            (int(cell["home_goals"]), int(cell["away_goals"])): float(cell["probability"])
            for cell in row["matrix"]
        }
        zero_probability_support_preserved = (
            zero_probability_support_preserved
            and all(
                prior_probability[(int(cell["home_goals"]), int(cell["away_goals"]))] > EPS
                or float(cell["probability"]) <= PROB_TOL
                for cell in matrix
            )
        )

        derived_outcome = {name: 0.0 for name in OUTCOMES}
        by_total: dict[int, float] = defaultdict(float)
        for cell in matrix:
            home = int(cell["home_goals"])
            away = int(cell["away_goals"])
            probability = float(cell["probability"])
            derived_outcome[score_outcome(home, away)] += probability
            by_total[home + away] += probability
        outcome_residuals.append(
            max(
                abs(derived_outcome[name] - float(row["e3d1_probs"][name]))
                for name in OUTCOMES
            )
        )
        conditional_sum_residual = 0.0
        for total_value, mass in by_total.items():
            if mass <= PROB_TOL:
                continue
            conditional_sum = sum(
                float(cell["probability"]) / mass
                for cell in matrix
                if int(cell["home_goals"]) + int(cell["away_goals"]) == total_value
            )
            conditional_sum_residual = max(
                conditional_sum_residual, abs(conditional_sum - 1.0)
            )
        total_conditional_residuals.append(conditional_sum_residual)
    return {
        "count": len(rows),
        "all_finite_nonnegative": finite,
        "all_legal_score_coordinates": legal,
        "support_identity": support_identity,
        "zero_probability_support_preserved": zero_probability_support_preserved,
        "max_probability_residual": max(probability_residuals, default=None),
        "max_outcome_derivation_residual": max(outcome_residuals, default=None),
        "max_conditional_normalization_residual": max(
            total_conditional_residuals, default=None
        ),
        "modeled_count": sum(bool(row["e3d1_modeled"]) for row in rows),
        "baseline_count": sum(not bool(row["e3d1_modeled"]) for row in rows),
    }


def section_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "market_outcome": e3b1.metrics(rows, "market_probs"),
        "champion_outcome": e3b1.metrics(rows, "champion_probs"),
        "e3b1_outcome": e3b1.metrics(rows, "e3b1_probs"),
        "e3d1_outcome": e3b1.metrics(rows, "e3d1_probs"),
        "champion_score": e3b2.score_metrics(rows, "matrix"),
        "e3d1_score": e3b2.score_metrics(rows, "e3d1_matrix"),
        "champion_total": e3c1.total_metrics(rows, "matrix"),
        "e3d1_total": e3c1.total_metrics(rows, "e3d1_matrix"),
        "audit": matrix_audit(rows),
    }


def per_league(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for competition_id, name in b100.BIG5.items():
        subset = [row for row in rows if row["competition_id"] == competition_id]
        item = section_metrics(subset)
        item["competition_zh"] = name
        result[competition_id] = item
    return result


def build_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_rows = []
    joins = {}
    base_folds = {}
    failures = []
    for competition_id in b100.BIG5:
        try:
            season_rows, folds = e3a.nested_competition(competition_id)
            rows, audit = e3b1.join(competition_id, season_rows)
            all_rows.extend(rows)
            joins[competition_id] = audit
            base_folds[competition_id] = folds
        except Exception as exc:
            failures.append(
                {
                    "competition_id": competition_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    if failures or not all_rows:
        raise RuntimeError(json.dumps(failures or [{"error": "no rows"}], ensure_ascii=False))
    e3b1_rows, e3b1_folds = e3b1.expanding_oos(all_rows)
    return e3b1_rows, {
        "join_audit": joins,
        "base_parameter_folds": base_folds,
        "e3b1_folds": e3b1_folds,
    }


def promotion_gate(full: dict[str, Any]) -> dict[str, Any]:
    per_domain = full["per_league"]
    outcome_better_market = (
        float(full["e3d1_outcome"]["logloss"])
        < float(full["market_outcome"]["logloss"])
    )
    draw_better_e3b1 = (
        float(full["e3d1_outcome"]["draw_f1"])
        > float(full["e3b1_outcome"]["draw_f1"])
    )
    score_not_worse = (
        float(full["e3d1_score"]["exact_score_logloss"])
        <= float(full["champion_score"]["exact_score_logloss"])
    )
    total_not_worse = (
        float(full["e3d1_total"]["total_logloss"])
        <= float(full["champion_total"]["total_logloss"])
    )
    all_leagues_outcome_better_market = all(
        float(item["e3d1_outcome"]["logloss"])
        < float(item["market_outcome"]["logloss"])
        for item in per_domain.values()
    )
    candidate = all(
        (
            outcome_better_market,
            draw_better_e3b1,
            score_not_worse,
            total_not_worse,
            all_leagues_outcome_better_market,
        )
    )
    return {
        "candidate": candidate,
        "outcome_logloss_better_than_market": outcome_better_market,
        "draw_f1_better_than_e3b1": draw_better_e3b1,
        "score_logloss_not_worse_than_champion": score_not_worse,
        "total_logloss_not_worse_than_champion": total_not_worse,
        "all_leagues_outcome_logloss_better_than_market": all_leagues_outcome_better_market,
    }


def markdown(report: dict[str, Any]) -> str:
    full = report["full_oos"]
    b100_section = report["b100"]
    lines = [
        "# E3d-1 Direct Legal (T,D) Joint Head",
        "",
        "Research-only; formal_weight=0; no automatic promotion.",
        "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Full rolling OOS: {full['count']}",
        f"- Modeled / baseline: {full['audit']['modeled_count']} / {full['audit']['baseline_count']}",
        f"- Fixed B100: {b100_section['count']}",
        "",
        "## Architecture",
        "",
        "- Direct total head: `P(T=t|X)`.",
        "- Conditional legal allocation: `P(D=d|T=t,X)` represented as `P(H=h|T=t,X)`.",
        "- Final score matrix: `P(T=t|X) * P(H=h|T=t,X)` with `A=t-h`.",
        "- Champion matrix is only a logarithmic offset; E3b-1/E3c-1 are not targets.",
        "",
        "## Full OOS outcome metrics",
        "",
        "| Model | Accuracy | Balanced | Macro-F1 | Draw P | Draw R | Draw F1 | LogLoss | Brier | RPS | ECE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, key in (
        ("Market", "market_outcome"),
        ("Champion", "champion_outcome"),
        ("E3b-1", "e3b1_outcome"),
        ("E3d-1", "e3d1_outcome"),
    ):
        metrics = full[key]
        lines.append(
            f"| {label} | {metrics['accuracy']:.4%} | {metrics['balanced_accuracy']:.4%} | "
            f"{metrics['macro_f1']:.4%} | {metrics['draw_precision']:.4%} | "
            f"{metrics['draw_recall']:.4%} | {metrics['draw_f1']:.4%} | "
            f"{metrics['logloss']:.6f} | {metrics['brier']:.6f} | "
            f"{metrics['rps']:.6f} | {metrics['confidence_ece_10bin']:.6f} |"
        )
    lines.extend(
        (
            "",
            "## Joint matrix metrics",
            "",
            "| Matrix | Exact Top-1 | Exact Top-3 | Exact LL | BTTS Brier | BTTS LL | Total LL | Total Brier | U2.5 Brier |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        )
    )
    for label, score_key, total_key in (
        ("Champion", "champion_score", "champion_total"),
        ("E3d-1", "e3d1_score", "e3d1_total"),
    ):
        score = full[score_key]
        total = full[total_key]
        lines.append(
            f"| {label} | {score['exact_score_top1_accuracy']:.4%} | "
            f"{score['exact_score_top3_coverage']:.4%} | {score['exact_score_logloss']:.6f} | "
            f"{score['btts_brier']:.6f} | {score['btts_logloss']:.6f} | "
            f"{total['total_logloss']:.6f} | {total['total_brier']:.6f} | "
            f"{total['under25_brier']:.6f} |"
        )
    lines.extend(
        (
            "",
            "## Per-league outcome audit",
            "",
            "| League | N | Market LL | E3d-1 LL | E3d-1 Accuracy | Draw F1 | Exact LL delta | Total LL delta |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        )
    )
    for item in full["per_league"].values():
        lines.append(
            f"| {item['competition_zh']} | {item['count']} | "
            f"{item['market_outcome']['logloss']:.6f} | "
            f"{item['e3d1_outcome']['logloss']:.6f} | "
            f"{item['e3d1_outcome']['accuracy']:.4%} | "
            f"{item['e3d1_outcome']['draw_f1']:.4%} | "
            f"{item['e3d1_score']['exact_score_logloss'] - item['champion_score']['exact_score_logloss']:+.6f} | "
            f"{item['e3d1_total']['total_logloss'] - item['champion_total']['total_logloss']:+.6f} |"
        )
    lines.extend(
        (
            "",
            "## Fixed B100",
            "",
            f"- E3d-1 Accuracy: {b100_section['e3d1_outcome']['accuracy']:.4%}",
            f"- E3d-1 Draw F1: {b100_section['e3d1_outcome']['draw_f1']:.4%}",
            f"- E3d-1 Exact Top-1 / Top-3: "
            f"{b100_section['e3d1_score']['exact_score_top1_accuracy']:.4%} / "
            f"{b100_section['e3d1_score']['exact_score_top3_coverage']:.4%}",
            "",
            "## Verdict",
            "",
            f"- Implementation/audit: {report['research_status']}",
            f"- Promotion candidate: {report['promotion_gate']['candidate']}",
            "- Formal weight remains 0.",
            "- B100 cannot override the full rolling-OOS gate.",
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
        rows, lineage = build_records()
        evaluated, folds = expanding_oos(rows)
        failures = [row for row in evaluated if row["e3d1_status"] not in ("MODELED", "BASELINE_CHAMPION")]
        if failures:
            raise RuntimeError(f"E3d-1 prediction failures: {len(failures)}")

        by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in evaluated:
            by_competition[row["competition_id"]].append(row)
        b100_rows, selection = e3a.fixed_b100(by_competition)
        expected_b100 = b100.TARGET_PER_LEAGUE * len(b100.BIG5)

        full = section_metrics(evaluated)
        fixed = section_metrics(b100_rows)
        full["per_league"] = per_league(evaluated)
        fixed["per_league"] = per_league(b100_rows)
        fixed["selection"] = selection

        audit = full["audit"]
        passed = (
            len(b100_rows) == expected_b100
            and audit["all_finite_nonnegative"]
            and audit["all_legal_score_coordinates"]
            and audit["support_identity"]
            and audit["zero_probability_support_preserved"]
            and float(audit["max_probability_residual"]) <= PROB_TOL
            and float(audit["max_outcome_derivation_residual"]) <= PROB_TOL
            and float(audit["max_conditional_normalization_residual"]) <= PROB_TOL
        )
        report = {
            "schema_version": "1.0",
            "research_status": "PASS" if passed else "FAIL",
            "repository_head": repository_head(),
            "scope": "90_minutes_including_stoppage",
            "experiment": "E3D1_DIRECT_LEGAL_TD_JOINT_HEAD",
            "architecture": {
                "source_space": "legal joint (T,D), represented as T and H|T",
                "direct_total_head": True,
                "conditional_goal_difference_head": True,
                "score_matrix_by_construction": True,
                "e3b1_or_e3c1_training_target": False,
                "champion_role": "logarithmic probability offset only",
                "match_feature_count": len(match_vector(rows[0])[0]),
                "l2_grid": list(L2_GRID),
                "selection_primary": "outcome LogLoss on latest earlier-season OOS",
                "selection_guards": {
                    "exact_score_logloss_relative": GUARD_RELATIVE,
                    "total_logloss_relative": GUARD_RELATIVE,
                    "btts_logloss_relative": GUARD_RELATIVE,
                },
            },
            "full_oos": full,
            "b100": fixed,
            "folds": folds,
            "lineage": lineage,
            "audit": {
                "b100_count_contract": "PASS" if len(b100_rows) == expected_b100 else "FAIL",
                "matrix": audit,
                "target_season_used_for_selection_or_training": False,
            },
            "promotion_gate": promotion_gate(full),
            "promotion": {
                "automatic_promotion": False,
                "formal_weight": 0,
                "status": "CHALLENGE_LAYER_ONLY",
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
            "experiment": "E3D1_DIRECT_LEGAL_TD_JOINT_HEAD",
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

    json_path = output_dir / "e3d1_direct_td_joint_head.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["research_status"] == "PASS":
        (output_dir / "e3d1_direct_td_joint_head.md").write_text(
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
                            "e3d1_outcome",
                            "champion_score",
                            "e3d1_score",
                            "champion_total",
                            "e3d1_total",
                            "audit",
                        )
                    },
                    "b100": {
                        key: report.get("b100", {}).get(key)
                        for key in (
                            "count",
                            "market_outcome",
                            "champion_outcome",
                            "e3b1_outcome",
                            "e3d1_outcome",
                            "champion_score",
                            "e3d1_score",
                        )
                    },
                    "promotion_gate": report.get("promotion_gate"),
                    "failures": report.get("failures"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0 if report["research_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
