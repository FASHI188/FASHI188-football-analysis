from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import csv
import gzip
import hashlib
import io
import json
import re
import unicodedata
from typing import Iterable, Mapping

class C2DataError(ValueError):
    pass

_FIXTURE_LINE = re.compile(r"^\s*(?:\d{1,2}:\d{2}\s+)?(.+?)\s{2,}v\s+(.+?)(?:\s{2,}\d+-\d+(?:\s+\([^)]*\))?)?\s*$")

def _text(s: str) -> str:
    return unicodedata.normalize("NFC", s.strip())

def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()

def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def parse_iso_aware(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise C2DataError("timezone-aware datetime required")
    return dt

def snapshot_eligible(*, published_at: str, cutoff: str) -> bool:
    return parse_iso_aware(published_at) <= parse_iso_aware(cutoff)

def verify_snapshot_bytes(content: bytes, expected_git_blob: str) -> dict:
    got = git_blob_sha(content)
    if got != expected_git_blob:
        raise C2DataError(f"GIT_BLOB_MISMATCH:{got}:{expected_git_blob}")
    return {"git_blob_sha": got, "content_sha256": sha256(content), "bytes": len(content)}

def parse_fixture_team_names(text: str) -> set[str]:
    """Read only team names from OpenFootball fixture lines; score tokens are ignored."""
    teams: set[str] = set()
    for raw in text.splitlines():
        if " v " not in raw:
            continue
        m = _FIXTURE_LINE.match(raw)
        if not m:
            continue
        home, away = _text(m.group(1)), _text(m.group(2))
        if home and away:
            teams.add(home); teams.add(away)
    return teams

@dataclass(frozen=True)
class ClubRecord:
    canonical: str
    aliases: tuple[str, ...]
    @property
    def exact_names(self) -> frozenset[str]:
        return frozenset((_text(self.canonical), *(_text(x) for x in self.aliases)))

def parse_openfootball_club_registry(text: str) -> list[ClubRecord]:
    records: list[ClubRecord] = []
    canonical: str | None = None
    aliases: list[str] = []
    def flush() -> None:
        nonlocal canonical, aliases
        if canonical:
            records.append(ClubRecord(canonical=_text(canonical), aliases=tuple(dict.fromkeys(_text(x) for x in aliases if _text(x)))))
        canonical = None; aliases = []
    for raw in text.splitlines():
        line = raw.rstrip(); stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("="):
            continue
        if stripped.startswith("|"):
            if canonical is None:
                continue
            part = stripped[1:].split("#", 1)[0]
            aliases.extend(x.strip() for x in part.split("|") if x.strip())
            continue
        if line[:1].isspace():
            continue
        flush(); canonical = stripped.split("##", 1)[0].split(",", 1)[0].strip()
    flush(); return records

def build_exact_alias_index(records: Iterable[ClubRecord]) -> dict[str, str]:
    idx: dict[str, str] = {}; conflicts: set[str] = set()
    for rec in records:
        for name in rec.exact_names:
            if name in idx and idx[name] != rec.canonical: conflicts.add(name)
            else: idx[name] = rec.canonical
    for name in conflicts: idx.pop(name, None)
    return idx

def resolve_exact(name: str, alias_index: Mapping[str, str]) -> str:
    key = _text(name)
    if key not in alias_index:
        raise C2DataError(f"UNRESOLVED_EXACT_IDENTITY:{key}")
    return alias_index[key]

def derive_promotions(prior_second: Iterable[str], current_top: Iterable[str], alias_index: Mapping[str, str]) -> list[str]:
    prior = {resolve_exact(x, alias_index) for x in prior_second}
    current = {resolve_exact(x, alias_index) for x in current_top}
    return sorted(prior & current)

def read_transfermarkt_clubs_gz(content: bytes) -> list[dict[str, str]]:
    with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        rows = list(csv.DictReader(text))
    required = {"club_id", "name"}
    if not rows or not required.issubset(rows[0]):
        raise C2DataError("TRANSFERMARKT_CLUBS_SCHEMA_MISSING")
    return rows

def bind_canonical_to_transfermarkt(records: Iterable[ClubRecord], transfermarkt_rows: Iterable[Mapping[str, str]], target_canonicals: Iterable[str]) -> dict:
    by_name: dict[str, list[Mapping[str, str]]] = {}
    for row in transfermarkt_rows:
        n = _text(str(row.get("name", "")))
        if n: by_name.setdefault(n, []).append(row)
    rec_by_canonical = {r.canonical: r for r in records}
    bindings, unresolved, conflicts = [], [], []
    for canonical in sorted(set(target_canonicals)):
        rec = rec_by_canonical.get(canonical)
        if rec is None:
            unresolved.append({"canonical_name": canonical, "reason": "CANONICAL_NOT_IN_OPENFOOTBALL_REGISTRY"}); continue
        matches: dict[str, Mapping[str, str]] = {}; matched_names: list[str] = []
        for exact_name in rec.exact_names:
            for row in by_name.get(exact_name, []):
                cid = str(row.get("club_id", "")).strip()
                if cid: matches[cid] = row; matched_names.append(exact_name)
        if len(matches) == 1:
            cid, row = next(iter(matches.items()))
            bindings.append({"canonical_name":canonical,"transfermarkt_club_id":cid,"transfermarkt_name":_text(str(row["name"])),"basis":"SOURCE_DECLARED_EXACT_ALIAS_OR_CANONICAL","matched_exact_names":sorted(set(matched_names))})
        elif len(matches) == 0:
            unresolved.append({"canonical_name":canonical,"reason":"NO_EXACT_TRANSFERMARKT_NAME_MATCH"})
        else:
            conflicts.append({"canonical_name":canonical,"reason":"MULTIPLE_TRANSFERMARKT_CLUB_IDS","club_ids":sorted(matches)})
    status = "PASS" if not unresolved and not conflicts else "STOP_DATA_COVERAGE"
    return {"status":status,"bindings":bindings,"unresolved":unresolved,"conflicts":conflicts,"binding_count":len(bindings)}

def source_receipt(source: Mapping[str, str], content: bytes, observed_at: str) -> dict:
    verified = verify_snapshot_bytes(content, source["blob_sha"])
    return {"repository":source["repository"],"commit":source["commit"],"path":source["path"],"git_blob_sha":source["blob_sha"],"content_sha256":verified["content_sha256"],"bytes":verified["bytes"],"published_at":source["published_at"],"available_at":source["published_at"],"observed_at":observed_at,"availability_basis":"ARCHIVE_RELEASE_AT","license":"CC0-1.0"}

def deterministic_json_sha256(obj: object) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
