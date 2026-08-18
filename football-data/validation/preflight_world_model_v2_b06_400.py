#!/usr/bin/env python3
"""Zero-target-label identity/coverage preflight for the true 400-match WMV2 B06.

The upstream StatsBomb match metadata payload co-locates score fields. This parser
intentionally never references them. Event objects are checked with HTTP HEAD only;
no event payload is GET during this preflight.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

USER_AGENT = "FASHI188-football-analysis/wmv2-b06-400-preflight"
SEED = "WORLD_MODEL_V2_B06_400_20260818_R1"
SELECT_N = 400
MIN_EVENT_HEAD_RATE = 0.98
RAW = "https://raw.githubusercontent.com/hudl/open-data/master/data"

B06_SOURCES = (
    (43, 269, "FIFA World Cup", "1958"),
    (43, 270, "FIFA World Cup", "1962"),
    (43, 272, "FIFA World Cup", "1970"),
    (43, 51, "FIFA World Cup", "1974"),
    (43, 54, "FIFA World Cup", "1986"),
    (43, 55, "FIFA World Cup", "1990"),
    (43, 3, "FIFA World Cup", "2018"),
    (43, 106, "FIFA World Cup", "2022"),
    (223, 282, "Copa America", "2024"),
)

# Provider match-ID overlap guard for all World Model panels/probes whose metadata
# has already been opened. Reserved La Liga confirmation is deliberately NOT fetched.
VIEWED_SOURCES = (
    (2, 27, "EPL 2015/2016 V0"),
    (9, 27, "Bundesliga 2015/2016 V1 coverage"),
    (37, 4, "WSL 2018/2019 V1"),
    (37, 42, "WSL 2019/2020 V1"),
    (37, 90, "WSL 2020/2021 V1"),
    (37, 281, "WSL 2023/2024 V1"),
    (49, 3, "NWSL 2018 V2 metadata"),
    (49, 107, "NWSL 2023 V2 metadata"),
    (7, 108, "Ligue1 2021/2022 historical coverage probe"),
    (7, 235, "Ligue1 2022/2023 historical coverage probe"),
)


def url_matches(competition_id: int, season_id: int) -> str:
    return f"{RAW}/matches/{competition_id}/{season_id}.json"


def url_event(match_id: int) -> str:
    return f"{RAW}/events/{match_id}.json"


def get_bytes(url: str, attempts: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read()
        except Exception as exc:  # pragma: no cover - network retry
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"GET failed: {url}: {last}")


def load_identity_only(competition_id: int, season_id: int) -> tuple[list[dict[str, Any]], str]:
    url = url_matches(competition_id, season_id)
    raw = get_bytes(url)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"match metadata is not a list: {url}")
    rows: list[dict[str, Any]] = []
    for item in payload:
        row = {
            "match_id": int(item["match_id"]),
            "match_date": str(item["match_date"]),
            "kick_off": str(item.get("kick_off") or ""),
            "competition_id": int(item["competition"]["competition_id"]),
            "season_id": int(item["season"]["season_id"]),
            "home_team_id": int(item["home_team"]["home_team_id"]),
            "away_team_id": int(item["away_team"]["away_team_id"]),
        }
        if row["competition_id"] != competition_id or row["season_id"] != season_id:
            raise RuntimeError(f"source identity mismatch: expected {competition_id}/{season_id}, got {row}")
        rows.append(row)
    return rows, hashlib.sha256(raw).hexdigest()


def rank_key(row: dict[str, Any]) -> str:
    identity = f"{row['competition_id']}|{row['season_id']}|{row['match_id']}"
    return hashlib.sha256(f"{SEED}|{identity}".encode("utf-8")).hexdigest()


def canonical_manifest_sha(rows: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "competition_id": int(r["competition_id"]),
            "season_id": int(r["season_id"]),
            "match_id": int(r["match_id"]),
            "match_date": str(r["match_date"]),
            "kick_off": str(r["kick_off"]),
            "home_team_id": int(r["home_team_id"]),
            "away_team_id": int(r["away_team_id"]),
            "rank_key": str(r["rank_key"]),
        }
        for r in rows
    ]
    text = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def head_ok(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as response:
            return 200 <= int(response.status) < 400
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    candidate_rows: list[dict[str, Any]] = []
    source_receipts: list[dict[str, Any]] = []
    for competition_id, season_id, competition, season in B06_SOURCES:
        rows, raw_sha = load_identity_only(competition_id, season_id)
        candidate_rows.extend(rows)
        source_receipts.append({
            "competition_id": competition_id,
            "season_id": season_id,
            "competition": competition,
            "season": season,
            "metadata_rows": len(rows),
            "raw_metadata_sha256": raw_sha,
        })

    candidate_ids = [int(r["match_id"]) for r in candidate_rows]
    duplicate_candidate_ids = sorted(mid for mid, count in Counter(candidate_ids).items() if count > 1)
    if duplicate_candidate_ids:
        raise RuntimeError(f"duplicate candidate match_ids: {duplicate_candidate_ids[:20]}")

    candidate_pool_pass = len(candidate_rows) >= SELECT_N
    selected: list[dict[str, Any]] = []
    if candidate_pool_pass:
        ranked = []
        for row in candidate_rows:
            item = dict(row)
            item["rank_key"] = rank_key(item)
            ranked.append(item)
        ranked.sort(key=lambda r: (r["rank_key"], r["competition_id"], r["season_id"], r["match_id"]))
        selected = ranked[:SELECT_N]

    viewed_ids: set[int] = set()
    viewed_receipts: list[dict[str, Any]] = []
    for competition_id, season_id, name in VIEWED_SOURCES:
        rows, raw_sha = load_identity_only(competition_id, season_id)
        ids = {int(r["match_id"]) for r in rows}
        viewed_ids.update(ids)
        viewed_receipts.append({
            "competition_id": competition_id,
            "season_id": season_id,
            "name": name,
            "metadata_rows": len(rows),
            "raw_metadata_sha256": raw_sha,
        })

    selected_ids = [int(r["match_id"]) for r in selected]
    overlap = sorted(set(selected_ids) & viewed_ids)
    overlap_pass = candidate_pool_pass and len(overlap) == 0

    event_ok = 0
    event_missing: list[int] = []
    if candidate_pool_pass and len(selected) == SELECT_N and overlap_pass:
        with ThreadPoolExecutor(max_workers=24) as pool:
            futures = {pool.submit(head_ok, url_event(mid)): mid for mid in selected_ids}
            for future in as_completed(futures):
                mid = futures[future]
                if future.result():
                    event_ok += 1
                else:
                    event_missing.append(mid)
    event_rate = event_ok / SELECT_N if len(selected) == SELECT_N else 0.0
    event_pass = len(selected) == SELECT_N and event_rate >= MIN_EVENT_HEAD_RATE

    manifest_sha = canonical_manifest_sha(selected) if len(selected) == SELECT_N else None
    checks = {
        "candidate_pool_at_least_400": candidate_pool_pass,
        "selected_exactly_400": len(selected) == SELECT_N,
        "overlap_with_world_model_viewed_match_ids_is_zero": overlap_pass,
        "event_head_success_rate_at_least_0_98": event_pass,
    }
    passed = all(checks.values())
    status = "B06_400_IDENTITY_FROZEN_TARGETS_UNOPENED" if passed else "STOP_DATA_COVERAGE"

    manifest = {
        "schema_version": "WORLD_MODEL_V2_B06_400_MANIFEST_1",
        "package_id": "B06",
        "package_size": len(selected),
        "seed": SEED,
        "selection": "ascending sha256(seed|competition_id|season_id|match_id), first 400",
        "manifest_sha256": manifest_sha,
        "rows": selected,
    }
    summary = {
        "schema_version": "WORLD_MODEL_V2_B06_400_PREFLIGHT_1",
        "status": status,
        "package_id": "B06",
        "candidate_pool_rows": len(candidate_rows),
        "selected_rows": len(selected),
        "source_receipts": source_receipts,
        "manifest_sha256": manifest_sha,
        "selected_first_match_date": min((r["match_date"] for r in selected), default=None),
        "selected_last_match_date": max((r["match_date"] for r in selected), default=None),
        "selected_competition_counts": dict(sorted(Counter(str(r["competition_id"]) for r in selected).items())),
        "selected_season_counts": dict(sorted(Counter(f"{r['competition_id']}/{r['season_id']}" for r in selected).items())),
        "overlap_with_world_model_viewed_match_ids": len(overlap),
        "overlap_match_ids": overlap,
        "event_head_success": event_ok,
        "event_head_missing": len(event_missing),
        "event_head_missing_ids": sorted(event_missing),
        "event_head_success_rate": event_rate,
        "checks": checks,
        "target_labels_opened": 0,
        "score_fields_referenced": [],
        "event_payload_get_requests": 0,
        "model_fit_performed": False,
        "scientific_metrics_evaluated": False,
        "b05_opened": False,
        "laliga_reserved_confirmation_opened": False,
        "formal_weight": 0,
        "viewed_identity_receipts": viewed_receipts,
        "next_if_pass": "Persist/freeze this exact manifest and refreeze only the V2 data-source adapter before one B06 scientific run.",
        "next_if_fail": "STOP. Do not lower 400, change the source pool, or GET event payloads automatically.",
    }

    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
