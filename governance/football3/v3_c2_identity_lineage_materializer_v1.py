from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import hashlib, re
from typing import Iterable

_SHA1=re.compile(r"^[0-9a-f]{40}$")
_SHA256=re.compile(r"^[0-9a-f]{64}$")

class IdentityLineageError(ValueError): pass

def _aware(x:datetime,name:str)->None:
    if x.tzinfo is None or x.utcoffset() is None:
        raise IdentityLineageError(f"{name} must be timezone-aware")

@dataclass(frozen=True)
class IdentityAssertion:
    source:str
    source_scheme:str
    source_club_id:str
    canonical_club_id:str
    observed_at:datetime
    available_at:datetime
    content_sha256:str
    basis:str

    def validate(self)->None:
        for n in ("source","source_scheme","source_club_id","canonical_club_id","basis"):
            if not str(getattr(self,n)).strip(): raise IdentityLineageError(f"{n} required")
        _aware(self.observed_at,"observed_at"); _aware(self.available_at,"available_at")
        if self.observed_at > self.available_at: raise IdentityLineageError("observed_at > available_at")
        if not _SHA256.fullmatch(self.content_sha256): raise IdentityLineageError("content_sha256 invalid")
        if self.basis not in {"PROVIDER_STABLE_ID","PREDECLARED_EXACT_CROSSWALK","EXTERNAL_STABLE_ID_EQUIVALENCE"}:
            raise IdentityLineageError("non-exact identity basis forbidden")

    def eligible(self, cutoff:datetime)->bool:
        self.validate(); _aware(cutoff,"cutoff")
        return self.available_at <= cutoff

@dataclass(frozen=True)
class MembershipAssertion:
    canonical_club_id:str
    competition_id:str
    season:str
    available_at:datetime
    source:str
    source_commit:str
    source_blob_sha:str

    def validate(self)->None:
        for n in ("canonical_club_id","competition_id","season","source"):
            if not str(getattr(self,n)).strip(): raise IdentityLineageError(f"{n} required")
        _aware(self.available_at,"available_at")
        if not _SHA1.fullmatch(self.source_commit): raise IdentityLineageError("source_commit invalid")
        if not _SHA1.fullmatch(self.source_blob_sha): raise IdentityLineageError("source_blob_sha invalid")

    def eligible(self, cutoff:datetime)->bool:
        self.validate(); _aware(cutoff,"cutoff"); return self.available_at <= cutoff

def exact_identity_join(assertions:Iterable[IdentityAssertion], cutoff:datetime)->dict[str,str]:
    _aware(cutoff,"cutoff")
    by_source={}
    for a in assertions:
        a.validate()
        if not a.eligible(cutoff): continue
        key=(a.source_scheme,a.source_club_id)
        prior=by_source.get(key)
        if prior is not None and prior != a.canonical_club_id:
            raise IdentityLineageError("IDENTITY_OR_STATE_FAILURE: source id conflict")
        by_source[key]=a.canonical_club_id
    return {f"{k[0]}:{k[1]}":v for k,v in sorted(by_source.items())}

def classify_movement(*, club_id:str, prior_competition:str|None, target_competition:str|None,
                      target_top:str, approved_lower:str, earlier_top_membership:bool=False,
                      identity_conflict:bool=False)->str:
    if not club_id.strip() or identity_conflict: return "UNKNOWN"
    if prior_competition==target_top and target_competition==target_top: return "STAYING"
    if prior_competition==approved_lower and target_competition==target_top:
        return "RETURNING" if earlier_top_membership else "PROMOTED"
    if prior_competition==target_top and target_competition==approved_lower: return "RELEGATED"
    return "UNKNOWN"

def strict_membership_at_cutoff(records:Iterable[MembershipAssertion], cutoff:datetime)->dict[tuple[str,str],str]:
    _aware(cutoff,"cutoff")
    out={}
    for r in records:
        r.validate()
        if not r.eligible(cutoff): continue
        key=(r.canonical_club_id,r.season)
        old=out.get(key)
        if old is not None and old != r.competition_id:
            raise IdentityLineageError("IDENTITY_OR_STATE_FAILURE: membership conflict")
        out[key]=r.competition_id
    return out

def evidence_digest(parts:Iterable[str])->str:
    return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()
