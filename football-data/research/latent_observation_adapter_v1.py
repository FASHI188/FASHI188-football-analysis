#!/usr/bin/env python3
"""Research-only PIT observation adapter for football3 adaptive latent strength.

No file, network, Provider, market, label-dataset, secret, or CURRENT access occurs
here. Callers supply already-collected completed-match intensity evidence plus
provenance. Attack is positive for above-reference attacking intensity. Defence is
positive for stronger defensive resistance, so concession intensity is normalized
with the inverse log1p difference: reference - observed concession intensity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class ObservationAdapterError(ValueError):
    pass


def _aware(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ObservationAdapterError(f"{field} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ObservationAdapterError(f"{field} must include timezone")
    return value


def _nonnegative(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ObservationAdapterError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ObservationAdapterError(f"{field} must be finite and >= 0")
    return number


def _positive(value: Any, field: str) -> float:
    number = _nonnegative(value, field)
    if number <= 0.0:
        raise ObservationAdapterError(f"{field} must be > 0")
    return number


def _nonempty(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ObservationAdapterError(f"{field} must be non-empty")
    return text


def _sha256(value: Any) -> str:
    text = _nonempty(value, "payload_sha256")
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ObservationAdapterError("payload_sha256 must be 64 lowercase hex characters")
    return text


@dataclass(frozen=True)
class PITIntensityEvidence:
    team: str
    fixture_id: str
    competition: str
    source_kind: str
    source_identity: str
    source_url: str
    payload_sha256: str
    collector_run_id: str
    attack_intensity: float
    defence_intensity: float
    attack_reference: float
    defence_reference: float
    attack_observation_variance: float
    defence_observation_variance: float
    event_completed_at: datetime
    source_published_at: datetime | None
    source_published_at_trusted: bool
    collector_first_observed_at: datetime
    retrieved_at: datetime
    ingested_at: datetime
    prediction_cutoff: datetime


ALLOWED_SOURCE_KINDS = frozenset({
    "completed_match_goals",
    "completed_match_xg",
    "completed_match_npxg",
})


def adapt_completed_match_intensity(evidence: PITIntensityEvidence) -> dict[str, Any]:
    """Normalize one completed-match PIT observation for latent-state update.

    `defence_intensity` is concession intensity (GA/xGA/npxGA). The returned
    defence observation has the opposite orientation: positive means stronger
    defensive resistance, which is the orientation required by the latent core's
    `attack - opponent_defence` equation.
    """
    if not isinstance(evidence, PITIntensityEvidence):
        raise ObservationAdapterError("evidence must be PITIntensityEvidence")

    team = _nonempty(evidence.team, "team")
    fixture_id = _nonempty(evidence.fixture_id, "fixture_id")
    competition = _nonempty(evidence.competition, "competition")
    source_kind = _nonempty(evidence.source_kind, "source_kind")
    source_identity = _nonempty(evidence.source_identity, "source_identity")
    source_url = _nonempty(evidence.source_url, "source_url")
    payload_sha256 = _sha256(evidence.payload_sha256)
    collector_run_id = _nonempty(evidence.collector_run_id, "collector_run_id")
    if source_kind not in ALLOWED_SOURCE_KINDS:
        raise ObservationAdapterError(f"unsupported source_kind: {source_kind}")
    if not isinstance(evidence.source_published_at_trusted, bool):
        raise ObservationAdapterError("source_published_at_trusted must be bool")

    event_completed_at = _aware(evidence.event_completed_at, "event_completed_at")
    first_observed = _aware(evidence.collector_first_observed_at, "collector_first_observed_at")
    retrieved_at = _aware(evidence.retrieved_at, "retrieved_at")
    ingested_at = _aware(evidence.ingested_at, "ingested_at")
    cutoff = _aware(evidence.prediction_cutoff, "prediction_cutoff")
    source_published = None
    if evidence.source_published_at is not None:
        source_published = _aware(evidence.source_published_at, "source_published_at")

    if evidence.source_published_at_trusted:
        if source_published is None:
            raise ObservationAdapterError("trusted source_published_at requires a timestamp")
        if source_published > first_observed:
            raise ObservationAdapterError("trusted source_published_at must be <= collector_first_observed_at")
        availability = source_published
        availability_basis = "trusted_source_published_at"
    else:
        availability = first_observed
        availability_basis = "collector_first_observed_at"

    if event_completed_at > availability:
        raise ObservationAdapterError("completed-match evidence cannot be available before event completion")
    if first_observed > retrieved_at:
        raise ObservationAdapterError("collector_first_observed_at must be <= retrieved_at")
    if retrieved_at > ingested_at:
        raise ObservationAdapterError("retrieved_at must be <= ingested_at")
    if availability >= cutoff:
        raise ObservationAdapterError("provable_available_at must be strictly before prediction_cutoff")

    attack = _nonnegative(evidence.attack_intensity, "attack_intensity")
    concession = _nonnegative(evidence.defence_intensity, "defence_intensity")
    attack_ref = _positive(evidence.attack_reference, "attack_reference")
    defence_ref = _positive(evidence.defence_reference, "defence_reference")
    attack_var = _positive(evidence.attack_observation_variance, "attack_observation_variance")
    defence_var = _positive(evidence.defence_observation_variance, "defence_observation_variance")

    attack_observation = math.log1p(attack) - math.log1p(attack_ref)
    defence_observation = math.log1p(defence_ref) - math.log1p(concession)
    if not math.isfinite(attack_observation) or not math.isfinite(defence_observation):
        raise ObservationAdapterError("normalized observations must be finite")

    return {
        "schema": "football3_latent_observation_adapter_v1",
        "team": team,
        "fixture_id": fixture_id,
        "competition": competition,
        "source_kind": source_kind,
        "source_identity": source_identity,
        "source_url": source_url,
        "payload_sha256": payload_sha256,
        "collector_run_id": collector_run_id,
        "event_completed_at": event_completed_at.isoformat(),
        "source_published_at": source_published.isoformat() if source_published else None,
        "source_published_at_trusted": evidence.source_published_at_trusted,
        "collector_first_observed_at": first_observed.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "ingested_at": ingested_at.isoformat(),
        "provable_available_at": availability.isoformat(),
        "availability_basis": availability_basis,
        "prediction_cutoff": cutoff.isoformat(),
        "attack_observation": attack_observation,
        "defence_observation": defence_observation,
        "defence_orientation": "positive_is_stronger_defensive_resistance",
        "defence_intensity_semantics": "concession_intensity_ga_xga_or_npxga",
        "attack_observation_variance": attack_var,
        "defence_observation_variance": defence_var,
        "market_input_used": False,
        "research_only": True,
        "formal_weight": 0.0,
    }
