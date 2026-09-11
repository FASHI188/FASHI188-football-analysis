from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA = "football3-nextgen-current-v2-foundation-v1"
STATUS_NOT_AVAILABLE = "NOT_AVAILABLE"
RESEARCH_STATUS = "RESEARCH_ONLY_FOUNDATION"

EXPERT_NAMES = (
    "total_goals",
    "market",
    "dynamic_strength",
    "player",
    "expected_lineup",
    "goalkeeper",
    "injury",
    "travel",
    "set_piece",
    "tactical_matchup",
    "promoted_team_cold_start",
    "cross_league_transfer",
)

CAPABILITY_NAMES = ("score", "state", "competition")

RECEIPT_RESERVED_FIELDS = (
    "nextgen_foundation_schema",
    "nextgen_research_status",
    "nextgen_expert_statuses",
    "nextgen_ablation",
    "nextgen_capability",
    "nextgen_data_quality",
    "nextgen_matrix_delta_summary",
)


class FoundationContractError(RuntimeError):
    pass


def _aware(value: datetime, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise FoundationContractError(f"{name} must be timezone-aware datetime")
    return value


@dataclass(frozen=True)
class PITTimeInterface:
    as_of: datetime
    cutoff: datetime
    observed_at: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        as_of = _aware(self.as_of, "as_of")
        cutoff = _aware(self.cutoff, "cutoff")
        observed_at = _aware(self.observed_at, "observed_at")
        available_at = _aware(self.available_at, "available_at")
        if observed_at > available_at:
            raise FoundationContractError("observed_at must not be after available_at")
        if available_at > cutoff:
            raise FoundationContractError("available_at after cutoff is not PIT-eligible")
        if as_of < cutoff:
            raise FoundationContractError("as_of must be at or after cutoff")


@dataclass(frozen=True)
class CapabilityInterface:
    score: str = STATUS_NOT_AVAILABLE
    state: str = STATUS_NOT_AVAILABLE
    competition: str = STATUS_NOT_AVAILABLE

    def __post_init__(self) -> None:
        for name in CAPABILITY_NAMES:
            if getattr(self, name) != STATUS_NOT_AVAILABLE:
                raise FoundationContractError(f"unactivated capability must be {STATUS_NOT_AVAILABLE}: {name}")


@dataclass(frozen=True)
class DataQualityInterface:
    source: str = STATUS_NOT_AVAILABLE
    schema: str = STATUS_NOT_AVAILABLE
    identity: str = STATUS_NOT_AVAILABLE
    coverage: str = STATUS_NOT_AVAILABLE
    timing: str = STATUS_NOT_AVAILABLE
    missingness: str = STATUS_NOT_AVAILABLE

    def __post_init__(self) -> None:
        for name in ("source", "schema", "identity", "coverage", "timing", "missingness"):
            if getattr(self, name) != STATUS_NOT_AVAILABLE:
                raise FoundationContractError(f"unactivated data-quality surface must be {STATUS_NOT_AVAILABLE}: {name}")


@dataclass(frozen=True)
class ExpertInterface:
    name: str
    status: str = STATUS_NOT_AVAILABLE
    weight: float = 0.0
    matrix_delta: float = 0.0
    evidence_quality: str = STATUS_NOT_AVAILABLE
    rejection_reason: str = "NOT_ACTIVATED"

    def __post_init__(self) -> None:
        if self.name not in EXPERT_NAMES:
            raise FoundationContractError(f"unknown expert interface: {self.name}")
        if self.status != STATUS_NOT_AVAILABLE:
            raise FoundationContractError(f"unactivated expert status must be {STATUS_NOT_AVAILABLE}: {self.name}")
        if type(self.weight) not in (int, float) or not math.isfinite(float(self.weight)) or float(self.weight) != 0.0:
            raise FoundationContractError(f"unactivated expert weight must be 0: {self.name}")
        if type(self.matrix_delta) not in (int, float) or not math.isfinite(float(self.matrix_delta)) or float(self.matrix_delta) != 0.0:
            raise FoundationContractError(f"unactivated expert matrix_delta must be 0: {self.name}")
        if self.evidence_quality != STATUS_NOT_AVAILABLE:
            raise FoundationContractError(f"unactivated expert evidence must be {STATUS_NOT_AVAILABLE}: {self.name}")


def empty_experts() -> tuple[ExpertInterface, ...]:
    return tuple(ExpertInterface(name=name) for name in EXPERT_NAMES)


def validate_experts(experts: Sequence[ExpertInterface]) -> tuple[ExpertInterface, ...]:
    rows = tuple(experts)
    if tuple(row.name for row in rows) != EXPERT_NAMES:
        raise FoundationContractError("expert interface set/order mismatch")
    return rows


def ablation_snapshot(experts: Sequence[ExpertInterface]) -> dict[str, dict[str, Any]]:
    rows = validate_experts(experts)
    return {
        row.name: {
            "status": row.status,
            "weight": float(row.weight),
            "matrix_delta": float(row.matrix_delta),
            "evidence_quality": row.evidence_quality,
            "rejection_reason": row.rejection_reason,
        }
        for row in rows
    }


def receipt_reserved_fields() -> tuple[str, ...]:
    return RECEIPT_RESERVED_FIELDS


def _probability(value: Any, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise FoundationContractError(f"invalid probability: {name}") from exc
    if not math.isfinite(out) or out < 0.0 or out > 1.0:
        raise FoundationContractError(f"invalid probability: {name}")
    return out


def validate_unified_score_matrix(prediction: Mapping[str, Any]) -> tuple[tuple[int, int, float], ...]:
    matrix = prediction.get("score_matrix")
    if type(matrix) is not list or not matrix:
        raise FoundationContractError("current V2 score_matrix missing")
    cells: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    for raw in matrix:
        if type(raw) is not dict:
            raise FoundationContractError("invalid score_matrix cell")
        try:
            home_goals = int(raw["home_goals"])
            away_goals = int(raw["away_goals"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FoundationContractError("invalid score_matrix cell identity") from exc
        if home_goals < 0 or away_goals < 0:
            raise FoundationContractError("negative score support")
        key = (home_goals, away_goals)
        if key in seen:
            raise FoundationContractError("duplicate score support")
        seen.add(key)
        probability = _probability(raw.get("probability"), "score_matrix")
        cells.append((home_goals, away_goals, probability))
    if abs(math.fsum(cell[2] for cell in cells) - 1.0) > 1e-9:
        raise FoundationContractError("score_matrix mass is not 1")

    p_home = math.fsum(p for h, a, p in cells if h > a)
    p_draw = math.fsum(p for h, a, p in cells if h == a)
    p_away = math.fsum(p for h, a, p in cells if h < a)
    claimed = (
        _probability(prediction.get("p_home"), "p_home"),
        _probability(prediction.get("p_draw"), "p_draw"),
        _probability(prediction.get("p_away"), "p_away"),
    )
    actual = (p_home, p_draw, p_away)
    if max(abs(a - b) for a, b in zip(actual, claimed)) > 5e-12:
        raise FoundationContractError("score_matrix/1X2 identity mismatch")
    return tuple(cells)


def validate_current_v2_batch(batch: Any) -> None:
    if type(batch) is not list:
        raise FoundationContractError("current V2 batch must be list")
    for row in batch:
        if type(row) is not dict or type(row.get("prediction")) is not dict or type(row.get("audit")) is not dict:
            raise FoundationContractError("current V2 row shape invalid")
        validate_unified_score_matrix(row["prediction"])
        route = row["audit"].get("route")
        if route not in {"FUSION_V2_ACTIVE", "FROZEN_V1_EXACT_FALLBACK"}:
            raise FoundationContractError(f"unknown current V2 route: {route}")


def run_with_current_v2(
    current_v2_predictor: Callable[[Any, Iterable[Any]], list[dict[str, Any]]],
    state: Any,
    fixtures: Iterable[Any],
    *,
    experts: Sequence[ExpertInterface] | None = None,
) -> list[dict[str, Any]]:
    """Research-only stage-0 pass-through.

    The supplied predictor is contractually bound by the foundation JSON/workflow to
    new_engine_v1.formal_fusion_v2.predict_formal_batch at the canonical V2 baseline.
    This function does not load a model/provider, does not mutate state/receipt data,
    and cannot apply any expert delta. With all stage-0 experts inactive, the exact
    object returned by current V2 is returned unchanged.
    """
    validate_experts(empty_experts() if experts is None else experts)
    baseline = current_v2_predictor(state, fixtures)
    validate_current_v2_batch(baseline)
    return baseline


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def derived_distribution_semantics(prediction: Mapping[str, Any]) -> dict[str, Any]:
    cells = validate_unified_score_matrix(prediction)
    probs = (
        ("HOME", float(prediction["p_home"])),
        ("DRAW", float(prediction["p_draw"])),
        ("AWAY", float(prediction["p_away"])),
    )
    top1 = max(probs, key=lambda x: (x[1], {"HOME": 2, "DRAW": 1, "AWAY": 0}[x[0]]))
    return {
        "one_x_two_top1": {"selection": top1[0], "probability": top1[1]},
        "over_2_5": math.fsum(p for h, a, p in cells if h + a >= 3),
        "btts_yes": math.fsum(p for h, a, p in cells if h > 0 and a > 0),
        "mu_home": None,
        "mu_away": None,
        "mu_semantics": "NOT_DEFINED_FOR_GLOBAL_MIXTURE_SINGLE_POISSON_MU_NOT_CLAIMED",
    }


def research_metadata(
    *,
    time: PITTimeInterface | None = None,
    capability: CapabilityInterface | None = None,
    data_quality: DataQualityInterface | None = None,
    experts: Sequence[ExpertInterface] | None = None,
) -> dict[str, Any]:
    rows = validate_experts(empty_experts() if experts is None else experts)
    return {
        "schema_version": SCHEMA,
        "research_status": RESEARCH_STATUS,
        "time_interface_present": time is not None,
        "capability": (capability or CapabilityInterface()).__dict__,
        "data_quality": (data_quality or DataQualityInterface()).__dict__,
        "experts": ablation_snapshot(rows),
        "receipt_reserved_fields": list(RECEIPT_RESERVED_FIELDS),
        "formal_output_mutated": False,
        "second_loader_created": False,
        "second_provider_created": False,
        "production_chain_created": False,
        "training": False,
        "tuning": False,
        "new_target_labels": False,
    }
