"""Protocol adapters for native R43 score-matrix components.

R43Q is an optional research market-score baseline candidate. In the unified
inference path it can consume 1X2/AH/OU only from a legal atomic PIT market
record. R43T remains a stateful score-matrix component with explicit kickoff-group
lifecycle. Neither changes the formal V500 default baseline or promotion state.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping

import numpy as np

from components.r43q_market_score_core import R43QMarketScoreCore, score_matrix
from components.r43t_dynamic_bivariate_state import R43TDynamicBivariateState
from pipeline.unified_inference import (
    ConsumerFeatureEvidence,
    FixtureRequest,
    canonical_matrix,
    matrix_hash,
)
from pit.feature_store import PITReadResult, PointInTimeFeatureStore


MARKET_FEATURE_FAMILY = "market_1x2_ah_ou"
MARKET_NUMERICAL_FEATURE_NAMES = (
    "one_x_two_odds",
    "asian_handicap",
    "over_under",
    "snapshot_timestamp_utc",
)


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _utc(value: str) -> datetime:
    token = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(token)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise RuntimeError("market snapshot timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


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
    """PIT-bound optional exact R43Q market-score baseline.

    The baseline is not formal default. It deliberately accepts no direct market
    payload: its three market surfaces must be present in one PIT record so their
    timing and source lineage are part of the numerical activation receipt.
    """

    component_id = "R43Q_market_score_baseline"
    component_version = "r43gov0-m8-q-pit-baseline-v2"
    formal_default = False
    pit_bound_market = True
    source_blob_sha = R43QMarketScoreCore.source_blob_sha
    required_numeric_feature_families = (MARKET_FEATURE_FAMILY,)

    def __init__(self, pit_store: PointInTimeFeatureStore):
        self.pit_store = pit_store
        self._last_evidence: tuple[ConsumerFeatureEvidence, ...] = ()

    def _read_atomic_market_snapshot(self, request: FixtureRequest) -> tuple[PITReadResult, Mapping[str, Any]]:
        pit = self.pit_store.read(
            MARKET_FEATURE_FAMILY,
            request.fixture_id,
            request.as_of,
            canonical_entity_id=request.fixture_id,
            require_historical_use=True,
        )
        record = pit.latest()
        if record is None:
            raise RuntimeError("R43Q market PIT record unavailable at requested as_of")
        if record.entity_type != "fixture_market":
            raise RuntimeError("R43Q market PIT record entity_type must be fixture_market")
        if record.canonical_entity_id != request.fixture_id:
            raise RuntimeError("R43Q market PIT fixture identity mismatch")
        value = record.value
        if not isinstance(value, Mapping):
            raise RuntimeError("R43Q market PIT value must be a mapping")
        missing = [name for name in MARKET_NUMERICAL_FEATURE_NAMES if name not in value]
        if missing:
            raise RuntimeError(f"R43Q atomic market snapshot missing fields: {missing}")
        snapshot_at = _utc(str(value["snapshot_timestamp_utc"]))
        if snapshot_at > record.observed_at:
            raise RuntimeError("R43Q market snapshot timestamp is later than observed_at")
        if snapshot_at > request.as_of.astimezone(timezone.utc):
            raise RuntimeError("R43Q market snapshot is later than prediction as_of")

        selected = PITReadResult(
            status=pit.status,
            feature_family=pit.feature_family,
            fixture_id=pit.fixture_id,
            canonical_entity_id=pit.canonical_entity_id,
            as_of=pit.as_of,
            records=(record,),
            rejected_counts=pit.rejected_counts,
            store_fingerprint=pit.store_fingerprint,
        )
        return selected, value

    def predict(
        self,
        request: FixtureRequest,
        canonical_home_team_id: str,
        canonical_away_team_id: str,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if payload:
            raise RuntimeError("R43Q unified baseline forbids direct payload; use PIT market record")
        pit, value = self._read_atomic_market_snapshot(request)
        try:
            one_x_two_odds = value["one_x_two_odds"]
            asian_handicap = value["asian_handicap"]
            over_under = value["over_under"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("R43Q PIT baseline requires atomic 1X2/AH/OU surfaces") from exc

        built = R43QMarketScoreCore.build(one_x_two_odds, asian_handicap, over_under)
        cells = dense_to_cells(built["score_matrix"])
        record = pit.latest()
        assert record is not None
        numerical_values = {
            "snapshot_timestamp_utc": value["snapshot_timestamp_utc"],
            "one_x_two_odds": one_x_two_odds,
            "asian_handicap": asian_handicap,
            "over_under": over_under,
            "source_record_hash": record.record_hash,
        }
        input_hash = _stable_hash({
            "feature_family": MARKET_FEATURE_FAMILY,
            "record_hash": record.record_hash,
            "numerical_values": numerical_values,
        })
        output_hash = matrix_hash(cells)
        self._last_evidence = (
            ConsumerFeatureEvidence(
                feature_family=MARKET_FEATURE_FAMILY,
                pit_result=pit,
                numerical_values=numerical_values,
                numerical_feature_names=MARKET_NUMERICAL_FEATURE_NAMES,
                component_input_hash=input_hash,
                component_output_hash=output_hash,
                consumer_id=self.component_id,
            ),
        )
        return cells

    def numerical_feature_evidence(self) -> tuple[ConsumerFeatureEvidence, ...]:
        return self._last_evidence


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