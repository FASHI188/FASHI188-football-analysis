from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

class PITContractError(ValueError):
    pass

class AvailabilityBasis(str, Enum):
    PROVIDER_PUBLISHED_AT = "PROVIDER_PUBLISHED_AT"
    PROVIDER_LAST_UPDATE = "PROVIDER_LAST_UPDATE"
    ARCHIVE_RELEASE_AT = "ARCHIVE_RELEASE_AT"
    COLLECTOR_FIRST_OBSERVED_AT = "COLLECTOR_FIRST_OBSERVED_AT"
    RETRIEVAL_PROXY = "RETRIEVAL_PROXY"
    UNKNOWN = "UNKNOWN"

@dataclass(frozen=True)
class FixtureIdentity:
    canonical_fixture_id: str
    competition_id: str
    provider_fixture_id: str
    scheduled_kickoff: datetime
    kickoff_revision_id: str

    def validate(self) -> None:
        for name in ("canonical_fixture_id", "competition_id", "provider_fixture_id", "kickoff_revision_id"):
            if not getattr(self, name).strip():
                raise PITContractError(f"{name} is required")
        _require_aware(self.scheduled_kickoff, "scheduled_kickoff")

    def deterministic_key(self) -> str:
        self.validate()
        raw = "|".join((self.canonical_fixture_id, self.competition_id, self.provider_fixture_id,
                        self.scheduled_kickoff.isoformat(), self.kickoff_revision_id))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClubIdentityBinding:
    source: str
    source_club_key: str
    canonical_club_id: str
    competition_season: str
    binding_basis: str
    binding_sha256: str

    def validate(self) -> None:
        for name in ("source", "source_club_key", "canonical_club_id", "competition_season", "binding_basis"):
            if not getattr(self, name).strip():
                raise PITContractError(f"{name} is required")
        if self.binding_basis not in {"PROVIDER_STABLE_ID", "PREDECLARED_EXACT_CROSSWALK"}:
            raise PITContractError("club identity requires provider stable id or predeclared exact crosswalk; fuzzy/name guessing forbidden")
        if not _SHA256.fullmatch(self.binding_sha256):
            raise PITContractError("binding_sha256 must be lowercase sha256 hex")

    def deterministic_key(self) -> str:
        self.validate()
        raw = "|".join((self.source, self.source_club_key, self.canonical_club_id, self.competition_season, self.binding_basis, self.binding_sha256))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

@dataclass(frozen=True)
class PITObservationEnvelope:
    source: str
    schema: str
    source_identity: str
    evidence_kind: str
    fixture: FixtureIdentity
    event_time: datetime
    observed_at: datetime
    available_at: datetime
    retrieved_at: datetime
    freeze_at: datetime
    availability_basis: AvailabilityBasis
    content_sha256: str
    revision_lineage: str
    license_state: str
    payload_ref: str

    def validate(self) -> None:
        self.fixture.validate()
        for name in ("source", "schema", "source_identity", "evidence_kind", "revision_lineage", "license_state", "payload_ref"):
            if not getattr(self, name).strip():
                raise PITContractError(f"{name} is required")
        for name in ("event_time", "observed_at", "available_at", "retrieved_at", "freeze_at"):
            _require_aware(getattr(self, name), name)
        if self.observed_at > self.available_at:
            raise PITContractError("observed_at must be <= available_at")
        if self.availability_basis in (AvailabilityBasis.RETRIEVAL_PROXY, AvailabilityBasis.UNKNOWN):
            raise PITContractError("retrieval proxy/unknown availability is not PIT evidence")
        if not _SHA256.fullmatch(self.content_sha256):
            raise PITContractError("content_sha256 must be lowercase sha256 hex")

    def pit_eligible_at_freeze(self) -> bool:
        self.validate()
        return self.available_at <= self.freeze_at and self.observed_at <= self.freeze_at

    def historical_backfill_safe(self) -> bool:
        self.validate()
        return self.availability_basis in {
            AvailabilityBasis.PROVIDER_PUBLISHED_AT,
            AvailabilityBasis.PROVIDER_LAST_UPDATE,
            AvailabilityBasis.ARCHIVE_RELEASE_AT,
        } and self.available_at <= self.freeze_at

    def prospective_capture_safe(self) -> bool:
        self.validate()
        return self.available_at <= self.freeze_at

    def canonical_receipt_payload(self) -> Mapping[str, Any]:
        self.validate()
        d = asdict(self)
        d["availability_basis"] = self.availability_basis.value
        d["fixture"]["scheduled_kickoff"] = self.fixture.scheduled_kickoff.isoformat()
        for k in ("event_time", "observed_at", "available_at", "retrieved_at", "freeze_at"):
            d[k] = getattr(self, k).isoformat()
        return d

    def receipt_sha256(self) -> str:
        b = json.dumps(self.canonical_receipt_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(b).hexdigest()

def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PITContractError(f"{name} must be timezone-aware")

def content_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()

def classify_static_archive_for_target(*, archive_release_at: datetime, target_freeze_at: datetime) -> str:
    _require_aware(archive_release_at, "archive_release_at")
    _require_aware(target_freeze_at, "target_freeze_at")
    return "PIT_ELIGIBLE_FOR_FUTURE_TARGET" if archive_release_at <= target_freeze_at else "NOT_PIT_ELIGIBLE"

def reject_event_time_as_availability(event_time: datetime, available_at: datetime | None) -> None:
    _require_aware(event_time, "event_time")
    if available_at is None:
        raise PITContractError("event_time cannot be substituted for missing available_at")
    _require_aware(available_at, "available_at")
