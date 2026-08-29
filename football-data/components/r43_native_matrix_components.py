"""Protocol adapters for native R43 score-matrix components.

R43Q is exposed as an optional market-score baseline candidate. R43T is exposed
as a stateful ScoreMatrixComponent whose kickoff-group lifecycle must be driven
explicitly. Neither changes the formal V500 default baseline or promotion state.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import numpy as np

from components.r43q_market_score_core import R43QMarketScoreCore, score_matrix
from components.r43t_dynamic_bivariate_state import R43TDynamicBivariateState
from pipeline.unified_inference import FixtureRequest, canonical_matrix, matrix_hash


def dense_to_cells(matrix: np.ndarray) -> list[dict[str, Any]]:
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2:
        raise ValueError("score matrix must be two-dimensional")
    return canonical_matrix([
        {"home_goals": int(h), "away_goals": int(a), "probability": float(m[h, a])}
        for h in range(m.shape[0])
        for a in range(m.shape[1])
    ])


class R43QMarketScoreBaseline:
    """Optional exact R43Q market-score baseline; never the formal default."""

    component_id = "R43Q_market_score_baseline"
    component_version = "r43gov0-m5h-q-baseline-v1"
    formal_default = False
    source_blob_sha = R43QMarketScoreCore.source_blob_sha

    def predict(
        self,
        request: FixtureRequest,
        canonical_home_team_id: str,
        canonical_away_team_id: str,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            one_x_two_odds = payload["one_x_two_odds"]
            asian_handicap = payload["asian_handicap"]
            over_under = payload["over_under"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("R43Q baseline requires 1X2/AH/OU same-timestamp payload") from exc
        built = R43QMarketScoreCore.build(one_x_two_odds, asian_handicap, over_under)
        return dense_to_cells(built["score_matrix"])


class R43TDynamicStateMatrixComponent:
    """Exact R43T state projection wrapped in the unified matrix protocol.

    The incoming matrix must equal the exact static R43Q Poisson matrix for the
    supplied static lambdas. A kickoff group must be opened before any projection;
    all group outcomes are settled only after all predictions have been frozen.
    """

    component_id = "R43T_dynamic_bivariate_residual_state_matrix"
    component_version = "r43gov0-m6-t-matrix-v2"

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)
        self.state = R43TDynamicBivariateState()
        self._projection_receipts: list[dict[str, float]] = []

    def begin_group(self) -> None:
        self.state.begin_group()
        self._projection_receipts = []

    def apply(
        self,
        matrix: list[dict[str, Any]],
        request: FixtureRequest | None,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            lh = float(payload["r43t_static_lambda_home"])
            la = float(payload["r43t_static_lambda_away"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("R43T matrix component requires static lambda payload") from exc

        expected = dense_to_cells(score_matrix(lh, la))
        if matrix_hash(matrix) != matrix_hash(expected):
            raise RuntimeError("R43T source static matrix mismatch")

        projection = self.state.project(lh, la)
        self._projection_receipts.append({
            "static_lambda_home": lh,
            "static_lambda_away": la,
            "dynamic_lambda_home": projection.lambda_home,
            "dynamic_lambda_away": projection.lambda_away,
            "state_total_pred": projection.state_total_pred,
            "state_diff_pred": projection.state_diff_pred,
        })
        return dense_to_cells(score_matrix(projection.lambda_home, projection.lambda_away))

    @staticmethod
    def settlement_observation(
        component_payload: Mapping[str, Any],
        actual_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build the exact post-group R43T observation from frozen inputs + result."""
        try:
            return {
                "lambda_home": float(component_payload["r43t_static_lambda_home"]),
                "lambda_away": float(component_payload["r43t_static_lambda_away"]),
                "hg": int(actual_result["home_goals_90"]),
                "ag": int(actual_result["away_goals_90"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("R43T settlement requires frozen static lambdas and 90-minute score") from exc

    def settle_group(self, observations: Iterable[Mapping[str, Any]]) -> None:
        self.state.settle_group(observations)

    def projection_receipts(self) -> tuple[Mapping[str, float], ...]:
        return tuple(dict(r) for r in self._projection_receipts)

    def snapshot(self) -> dict[str, Any]:
        out = self.state.snapshot()
        out["wrapper_enabled"] = self.enabled
        out["projection_receipts"] = [dict(r) for r in self._projection_receipts]
        return out
