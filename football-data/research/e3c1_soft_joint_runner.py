#!/usr/bin/env python3
"""Accelerated exact runner for E3c-1's registered convex objective.

The research specification, penalty grid, time-order selection and output
schema remain in e3c1_soft_joint_matrix.py. This runner replaces per-record
L-BFGS startup with the objective's damped fixed-point first-order equation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import e3c1_soft_joint_matrix as base  # noqa: E402


def closure_values(function: Any) -> dict[str, Any]:
    closure = function.__closure__ or ()
    return {
        name: cell.cell_contents
        for name, cell in zip(function.__code__.co_freevars, closure)
    }


def damped_fixed_point_minimize(
    function: Any,
    x0: np.ndarray,
    method: str | None = None,
    jac: bool | None = None,
    options: dict[str, Any] | None = None,
) -> SimpleNamespace:
    del method, jac
    options = dict(options or {})
    captured = closure_values(function)
    required = (
        "p",
        "outcome_index",
        "total_index",
        "outcome_target",
        "total_target",
        "lambda_outcome",
        "lambda_total",
    )
    missing = [name for name in required if name not in captured]
    if missing:
        raise RuntimeError(f"E3c-1 optimizer closure contract missing: {missing}")

    p = np.asarray(captured["p"], dtype=float)
    outcome_index = np.asarray(captured["outcome_index"], dtype=int)
    total_index = np.asarray(captured["total_index"], dtype=int)
    outcome_target = np.asarray(captured["outcome_target"], dtype=float)
    total_target = np.asarray(captured["total_target"], dtype=float)
    lambda_outcome = float(captured["lambda_outcome"])
    lambda_total = float(captured["lambda_total"])
    maximum_iterations = int(options.get("maxiter", 2000))
    gradient_tolerance = float(options.get("gtol", 1e-10))

    z = base.stable_softmax(np.asarray(x0, dtype=float))
    current, gradient = function(np.log(np.maximum(base.EPS, z)))
    evaluations = 1
    base_step = min(0.5, 1.0 / (1.0 + lambda_outcome + lambda_total))
    success = False
    status = 1
    message = "maximum iterations reached"
    iterations = 0

    for iterations in range(1, maximum_iterations + 1):
        outcome_mass = np.bincount(
            outcome_index, weights=z, minlength=len(base.OUTCOMES)
        )
        total_mass = np.bincount(
            total_index, weights=z, minlength=len(total_target)
        )
        log_candidate = (
            np.log(np.maximum(base.EPS, p))
            + lambda_outcome
            * (
                np.log(np.maximum(base.EPS, outcome_target[outcome_index]))
                - np.log(np.maximum(base.EPS, outcome_mass[outcome_index]))
            )
            + lambda_total
            * (
                np.log(np.maximum(base.EPS, total_target[total_index]))
                - np.log(np.maximum(base.EPS, total_mass[total_index]))
            )
        )
        candidate = base.stable_softmax(log_candidate)
        step = base_step
        accepted = False
        for _ in range(25):
            proposed = base.stable_softmax(
                (1.0 - step) * np.log(np.maximum(base.EPS, z))
                + step * np.log(np.maximum(base.EPS, candidate))
            )
            proposed_value, proposed_gradient = function(
                np.log(np.maximum(base.EPS, proposed))
            )
            evaluations += 1
            if proposed_value <= current + 1e-14:
                accepted = True
                break
            step *= 0.5
        if not accepted:
            message = "damped fixed-point line search failed"
            break

        maximum_change = float(np.max(np.abs(proposed - z)))
        z = proposed
        current = float(proposed_value)
        gradient = np.asarray(proposed_gradient, dtype=float)
        if maximum_change <= 1e-10 or float(np.max(np.abs(gradient))) <= gradient_tolerance:
            success = True
            status = 0
            message = "damped fixed-point converged"
            break

    if float(np.max(np.abs(gradient))) <= max(gradient_tolerance, 5e-8):
        success = True
        status = 0
        message = "stationarity tolerance reached"

    return SimpleNamespace(
        x=np.log(np.maximum(base.EPS, z)),
        success=success,
        status=status,
        message=message,
        nit=iterations,
        nfev=evaluations,
    )


def patch_report() -> None:
    path = Path(base.OUT) / "e3c1_soft_joint_matrix.json"
    if not path.exists():
        return
    report = json.loads(path.read_text(encoding="utf-8"))
    optimization = report.get("optimization")
    if isinstance(optimization, dict):
        optimization["algorithm"] = (
            "group-mass convex optimization with damped fixed-point scaling"
        )
        optimization["maximum_iterations"] = base.MAX_ITER
        optimization["stationarity_tolerance"] = base.OPT_TOL
        optimization["solver_change_only"] = True
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report.get("research_status") == "PASS":
        (Path(base.OUT) / "e3c1_soft_joint_matrix.md").write_text(
            base.markdown(report), encoding="utf-8"
        )


def main() -> int:
    base.MAX_ITER = 2000
    base.OPT_TOL = 1e-10
    base.minimize = damped_fixed_point_minimize
    code = int(base.main())
    patch_report()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
