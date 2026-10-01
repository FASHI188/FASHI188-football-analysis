#!/usr/bin/env python3
"""Zero-target-label coverage/identity preflight for World Model V2 B06.

This script may fetch StatsBomb match metadata, whose upstream payload co-locates
final scores, but it never references score keys. It never GETs event payloads;
event coverage is checked with HTTP HEAD only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

USER_AGENT = "FASHI188-football-analysis/world-model-v2-b06-preflight"
B06_SOURCES = (
    (7, 108, "Ligue 1", "2021/2022", "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/7/108.json"),
    (7, 235, "Ligue 1", "2022/2023", "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/7/235.json"),
)
VIEWED_SOURCES = (
    (2, 27, "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/2/27.json"),
    (9, 27, "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/9/27.json"),
    (37, 4, "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/37/4.json"),
    (37, 42, "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/37/42.json"),
    (37, 90, "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/37/90.json"),
    (37, 281, "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/37/281.json"),
    (49, 3, "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/49/3.json"),
    (49, 107, "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/49/107.json"),
)
EVENT_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data/events/{match_id}.json"
MIN_TOTAL_MATCHES = 220
MIN_EVENT_HEAD_RATE = 0.98


def download_bytes(url: str, attempts: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last}")


def load_identity_metadata(url: str) -> tuple[list[dict[str, Any]], str]:
    raw = download_bytes(url)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"match metadata payload is not a list: {url}")
    rows: list[dict[str, Any]] = []
    for item in payload:
        # Intentionally access identity/ordering fields only. Do not reference score keys.
        rows.append(
            {
                "match_id": int(item["match_id"]),
                "match_date": str(item["match_date"]),
                "kick_off": str(item.get("kick_off") or ""),
                "competition_id": int(item["competition"]["competition_id"]),
                "season_id": int(item["season"]["season_id"]),
                "home_team_id": int(item["home_team"]["home_team_id"]),
                "away_team_id": int(item["away_team"]["away_team_id"]),
            }
        )
    return rows, hashlib.sha256(raw).hexdigest()


def head_ok(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as response:
            return 200 <= int(response.status) < 400
    except Exception:
        return False


def canonical_identity_sha(match_ids: list[int]) -> str:
    text = "\n".join(str(v) for v in sorted(match_ids))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    b06_rows: list[dict[str, Any]] = []
    b06_source_receipts: list[dict[str, Any]] = []
    for competition_id, season_id, competition, season, url in B06_SOURCES:
        rows, source_sha = load_identity_metadata(url)
        if any(r["competition_id"] != competition_id or r["season_id"] != season_id for r in rows):
            raise RuntimeError(f"competition/season identity mismatch for {url}")
        b06_rows.extend(rows)
        b06_source_receipts.append(
            {
                "competition_id": competition_id,
                "season_id": season_id,
                "competition": competition,
                "season": season,
                "url": url,
                "metadata_rows": len(rows),
                "raw_sha256": source_sha,
            }
        )

    ids = [int(r["match_id"]) for r in b06_rows]
    duplicate_ids = sorted({mid for mid in ids if ids.count(mid) > 1})
    if duplicate_ids:
        raise RuntimeError(f"duplicate B06 match ids: {duplicate_ids[:20]}")

    viewed_ids: set[int] = set()
    viewed_receipts: list[dict[str, Any]] = []
    for competition_id, season_id, url in VIEWED_SOURCES:
        rows, source_sha = load_identity_metadata(url)
        source_ids = {int(r["match_id"]) for r in rows}
        viewed_ids.update(source_ids)
        viewed_receipts.append(
            {
                "competition_id": competition_id,
                "season_id": season_id,
                "metadata_rows": len(rows),
                "identity_sha256": canonical_identity_sha(list(source_ids)),
                "raw_sha256": source_sha,
            }
        )

    overlap = sorted(set(ids) & viewed_ids)
    metadata_count = len(ids)
    coverage_count_pass = metadata_count >= MIN_TOTAL_MATCHES

    event_ok = 0
    event_missing: list[int] = []
    if coverage_count_pass:
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(head_ok, EVENT_URL.format(match_id=mid)): mid for mid in ids}
            for future in as_completed(futures):
                mid = futures[future]
                if future.result():
                    event_ok += 1
                else:
                    event_missing.append(mid)
    event_rate = event_ok / metadata_count if coverage_count_pass and metadata_count else 0.0
    event_pass = coverage_count_pass and event_rate >= MIN_EVENT_HEAD_RATE
    overlap_pass = len(overlap) == 0
    passed = coverage_count_pass and event_pass and overlap_pass

    status = "B06_COVERAGE_PASS_TARGETS_UNOPENED" if passed else "STOP_DATA_COVERAGE"
    result = {
        "schema_version": "WORLD_MODEL_V2_B06_ZERO_LABEL_PREFLIGHT_1",
        "status": status,
        "package_id": "B06",
        "target_labels_opened": 0,
        "event_payload_get_requests": 0,
        "model_fit_performed": False,
        "scientific_metrics_evaluated": False,
        "b05_opened": False,
        "reserved_laliga_confirmation_opened": False,
        "formal_weight": 0,
        "b06": {
            "metadata_matches": metadata_count,
            "minimum_total_matches": MIN_TOTAL_MATCHES,
            "identity_sha256": canonical_identity_sha(ids),
            "first_match_date": min((r["match_date"] for r in b06_rows), default=None),
            "last_match_date": max((r["match_date"] for r in b06_rows), default=None),
            "source_receipts": b06_source_receipts,
            "overlap_with_viewed_match_ids": len(overlap),
            "overlap_match_ids": overlap,
            "event_head_success": event_ok,
            "event_head_missing": len(event_missing),
            "event_head_missing_ids": sorted(event_missing),
            "event_head_success_rate": event_rate,
            "minimum_event_head_success_rate": MIN_EVENT_HEAD_RATE,
        },
        "viewed_identity_ledger": viewed_receipts,
        "checks": {
            "metadata_count_at_least_220": coverage_count_pass,
            "exact_match_id_overlap_with_viewed_is_zero": overlap_pass,
            "event_head_success_rate_at_least_0_98": event_pass,
        },
        "next_if_pass": "Data-only source amendment and implementation re-freeze before the first B06 event payload GET. Do not change model/hyperparameters/metrics/gates.",
        "next_if_fail": "STOP. Do not lower gates, open event payloads, or automatically choose another panel.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
