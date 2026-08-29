"""Disabled score-matrix wrappers for legacy components whose native output is 1X2.

R43R and R43Y sources define probability transforms, not score-cell transforms.
These wrappers preserve each matrix outcome class's conditional score shape via
outcome_mass_matrix_transport. They fail closed unless the incoming matrix's
aggregate 1X2 exactly matches the source vector declared in the payload.
"""
from __future__ import annotations

from typing import Any, Mapping

from components.outcome_mass_matrix_transport import assert_1x2_match, lift_1x2_target
from components.r43r_football_residual import residual_prob
from components.r43y_draw_calibration import calibrate
from pipeline.unified_inference import FixtureRequest, one_x_two


class R43RScoreMatrixTransportComponent:
    component_id = "R43R_strong_shrink_football_residual_matrix_transport"
    component_version = "r43gov0-m5g-r-transport-v1"

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)

    def apply(
        self,
        matrix: list[dict[str, Any]],
        request: FixtureRequest | None,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            market = payload["r43r_market_probabilities"]
            football = payload["r43r_football_probabilities"]
            beta = float(payload["r43r_beta"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("R43R matrix transport requires market/football/beta payload") from exc
        assert_1x2_match(one_x_two(matrix), market)
        target = residual_prob(market, football, beta)
        return lift_1x2_target(matrix, target)


class R43YScoreMatrixTransportComponent:
    component_id = "R43Y_draw_calibration_matrix_transport"
    component_version = "r43gov0-m5g-y-transport-v1"

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)

    def apply(
        self,
        matrix: list[dict[str, Any]],
        request: FixtureRequest | None,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            source = payload["r43y_source_r43u0_probabilities"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("R43Y matrix transport requires source R43U0 1X2 payload") from exc
        assert_1x2_match(one_x_two(matrix), source)
        target = calibrate(source)
        return lift_1x2_target(matrix, target)
