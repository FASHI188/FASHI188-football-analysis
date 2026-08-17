#!/usr/bin/env python3
"""Execution shim for R6.

The evaluator's output-row selection includes identity labels already present in KEYS.
Deduplicate those output columns before the score join. This is an engineering-only shim:
no sample, feature, model, probability, gate, or metric changes.
"""
from __future__ import annotations

import evaluate_market6_gd0_integration_r6 as r6

_original_attach_scores = r6._attach_scores


def _attach_scores_dedup(frame, raw):
    clean = frame.loc[:, ~frame.columns.duplicated()].copy()
    return _original_attach_scores(clean, raw)


r6._attach_scores = _attach_scores_dedup

if __name__ == "__main__":
    r6.run()
