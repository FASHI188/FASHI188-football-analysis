from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from source_ingest import RawFact, PITViolation


def _dt(text: str) -> datetime:
    out = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if out.tzinfo is None:
        raise PITViolation("timezone required")
    return out.astimezone(timezone.utc)


class PITFeatureStore:
    def __init__(self) -> None:
        self._facts: dict[tuple[str, str, str], list[RawFact]] = defaultdict(list)
        self._seen_fact_sha256: set[str] = set()
        self._seen_source_event: dict[tuple[str, str], str] = {}

    def append(self, fact: RawFact, cutoff: str) -> None:
        fact.validate(cutoff)
        digest = fact.sha256()
        if digest in self._seen_fact_sha256:
            raise PITViolation("duplicate raw fact payload")
        source_event_id = None
        if isinstance(fact.value, dict):
            source_event_id = fact.value.get("source_event_id")
        if source_event_id:
            key = (fact.provenance.immutable_source_ref, str(source_event_id))
            previous = self._seen_source_event.get(key)
            if previous is not None:
                raise PITViolation(f"duplicate source event across facts source={key[0]} event={key[1]} prior_sha={previous}")
            self._seen_source_event[key] = digest
        self._seen_fact_sha256.add(digest)
        key = (fact.entity_type, fact.entity_id, fact.predicate)
        self._facts[key].append(fact)
        self._facts[key].sort(key=lambda f: (_dt(f.provenance.known_at), f.sha256()))

    def extend(self, facts: Iterable[RawFact], cutoff: str) -> None:
        for fact in facts:
            self.append(fact, cutoff)

    def before(self, entity_type: str, entity_id: str, predicate: str, cutoff: str) -> list[RawFact]:
        co = _dt(cutoff)
        return [f for f in self._facts.get((entity_type, entity_id, predicate), []) if _dt(f.provenance.known_at) < co]

    def latest(self, entity_type: str, entity_id: str, predicate: str, cutoff: str) -> RawFact | None:
        rows = self.before(entity_type, entity_id, predicate, cutoff)
        return rows[-1] if rows else None

    def snapshot_sha256(self, cutoff: str) -> str:
        co = _dt(cutoff)
        rows = []
        for key, facts in sorted(self._facts.items()):
            for f in facts:
                if _dt(f.provenance.known_at) < co:
                    rows.append((key, f.sha256()))
        raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()
