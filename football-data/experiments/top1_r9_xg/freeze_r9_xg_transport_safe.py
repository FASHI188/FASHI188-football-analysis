#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import re
import urllib.request
import zlib
from pathlib import Path

import run_experiment_r9 as r9


def transport_safe_get(legacy_url: str) -> bytes:
    m = re.fullmatch(r"https://understat\.com/league/([^/]+)/(\d{4})", legacy_url)
    if not m:
        raise RuntimeError(f"unexpected Understat URL: {legacy_url}")
    league, season = m.groups()
    url = f"https://understat.com/getLeagueData/{league}/{season}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "football3-research/9.0-transport-safe",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Encoding": "identity",
            "Referer": legacy_url,
        },
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        wire = response.read()
        encoding = str(response.headers.get("Content-Encoding") or "").lower().strip()

    decoded = wire
    if wire[:2] == b"\x1f\x8b" or "gzip" in encoding:
        decoded = gzip.decompress(wire)
    elif "deflate" in encoding:
        decoded = zlib.decompress(wire)

    payload = json.loads(decoded.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid Understat payload: {url}")
    dates = payload.get("dates")
    if not isinstance(dates, (list, dict)) or not dates:
        raise RuntimeError(f"missing Understat dates: {url}")
    return decoded


def extract_ajax_dates(blob: bytes):
    payload = json.loads(blob.decode("utf-8"))
    dates = payload.get("dates")
    if isinstance(dates, dict):
        return list(dates.values())
    if isinstance(dates, list):
        return dates
    raise RuntimeError("invalid Understat dates container")


def main() -> int:
    # Keep R9's row validation, 20k selection, hashing and manifest contract intact.
    # Replace transport/parser only; this mirrors the repository's V6.18.6r3 repair.
    r9.get = transport_safe_get
    r9.extract_dates = extract_ajax_dates
    r9.freeze()

    manifest_path = r9.DATA / "source_manifest_r9.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"] = "Understat getLeagueData AJAX JSON via transport-safe wrapper"
    manifest["transport_contract"] = {
        "accept_encoding": "identity",
        "gzip_fallback_decode": True,
        "deflate_fallback_decode": True,
        "ajax_json": True,
        "model_or_evaluation_logic_changed": False,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
