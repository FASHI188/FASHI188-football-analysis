#!/usr/bin/env python3
"""Correctness wrapper for the V4.8 direct categorical total-goals research run.

The underlying challenger uses one RPS helper for both 8-bin totals and 3-way
1X2.  This wrapper replaces that helper at runtime with the dimension-correct
strictly proper RPS denominator (K-1) before any training or evaluation occurs.
"""
from __future__ import annotations

import sys
from pathlib import Path

VALIDATION_DIR = Path(__file__).resolve().parent
ENGINE_DIR = VALIDATION_DIR.parents[0] / "engine"
for item in (str(VALIDATION_DIR), str(ENGINE_DIR)):
    if item not in sys.path:
        sys.path.insert(0, item)

import direct_total_distribution_challenger_v480 as challenger


def dimension_correct_rps(values: list[float], actual_index: int) -> float:
    cp = 0.0
    co = 0.0
    score = 0.0
    for index in range(len(values) - 1):
        cp += float(values[index])
        co += 1.0 if actual_index == index else 0.0
        score += (cp - co) ** 2
    return score / max(1, len(values) - 1)


challenger._rps = dimension_correct_rps


if __name__ == "__main__":
    raise SystemExit(challenger.main())
