from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib, json, re
from typing import Iterable

_HEX40=re.compile(r"^[0-9a-f]{40}$")
_HEX64=re.compile(r"^[0-9a-f]{64}$")

class SnapshotContractError(ValueError):
    pass

def _aware(x: datetime, name: str) -> None:
    if x.tzinfo is None or x.utcoffset() is None:
        raise SnapshotContractError(f"{name} must be timezone-aware")

@dataclass(frozen=True)
class GitSnapshotEvidence:
    repository: str
    commit_sha: str
    path: str
    blob_sha: str
    published_at: datetime
    observed_at: datetime
    retrieved_at: datetime
    license_state: str
    source_identity: str

    def validate(self) -> None:
        for n in ("repository","path","license_state","source_identity"):
            if not getattr(self,n).strip():
                raise SnapshotContractError(f"{n} required")
        if not _HEX40.fullmatch(self.commit_sha):
            raise SnapshotContractError("commit_sha must be 40 lowercase hex")
        if not _HEX40.fullmatch(self.blob_sha):
            raise SnapshotContractError("blob_sha must be 40 lowercase hex")
        for n in ("published_at","observed_at","retrieved_at"):
            _aware(getattr(self,n),n)
        if self.observed_at < self.published_at:
            raise SnapshotContractError("observed_at cannot predate published_at")
        if self.retrieved_at < self.observed_at:
            raise SnapshotContractError("retrieved_at cannot predate observed_at")

    def pit_eligible(self, cutoff: datetime) -> bool:
        self.validate(); _aware(cutoff,"cutoff")
        return self.published_at <= cutoff

    def receipt_payload(self) -> dict:
        self.validate()
        d=asdict(self)
        for k in ("published_at","observed_at","retrieved_at"):
            d[k]=getattr(self,k).isoformat()
        d["availability_basis"]="ARCHIVE_RELEASE_AT"
        d["available_at"]=self.published_at.isoformat()
        return d

    def receipt_sha256(self) -> str:
        raw=json.dumps(self.receipt_payload(),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
        return hashlib.sha256(raw).hexdigest()

def select_latest_snapshot_at_or_before_cutoff(rows: Iterable[GitSnapshotEvidence], cutoff: datetime) -> GitSnapshotEvidence:
    _aware(cutoff,"cutoff")
    valid=[]
    for row in rows:
        row.validate()
        if row.published_at <= cutoff:
            valid.append(row)
    if not valid:
        raise SnapshotContractError("NO_STRICT_PIT_GIT_SNAPSHOT_AT_OR_BEFORE_CUTOFF")
    return max(valid,key=lambda x:(x.published_at,x.commit_sha,x.blob_sha))

def reject_latest_archive_for_earlier_cutoff(snapshot: GitSnapshotEvidence, cutoff: datetime) -> None:
    if not snapshot.pit_eligible(cutoff):
        raise SnapshotContractError("LATEST_ARCHIVE_CANNOT_BE_BACKFILLED_BEFORE_PUBLICATION")
