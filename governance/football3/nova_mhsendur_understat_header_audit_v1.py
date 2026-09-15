#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path


class AuditError(ValueError):
    pass


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def validate_lock(lock: dict) -> None:
    if lock.get("schema_version") != "football3-nova-n1-mhsendur-understat-header-lock-v1":
        raise AuditError("unexpected schema_version")
    src = lock.get("source", {})
    for key in ("repository", "revision", "readme_blob_sha1", "archive_path", "archive_blob_sha1", "raw_url"):
        if not src.get(key):
            raise AuditError(f"missing source field {key}")
    perm = lock.get("permission", {})
    if perm.get("class") != "RESEARCH_ONLY_EXPLICIT_REUSE":
        raise AuditError("permission class drift")
    if perm.get("production_eligible") is not False:
        raise AuditError("research-only source cannot be production eligible")
    gov = lock.get("governance", {})
    if gov.get("candidate_confirmation_allowed") is not False:
        raise AuditError("historical source cannot be candidate confirmation")
    if any(gov.get(k) is not False for k in ("formal_v2_changed", "current_changed", "production_changed")):
        raise AuditError("formal state change forbidden")
    roles = lock.get("locked_feature_roles")
    expected = ["date", "h_a", "deep", "deep_allowed", "ppda", "ppda_allowed"]
    if roles != expected:
        raise AuditError("locked feature roles drift")


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Football3-Nova-N1-Header-Audit/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def header_fields(zf: zipfile.ZipFile, member: str) -> list[str]:
    with zf.open(member, "r") as fh:
        line = fh.readline(131072)
    if not line:
        raise AuditError(f"empty CSV member {member}")
    text = line.decode("utf-8-sig").rstrip("\r\n")
    return next(csv.reader([text]))


def audit(lock: dict, archive: bytes) -> dict:
    validate_lock(lock)
    expected_blob = lock["source"]["archive_blob_sha1"]
    observed_blob = git_blob_sha1(archive)
    if observed_blob != expected_blob:
        raise AuditError(f"archive blob SHA drift: {observed_blob}")

    required = set(lock["locked_feature_roles"])
    with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
        csv_members = sorted(
            n for n in zf.namelist()
            if not n.endswith("/") and n.lower().endswith(".csv")
        )
        if not csv_members:
            raise AuditError("archive contains no CSV members")
        member_receipts = []
        qualified = 0
        for member in csv_members:
            fields = header_fields(zf, member)
            normalized = [x.strip().lower() for x in fields]
            missing = sorted(required.difference(normalized))
            ok = not missing
            qualified += int(ok)
            member_receipts.append({
                "member": member,
                "header": fields,
                "missing_locked_roles": missing,
                "header_schema_qualified": ok,
            })

    status = "HEADER_SCHEMA_QUALIFIED" if qualified > 0 else "HEADER_SCHEMA_NOT_QUALIFIED"
    return {
        "status": status,
        "source_repository": lock["source"]["repository"],
        "source_revision": lock["source"]["revision"],
        "archive_path": lock["source"]["archive_path"],
        "archive_blob_sha1": observed_blob,
        "permission_class": lock["permission"]["class"],
        "production_eligible": False,
        "csv_member_count": len(member_receipts),
        "header_schema_qualified_member_count": qualified,
        "header_schema_not_qualified_member_count": len(member_receipts) - qualified,
        "locked_feature_roles": lock["locked_feature_roles"],
        "members": member_receipts,
        "data_rows_read": 0,
        "result_values_read": 0,
        "candidate_confirmation_allowed": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    lock = json.loads(Path(args.lock).read_text(encoding="utf-8"))
    archive = fetch_bytes(lock["source"]["raw_url"])
    receipt = audit(lock, archive)
    Path(args.out).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
