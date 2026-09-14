#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

NOVA_STAGE0_VERSION = "FOOTBALL3_NOVA_STAGE0_FOUNDATION_V1"
EXACT_BASE_REF = "football3/formal-gpt-runner-integration-v1"
EXACT_BASE_HEAD = "475dedfd177b02208f550fe97a89bbd1efa52125"
FORMAL_V2_HEAD = "e12f5d1193be5d81f60301cf34ab2140e11712a9"
FORMAL_POINTER_BLOB_SHA = "f07743f9c873d415fad9612f3225f64c64b10542"
CURRENT_VERSION = "V5.4.0"
CURRENT_SHA256 = "71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
FORMAL_V2_MODEL_ID = "HISTORICAL_XG_FUSION_V2_FORMAL"
FORMAL_V2_FALLBACK = "FROZEN_V1_EXACT_FALLBACK"
NOVA_REPLAY_ROUTE = "NOVA_T60_FORMAL_REPLAY"
REPLAY_COVERAGE_START_AT = None

NOT_AVAILABLE = "NOT_AVAILABLE"


class NovaStage0ContractError(ValueError):
    pass


class FormalBaselineUnavailable(NovaStage0ContractError):
    pass


@dataclass(frozen=True)
class CompetitionAdapter:
    key: str
    display_name: str
    formal_v2_competition_id: str | None
    formal_v2_baseline_supported: bool
    stage0_status: str


TARGET_ADAPTERS: Mapping[str, CompetitionAdapter] = {
    "ENG_PREMIER_LEAGUE": CompetitionAdapter(
        "ENG_PREMIER_LEAGUE", "English Premier League", "ENG_PremierLeague", True, "ADAPTER_READY"
    ),
    "ESP_LALIGA": CompetitionAdapter(
        "ESP_LALIGA", "Spanish LaLiga", "ESP_LaLiga", True, "ADAPTER_READY"
    ),
    "DEU_BUNDESLIGA": CompetitionAdapter(
        "DEU_BUNDESLIGA", "German Bundesliga", "GER_Bundesliga", True, "ADAPTER_READY"
    ),
    "ITA_SERIE_A": CompetitionAdapter(
        "ITA_SERIE_A", "Italian Serie A", "ITA_SerieA", True, "ADAPTER_READY"
    ),
    "FRA_LIGUE_1": CompetitionAdapter(
        "FRA_LIGUE_1", "French Ligue 1", "FRA_Ligue1", True, "ADAPTER_READY"
    ),
    "JPN_J1": CompetitionAdapter(
        "JPN_J1", "Japan J1", "JPN_J1", True, "ADAPTER_READY"
    ),
    "KOR_K1": CompetitionAdapter(
        "KOR_K1", "Korea K1", "KOR_KLeague1", True, "ADAPTER_READY"
    ),
}


@dataclass(frozen=True)
class ExpertOutput:
    name: str
    status: str = NOT_AVAILABLE
    weight: int = 0
    matrix_delta: int = 0
    evidence_quality: str = "NOT_EVALUATED"
    coverage: int = 0
    uncertainty: str = "NOT_EVALUATED"
    rejection_reason: str = "STAGE0_NOT_SCIENTIFICALLY_ACTIVATED"

    def validate_stage0(self) -> None:
        if self.status != NOT_AVAILABLE or self.weight != 0 or self.matrix_delta != 0:
            raise NovaStage0ContractError("STAGE0_EXPERT_MUST_BE_NOT_AVAILABLE_ZERO_WEIGHT_ZERO_DELTA")

    def as_dict(self) -> dict[str, Any]:
        self.validate_stage0()
        return {
            "name": self.name,
            "status": self.status,
            "weight": self.weight,
            "matrix_delta": self.matrix_delta,
            "evidence_quality": self.evidence_quality,
            "coverage": self.coverage,
            "uncertainty": self.uncertainty,
            "rejection_reason": self.rejection_reason,
        }


def empty_expert(name: str) -> ExpertOutput:
    if not isinstance(name, str) or not name.strip():
        raise NovaStage0ContractError("EXPERT_NAME_REQUIRED")
    return ExpertOutput(name=name.strip())


def _validate_matrix(matrix: Sequence[Sequence[float]]) -> None:
    if not isinstance(matrix, (list, tuple)) or not matrix:
        raise NovaStage0ContractError("SCORE_MATRIX_REQUIRED")
    width: int | None = None
    total = 0.0
    for row in matrix:
        if not isinstance(row, (list, tuple)) or not row:
            raise NovaStage0ContractError("SCORE_MATRIX_ROW_REQUIRED")
        if width is None:
            width = len(row)
        elif len(row) != width:
            raise NovaStage0ContractError("SCORE_MATRIX_NOT_RECTANGULAR")
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise NovaStage0ContractError("SCORE_MATRIX_VALUE_INVALID")
            f = float(value)
            if not math.isfinite(f) or f < 0.0:
                raise NovaStage0ContractError("SCORE_MATRIX_VALUE_INVALID")
            total += f
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise NovaStage0ContractError("SCORE_MATRIX_MUST_SUM_TO_ONE")


def apply_stage0_experts(
    formal_v2_matrix: Sequence[Sequence[float]], experts: Iterable[ExpertOutput]
) -> Sequence[Sequence[float]]:
    """Validate Stage-0 expert inactivity and return the formal matrix untouched.

    No arithmetic is performed on the formal matrix. The exact input object is returned.
    """
    _validate_matrix(formal_v2_matrix)
    for expert in experts:
        if not isinstance(expert, ExpertOutput):
            raise NovaStage0ContractError("EXPERT_OUTPUT_TYPE_INVALID")
        expert.validate_stage0()
    return formal_v2_matrix


def formal_v2_passthrough(formal_payload: Any, adapter_key: str) -> Any:
    """Return the already-produced formal V2 payload without recomputation.

    This is deliberately not a loader/provider/runner. Stage 0 only accepts adapters
    explicitly present in the currently activated Historical XG Fusion V2 formal_scope.
    """
    adapter = TARGET_ADAPTERS.get(adapter_key)
    if adapter is None:
        raise FormalBaselineUnavailable("NOVA_ADAPTER_UNKNOWN")
    if not adapter.formal_v2_baseline_supported:
        raise FormalBaselineUnavailable("FORMAL_V2_BASELINE_NOT_AVAILABLE_FOR_ADAPTER")
    return formal_payload


def derive_matrix_outputs(matrix: Sequence[Sequence[float]]) -> dict[str, float]:
    """Derive internally consistent research observables from one score matrix."""
    _validate_matrix(matrix)
    home = draw = away = over_25 = btts = 0.0
    for h, row in enumerate(matrix):
        for a, raw in enumerate(row):
            p = float(raw)
            if h > a:
                home += p
            elif h == a:
                draw += p
            else:
                away += p
            if h + a >= 3:
                over_25 += p
            if h > 0 and a > 0:
                btts += p
    return {
        "home_win": home,
        "draw": draw,
        "away_win": away,
        "over_2_5": over_25,
        "btts": btts,
    }


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise NovaStage0ContractError("TIME_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def authoritative_cutoff(kickoff: datetime) -> datetime:
    return _aware_utc(kickoff) - timedelta(minutes=60)


def eligible_observations(
    observations: Iterable[Mapping[str, Any]], *, fixture_id: str, kickoff_revision_id: str, cutoff: datetime
) -> tuple[Mapping[str, Any], ...]:
    cutoff_utc = _aware_utc(cutoff)
    selected: list[Mapping[str, Any]] = []
    for obs in observations:
        if obs.get("fixture_id") != fixture_id or obs.get("kickoff_revision_id") != kickoff_revision_id:
            continue
        available_at = obs.get("available_at")
        if not isinstance(available_at, datetime):
            raise NovaStage0ContractError("OBSERVATION_AVAILABLE_AT_REQUIRED")
        if _aware_utc(available_at) <= cutoff_utc:
            selected.append(obs)
    selected.sort(key=lambda x: _aware_utc(x["available_at"]))
    return tuple(selected)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def replay_ledger_key(
    *, fixture_id: str, kickoff_revision_id: str, cutoff: str, model_head: str, state_sha: str, input_sha: str
) -> str:
    payload = {
        "fixture_id": fixture_id,
        "kickoff_revision_id": kickoff_revision_id,
        "cutoff": cutoff,
        "model_head": model_head,
        "state_sha": state_sha,
        "input_sha": input_sha,
    }
    return sha256_json(payload)


_FORBIDDEN_SCORE_KEYS = frozenset(
    {
        "final_score",
        "full_time_score",
        "result",
        "match_result",
        "winner",
        "in_match_events",
        "match_events",
        "postmatch",
        "post_match",
        "postmatch_rating",
        "final_lineup",
    }
)


def assert_score_blind(value: Any) -> None:
    """Recursively reject result/after-kickoff fields before prediction sealing."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in _FORBIDDEN_SCORE_KEYS:
                raise NovaStage0ContractError(f"SCORE_BLIND_FIELD_FORBIDDEN:{key}")
            assert_score_blind(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            assert_score_blind(nested)


def stage0_identity() -> dict[str, Any]:
    return {
        "nova_stage0_version": NOVA_STAGE0_VERSION,
        "exact_base_ref": EXACT_BASE_REF,
        "exact_base_head": EXACT_BASE_HEAD,
        "formal_v2_model_id": FORMAL_V2_MODEL_ID,
        "formal_v2_head": FORMAL_V2_HEAD,
        "formal_pointer_blob_sha": FORMAL_POINTER_BLOB_SHA,
        "formal_v2_fallback": FORMAL_V2_FALLBACK,
        "current_version": CURRENT_VERSION,
        "current_sha256": CURRENT_SHA256,
        "replay_route": NOVA_REPLAY_ROUTE,
        "replay_coverage_start_at": REPLAY_COVERAGE_START_AT,
    }
