#!/usr/bin/env python3
"""Technical compatibility runner for C067.

The checked-in R6 orchestration has two deterministic implementation defects that
prevent exact reproduction before any C067 science is reached:

1. the R5 binary helper expects the derived column
   `gd0 = (goal_difference == 0)`, while R6 does not materialize it;
2. R6 constructs its score-attachment frame as `KEYS + KEYS + market6_complete`,
   which creates duplicate identity column labels and makes pandas merge fail.

This runner repairs only those deterministic data-frame plumbing defects at the R6
helper boundaries. It does not change any R5/R6/C067 feature, coefficient, threshold,
split, target definition, probability formula, metric, or development gate.
"""
from __future__ import annotations

import pandas as pd

import evaluate_market6_gd0_integration_r6 as r6


_original_binary_probability = r6._binary_probability
_original_attach_scores = r6._attach_scores


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


def _attach_scores_unique_columns(frame: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    if frame.columns.duplicated().any():
        frame = frame.loc[:, ~frame.columns.duplicated()].copy()
    if frame.columns.duplicated().any():
        raise RuntimeError("R6 compatibility repair failed to normalize duplicate columns")
    return _original_attach_scores(frame, raw)


r6._binary_probability = _binary_probability_with_gd0
r6._attach_scores = _attach_scores_unique_columns

from evaluate_c067_draw_residual_activator_r1 import run  # noqa: E402


if __name__ == "__main__":
    run()
