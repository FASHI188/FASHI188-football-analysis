#!/usr/bin/env python3
"""Pure in-memory zero-label target-identity lock helper for football3 research."""
from __future__ import annotations
import csv, hashlib, io, json
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable

class IdentityLockError(ValueError): pass
ALLOWED_KEYS=frozenset({"competition_id","fixture_id","kickoff_at","home_team_id","away_team_id","prediction_cutoff"})

def _text(v: Any, field: str)->str:
    s=str(v or "").strip()
    if not s: raise IdentityLockError(f"{field} must be non-empty")
    return s

def _aware(v: Any, field: str)->datetime:
    if not isinstance(v,datetime): raise IdentityLockError(f"{field} must be datetime")
    if v.tzinfo is None or v.utcoffset() is None: raise IdentityLockError(f"{field} must include timezone")
    return v

def _utc_iso(v: datetime)->str:
    u=v.astimezone(timezone.utc)
    return u.isoformat().replace("+00:00","Z")

def build_identity_lock(rows: Iterable[dict[str,Any]])->dict[str,Any]:
    if isinstance(rows,(str,bytes,dict)) or not hasattr(rows,"__iter__"): raise IdentityLockError("rows must be iterable of dict")
    canon=[]
    for i,row in enumerate(rows):
        if not isinstance(row,dict): raise IdentityLockError(f"row {i} must be dict")
        if set(row)!=ALLOWED_KEYS:
            extra=sorted(set(row)-ALLOWED_KEYS); missing=sorted(ALLOWED_KEYS-set(row))
            raise IdentityLockError(f"row {i} keys mismatch extra={extra} missing={missing}")
        ko=_aware(row["kickoff_at"],"kickoff_at"); pc=_aware(row["prediction_cutoff"],"prediction_cutoff")
        if ko-pc != timedelta(minutes=15): raise IdentityLockError("prediction_cutoff must equal kickoff_at - 15 minutes exactly")
        item={"competition_id":_text(row["competition_id"],"competition_id"),"fixture_id":_text(row["fixture_id"],"fixture_id"),"kickoff_at":_utc_iso(ko),"home_team_id":_text(row["home_team_id"],"home_team_id"),"away_team_id":_text(row["away_team_id"],"away_team_id"),"prediction_cutoff":_utc_iso(pc)}
        if item["home_team_id"]==item["away_team_id"]: raise IdentityLockError("home_team_id and away_team_id must differ")
        canon.append(item)
    if not canon: raise IdentityLockError("identity lock requires at least one row")
    key=lambda r:(r["competition_id"],r["fixture_id"],r["kickoff_at"],r["home_team_id"],r["away_team_id"],r["prediction_cutoff"])
    canon.sort(key=key)
    seen=set(); ids=[]
    for r in canon:
        tup=key(r)
        if tup in seen: raise IdentityLockError("duplicate semantic target identity")
        seen.add(tup)
        raw=json.dumps(r,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
        ids.append(hashlib.sha256(raw).hexdigest())
    out=io.StringIO(newline=""); w=csv.writer(out,lineterminator="\n"); w.writerow(["identity_sha256"])
    for h in ids: w.writerow([h])
    csv_text=out.getvalue()
    ordered_identity_sha256=hashlib.sha256("\n".join(ids).encode()).hexdigest()
    lock_sha256=hashlib.sha256(csv_text.encode()).hexdigest()
    return {"schema":"football3_identity_lock_v1","row_count":len(ids),"identity_csv":csv_text,"identity_lock_sha256":lock_sha256,"ordered_identity_sha256":ordered_identity_sha256,"real_target_values_read":0,"research_only":True,"formal_weight":0.0}
