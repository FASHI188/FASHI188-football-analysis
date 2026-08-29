"""Exact research-governance migration of R43U0 fixed diagonal inflation.

Pinned source commit: 3983d9168ca51234a810ede379d97c62afa3fff8
Pinned source blob: 4ad46cca4acb618068f6db2601cf96bad4109698

Historical R43U0 passed its postview architecture gate, but did not reach the
0.53 full-volume accuracy floor. R43U1 forward confirmation has not yet
settled any predictions. Therefore this component remains disabled by default.
"""
from __future__ import annotations

from typing import Any

import numpy as np

DIAGONAL_FACTOR = 1.25
SOURCE_COMMIT = "3983d9168ca51234a810ede379d97c62afa3fff8"
SOURCE_BLOB_SHA = "4ad46cca4acb618068f6db2601cf96bad4109698"


def inflate(matrix: np.ndarray) -> np.ndarray:
    z = np.array(matrix, dtype=float, copy=True)
    for i in range(min(z.shape)):
        z[i, i] *= DIAGONAL_FACTOR
    z /= z.sum()
    return z


class R43UDiagonalGain:
    component_id = "R43U_fixed_diagonal_inflation"
    component_version = "r43gov0-m5e-u-v1"
    enabled = False
    diagonal_factor = DIAGONAL_FACTOR
    source_commit = SOURCE_COMMIT
    source_blob_sha = SOURCE_BLOB_SHA
    historical_architecture_gate_passed = True
    historical_full_volume_53pct_target_met = False
    historical_breakthrough_candidate = True
    historical_action = "FREEZE_DIAGONAL_INFLATION_FOR_NEW_FORWARD_CONFIRMATION"
    forward_locked_predictions = 41
    forward_settled_predictions = 0
    forward_confirmation_passed = False
    forward_action = "CONTINUE_FORWARD_ACCUMULATION_NO_RETUNING"

    @staticmethod
    def apply(score_matrix: np.ndarray) -> np.ndarray:
        return inflate(score_matrix)

    @classmethod
    def receipt(cls) -> dict[str, Any]:
        return {
            "component_id": cls.component_id,
            "component_version": cls.component_version,
            "enabled": cls.enabled,
            "diagonal_factor": cls.diagonal_factor,
            "source_commit": cls.source_commit,
            "source_blob_sha": cls.source_blob_sha,
            "historical_architecture_gate_passed": cls.historical_architecture_gate_passed,
            "historical_full_volume_53pct_target_met": cls.historical_full_volume_53pct_target_met,
            "historical_breakthrough_candidate": cls.historical_breakthrough_candidate,
            "historical_action": cls.historical_action,
            "forward_locked_predictions": cls.forward_locked_predictions,
            "forward_settled_predictions": cls.forward_settled_predictions,
            "forward_confirmation_passed": cls.forward_confirmation_passed,
            "forward_action": cls.forward_action,
        }
