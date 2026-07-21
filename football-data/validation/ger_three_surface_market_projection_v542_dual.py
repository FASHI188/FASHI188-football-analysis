#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for path in (VALIDATION, ENGINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ger_three_surface_market_projection_v534 as original
from platform_core import PlatformError, score_matrix_rows

OUT = ROOT / "manifests" / "ger_three_surface_market_projection_v542_status.json"
TOL = original.TOL


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [list(row) + [float(rhs)] for row, rhs in zip(a, b)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-16:
            raise ArithmeticError("singular Newton Hessian")
        m[col], m[pivot] = m[pivot], m[col]
        div = m[col][col]
        m[col] = [v / div for v in m[col]]
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col]
            if factor == 0.0:
                continue
            m[r] = [rv - factor * cv for rv, cv in zip(m[r], m[col])]
    return [m[i][-1] for i in range(n)]


def _direct_three_surface_project(prior, one, ou, line: float, target_w_over_l: float):
    rows = []
    for h, a, p in score_matrix_rows(prior):
        if p <= 0.0:
            rows.append((h, a, float(p), None))
            continue
        w, l = original._cell_ah_components(h, a, line)
        g = w - target_w_over_l * l
        x = [
            1.0 if h > a else 0.0,
            1.0 if h == a else 0.0,
            1.0 if h + a >= 3 else 0.0,
            float(g),
        ]
        rows.append((h, a, float(p), x))

    positive = [(h, a, p, x) for h, a, p, x in rows if p > 0.0 and x is not None]
    if not positive:
        raise PlatformError("three-surface prior has no positive support")

    target = [float(one["home"]), float(one["draw"]), float(ou["over"]), 0.0]
    lam = [0.0, 0.0, 0.0, 0.0]

    def evaluate(lmb: list[float]):
        logs = [math.log(p) + sum(li * xi for li, xi in zip(lmb, x)) for _h, _a, p, x in positive]
        anchor = max(logs)
        weights = [math.exp(v - anchor) for v in logs]
        z = sum(weights)
        probs = [w / z for w in weights]
        means = [sum(q * row[3][j] for q, row in zip(probs, positive)) for j in range(4)]
        grad = [means[j] - target[j] for j in range(4)]
        cov = [[0.0] * 4 for _ in range(4)]
        for q, row in zip(probs, positive):
            x = row[3]
            for j in range(4):
                for k in range(4):
                    cov[j][k] += q * (x[j] - means[j]) * (x[k] - means[k])
        return probs, means, grad, cov

    iterations = 0
    for iterations in range(1, 101):
        probs, means, grad, cov = evaluate(lam)
        norm = max(abs(v) for v in grad)
        if norm <= 1e-12:
            break
        delta = None
        for ridge in (0.0, 1e-14, 1e-12, 1e-10, 1e-8):
            hess = [row[:] for row in cov]
            for j in range(4):
                hess[j][j] += ridge
            try:
                delta = _solve_linear(hess, grad)
                break
            except ArithmeticError:
                continue
        if delta is None:
            raise PlatformError("three-surface dual Newton Hessian remained singular")

        current_obj = sum(v * v for v in grad)
        accepted = False
        step = 1.0
        for _ in range(30):
            proposal = [li - step * di for li, di in zip(lam, delta)]
            _p2, _m2, g2, _c2 = evaluate(proposal)
            new_obj = sum(v * v for v in g2)
            if new_obj < current_obj:
                lam = proposal
                accepted = True
                break
            step *= 0.5
        if not accepted:
            raise PlatformError(f"three-surface dual Newton line search failed; residual={norm}")
    else:
        raise PlatformError("three-surface dual Newton exceeded 100 iterations")

    probs, _means, grad, _cov = evaluate(lam)
    pmap = {(h, a): q for q, (h, a, _p, _x) in zip(probs, positive)}
    out = [
        {"home_goals": h, "away_goals": a, "probability": pmap.get((h, a), 0.0) if p > 0.0 else 0.0}
        for h, a, p, _x in rows
    ]
    out = original.base._renormalize(out)
    constraint = original.base._constraint_residual(out, one, ou)
    ah_residual = abs(original._ah_moment(out, line, target_w_over_l))
    max_residual = max(float(constraint["max_residual"]), ah_residual)
    if max_residual > TOL:
        raise PlatformError(f"direct dual KL misses frozen constraints: residual={max_residual}")
    psum = sum(p for _h, _a, p in score_matrix_rows(out))
    q = original._ah_quantities(out, line)
    audit = {
        "iterations": iterations,
        "one_x_two_max_residual": constraint["one_x_two_max_residual"],
        "ou25_max_residual": constraint["ou25_max_residual"],
        "theta_ah": lam[3],
        "ah_moment_residual": ah_residual,
        "target_W_over_L": target_w_over_l,
        "achieved_W_over_L": q["W_over_L"],
        "W": q["W"],
        "L": q["L"],
        "max_constraint_residual": max_residual,
        "converged": True,
        "probability_sum_residual": abs(psum - 1.0),
        "kl_from_formal_matrix": original.base._kl(out, prior),
        "dual_lambdas": {
            "home": lam[0], "draw": lam[1], "over25": lam[2], "ah_moment": lam[3]
        },
        "solver": "direct_minimum_KL_dual_Newton_4_linear_constraints"
    }
    return out, audit


def main() -> int:
    original._three_surface_project = _direct_three_surface_project
    original.OUT = OUT
    try:
        code = original.main()
        payload = json.loads(OUT.read_text(encoding="utf-8"))
        payload["schema_version"] = "V5.4.2-GER-three-surface-direct-dual-r1"
        payload["execution_implementation"] = "direct_minimum_KL_dual_Newton_same_frozen_market_constraints"
        payload["formal_weight_change"] = False
        payload["probability_change"] = False
        payload["formal_pit_market_eligible"] = False
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return int(code or 0)
    except BaseException as exc:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "schema_version": "V5.4.2-GER-three-surface-direct-dual-diagnostic-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "competition_id": original.CID,
            "season": original.SEASON,
            "status": "EXECUTION_FAIL",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "formal_weight_change": False,
            "probability_change": False,
            "formal_pit_market_eligible": False
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
