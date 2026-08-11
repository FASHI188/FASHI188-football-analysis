#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def packed(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fail(message: str) -> None:
    raise RuntimeError(message)


def checked_out_head(explicit: str | None) -> str:
    if explicit:
        value = explicit.strip().lower()
    else:
        value = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip().lower()
    if not SHA_RE.fullmatch(value):
        fail("CHECKED_OUT_HEAD_SHA_INVALID")
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exact-market-output", required=True)
    ap.add_argument("--checked-out-head-sha")
    args = ap.parse_args()

    root = Path(args.exact_market_output)
    receipt_path = root / "exact_payload_market_receipt_r44a.json"
    sha_path = root / "exact_payload_market_receipt_r44a.sha256"
    if not receipt_path.is_file() or not sha_path.is_file():
        fail("R44A_EXACT_RECEIPT_MISSING")

    raw = receipt_path.read_bytes()
    expected = sha_path.read_text(encoding="ascii").strip()
    if expected != sha256(raw):
        fail("R44A_EXACT_RECEIPT_SHA_MISMATCH")
    r = json.loads(raw)
    if r.get("schema_version") != "V520-R44A-EXACT-PAYLOAD-MARKET-RECEIPT-1.0":
        fail("R44A_EXACT_RECEIPT_SCHEMA_MISMATCH")
    if not r.get("bookmaker_specific_quotes_extracted"):
        fail("STRUCTURED_QUOTES_NOT_EXTRACTED")
    if r.get("formal_market_snapshot") is not False or r.get("formal_execution_ready") is not False:
        fail("FORMAL_BOUNDARY_INVALID")

    head = checked_out_head(args.checked_out_head_sha)
    event_sha = (os.getenv("GITHUB_SHA") or "").strip().lower() or None
    if event_sha is not None and not SHA_RE.fullmatch(event_sha):
        fail("GITHUB_EVENT_SHA_INVALID")

    r["schema_version"] = "V520-R44A-EXACT-PAYLOAD-MARKET-RECEIPT-1.1"
    r["run_identity"] = {
        "github_run_id": (os.getenv("GITHUB_RUN_ID") or None),
        "github_run_attempt": (os.getenv("GITHUB_RUN_ATTEMPT") or None),
        "checked_out_head_sha": head,
        "github_event_sha": event_sha,
        "github_head_ref": (os.getenv("GITHUB_HEAD_REF") or None),
        "github_ref": (os.getenv("GITHUB_REF") or None),
        "identity_semantics": "checked_out_head_sha is git rev-parse HEAD and is authoritative for code identity; github_event_sha is retained separately because pull_request events may point at a merge ref.",
    }

    encoded = packed(r) + b"\n"
    receipt_path.write_bytes(encoded)
    sha_path.write_text(sha256(encoded) + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": "PASS_R44A_RUN_IDENTITY_FINALIZED",
        "checked_out_head_sha": head,
        "github_event_sha": event_sha,
        "receipt_sha256": sha256(encoded),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R44A_RUN_IDENTITY_FINALIZE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
