#!/usr/bin/env python3
"""V6.18.6 r3 transport wrapper.

V6.18.6 r2 failed before any fixture/data-quality scoring because Understat returned a
compressed AJAX body (gzip magic 0x1f 0x8b) and the r1 fetcher decoded raw bytes as
UTF-8. All five domains failed identically, so r2 contains no coverage evidence.

This wrapper changes transport decoding only:
- request Accept-Encoding: identity;
- if a server still returns gzip/deflate, decompress explicitly;
- hash both wire bytes and decoded bytes for auditability.

Fixture identity, exact-token requirement, ±2 day tolerance, fuzzy-diagnostic-only
policy, state construction, 90% preferred coverage threshold and governance are
unchanged. No model fitting or probability changes.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import urllib.request
import zlib
from typing import Any

import v6_understat_fixture_alignment_audit_v6186 as audit
from platform_core import PlatformError


def fetch_understat_payload_transport_safe(
    league: str, year: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"https://understat.com/getLeagueData/{league}/{year}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "football-v6.18.6-xg-alignment-audit/1.1",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Encoding": "identity",
            "Referer": f"https://understat.com/league/{league}/{year}",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        wire = response.read()
        content_encoding = str(response.headers.get("Content-Encoding") or "").lower().strip()
        content_type = str(response.headers.get("Content-Type") or "").strip()

    decoded = wire
    transport = "identity"
    try:
        if wire[:2] == b"\x1f\x8b" or "gzip" in content_encoding:
            decoded = gzip.decompress(wire)
            transport = "gzip"
        elif "deflate" in content_encoding:
            decoded = zlib.decompress(wire)
            transport = "deflate"
    except Exception as exc:
        raise PlatformError(f"Understat decompression failed: {url}: {exc}") from exc

    try:
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise PlatformError(
            f"Understat AJAX JSON decode failed: {url}: encoding={content_encoding!r} "
            f"content_type={content_type!r} wire_prefix={wire[:8].hex()}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise PlatformError(f"invalid Understat payload: {url}")
    teams = payload.get("teams")
    dates = payload.get("dates")
    if not isinstance(teams, dict) or not teams:
        raise PlatformError(f"missing teams: {url}")
    if not isinstance(dates, (list, dict)) or not dates:
        raise PlatformError(f"missing dates: {url}")

    return payload, {
        "url": url,
        "transport": transport,
        "content_encoding_header": content_encoding,
        "content_type": content_type,
        "wire_sha256": hashlib.sha256(wire).hexdigest(),
        "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
        "wire_bytes": len(wire),
        "decoded_bytes": len(decoded),
        "team_count": len(teams),
        "fixture_container_type": type(dates).__name__,
        "payload_keys": sorted(payload.keys()),
    }


def main() -> int:
    # Retain r2's boolean-reference repair and replace transport only.
    audit.true = True
    audit.false = False
    audit.fetch_understat_payload = fetch_understat_payload_transport_safe
    code = audit.main()
    path = audit.OUT
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "V6.18.6-understat-fixture-alignment-audit-r3-transport-safe"
        payload["pre_score_repairs"] = {
            "python_boolean_reference_repaired": True,
            "compressed_ajax_transport_repaired": True,
            "audit_design_changed": False,
            "fixture_identity_changed": False,
            "date_tolerance_changed": False,
            "fuzzy_training_rows_allowed": False,
            "r2_had_coverage_evidence": False,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
