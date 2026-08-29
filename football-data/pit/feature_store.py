"""Point-in-time feature store contract for Football3.

The store is intentionally model-agnostic.  It enforces historical as-of legality
before a feature can reach an assembler.  Source adapters remain responsible for
proving and supplying truthful known_at/source lineage metadata.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


ACTIVE = "active"
INACTIVE = "inactive"


def _dt(value: str | datetime, field: str) -> datetime:
    if isinstance(value, datetime):
        out = value
    else:
        token = str(value).strip()
        if token.endswith("Z"):
            token = token[:-1] + "+00:00"
        out = datetime.fromisoformat(token)
    if out.tzinfo is None or out.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return out.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class PITFeatureRecord:
    feature_family: str
    entity_type: str
    canonical_entity_id: str
    fixture_id: str
    value: Any
    source_name: str
    source_record_id: str
    source_hash: str
    observed_at: datetime
    known_at: datetime
    effective_at: datetime
    expires_at: datetime | None
    leakage_class: str
    historical_use_allowed: bool
    adapter_version: str
    record_hash: str = ""

    def __post_init__(self) -> None:
        required = {
            "feature_family": self.feature_family,
            "entity_type": self.entity_type,
            "canonical_entity_id": self.canonical_entity_id,
            "fixture_id": self.fixture_id,
            "source_name": self.source_name,
            "source_record_id": self.source_record_id,
            "source_hash": self.source_hash,
            "leakage_class": self.leakage_class,
            "adapter_version": self.adapter_version,
        }
        for field, value in required.items():
            if not str(value).strip():
                raise ValueError(f"{field} must be non-empty")
        observed = _dt(self.observed_at, "observed_at")
        known = _dt(self.known_at, "known_at")
        effective = _dt(self.effective_at, "effective_at")
        expires = _dt(self.expires_at, "expires_at") if self.expires_at is not None else None
        if known < observed:
            raise ValueError("known_at cannot be earlier than observed_at")
        if expires is not None and expires <= effective:
            raise ValueError("expires_at must be later than effective_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "known_at", known)
        object.__setattr__(self, "effective_at", effective)
        object.__setattr__(self, "expires_at", expires)
        payload = self._hash_payload()
        digest = _stable_hash(payload)
        if self.record_hash and self.record_hash != digest:
            raise ValueError("record_hash mismatch")
        object.__setattr__(self, "record_hash", digest)

    def _hash_payload(self) -> dict[str, Any]:
        return {
            "feature_family": self.feature_family,
            "entity_type": self.entity_type,
            "canonical_entity_id": self.canonical_entity_id,
            "fixture_id": self.fixture_id,
            "value": self.value,
            "source_name": self.source_name,
            "source_record_id": self.source_record_id,
            "source_hash": self.source_hash,
            "observed_at": _iso(_dt(self.observed_at, "observed_at")),
            "known_at": _iso(_dt(self.known_at, "known_at")),
            "effective_at": _iso(_dt(self.effective_at, "effective_at")),
            "expires_at": _iso(_dt(self.expires_at, "expires_at")) if self.expires_at is not None else None,
            "leakage_class": self.leakage_class,
            "historical_use_allowed": bool(self.historical_use_allowed),
            "adapter_version": self.adapter_version,
        }

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for field in ("observed_at", "known_at", "effective_at", "expires_at"):
            out[field] = _iso(out[field]) if out[field] is not None else None
        return out

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PITFeatureRecord":
        return cls(
            feature_family=str(raw["feature_family"]),
            entity_type=str(raw["entity_type"]),
            canonical_entity_id=str(raw["canonical_entity_id"]),
            fixture_id=str(raw["fixture_id"]),
            value=raw.get("value"),
            source_name=str(raw["source_name"]),
            source_record_id=str(raw["source_record_id"]),
            source_hash=str(raw["source_hash"]),
            observed_at=_dt(raw["observed_at"], "observed_at"),
            known_at=_dt(raw["known_at"], "known_at"),
            effective_at=_dt(raw["effective_at"], "effective_at"),
            expires_at=_dt(raw["expires_at"], "expires_at") if raw.get("expires_at") else None,
            leakage_class=str(raw["leakage_class"]),
            historical_use_allowed=bool(raw["historical_use_allowed"]),
            adapter_version=str(raw["adapter_version"]),
            record_hash=str(raw.get("record_hash") or ""),
        )


@dataclass(frozen=True)
class PITReadResult:
    status: str
    feature_family: str
    fixture_id: str
    canonical_entity_id: str | None
    as_of: datetime
    records: tuple[PITFeatureRecord, ...]
    rejected_counts: Mapping[str, int]
    store_fingerprint: str

    def latest(self) -> PITFeatureRecord | None:
        if not self.records:
            return None
        return max(self.records, key=lambda r: (r.known_at, r.observed_at, r.record_hash))

    def to_receipt(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "feature_family": self.feature_family,
            "fixture_id": self.fixture_id,
            "canonical_entity_id": self.canonical_entity_id,
            "as_of": _iso(self.as_of),
            "active_record_count": len(self.records),
            "active_record_hashes": [r.record_hash for r in self.records],
            "latest_record_hash": self.latest().record_hash if self.latest() else None,
            "rejected_counts": dict(sorted(self.rejected_counts.items())),
            "store_fingerprint": self.store_fingerprint,
        }


class PointInTimeFeatureStore:
    def __init__(self, records: Iterable[PITFeatureRecord | Mapping[str, Any]] = ()):
        self._records: list[PITFeatureRecord] = []
        for record in records:
            self.add(record)

    def add(self, record: PITFeatureRecord | Mapping[str, Any]) -> str:
        item = record if isinstance(record, PITFeatureRecord) else PITFeatureRecord.from_mapping(record)
        self._records.append(item)
        return item.record_hash

    @property
    def fingerprint(self) -> str:
        return _stable_hash(sorted(record.record_hash for record in self._records))

    def read(
        self,
        feature_family: str,
        fixture_id: str,
        as_of: str | datetime,
        canonical_entity_id: str | None = None,
        require_historical_use: bool = True,
    ) -> PITReadResult:
        target_time = _dt(as_of, "as_of")
        family = str(feature_family).strip()
        fixture = str(fixture_id).strip()
        entity = str(canonical_entity_id).strip() if canonical_entity_id is not None else None
        if not family or not fixture:
            raise ValueError("feature_family and fixture_id must be non-empty")

        accepted: list[PITFeatureRecord] = []
        rejected: dict[str, int] = {}

        def reject(reason: str) -> None:
            rejected[reason] = rejected.get(reason, 0) + 1

        for record in self._records:
            if record.feature_family != family:
                continue
            if record.fixture_id != fixture:
                continue
            if entity is not None and record.canonical_entity_id != entity:
                reject("canonical_entity_mismatch")
                continue
            if require_historical_use and not record.historical_use_allowed:
                reject("historical_use_not_allowed")
                continue
            if record.known_at > target_time:
                reject("known_after_as_of")
                continue
            if record.effective_at > target_time:
                reject("effective_after_as_of")
                continue
            if record.expires_at is not None and target_time >= record.expires_at:
                reject("expired_at_as_of")
                continue
            accepted.append(record)

        accepted.sort(key=lambda r: (r.known_at, r.observed_at, r.source_name, r.source_record_id, r.record_hash))
        return PITReadResult(
            ACTIVE if accepted else INACTIVE,
            family,
            fixture,
            entity,
            target_time,
            tuple(accepted),
            rejected,
            self.fingerprint,
        )

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "PointInTimeFeatureStore":
        records = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(PITFeatureRecord.from_mapping(json.loads(line)))
        return cls(records)

    def append_jsonl(self, path: str | Path, record: PITFeatureRecord | Mapping[str, Any]) -> str:
        item = record if isinstance(record, PITFeatureRecord) else PITFeatureRecord.from_mapping(record)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")
        self._records.append(item)
        return item.record_hash
