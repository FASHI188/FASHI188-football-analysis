from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import json
import re
from typing import Iterable, Mapping, Any

_SHA1 = re.compile(r"^[0-9a-f]{40}$")

class C2MaterializationError(ValueError):
    pass

def _aware(dt: datetime, name: str) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise C2MaterializationError(f"{name} must be timezone-aware")

@dataclass(frozen=True)
class GitSourceSnapshot:
    source_id: str
    repository: str
    commit: str
    commit_published_at: datetime
    path: str
    blob_sha: str
    license_state: str

    def validate(self) -> None:
        for name in ("source_id","repository","path","license_state"):
            if not getattr(self, name).strip():
                raise C2MaterializationError(f"{name} required")
        if not _SHA1.fullmatch(self.commit):
            raise C2MaterializationError("commit must be exact 40-char git sha1")
        if not _SHA1.fullmatch(self.blob_sha):
            raise C2MaterializationError("blob_sha must be exact 40-char git blob sha1")
        _aware(self.commit_published_at, "commit_published_at")

    def eligible_for_cutoff(self, cutoff: datetime) -> bool:
        self.validate(); _aware(cutoff, "cutoff")
        return self.commit_published_at <= cutoff

    def receipt(self, cutoff: datetime) -> Mapping[str, Any]:
        if not self.eligible_for_cutoff(cutoff):
            raise C2MaterializationError("snapshot published after target cutoff")
        payload = asdict(self)
        payload["commit_published_at"] = self.commit_published_at.isoformat()
        payload["target_cutoff"] = cutoff.isoformat()
        payload["availability_basis"] = "ARCHIVE_RELEASE_AT"
        payload["historical_backfill"] = False
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return {**payload, "receipt_sha256": hashlib.sha256(body).hexdigest()}

def select_latest_eligible_snapshot(snapshots: Iterable[GitSourceSnapshot], cutoff: datetime) -> GitSourceSnapshot:
    _aware(cutoff, "cutoff")
    eligible = []
    for s in snapshots:
        s.validate()
        if s.commit_published_at <= cutoff:
            eligible.append(s)
    if not eligible:
        raise C2MaterializationError("STOP_DATA_COVERAGE:NO_ELIGIBLE_SOURCE_COMMIT_AT_CUTOFF")
    eligible.sort(key=lambda s: (s.commit_published_at, s.commit, s.path))
    return eligible[-1]

def validate_identity_binding(binding: Mapping[str, str]) -> None:
    required = ("source","source_club_key","canonical_club_id","competition_season","binding_basis","binding_sha256","evidence_source","evidence_snapshot_sha")
    for key in required:
        if not str(binding.get(key, "")).strip():
            raise C2MaterializationError(f"missing identity field {key}")
    if binding["binding_basis"] not in {"PROVIDER_STABLE_ID","PREDECLARED_EXACT_CROSSWALK","EXTERNAL_STABLE_ID_EQUIVALENCE"}:
        raise C2MaterializationError("fuzzy/name-only identity is forbidden")
    if not re.fullmatch(r"[0-9a-f]{64}", binding["binding_sha256"]):
        raise C2MaterializationError("binding_sha256 must be lowercase sha256")
    if not _SHA1.fullmatch(binding["evidence_snapshot_sha"]):
        raise C2MaterializationError("evidence_snapshot_sha must be exact git sha1")

def materialization_status(*, historical_cutoff_snapshots: int, exact_identity_bindings: int, promotion_lineage_closed: bool, cross_league_scale_frozen: bool) -> str:
    if historical_cutoff_snapshots <= 0 or exact_identity_bindings <= 0:
        return "STOP_DATA_COVERAGE"
    if not promotion_lineage_closed or not cross_league_scale_frozen:
        return "PARTIAL_DATA_FOUNDATION_READY_STOP_DATA_COVERAGE"
    return "ZERO_LABEL_DATA_READY_PREREG_REQUIRED"
