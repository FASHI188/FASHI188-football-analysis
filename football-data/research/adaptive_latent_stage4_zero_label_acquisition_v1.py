#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

RESEARCH_DIR = Path(__file__).resolve().parent
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from adaptive_latent_identity_lock_v1 import build_identity_lock
from adaptive_latent_stage4_zero_label_core_v1 import (
    COMPETITIONS, MIN_TOTAL_TARGETS, SOFASCORE_ORIGIN, HttpObservation,
    Stage4AcquisitionError, _event_identity, assert_zero_label_fixture_payload,
    extract_expected_goals_statistics, fixture_url, iso_utc, nonempty, statistics_url,
)

USER_AGENT = "Mozilla/5.0 (compatible; football3-stage4-zero-label/1.0; +https://github.com/FASHI188/FASHI188-football-analysis)"

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def fetch_json_fixed(url: str, timeout: int = 35) -> HttpObservation:
    if not url.startswith(f"{SOFASCORE_ORIGIN}/api/v1/"):
        raise Stage4AcquisitionError("provider URL is outside fixed SofaScore API origin")
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*", "Cache-Control": "no-cache"})
    with urlopen(req, timeout=timeout) as response:  # nosec B310 - fixed allowlisted HTTPS origin only
        raw = response.read()
        received_at = _utcnow()
        status = int(getattr(response, "status", 200))
        content_type = str(response.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise Stage4AcquisitionError(f"HTTP {status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise Stage4AcquisitionError("provider response is not UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise Stage4AcquisitionError("provider response must be JSON object")
    return HttpObservation(payload, url, hashlib.sha256(raw).hexdigest(), status, content_type, received_at, len(raw))

def materialize_target_inventory(fetcher: Callable[[str], HttpObservation] = fetch_json_fixed) -> tuple[dict[str, Any], str, dict[str, Any]]:
    run_id = nonempty(os.environ.get("GITHUB_RUN_ID") or "manual-local", "collector_run_id")
    run_attempt = nonempty(os.environ.get("GITHUB_RUN_ATTEMPT") or "0", "collector_run_attempt")
    targets: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for cid, spec in COMPETITIONS.items():
        url = fixture_url(int(spec["tournament_id"]), int(spec["season_id"]))
        obs = fetcher(url)
        assert_zero_label_fixture_payload(obs.payload)
        events = obs.payload.get("events")
        if not isinstance(events, list):
            raise Stage4AcquisitionError(f"{cid}: events array missing")
        count = 0
        for event in events:
            row = _event_identity(event, cid, spec, obs.received_at)
            if row is not None:
                targets.append(row)
                count += 1
        if count == 0:
            raise Stage4AcquisitionError(f"{cid}: no target inside frozen lead window")
        receipts.append({
            "competition_id": cid, "request_url": obs.request_url, "http_status": obs.http_status,
            "content_type": obs.content_type, "payload_sha256": obs.payload_sha256,
            "payload_byte_count": obs.byte_count, "collector_first_observed_at": iso_utc(obs.received_at),
            "retrieved_at": iso_utc(obs.received_at), "raw_payload_persisted": False,
            "raw_payload_consumed_by_model": False, "zero_label_forbidden_key_scan": "PASS",
            "selected_target_count": count,
        })
    if len(targets) < MIN_TOTAL_TARGETS:
        raise Stage4AcquisitionError(f"coverage below minimum: {len(targets)} < {MIN_TOTAL_TARGETS}")
    semantic_seen: set[tuple[str, str]] = set()
    provider_seen: set[int] = set()
    for row in targets:
        key = (row["competition_id"], row["fixture_id"])
        if key in semantic_seen:
            raise Stage4AcquisitionError(f"duplicate provider fixture identity: {key}")
        semantic_seen.add(key)
        eid = int(row["provider_event_id"])
        if eid in provider_seen:
            raise Stage4AcquisitionError(f"provider event id reused across target inventory: {eid}")
        provider_seen.add(eid)
    lock_rows = [{k: row[k] for k in ("competition_id", "fixture_id", "kickoff_at", "home_team_id", "away_team_id", "prediction_cutoff")} for row in targets]
    lock = build_identity_lock(lock_rows)
    inventory_rows = []
    for row in sorted(targets, key=lambda x: (x["kickoff_at"], x["competition_id"], x["fixture_id"])):
        item = dict(row)
        item["kickoff_at"] = iso_utc(row["kickoff_at"])
        item["prediction_cutoff"] = iso_utc(row["prediction_cutoff"])
        inventory_rows.append(item)
    generated = max(datetime.fromisoformat(r["collector_first_observed_at"].replace("Z", "+00:00")) for r in receipts)
    inventory = {
        "schema": "football3_adaptive_latent_stage4_zero_label_target_inventory_v1",
        "status": "PASS_ZERO_LABEL_TARGET_IDENTITY_LOCK", "provider_name": "SofaScore public API",
        "provider_authentication": "none", "collector_run_id": run_id, "collector_run_attempt": run_attempt,
        "generated_at_utc": iso_utc(generated), "season_year": "26/27", "lead_window_hours": [1, 336],
        "prediction_cutoff_policy": "kickoff_at - 15 minutes exactly", "target_row_count": len(inventory_rows),
        "required_competitions": list(COMPETITIONS), "provider_receipts": receipts, "targets": inventory_rows,
        "identity_lock_sha256": lock["identity_lock_sha256"], "ordered_identity_sha256": lock["ordered_identity_sha256"],
        "label_fields_persisted": 0, "real_target_values_read": 0, "market_values_persisted": 0,
        "raw_provider_payload_persisted": False, "formal_weight": 0.0, "research_only": True,
        "schedule_drift_policy": "any provider-event kickoff drift before label access invalidates that frozen identity; no silent substitution",
    }
    return inventory, lock["identity_csv"], lock

def _write_outputs(output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    inventory, identity_csv, lock = materialize_target_inventory()
    inventory_path = output_root / "target_inventory_v1.json"
    identity_path = output_root / "target_identity_sha256_v1.csv"
    status_path = output_root / "stage4_acquisition_status_v1.json"
    inv_bytes = (json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    id_bytes = identity_csv.encode()
    inventory_path.write_bytes(inv_bytes)
    identity_path.write_bytes(id_bytes)
    status = {
        "schema": "football3_adaptive_latent_stage4_acquisition_status_v1",
        "status": "TARGET_IDENTITY_VERIFIED_XG_SOURCE_STILL_BLOCKED",
        "target_inventory_path": str(inventory_path), "target_inventory_sha256": hashlib.sha256(inv_bytes).hexdigest(),
        "target_identity_path": str(identity_path), "target_identity_sha256": hashlib.sha256(id_bytes).hexdigest(),
        "identity_lock_sha256": lock["identity_lock_sha256"], "ordered_identity_sha256": lock["ordered_identity_sha256"],
        "target_row_count": inventory["target_row_count"],
        "completed_match_xg_source_revision": "UNKNOWN_PENDING_SCORE_FREE_COMPLETION_PROOF",
        "xg_live_provider_call_performed": False, "event_root_provider_call_performed": False,
        "historical_results_provider_call_performed": False, "real_labels_read": 0, "training": False,
        "tuning": False, "real_scoring": False, "secret_access": False, "formal_weight": 0.0, "current_change": False,
    }
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return status

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(RESEARCH_DIR / "evidence" / "adaptive_latent_stage4"))
    args = parser.parse_args(argv)
    try:
        status = _write_outputs(Path(args.output_root))
    except Exception as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
