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

OUT = ROOT / "manifests" / "ger_three_surface_market_projection_v536_status.json"
TOL = original.TOL
EPS = original.EPS


def _fast_ah_projection(matrix, line: float, target_w_over_l: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Same minimum-KL exponential AH projection as v534, with safeguarded Newton root solving.

    The constraint, support, target ratio and tolerance are unchanged. Newton only accelerates
    solving the same monotone scalar moment equation; bisection remains the fail-safe.
    """
    rows = []
    for h, a, p in score_matrix_rows(matrix):
        w, l = original._cell_ah_components(h, a, line)
        g = w - target_w_over_l * l
        rows.append((h, a, float(p), float(g)))

    current = original._ah_moment(matrix, line, target_w_over_l)
    if abs(current) <= TOL:
        q = original._ah_quantities(matrix, line)
        return original.base._renormalize(matrix), {
            "theta_ah": 0.0,
            "ah_moment_residual": abs(current),
            "target_W_over_L": target_w_over_l,
            "achieved_W_over_L": q["W_over_L"],
            "root_method": "already_satisfied",
            "root_iterations": 0,
        }

    positive_rows = [(h, a, p, g) for h, a, p, g in rows if p > 0.0]
    if not positive_rows:
        raise PlatformError("AH projection prior has no positive support")
    g_values = [g for _, _, _, g in positive_rows]
    if min(g_values) > 0.0 or max(g_values) < 0.0:
        raise PlatformError("AH settlement-ratio constraint infeasible on prior support")

    def evaluate(theta: float) -> tuple[float, float, list[float]]:
        logs = [math.log(p) + theta * g for _, _, p, g in positive_rows]
        anchor = max(logs)
        weights = [math.exp(value - anchor) for value in logs]
        z = sum(weights)
        probs = [weight / z for weight in weights]
        moment = sum(prob * row[3] for prob, row in zip(probs, positive_rows))
        second = sum(prob * (row[3] ** 2) for prob, row in zip(probs, positive_rows))
        variance = max(0.0, second - moment * moment)
        return moment, variance, probs

    lo, hi = -80.0, 80.0
    lo_m, _, _ = evaluate(lo)
    hi_m, _, _ = evaluate(hi)
    if lo_m > 0.0 or hi_m < 0.0:
        raise PlatformError(f"AH exponential tilt failed to bracket zero: lo={lo_m} hi={hi_m}")

    theta = 0.0
    probs: list[float] = []
    root_iterations = 0
    for root_iterations in range(1, 81):
        moment, variance, probs = evaluate(theta)
        if abs(moment) <= min(TOL * 0.1, 1e-12):
            break
        if moment < 0.0:
            lo = theta
        else:
            hi = theta
        if variance > 1e-18:
            proposal = theta - moment / variance
        else:
            proposal = math.nan
        if not math.isfinite(proposal) or proposal <= lo or proposal >= hi:
            proposal = (lo + hi) / 2.0
        theta = proposal
    else:
        # Deterministic bisection fallback to the same original bracket/tolerance.
        for extra in range(1, 121):
            theta = (lo + hi) / 2.0
            moment, _, probs = evaluate(theta)
            root_iterations = 80 + extra
            if abs(moment) <= min(TOL * 0.1, 1e-12):
                break
            if moment < 0.0:
                lo = theta
            else:
                hi = theta

    moment, _, probs = evaluate(theta)
    if abs(moment) > TOL:
        raise PlatformError(f"fast AH root did not meet frozen tolerance: residual={abs(moment)}")

    probability_map = {(h, a): prob for prob, (h, a, _p, _g) in zip(probs, positive_rows)}
    out = [
        {
            "home_goals": h,
            "away_goals": a,
            "probability": probability_map.get((h, a), 0.0) if p > 0.0 else 0.0,
        }
        for h, a, p, _g in rows
    ]
    out = original.base._renormalize(out)
    q = original._ah_quantities(out, line)
    residual = abs(q["W"] - target_w_over_l * q["L"])
    if residual > TOL:
        raise PlatformError(f"fast AH projected matrix misses frozen tolerance: residual={residual}")
    return out, {
        "theta_ah": theta,
        "ah_moment_residual": residual,
        "target_W_over_L": target_w_over_l,
        "achieved_W_over_L": q["W_over_L"],
        "W": q["W"],
        "L": q["L"],
        "root_method": "safeguarded_newton_bisection",
        "root_iterations": root_iterations,
    }


def _write_failure(exc: BaseException) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "V5.3.6-ger-three-surface-fast-diagnostic-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": original.CID,
        "season": original.SEASON,
        "status": "EXECUTION_FAIL",
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "formal_weight_change": False,
        "probability_change": False,
        "formal_pit_market_eligible": False,
        "governance": "Execution optimization only. Frozen 1X2/OU/AH constraints, support and 1e-10 convergence gate are unchanged.",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    original._ah_projection = _fast_ah_projection
    original.OUT = OUT
    try:
        code = original.main()
        payload = json.loads(OUT.read_text(encoding="utf-8"))
        payload["schema_version"] = "V5.3.6-ger-three-surface-fast-r1"
        payload["execution_implementation"] = "safeguarded_newton_bisection_same_frozen_constraints"
        payload["formal_weight_change"] = False
        payload["probability_change"] = False
        payload["formal_pit_market_eligible"] = False
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return int(code or 0)
    except BaseException as exc:
        _write_failure(exc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
