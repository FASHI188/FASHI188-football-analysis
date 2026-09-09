#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import current_v2_retrospective_replay_acceptance_v1 as acceptance
import live_delta_acquisition_v1 as live

SCHEMA = "football3-current-v2-retrospective-replay-acceptance-transport-retry-v2"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
MAX_ORIGINAL_ATTEMPTS = 3
MAX_BROWSER_ATTEMPTS = 3
FOOTBALL_DATA_PREFIX = "https://www.football-data.co.uk/"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,application/json,*/*",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.football-data.co.uk/data.php",
}
TRANSIENT_TOKENS = (
    "HTTP Error 429",
    "HTTP Error 500",
    "HTTP Error 502",
    "HTTP Error 503",
    "HTTP Error 504",
    "timed out",
    "Temporary failure",
    "Connection reset",
    "Remote end closed connection",
)


def _is_transient(exc: Exception) -> bool:
    text = str(exc)
    return any(token in text for token in TRANSIENT_TOKENS)


def install_retry() -> tuple[object, dict[str, Any]]:
    original = live._fetch
    cache: dict[tuple[str, bytes, tuple[tuple[str, str], ...]], tuple[bytes, str]] = {}
    audit: dict[str, Any] = {
        "schema_version": SCHEMA,
        "mode": MODE,
        "authority_changed": False,
        "source_url_changed": False,
        "model_or_current_or_weight_changed": False,
        "prospective_path_changed": False,
        "strict_pit_path_changed": False,
        "calls": 0,
        "cache_hits": 0,
        "original_transport_success": 0,
        "browser_compatibility_fallback_success": 0,
        "failures": [],
    }

    def retry_fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
                    timeout: int = 60):
        key = (url, data or b"", tuple(sorted((headers or {}).items())))
        if key in cache:
            audit["cache_hits"] += 1
            return cache[key]
        audit["calls"] += 1
        last: Exception | None = None
        for attempt in range(1, MAX_ORIGINAL_ATTEMPTS + 1):
            try:
                value = original(url, data=data, headers=headers, timeout=timeout)
                audit["original_transport_success"] += 1
                cache[key] = value
                return value
            except live.AcquisitionError as exc:
                last = exc
                if not _is_transient(exc) or attempt >= MAX_ORIGINAL_ATTEMPTS:
                    break
                time.sleep(float(attempt * 2))

        # Same governed authority and same URL. Only the HTTP compatibility headers
        # change, and only inside this research acceptance process. Shared live_delta,
        # prospective requests and STRICT_PIT are never patched persistently.
        if url.startswith(FOOTBALL_DATA_PREFIX):
            compat_headers = dict(headers or {})
            compat_headers.update(BROWSER_HEADERS)
            for attempt in range(1, MAX_BROWSER_ATTEMPTS + 1):
                try:
                    value = original(url, data=data, headers=compat_headers, timeout=timeout)
                    audit["browser_compatibility_fallback_success"] += 1
                    cache[key] = value
                    return value
                except live.AcquisitionError as exc:
                    last = exc
                    if not _is_transient(exc) or attempt >= MAX_BROWSER_ATTEMPTS:
                        break
                    time.sleep(float(attempt * 2))

        audit["failures"].append({
            "url": url,
            "error_type": type(last).__name__ if last is not None else "UNKNOWN",
            "transient": bool(last is not None and _is_transient(last)),
        })
        if isinstance(last, live.AcquisitionError):
            raise last
        raise live.AcquisitionError(f"acceptance transport failed: {url}: {last}") from last

    live._fetch = retry_fetch
    return original, audit


def _work_dir() -> Path | None:
    if "--work" not in sys.argv:
        return None
    try:
        return Path(sys.argv[sys.argv.index("--work") + 1]).resolve()
    except Exception:
        return None


def main() -> int:
    original, audit = install_retry()
    rc = 1
    try:
        rc = int(acceptance.main())
        return rc
    finally:
        live._fetch = original
        work = _work_dir()
        if work is not None:
            work.mkdir(parents=True, exist_ok=True)
            audit["status"] = "PASS" if rc == 0 and not audit["failures"] else "FAIL"
            (work / "acceptance_transport_audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
                encoding="utf-8",
            )


if __name__ == "__main__":
    raise SystemExit(main())
