#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
import urllib.error
from pathlib import Path

import run_r43j0 as j0

# Engineering-only transport repair for provider HTTP 429.
# Scientific model, features, hyperparameters, blend, splits and gates remain in run_r43j0.py unchanged.
_original_download_stats = j0.f5.r42h.download_stats
_cached_stats_path: Path | None = None


def cached_download_stats() -> Path:
    global _cached_stats_path
    if _cached_stats_path is not None and _cached_stats_path.exists():
        return _cached_stats_path

    last_error: Exception | None = None
    for attempt in range(6):
        try:
            p = Path(_original_download_stats())
            if not p.exists() or p.stat().st_size == 0:
                raise RuntimeError("download_stats returned missing/empty file")
            _cached_stats_path = p
            return p
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt == 5:
                raise
            time.sleep(min(60, 5 * (2 ** attempt)))

    raise RuntimeError(f"stats download retries exhausted: {last_error}")


j0.f5.r42h.download_stats = cached_download_stats


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        j0.run()
    elif cmd == "verify":
        j0.verify()
    else:
        raise SystemExit(f"unknown command {cmd}")
