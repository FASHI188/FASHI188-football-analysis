#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_r43o1 as core  # noqa: E402

CACHE_DIR = HERE / "cache"
CACHE_PATH = CACHE_DIR / "fixture_players_stats_flat.parquet"
TMP_PATH = CACHE_DIR / "fixture_players_stats_flat.parquet.part"
RETRY_DELAYS = (0, 10, 30, 60, 120, 240)


def cached_download_stats() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    expected = core.f5.r42h.EXPECTED_STATS_SHA256
    if CACHE_PATH.exists():
        got = core.f5.r42h.fsha(CACHE_PATH)
        if got == expected:
            return CACHE_PATH
        CACHE_PATH.unlink()

    last_exc = None
    for delay in RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            TMP_PATH.unlink(missing_ok=True)
            req = urllib.request.Request(
                core.f5.r42h.STATS_URL,
                headers={"User-Agent": "football3-r43o1-frozen-cache/1.0"},
            )
            with urllib.request.urlopen(req, timeout=300) as response, TMP_PATH.open("wb") as out:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
            got = core.f5.r42h.fsha(TMP_PATH)
            if got != expected:
                raise RuntimeError(f"fixture_players_stats_flat source drift: {got}")
            os.replace(TMP_PATH, CACHE_PATH)
            return CACHE_PATH
        except urllib.error.HTTPError as exc:
            TMP_PATH.unlink(missing_ok=True)
            last_exc = exc
            if exc.code != 429 and exc.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            TMP_PATH.unlink(missing_ok=True)
            last_exc = exc

    raise RuntimeError(f"frozen stats download exhausted retries: {last_exc}")


def main() -> None:
    # Engineering-only monkeypatch: the frozen URL and expected SHA remain those
    # defined by the hash-verified R42H dependency. Scientific R43O1 settings are untouched.
    core.f5.r42h.download_stats = cached_download_stats
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        core.run()
    elif cmd == "verify":
        core.verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
