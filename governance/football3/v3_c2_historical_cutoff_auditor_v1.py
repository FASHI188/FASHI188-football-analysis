from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Iterable, Mapping, Any

_SHA1 = re.compile(r"^[0-9a-f]{40}$")

class HistoricalCutoffAuditError(ValueError):
    pass

def _aware(dt: datetime, name: str) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise HistoricalCutoffAuditError(f"{name} must be timezone-aware")

@dataclass(frozen=True)
class CommitSnapshot:
    source_id: str
    repository: str
    path: str
    commit: str
    published_at: datetime
    tree_sha: str
    blob_sha: str
    blob_bytes: int
    license_state: str

    def validate(self) -> None:
        for name in ("source_id","repository","path","license_state"):
            if not str(getattr(self, name)).strip():
                raise HistoricalCutoffAuditError(f"{name} required")
        for name in ("commit","tree_sha","blob_sha"):
            if not _SHA1.fullmatch(getattr(self, name)):
                raise HistoricalCutoffAuditError(f"{name} must be exact 40-char git sha1")
        _aware(self.published_at, "published_at")
        if self.blob_bytes < 0:
            raise HistoricalCutoffAuditError("blob_bytes must be non-negative")

    def eligible(self, cutoff: datetime) -> bool:
        self.validate(); _aware(cutoff, "cutoff")
        return self.published_at <= cutoff

    def nontrivial_blob_guard(self, min_bytes: int = 1024) -> bool:
        self.validate()
        if min_bytes <= 0:
            raise HistoricalCutoffAuditError("min_bytes must be positive")
        return self.blob_bytes >= min_bytes

def select_snapshot(history: Iterable[CommitSnapshot], cutoff: datetime, *, require_materiality: bool = True, min_bytes: int = 1024) -> CommitSnapshot:
    _aware(cutoff, "cutoff")
    eligible = []
    for item in history:
        item.validate()
        if item.published_at <= cutoff and (not require_materiality or item.nontrivial_blob_guard(min_bytes)):
            eligible.append(item)
    if not eligible:
        raise HistoricalCutoffAuditError("STOP_DATA_COVERAGE:NO_ELIGIBLE_NONTRIVIAL_SNAPSHOT_AT_CUTOFF")
    eligible.sort(key=lambda x: (x.published_at, x.commit, x.path))
    return eligible[-1]

def audit_preseason_sources(entries: Iterable[CommitSnapshot], season_start_cutoff: datetime, *, min_bytes: int = 1024) -> Mapping[str, Any]:
    _aware(season_start_cutoff, "season_start_cutoff")
    items=list(entries)
    for x in items: x.validate()
    before=[x for x in items if x.published_at <= season_start_cutoff]
    material=[x for x in before if x.nontrivial_blob_guard(min_bytes)]
    return {"source_count":len(items),"preseason_snapshot_count":len(before),"materiality_pass_count":len(material),"materiality_fail_count":len(before)-len(material),"all_preseason_present":len(before)==len(items),"all_material":len(material)==len(items)}

def classify_membership_movement(*, canonical_club_id: str, prior_competition: str | None, target_competition: str | None, target_top_division: str, approved_lower_division: str, had_earlier_top_division: bool = False, identity_conflict: bool = False) -> str:
    if not canonical_club_id.strip() or identity_conflict:
        return "UNKNOWN"
    if prior_competition == target_top_division and target_competition == target_top_division:
        return "STAYING"
    if prior_competition == approved_lower_division and target_competition == target_top_division:
        return "RETURNING" if had_earlier_top_division else "PROMOTED"
    if prior_competition == target_top_division and target_competition == approved_lower_division:
        return "RELEGATED"
    return "UNKNOWN"
