#!/usr/bin/env python3
from __future__ import annotations

import time

import current_v2_retrospective_replay_acceptance_v1 as acceptance
import live_delta_acquisition_v1 as live

SCHEMA = "football3-current-v2-retrospective-replay-acceptance-transport-retry-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
MAX_ATTEMPTS = 6
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


def install_retry() -> tuple[object, object]:
    original = live._fetch

    def retry_fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
                    timeout: int = 60):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return original(url, data=data, headers=headers, timeout=timeout)
            except live.AcquisitionError as exc:
                if not _is_transient(exc) or attempt >= MAX_ATTEMPTS:
                    raise
                time.sleep(attempt * 2)
        raise AssertionError("unreachable acceptance transport retry state")

    live._fetch = retry_fetch
    return original, retry_fetch


def main() -> int:
    original, _retry = install_retry()
    try:
        return acceptance.main()
    finally:
        live._fetch = original


if __name__ == "__main__":
    raise SystemExit(main())
