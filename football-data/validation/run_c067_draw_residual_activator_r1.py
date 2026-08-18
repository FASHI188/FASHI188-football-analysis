#!/usr/bin/env python3
"""Technical compatibility runner for C067.

The recorded R6 implementation calls the R5 binary helper, which expects a derived
`gd0` column, but the checked-in R6 orchestration does not materialize that derived
column before the call.  The original R5 definition is exactly
`gd0 = (goal_difference == 0)`.

This runner restores that deterministic derived column at the helper boundary and does
not change any R5/R6/C067 feature, coefficient, threshold, split, target definition, or
scientific/development gate.
"""
from __future__ import annotations

import pandas as pd

import evaluate_market6_gd0_integration_r6 as r6


_original_binary_probability = r6._binary_probability


def _binary_probability_with_gd0(
    fit: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    base_cfg: dict,
    C: float,
):
    fit2 = fit.copy()
    test2 = test.copy()
    if "gd0" not in fit2.columns:
        if "goal_difference" not in fit2.columns:
            raise RuntimeError("R6 compatibility repair cannot derive gd0: goal_difference missing")
        fit2["gd0"] = (fit2["goal_difference"].astype(int) == 0).astype(int)
    if "gd0" not in test2.columns and "goal_difference" in test2.columns:
        test2["gd0"] = (test2["goal_difference"].astype(int) == 0).astype(int)
    return _original_binary_probability(fit2, test2, features, base_cfg, C)


r6._binary_probability = _binary_probability_with_gd0

from evaluate_c067_draw_residual_activator_r1 import run  # noqa: E402


if __name__ == "__main__":
    run()
