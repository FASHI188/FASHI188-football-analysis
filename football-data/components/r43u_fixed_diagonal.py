"""Exact compatibility migration of R43U0 fixed diagonal inflation.

Source: football3/r43u0-fixed-diagonal-inflation
Blob: 4ad46cca4acb618068f6db2601cf96bad4109698
The component is deliberately disabled by default.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from pipeline.unified_inference import FixtureRequest, canonical_matrix


SOURCE_BLOB_SHA = "4ad46cca4acb618068f6db2601cf96bad4109698"
DIAGONAL_FACTOR = 1.25


class R43UFixedDiagonalInflationComponent:
    component_id = "R43U_fixed_diagonal_inflation"
    component_version = "r43gov0-m5b-u-v1"

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)

    def apply(
        self,
        matrix: list[dict[str, Any]],
        request: FixtureRequest | None,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        cells = canonical_matrix(matrix)
        max_home = max(cell["home_goals"] for cell in cells)
        max_away = max(cell["away_goals"] for cell in cells)
        dense = np.zeros((max_home + 1, max_away + 1), dtype=float)
        present: list[tuple[int, int]] = []
        for cell in cells:
            h = int(cell["home_goals"])
            a = int(cell["away_goals"])
            dense[h, a] = float(cell["probability"])
            present.append((h, a))

        # Exact R43U0 operation migrated from source: copy, multiply every h==a
        # cell by 1.25, then normalize by the full matrix sum.
        z = np.array(dense, dtype=float, copy=True)
        for i in range(min(z.shape)):
            z[i, i] *= DIAGONAL_FACTOR
        z /= z.sum()

        return [
            {"home_goals": h, "away_goals": a, "probability": float(z[h, a])}
            for h, a in present
        ]
