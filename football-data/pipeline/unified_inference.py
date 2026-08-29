"""Shared Football3 inference orchestration for dataset, replay and live modes.

M4 starts with baseline-only numerical behavior.  Mode is metadata; the numerical
call chain is intentionally identical across modes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping, Protocol

from assembly.feature_assembler import FeatureActivation, FeatureAssembler
from identity.team_identity import RESOLVED, TeamIdentityResolver
from pit.feature_store import PointInTimeFeatureStore


MODES = {"dataset", "replay", "live"}
CLASSES = ("home", "draw", "away")


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_matrix(matrix: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for cell in matrix:
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        p = float(cell["probability"])
        if p < 0.0:
            raise ValueError("negative score-matrix probability")
        out.append({"home_goals": h, "away_goals": a, "probability": p})
    out.sort(key=lambda c: (c["home_goals"], c["away_goals"]))
    total = sum(c["probability"] for c in out)
    if not out or abs(total - 1.0) > 1e-9:
        raise ValueError(f"score matrix must sum to 1, got {total}")
    return out


def matrix_hash(matrix: Iterable[Mapping[str, Any]]) -> str:
    return _stable_hash(canonical_matrix(matrix))


def one_x_two(matrix: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    probs = {"home": 0.0, "draw": 0.0, "away": 0.0}
    for cell in canonical_matrix(matrix):
        h, a, p = cell["home_goals"], cell["away_goals"], cell["probability"]
        key = "home" if h > a else "draw" if h == a else "away"
        probs[key] += p
    return probs


def top1(probabilities: Mapping[str, float]) -> str:
    return max(CLASSES, key=lambda k: (float(probabilities[k]), -CLASSES.index(k)))


@dataclass(frozen=True)
class FixtureRequest:
    fixture_id: str
    as_of: datetime
    home_source_namespace: str
    home_source_team_id: str | int | None
    home_source_name: str | None
    away_source_namespace: str
    away_source_team_id: str | int | None
    away_source_name: str | None


@dataclass(frozen=True)
class FeatureReadSpec:
    feature_family: str
    entity_side: str | None = None  # home, away, or None for fixture/global
    numerical_values: Mapping[str, Any] | None = None
    numerical_feature_names: tuple[str, ...] = ()
    component_input_hash: str | None = None
    component_output_hash: str | None = None


class BaselineComponent(Protocol):
    component_id: str
    component_version: str

    def predict(self, request: FixtureRequest, canonical_home_team_id: str, canonical_away_team_id: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        ...


class ScoreMatrixComponent(Protocol):
    component_id: str
    component_version: str
    enabled: bool

    def apply(self, matrix: list[dict[str, Any]], request: FixtureRequest, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class PredictionResult:
    mode: str
    fixture_id: str
    canonical_home_team_id: str
    canonical_away_team_id: str
    probabilities: Mapping[str, float]
    top1: str
    score_matrix: tuple[Mapping[str, Any], ...]
    score_matrix_hash: str
    feature_activation_receipt: Mapping[str, Any]
    component_chain: tuple[Mapping[str, Any], ...]


class UnifiedInferenceEngine:
    def __init__(
        self,
        identity_resolver: TeamIdentityResolver,
        pit_store: PointInTimeFeatureStore,
        feature_assembler: FeatureAssembler,
        baseline_component: BaselineComponent,
        components: Iterable[ScoreMatrixComponent] = (),
    ):
        self.identity_resolver = identity_resolver
        self.pit_store = pit_store
        self.feature_assembler = feature_assembler
        self.baseline_component = baseline_component
        self.components = tuple(components)

    def predict(
        self,
        mode: str,
        request: FixtureRequest,
        baseline_payload: Mapping[str, Any],
        feature_specs: Iterable[FeatureReadSpec] = (),
        component_payload: Mapping[str, Any] | None = None,
    ) -> PredictionResult:
        if mode not in MODES:
            raise ValueError(f"unsupported mode: {mode}")
        if request.as_of.tzinfo is None or request.as_of.utcoffset() is None:
            raise ValueError("request.as_of must be timezone-aware")

        home = self.identity_resolver.resolve(
            request.home_source_namespace, request.home_source_team_id, request.home_source_name
        )
        away = self.identity_resolver.resolve(
            request.away_source_namespace, request.away_source_team_id, request.away_source_name
        )
        if home.status != RESOLVED or away.status != RESOLVED:
            raise ValueError({
                "identity_resolution_failed": True,
                "home": home.to_dict(),
                "away": away.to_dict(),
            })
        assert home.canonical_team_id and away.canonical_team_id

        activations: list[FeatureActivation] = []
        for spec in feature_specs:
            entity_id = None
            if spec.entity_side == "home":
                entity_id = home.canonical_team_id
            elif spec.entity_side == "away":
                entity_id = away.canonical_team_id
            elif spec.entity_side not in (None, "home", "away"):
                raise ValueError(f"invalid entity_side: {spec.entity_side}")
            pit = self.pit_store.read(
                spec.feature_family,
                request.fixture_id,
                request.as_of,
                canonical_entity_id=entity_id,
                require_historical_use=(mode != "live"),
            )
            activations.append(
                self.feature_assembler.assemble_family(
                    spec.feature_family,
                    pit,
                    numerical_values=spec.numerical_values,
                    numerical_feature_names=spec.numerical_feature_names,
                    component_input_hash=spec.component_input_hash,
                    component_output_hash=spec.component_output_hash,
                )
            )

        matrix = canonical_matrix(
            self.baseline_component.predict(
                request, home.canonical_team_id, away.canonical_team_id, baseline_payload
            )
        )
        chain: list[dict[str, Any]] = [{
            "component_id": self.baseline_component.component_id,
            "component_version": self.baseline_component.component_version,
            "enabled": True,
            "input_matrix_hash": None,
            "output_matrix_hash": matrix_hash(matrix),
        }]

        payload = component_payload or {}
        for component in self.components:
            if not component.enabled:
                chain.append({
                    "component_id": component.component_id,
                    "component_version": component.component_version,
                    "enabled": False,
                    "input_matrix_hash": matrix_hash(matrix),
                    "output_matrix_hash": matrix_hash(matrix),
                })
                continue
            before = matrix_hash(matrix)
            matrix = canonical_matrix(component.apply(matrix, request, payload))
            after = matrix_hash(matrix)
            chain.append({
                "component_id": component.component_id,
                "component_version": component.component_version,
                "enabled": True,
                "input_matrix_hash": before,
                "output_matrix_hash": after,
            })

        probs = one_x_two(matrix)
        pick = top1(probs)
        final_hash = matrix_hash(matrix)
        receipt = self.feature_assembler.build_receipt(
            fixture_id=request.fixture_id,
            as_of=request.as_of.astimezone(timezone.utc),
            canonical_home_team_id=home.canonical_team_id,
            canonical_away_team_id=away.canonical_team_id,
            activations=activations,
            final_score_matrix_hash=final_hash,
            final_1x2=probs,
            final_top1=pick,
        )
        return PredictionResult(
            mode,
            request.fixture_id,
            home.canonical_team_id,
            away.canonical_team_id,
            probs,
            pick,
            tuple(matrix),
            final_hash,
            receipt.to_dict(),
            tuple(chain),
        )
