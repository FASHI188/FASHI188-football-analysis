#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import shutil
import sys
import time
import urllib.error
from pathlib import Path

import run_r43j0 as j0

# Engineering-only transport repair for provider HTTP 429.
# Scientific model, features, hyperparameters, blend, splits and gates remain in run_r43j0.py unchanged.
CACHE = Path(__file__).resolve().parent / "source_cache"
CACHE.mkdir(parents=True, exist_ok=True)

_original_r42i_download = j0.f5.r42i.download
_original_f0_download = j0.f5.f0.download
_original_download_stats = j0.f5.r42h.download_stats
_cached_stats_path: Path | None = None


def _cache_path(url: str) -> Path:
    suffix = ".parquet" if ".parquet" in url else ".bin"
    return CACHE / (hashlib.sha256(url.encode("utf-8")).hexdigest() + suffix)


def _retry_call(fn, *args):
    last_error: Exception | None = None
    for attempt in range(7):
        try:
            return fn(*args)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt == 6:
                raise
            time.sleep(min(90, 5 * (2 ** attempt)))
    raise RuntimeError(f"download retries exhausted: {last_error}")


def cached_r42i_download(url: str, path: Path, user_agent: str) -> None:
    path = Path(path)
    cache = _cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and cache.stat().st_size > 0:
        shutil.copyfile(cache, path)
        return
    _retry_call(_original_r42i_download, url, path, user_agent)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"empty r42i source: {url}")
    shutil.copyfile(path, cache)


def cached_f0_download(url: str, path: Path) -> None:
    path = Path(path)
    cache = _cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    if cache.exists() and cache.stat().st_size > 0:
        shutil.copyfile(cache, path)
        return
    _retry_call(_original_f0_download, url, path)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"empty f0 source: {url}")
    shutil.copyfile(path, cache)


def cached_download_stats() -> Path:
    global _cached_stats_path
    if _cached_stats_path is not None and _cached_stats_path.exists() and _cached_stats_path.stat().st_size > 0:
        return _cached_stats_path
    p = Path(_retry_call(_original_download_stats))
    if not p.exists() or p.stat().st_size == 0:
        raise RuntimeError("download_stats returned missing/empty file")
    _cached_stats_path = p
    return p


j0.f5.r42i.download = cached_r42i_download
j0.f5.f0.download = cached_f0_download
j0.f5.r42h.download_stats = cached_download_stats


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        j0.run()
    elif cmd == "verify":
        j0.verify()
    else:
        raise SystemExit(f"unknown command {cmd}")
