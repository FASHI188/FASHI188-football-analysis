"""Frozen V500 score-matrix adapter for the unified Football3 inference path.

This adapter does not reimplement V500. It accepts a score matrix produced by the
frozen V500 code blob and verifies its declared lineage/hash before handing the
matrix to the shared inference engine unchanged.
"""
from __future__ import annotations

from typing import Any, Mapping

from pipeline.unified_inference import FixtureRequest, canonical_matrix, matrix_hash


FROZEN_V500_BLOB_SHA = "9c302506c49aa1847e60cd7896fc1a80f3b6b457"


class FrozenV500MatrixBaseline:
    component_id = "v500_frozen_score_matrix"
    component_version = "r43gov0-m4-v1"

    def predict(
        self,
        request: FixtureRequest,
        canonical_home_team_id: str,
        canonical_away_team_id: str,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if str(payload.get("source_model_blob_sha") or "") != FROZEN_V500_BLOB_SHA:
            raise ValueError("V500 source_model_blob_sha mismatch")
        raw = payload.get("score_matrix")
        if raw is None:
            raise ValueError("frozen V500 score_matrix is required")
        matrix = canonical_matrix(raw)
        actual_hash = matrix_hash(matrix)
        declared_hash = payload.get("score_matrix_hash")
        if declared_hash is not None and str(declared_hash) != actual_hash:
            raise ValueError("frozen V500 score_matrix_hash mismatch")
        return matrix
