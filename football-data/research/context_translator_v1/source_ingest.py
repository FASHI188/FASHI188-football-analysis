from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterable

ALLOWED_TIERS = {"TIER_1_OFFICIAL", "TIER_2_OPEN_STRUCTURED", "TIER_3_APPROVED_ARCHIVE"}
ALLOWED_PREDICATES = {
    "schedule", "prior_result", "prior_event", "prior_lineup", "prior_minutes",
    "injury", "suspension", "expected_return", "coach_change", "expected_lineup",
    "confirmed_lineup", "venue", "weather", "competition_rule", "referee",
    "geospatial", "tracking", "process_event",
}
PROHIBITED_KEYS = {
    "home_goals", "away_goals", "final_score", "result", "target_result",
    "actual_substitution", "actual_red_card", "actual_var", "actual_stoppage",
}
REQUIRED_PROVENANCE = {
    "source_url", "raw_sha256", "published_at", "observed_at", "retrieved_at",
    "known_at", "source_tier", "extraction_confidence", "provider_license",
    "immutable_source_ref",
}


class PITViolation(RuntimeError):
    pass


def _dt(value: str | None, field: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PITViolation(f"{field} must be timezone-aware ISO datetime")
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PITViolation(f"invalid {field}: {value!r}") from exc
    if out.tzinfo is None or out.utcoffset() is None:
        raise PITViolation(f"{field} missing timezone")
    return out.astimezone(timezone.utc)


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Provenance:
    source_url: str
    raw_sha256: str
    published_at: str | None
    observed_at: str | None
    retrieved_at: str
    known_at: str
    source_tier: str
    extraction_confidence: float
    provider_license: str
    immutable_source_ref: str

    def validate(self, cutoff: str) -> None:
        if self.source_tier not in ALLOWED_TIERS:
            raise PITViolation(f"source tier denied: {self.source_tier}")
        if len(self.raw_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.raw_sha256):
            raise PITViolation("raw_sha256 must be lowercase 64-hex")
        if not self.source_url or not self.provider_license or not self.immutable_source_ref:
            raise PITViolation("source URL, license and immutable source ref are required")
        if not isinstance(self.extraction_confidence, (int, float)) or isinstance(self.extraction_confidence, bool):
            raise PITViolation("extraction_confidence must be numeric")
        if not 0.0 <= float(self.extraction_confidence) <= 1.0:
            raise PITViolation("extraction_confidence outside [0,1]")
        known = _dt(self.known_at, "known_at")
        co = _dt(cutoff, "cutoff")
        _dt(self.retrieved_at, "retrieved_at")
        _dt(self.published_at, "published_at", nullable=True)
        _dt(self.observed_at, "observed_at", nullable=True)
        if known >= co:
            raise PITViolation(f"known_at must be strictly before cutoff: {known} >= {co}")


@dataclass(frozen=True)
class RawFact:
    predicate: str
    entity_type: str
    entity_id: str
    value: Any
    provenance: Provenance

    def validate(self, cutoff: str) -> None:
        if self.predicate not in ALLOWED_PREDICATES:
            raise PITViolation(f"predicate default-denied: {self.predicate}")
        if not self.entity_type or not self.entity_id:
            raise PITViolation("entity identity missing")
        if isinstance(self.value, dict) and PROHIBITED_KEYS.intersection(self.value):
            raise PITViolation(f"target/post-match field denied: {sorted(PROHIBITED_KEYS.intersection(self.value))}")
        self.provenance.validate(cutoff)

    def sha256(self) -> str:
        return canonical_sha({"predicate": self.predicate, "entity_type": self.entity_type,
                              "entity_id": self.entity_id, "value": self.value,
                              "provenance": asdict(self.provenance)})


def fact_from_mapping(row: dict[str, Any], cutoff: str) -> RawFact:
    allowed = {"predicate", "entity_type", "entity_id", "value", "provenance"}
    if set(row) != allowed:
        raise PITViolation(f"raw fact schema mismatch extra/missing={sorted(set(row) ^ allowed)}")
    prov = row["provenance"]
    if not isinstance(prov, dict) or set(prov) != REQUIRED_PROVENANCE:
        raise PITViolation("provenance schema mismatch")
    fact = RawFact(
        predicate=str(row["predicate"]), entity_type=str(row["entity_type"]), entity_id=str(row["entity_id"]),
        value=row["value"], provenance=Provenance(**prov),
    )
    fact.validate(cutoff)
    return fact


def ingest(rows: Iterable[dict[str, Any]], cutoff: str) -> list[RawFact]:
    out = [fact_from_mapping(r, cutoff) for r in rows]
    hashes = [f.sha256() for f in out]
    if len(hashes) != len(set(hashes)):
        raise PITViolation("duplicate raw fact payload")
    return out
