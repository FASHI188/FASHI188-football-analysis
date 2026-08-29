"""Canonical team identity resolution for Football3 governance migration.

This module is plumbing only.  It performs deterministic exact source-ID and
explicitly approved alias resolution.  It intentionally does not perform fuzzy
matching and has no dependency on prediction/model code.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping


RESOLVED = "resolved"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"


def _text(value: Any) -> str:
    return str(value).strip()


def _namespace(value: Any) -> str:
    token = _text(value).casefold()
    if not token:
        raise ValueError("source_namespace must be non-empty")
    return token


def _normalize_alias(value: Any) -> str:
    token = unicodedata.normalize("NFKC", _text(value)).casefold()
    return re.sub(r"\s+", " ", token).strip()


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class TeamResolution:
    status: str
    source_namespace: str
    source_team_id: str | None
    source_name: str | None
    canonical_team_id: str | None
    mapping_method: str | None
    provenance_hash: str | None
    resolver_fingerprint: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Mapping:
    source_namespace: str
    source_team_id: str
    canonical_team_id: str
    mapping_method: str
    provenance_hash: str


@dataclass(frozen=True)
class _Alias:
    source_namespace: str
    normalized_alias: str
    canonical_team_id: str
    mapping_method: str
    provenance_hash: str


class TeamIdentityResolver:
    """Strict source-key -> canonical team resolver.

    A source namespace is required.  Within each namespace, retained source IDs
    and canonical IDs must form a strict one-to-one relation.  Any source-ID or
    reverse canonical conflict is blocked rather than guessed.

    Name resolution is allowed only for aliases explicitly supplied in the input
    records.  It is exact after conservative Unicode/case/whitespace normalization;
    there is no edit-distance, token or fuzzy fallback.
    """

    def __init__(self, records: Iterable[Mapping[str, Any]]):
        prepared: list[dict[str, str]] = []
        for raw in records:
            ns = _namespace(raw.get("source_namespace"))
            source_id = _text(raw.get("source_team_id")) if raw.get("source_team_id") is not None else ""
            canonical = _text(raw.get("canonical_team_id"))
            if not canonical:
                raise ValueError("canonical_team_id must be non-empty")
            method = _text(raw.get("mapping_method") or "pinned_crosswalk")
            provenance = _text(raw.get("provenance_hash") or "UNSPECIFIED")
            alias = _normalize_alias(raw.get("approved_name_alias")) if raw.get("approved_name_alias") else ""
            if not source_id and not alias:
                raise ValueError("record needs source_team_id or approved_name_alias")
            prepared.append(
                {
                    "source_namespace": ns,
                    "source_team_id": source_id,
                    "canonical_team_id": canonical,
                    "mapping_method": method,
                    "provenance_hash": provenance,
                    "approved_name_alias": alias,
                }
            )

        source_to_canonical: dict[tuple[str, str], set[str]] = {}
        canonical_to_source: dict[tuple[str, str], set[str]] = {}
        source_meta: dict[tuple[str, str, str], tuple[str, str]] = {}
        for row in prepared:
            sid = row["source_team_id"]
            if not sid:
                continue
            left = (row["source_namespace"], sid)
            right = (row["source_namespace"], row["canonical_team_id"])
            source_to_canonical.setdefault(left, set()).add(row["canonical_team_id"])
            canonical_to_source.setdefault(right, set()).add(sid)
            source_meta[(left[0], sid, row["canonical_team_id"])] = (
                row["mapping_method"],
                row["provenance_hash"],
            )

        blocked_source_keys: set[tuple[str, str]] = set()
        for key, canonicals in source_to_canonical.items():
            if len(canonicals) != 1:
                blocked_source_keys.add(key)
        for (ns, _canonical), source_ids in canonical_to_source.items():
            if len(source_ids) != 1:
                blocked_source_keys.update((ns, sid) for sid in source_ids)

        mappings: dict[tuple[str, str], _Mapping] = {}
        for key, canonicals in source_to_canonical.items():
            if key in blocked_source_keys:
                continue
            canonical = next(iter(canonicals))
            method, provenance = source_meta[(key[0], key[1], canonical)]
            mappings[key] = _Mapping(key[0], key[1], canonical, method, provenance)

        alias_to_canonical: dict[tuple[str, str], set[str]] = {}
        alias_meta: dict[tuple[str, str, str], tuple[str, str]] = {}
        for row in prepared:
            alias = row["approved_name_alias"]
            if not alias:
                continue
            key = (row["source_namespace"], alias)
            alias_to_canonical.setdefault(key, set()).add(row["canonical_team_id"])
            alias_meta[(key[0], key[1], row["canonical_team_id"])] = (
                row["mapping_method"],
                row["provenance_hash"],
            )

        blocked_aliases = {key for key, canonicals in alias_to_canonical.items() if len(canonicals) != 1}
        aliases: dict[tuple[str, str], _Alias] = {}
        for key, canonicals in alias_to_canonical.items():
            if key in blocked_aliases:
                continue
            canonical = next(iter(canonicals))
            method, provenance = alias_meta[(key[0], key[1], canonical)]
            aliases[key] = _Alias(key[0], key[1], canonical, method, provenance)

        fingerprint_payload = {
            "mappings": [asdict(mappings[k]) for k in sorted(mappings)],
            "blocked_source_keys": [list(k) for k in sorted(blocked_source_keys)],
            "aliases": [asdict(aliases[k]) for k in sorted(aliases)],
            "blocked_aliases": [list(k) for k in sorted(blocked_aliases)],
        }
        self._mappings = mappings
        self._blocked_source_keys = frozenset(blocked_source_keys)
        self._aliases = aliases
        self._blocked_aliases = frozenset(blocked_aliases)
        self._fingerprint = _stable_hash(fingerprint_payload)

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def diagnostics(self) -> dict[str, Any]:
        return {
            "strict_source_mappings": len(self._mappings),
            "blocked_source_keys": len(self._blocked_source_keys),
            "approved_aliases": len(self._aliases),
            "blocked_aliases": len(self._blocked_aliases),
            "resolver_fingerprint": self._fingerprint,
            "fuzzy_matching_enabled": False,
        }

    def resolve(
        self,
        source_namespace: str,
        source_team_id: Any | None = None,
        source_name: str | None = None,
    ) -> TeamResolution:
        ns = _namespace(source_namespace)
        sid = _text(source_team_id) if source_team_id is not None else None
        name = _text(source_name) if source_name is not None else None

        if sid:
            key = (ns, sid)
            if key in self._blocked_source_keys:
                return TeamResolution(
                    AMBIGUOUS, ns, sid, name, None, None, None, self._fingerprint,
                    "source_or_reverse_mapping_conflict",
                )
            mapping = self._mappings.get(key)
            if mapping is not None:
                return TeamResolution(
                    RESOLVED,
                    ns,
                    sid,
                    name,
                    mapping.canonical_team_id,
                    mapping.mapping_method,
                    mapping.provenance_hash,
                    self._fingerprint,
                )

        if name:
            alias_key = (ns, _normalize_alias(name))
            if alias_key in self._blocked_aliases:
                return TeamResolution(
                    AMBIGUOUS, ns, sid, name, None, None, None, self._fingerprint,
                    "approved_alias_conflict",
                )
            alias = self._aliases.get(alias_key)
            if alias is not None:
                return TeamResolution(
                    RESOLVED,
                    ns,
                    sid,
                    name,
                    alias.canonical_team_id,
                    alias.mapping_method,
                    alias.provenance_hash,
                    self._fingerprint,
                )

        return TeamResolution(
            UNRESOLVED, ns, sid, name, None, None, None, self._fingerprint,
            "no_exact_source_id_or_approved_alias",
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "TeamIdentityResolver":
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
        records = obj.get("records") if isinstance(obj, dict) else obj
        if not isinstance(records, list):
            raise ValueError("identity JSON must be a list or {'records': [...]} object")
        return cls(records)
