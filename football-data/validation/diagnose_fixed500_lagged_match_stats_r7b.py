#!/usr/bin/env python3
from __future__ import annotations

import json
import numpy as np

import diagnose_fixed500_lagged_match_stats_r7 as r7

_original_float_value = r7.float_value


def _safe_float_value(row, field):
    value = _original_float_value(row, field)
    return np.nan if value is None else float(value)


# Implementation-only repair: downstream R7 logic, sample, features and thresholds are unchanged.
r7.float_value = _safe_float_value


def main() -> None:
    x = r7.run()
    print(json.dumps({
        "verdict": x["scientific_verdict"],
        "sample": x["sample"],
        "coverage": x["stat_coverage"],
        "best": x["best_on_stats_ready_ou"],
        "cohorts": x["cohorts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
