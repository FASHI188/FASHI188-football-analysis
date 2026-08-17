#!/usr/bin/env python3
"""Execution shim for R6 engineering-only compatibility fixes.

1) Deduplicate output identity columns before the score join.
2) Restore the derived ``gd0 = (goal_difference == 0)`` field expected by the frozen
   R5 binary-head helper when R6 passes its chronological fit frame.

Neither fix changes sample identities, market6 features, C, model targets, probability
replacement rules, gates, or metrics.
"""
from __future__ import annotations

import evaluate_market6_gd0_integration_r6 as r6

_original_attach_scores = r6._attach_scores
_original_binary_probability = r6._binary_probability


def _attach_scores_dedup(frame, raw):
    clean = frame.loc[:, ~frame.columns.duplicated()].copy()
    return _original_attach_scores(clean, raw)


def _binary_probability_with_gd0(fit, test, features, base_cfg, C):
    fit_clean = fit.copy()
    if "gd0" not in fit_clean.columns:
        if "goal_difference" not in fit_clean.columns:
            raise r6.ResearchError("R6 engineering shim cannot derive gd0: goal_difference missing")
        fit_clean["gd0"] = (fit_clean["goal_difference"].astype(int) == 0).astype(int)
    return _original_binary_probability(fit_clean, test, features, base_cfg, C)


r6._attach_scores = _attach_scores_dedup
r6._binary_probability = _binary_probability_with_gd0

if __name__ == "__main__":
    r6.run()
