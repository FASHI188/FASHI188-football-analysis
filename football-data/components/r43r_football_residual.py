"""Exact research-governance migration of R43R0 strong-shrink football residual.
Source commit b76ecc841e7b52320d73a7045874765386c2f8e6; blob 8748e795bb92780c47af934c3187db14c254a415.
Disabled for formal inference. Historical R43R architecture gate failed; do not retune on the same settled overlap.
"""
from __future__ import annotations

import math
from typing import Any

from scipy.optimize import minimize_scalar

CLASSES = ("home", "draw", "away")
RIDGE_PENALTY = 40.0
BETA_BOUNDS = (-0.5, 0.5)
SOURCE_COMMIT = "b76ecc841e7b52320d73a7045874765386c2f8e6"
SOURCE_BLOB_SHA = "8748e795bb92780c47af934c3187db14c254a415"


def probs(d: dict) -> dict[str, float]:
    v = {k: float(d[k]) for k in CLASSES}
    if any((not math.isfinite(x) or x <= 0.0) for x in v.values()):
        raise ValueError("invalid probability")
    s = sum(v.values())
    return {k: v[k] / s for k in CLASSES}


def residual_prob(pm: dict[str, float], pf: dict[str, float], beta: float) -> dict[str, float]:
    # Market is the offset. Football contributes only its log-probability residual.
    z = {}
    for k in CLASSES:
        r = math.log(max(pf[k], 1e-15)) - math.log(max(pm[k], 1e-15))
        z[k] = math.log(max(pm[k], 1e-15)) + float(beta) * r
    mx = max(z.values())
    e = {k: math.exp(z[k] - mx) for k in CLASSES}
    s = sum(e.values())
    return {k: e[k] / s for k in CLASSES}


def fit_beta(train: list[dict]) -> float:
    def objective(beta: float) -> float:
        loss = 0.0
        for r in train:
            p = residual_prob(r["market"], r["football"], beta)
            loss -= math.log(max(p[r["y"]], 1e-15))
        loss += 0.5 * RIDGE_PENALTY * beta * beta
        return float(loss)

    res = minimize_scalar(
        objective,
        bounds=BETA_BOUNDS,
        method="bounded",
        options={"xatol": 1e-10, "maxiter": 300},
    )
    return float(res.x)


class R43RFootballResidual:
    component_id = "R43R_strong_shrink_football_residual"
    component_version = "r43gov0-m5d-r-v1"
    enabled = False
    source_commit = SOURCE_COMMIT
    source_blob_sha = SOURCE_BLOB_SHA
    ridge_penalty = RIDGE_PENALTY
    beta_bounds = BETA_BOUNDS
    historical_architecture_gate_passed = False
    historical_breakthrough_candidate = False
    historical_action = "DO_NOT_PROMOTE_AND_DO_NOT_RETUNE_ON_THIS_SETTLED_OVERLAP"

    @staticmethod
    def normalize(probabilities: dict) -> dict[str, float]:
        return probs(probabilities)

    @staticmethod
    def fit(history: list[dict]) -> float:
        return fit_beta(history)

    @staticmethod
    def apply(market: dict[str, float], football: dict[str, float], beta: float) -> dict[str, float]:
        return residual_prob(market, football, beta)

    @classmethod
    def receipt(cls, beta: float | None = None) -> dict[str, Any]:
        return {
            "component_id": cls.component_id,
            "component_version": cls.component_version,
            "enabled": cls.enabled,
            "source_commit": cls.source_commit,
            "source_blob_sha": cls.source_blob_sha,
            "ridge_penalty": cls.ridge_penalty,
            "beta_bounds": list(cls.beta_bounds),
            "beta": beta,
            "historical_architecture_gate_passed": cls.historical_architecture_gate_passed,
            "historical_breakthrough_candidate": cls.historical_breakthrough_candidate,
            "historical_action": cls.historical_action,
        }
