#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from draw_auto_research_math_r1 import canonical_json_sha256, hda_from_draw_and_elo, logit

BASELINE_CONFIG: dict[str, Any] = {
    "schema_version": "DRAW-INDEPENDENT-BASELINE-R1.4",
    "name": "INDEPENDENT_ELO_HISTORICAL_DRAW_BASELINE_R1",
    "claim_scope": "independent viewed-development research baseline; not the current formal model",
    "draw_prior": "Laplace-smoothed draw rate from the outer-training rows only",
    "home_away_allocation": "pre-match elo_difference_with_home_advantage",
    "candidate_parameters_used": [],
    "randomness": "none",
}


def baseline_identity() -> dict[str, Any]:
    return {"config": BASELINE_CONFIG, "canonical_json_sha256": canonical_json_sha256(BASELINE_CONFIG)}


def baseline_predictions(train_rows: Sequence[Any], target_rows: Sequence[Any]) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    if not train_rows or not target_rows:
        raise ValueError("baseline rows missing")
    draws = sum(getattr(row, "label") == "D" for row in train_rows)
    draw_rate = (draws + 1.0) / (len(train_rows) + 2.0)
    draw = np.full(len(target_rows), draw_rate, dtype=float)
    elo = np.asarray([getattr(row, "values")["elo_difference_with_home_advantage"] for row in target_rows], dtype=float)
    elo = np.where(np.isfinite(elo), elo, 60.0)
    prediction = hda_from_draw_and_elo(draw, elo)
    offset = np.full(len(target_rows), logit(draw_rate), dtype=float)
    receipt = {
        "baseline_name": BASELINE_CONFIG["name"],
        "baseline_config_sha256": canonical_json_sha256(BASELINE_CONFIG),
        "train_rows": len(train_rows),
        "target_rows": len(target_rows),
        "smoothed_draw_rate": draw_rate,
        "candidate_parameters_used": [],
    }
    return prediction, offset, receipt
