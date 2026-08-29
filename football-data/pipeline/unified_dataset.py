"""Unified Football3 prediction-dataset generation for dataset/replay/live modes.

All numerical predictions go through UnifiedInferenceEngine. Historical outcomes
are attached only after every prediction in the same kickoff group has been
frozen; enabled stateful components are updated only after that group boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping

from pipeline.unified_inference import FeatureReadSpec, FixtureRequest, PredictionResult, UnifiedInferenceEngine

CLASSES = ("home", "draw", "away")
HISTORICAL_MODES = {"dataset", "replay"}
ALL_MODES = HISTORICAL_MODES | {"live"}


def _dt(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _dt(value, "datetime").isoformat()


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SettledOutcome:
    home_goals_90: int
    away_goals_90: int

    def __post_init__(self) -> None:
        if int(self.home_goals_90) < 0 or int(self.away_goals_90) < 0:
            raise ValueError("90-minute goals must be non-negative")
        object.__setattr__(self, "home_goals_90", int(self.home_goals_90))
        object.__setattr__(self, "away_goals_90", int(self.away_goals_90))

    @property
    def actual_result(self) -> str:
        if self.home_goals_90 > self.away_goals_90:
            return "home"
        if self.home_goals_90 < self.away_goals_90:
            return "away"
        return "draw"

    def to_dict(self) -> dict[str, Any]:
        return {
            "home_goals_90": self.home_goals_90,
            "away_goals_90": self.away_goals_90,
            "actual_result": self.actual_result,
        }


@dataclass(frozen=True)
class PredictionCase:
    request: FixtureRequest
    kickoff_at: datetime
    baseline_payload: Mapping[str, Any]
    feature_specs: tuple[FeatureReadSpec, ...] = ()
    component_payload: Mapping[str, Any] | None = None
    outcome: SettledOutcome | None = None
    competition_id: str | None = None

    def __post_init__(self) -> None:
        kickoff = _dt(self.kickoff_at, "kickoff_at")
        as_of = _dt(self.request.as_of, "request.as_of")
        if not as_of < kickoff:
            raise ValueError("prediction as_of must be strictly before kickoff_at")
        object.__setattr__(self, "kickoff_at", kickoff)
        object.__setattr__(self, "feature_specs", tuple(self.feature_specs))


@dataclass(frozen=True)
class PredictionDatasetRow:
    mode: str
    fixture_id: str
    competition_id: str | None
    as_of: datetime
    kickoff_at: datetime
    canonical_home_team_id: str
    canonical_away_team_id: str
    probabilities: Mapping[str, float]
    top1: str
    score_matrix_hash: str
    feature_activation_receipt: Mapping[str, Any]
    component_chain: tuple[Mapping[str, Any], ...]
    prediction_numerics_hash: str
    actual_result: str | None
    home_goals_90: int | None
    away_goals_90: int | None
    row_hash: str

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["as_of"] = _iso(self.as_of)
        out["kickoff_at"] = _iso(self.kickoff_at)
        out["probabilities"] = dict(self.probabilities)
        out["component_chain"] = [dict(x) for x in self.component_chain]
        return out


def _prediction_hash(result: PredictionResult) -> str:
    return _stable_hash({
        "fixture_id": result.fixture_id,
        "canonical_home_team_id": result.canonical_home_team_id,
        "canonical_away_team_id": result.canonical_away_team_id,
        "probabilities": dict(result.probabilities),
        "top1": result.top1,
        "score_matrix_hash": result.score_matrix_hash,
        "component_chain": [dict(x) for x in result.component_chain],
        "feature_activation_receipt_hash": result.feature_activation_receipt.get("receipt_hash"),
    })


def _row_from_prediction(mode: str, case: PredictionCase, result: PredictionResult) -> PredictionDatasetRow:
    numerics_hash = _prediction_hash(result)
    outcome = case.outcome
    settled = outcome.to_dict() if outcome is not None and mode in HISTORICAL_MODES else None
    row_payload = {
        "mode": mode,
        "fixture_id": result.fixture_id,
        "competition_id": case.competition_id,
        "as_of": _iso(case.request.as_of),
        "kickoff_at": _iso(case.kickoff_at),
        "prediction_numerics_hash": numerics_hash,
        "actual_result": settled["actual_result"] if settled else None,
        "home_goals_90": settled["home_goals_90"] if settled else None,
        "away_goals_90": settled["away_goals_90"] if settled else None,
    }
    return PredictionDatasetRow(
        mode=mode,
        fixture_id=result.fixture_id,
        competition_id=case.competition_id,
        as_of=_dt(case.request.as_of, "request.as_of"),
        kickoff_at=case.kickoff_at,
        canonical_home_team_id=result.canonical_home_team_id,
        canonical_away_team_id=result.canonical_away_team_id,
        probabilities=dict(result.probabilities),
        top1=result.top1,
        score_matrix_hash=result.score_matrix_hash,
        feature_activation_receipt=dict(result.feature_activation_receipt),
        component_chain=tuple(dict(x) for x in result.component_chain),
        prediction_numerics_hash=numerics_hash,
        actual_result=settled["actual_result"] if settled else None,
        home_goals_90=settled["home_goals_90"] if settled else None,
        away_goals_90=settled["away_goals_90"] if settled else None,
        row_hash=_stable_hash(row_payload),
    )


class UnifiedDatasetGenerator:
    def __init__(self, engine: UnifiedInferenceEngine):
        self.engine = engine

    def _active_lifecycle_components(self) -> tuple[Any, ...]:
        out = []
        for component in self.engine.components:
            if not bool(getattr(component, "enabled", False)):
                continue
            begin = callable(getattr(component, "begin_group", None))
            settle = callable(getattr(component, "settle_group", None))
            if begin != settle:
                raise RuntimeError(f"incomplete kickoff-group lifecycle on {component.component_id}")
            if begin:
                if not callable(getattr(component, "settlement_observation", None)):
                    raise RuntimeError(f"missing settlement_observation on {component.component_id}")
                out.append(component)
        return tuple(out)

    def generate(self, mode: str, cases: Iterable[PredictionCase]) -> tuple[PredictionDatasetRow, ...]:
        if mode not in ALL_MODES:
            raise ValueError(f"unsupported mode: {mode}")
        ordered = sorted(tuple(cases), key=lambda c: (c.kickoff_at, c.request.fixture_id))
        fixture_ids = [c.request.fixture_id for c in ordered]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("duplicate fixture_id in dataset generation")
        if mode == "live" and any(c.outcome is not None for c in ordered):
            raise ValueError("live generation must not receive settled outcomes")

        lifecycle = self._active_lifecycle_components()
        if mode == "live" and lifecycle:
            raise RuntimeError("enabled stateful components require a separately governed pending-settlement live lifecycle")

        rows: list[PredictionDatasetRow] = []
        i = 0
        while i < len(ordered):
            kickoff = ordered[i].kickoff_at
            group: list[PredictionCase] = []
            while i < len(ordered) and ordered[i].kickoff_at == kickoff:
                group.append(ordered[i])
                i += 1

            if lifecycle and mode in HISTORICAL_MODES:
                if any(case.outcome is None for case in group):
                    raise RuntimeError("historical stateful replay requires outcomes for every fixture in kickoff group")
                for component in lifecycle:
                    component.begin_group()

            frozen: list[tuple[PredictionCase, PredictionResult]] = []
            for case in group:
                result = self.engine.predict(
                    mode,
                    case.request,
                    case.baseline_payload,
                    feature_specs=case.feature_specs,
                    component_payload=case.component_payload,
                )
                frozen.append((case, result))

            # Results are accessed only after all predictions in this kickoff group are frozen.
            if lifecycle and mode in HISTORICAL_MODES:
                for component in lifecycle:
                    observations = [
                        component.settlement_observation(case.component_payload or {}, case.outcome.to_dict())
                        for case, _ in frozen
                        if case.outcome is not None
                    ]
                    component.settle_group(observations)

            rows.extend(_row_from_prediction(mode, case, result) for case, result in frozen)

        return tuple(rows)


def dataset_fingerprint(rows: Iterable[PredictionDatasetRow]) -> str:
    ordered = sorted((row.row_hash for row in rows))
    return _stable_hash(ordered)


def time_ordered_folds(rows: Iterable[PredictionDatasetRow], k: int) -> tuple[tuple[PredictionDatasetRow, ...], ...]:
    items = sorted(tuple(rows), key=lambda r: (r.kickoff_at, r.fixture_id))
    if k < 2:
        raise ValueError("k must be at least 2")
    groups: list[list[PredictionDatasetRow]] = []
    for row in items:
        if not groups or groups[-1][0].kickoff_at != row.kickoff_at:
            groups.append([row])
        else:
            groups[-1].append(row)
    if len(groups) < k:
        raise ValueError("fewer kickoff groups than requested folds")

    total = len(items)
    folds: list[list[PredictionDatasetRow]] = []
    acc: list[PredictionDatasetRow] = []
    consumed = 0
    for group in groups:
        boundary = total * (len(folds) + 1) / k
        if len(folds) < k - 1 and acc and consumed + len(group) > boundary:
            folds.append(acc)
            acc = []
        acc.extend(group)
        consumed += len(group)
    if acc:
        folds.append(acc)
    if len(folds) != k or any(not fold for fold in folds):
        raise RuntimeError(f"bad time fold sizes {[len(f) for f in folds]}")
    return tuple(tuple(fold) for fold in folds)
