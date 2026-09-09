#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "football-data" / "formal_gpt_gateway_v1"
RUNTIME_DIR = ROOT / "football-data" / "formal_fast_runtime_v1"
for p in (str(ROOT / "football-data"), str(RUNTIME_DIR), str(GATEWAY_DIR), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_delta_acquisition_v1 as live
import current_v2_retrospective_replay_acceptance_v1 as acceptance

SCHEMA = "football3-current-v2-retrospective-acceptance-transport-v1"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
FOOTBALL_DATA_PREFIX = "https://www.football-data.co.uk/"


def _direct_same_authority(url: str, *, data: bytes | None = None,
                           headers: dict[str, str] | None = None,
                           timeout: int = 60) -> tuple[bytes, str]:
    merged = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/csv,application/json,text/plain,*/*",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.football-data.co.uk/data.php",
    }
    if headers:
        merged.update(headers)
        merged["User-Agent"] = BROWSER_UA
        merged["Accept-Encoding"] = "identity"
    req = urllib.request.Request(
        url,
        data=data,
        headers=merged,
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    if not raw:
        raise live.AcquisitionError(f"same-authority acceptance transport returned empty payload: {url}")
    return raw, hashlib.sha256(raw).hexdigest()


def install() -> tuple[Any, dict[str, Any]]:
    original = live._fetch
    cache: dict[tuple[str, bytes, tuple[tuple[str, str], ...]], tuple[bytes, str]] = {}
    audit: dict[str, Any] = {
        "schema_version": SCHEMA,
        "authority_changed": False,
        "source_url_changed": False,
        "model_or_current_or_weight_changed": False,
        "prospective_path_changed": False,
        "strict_pit_path_changed": False,
        "calls": 0,
        "original_transport_success": 0,
        "browser_compatibility_fallback_success": 0,
        "failures": [],
    }

    def resilient(url: str, *, data: bytes | None = None,
                  headers: dict[str, str] | None = None,
                  timeout: int = 60) -> tuple[bytes, str]:
        key = (url, data or b"", tuple(sorted((headers or {}).items())))
        if key in cache:
            return cache[key]
        audit["calls"] += 1
        last: Exception | None = None
        for attempt in range(1, 4):
            try:
                value = original(url, data=data, headers=headers, timeout=timeout)
                audit["original_transport_success"] += 1
                cache[key] = value
                return value
            except live.AcquisitionError as exc:
                last = exc
                if attempt < 3:
                    time.sleep(float(attempt))
        if url.startswith(FOOTBALL_DATA_PREFIX):
            for attempt in range(1, 4):
                try:
                    value = _direct_same_authority(url, data=data, headers=headers, timeout=timeout)
                    audit["browser_compatibility_fallback_success"] += 1
                    cache[key] = value
                    return value
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, live.AcquisitionError) as exc:
                    last = exc
                    if attempt < 3:
                        time.sleep(float(attempt * 2))
        audit["failures"].append({"url": url, "error_type": type(last).__name__ if last else "UNKNOWN"})
        if isinstance(last, live.AcquisitionError):
            raise last
        raise live.AcquisitionError(f"acceptance same-authority transport failed: {url}: {last}") from last

    live._fetch = resilient
    return original, audit


def main() -> int:
    original, audit = install()
    rc = 1
    try:
        rc = int(acceptance.main())
        return rc
    finally:
        live._fetch = original
        work: Path | None = None
        argv = list(sys.argv)
        if "--work" in argv:
            try:
                work = Path(argv[argv.index("--work") + 1]).resolve()
            except Exception:
                work = None
        if work is not None:
            work.mkdir(parents=True, exist_ok=True)
            audit["status"] = "PASS" if rc == 0 and not audit["failures"] else ("PASS_WITH_SAME_AUTHORITY_BROWSER_COMPATIBILITY" if rc == 0 else "FAIL")
            (work / "acceptance_transport_audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
                encoding="utf-8",
            )


if __name__ == "__main__":
    raise SystemExit(main())
