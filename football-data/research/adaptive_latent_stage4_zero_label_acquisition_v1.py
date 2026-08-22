#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

RESEARCH_DIR = Path(__file__).resolve().parent
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from adaptive_latent_identity_lock_v1 import build_identity_lock
from adaptive_latent_stage4_zero_label_core_v1 import (
    COMPETITIONS,
    MIN_TOTAL_TARGETS,
    FixtureProjectionObservation,
    Stage4AcquisitionError,
    _event_identity,
    iso_utc,
    nonempty,
    sha256_hex,
    validate_fixture_projection,
)

LIVE_PROVIDER_DISABLED_REASON = "STOP_SOURCE_SCHEMA_WHITELIST"

def materialize_target_inventory(
    fetcher: Callable[[str, dict[str, int | str]], FixtureProjectionObservation]
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    if fetcher is None:
        raise Stage4AcquisitionError("explicit projection fetcher is required")
    run_id = nonempty(os.environ.get("GITHUB_RUN_ID") or "manual-local", "collector_run_id")
    run_attempt = nonempty(os.environ.get("GITHUB_RUN_ATTEMPT") or "0", "collector_run_attempt")
    targets: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for cid, spec in COMPETITIONS.items():
        obs = fetcher(cid, spec)
        if not isinstance(obs, FixtureProjectionObservation):
            raise Stage4AcquisitionError("fetcher must return FixtureProjectionObservation")
        validate_fixture_projection(obs.payload)
        count = 0
        for event in obs.payload["events"]:
            row = _event_identity(event, cid, spec, obs.received_at)
            if row is not None:
                targets.append(row)
                count += 1
        if count == 0:
            raise Stage4AcquisitionError(f"{cid}: no target inside frozen lead window")
        receipts.append({
            "competition_id": cid,
            "source_identity": nonempty(obs.source_identity, "source_identity"),
            "source_url": nonempty(obs.source_url, "source_url"),
            "payload_sha256": sha256_hex(obs.payload_sha256, "payload_sha256"),
            "collector_first_observed_at": iso_utc(obs.received_at),
            "retrieved_at": iso_utc(obs.received_at),
            "schema_contract": "EXACT_WHITELIST_V2",
            "selected_target_count": count,
            "raw_provider_payload_persisted": False,
            "real_labels_read": 0,
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
    lock_rows = [{
        "competition_id": row["competition_id"],
        "fixture_id": row["fixture_id"],
        "kickoff_at": row["kickoff_at"],
        "home_team_id": row["home_team_id"],
        "away_team_id": row["away_team_id"],
        "prediction_cutoff": row["prediction_cutoff"],
    } for row in targets]
    lock = build_identity_lock(lock_rows)
    inventory_rows = []
    for row in sorted(targets, key=lambda x: (x["kickoff_at"], x["competition_id"], x["fixture_id"])):
        item = dict(row)
        item["kickoff_at"] = iso_utc(row["kickoff_at"])
        item["prediction_cutoff"] = iso_utc(row["prediction_cutoff"])
        inventory_rows.append(item)
    generated = max(datetime.fromisoformat(r["collector_first_observed_at"].replace("Z", "+00:00")) for r in receipts)
    inventory = {
        "schema": "football3_adaptive_latent_stage4_zero_label_target_inventory_v2",
        "status": "PASS_ZERO_LABEL_TARGET_IDENTITY_LOCK_EXACT_WHITELIST",
        "collector_run_id": run_id,
        "collector_run_attempt": run_attempt,
        "generated_at_utc": iso_utc(generated),
        "season_year": "26/27",
        "lead_window_hours": [1, 336],
        "prediction_cutoff_policy": "kickoff_at - 15 minutes exactly",
        "target_row_count": len(inventory_rows),
        "required_competitions": list(COMPETITIONS),
        "provider_receipts": receipts,
        "targets": inventory_rows,
        "identity_lock_sha256": lock["identity_lock_sha256"],
        "ordered_identity_sha256": lock["ordered_identity_sha256"],
        "label_fields_persisted": 0,
        "real_target_values_read": 0,
        "market_values_persisted": 0,
        "raw_provider_payload_persisted": False,
        "formal_weight": 0.0,
        "research_only": True,
    }
    return inventory, lock["identity_csv"], lock

def write_stop_status(output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    status = {
        "schema": "football3_adaptive_latent_stage4_acquisition_status_v2",
        "status": LIVE_PROVIDER_DISABLED_REASON,
        "live_provider_call_performed": False,
        "provider_schema_whitelist_frozen": False,
        "target_identity_materialized": False,
        "completed_match_xg_source_revision": "UNKNOWN_PENDING_SCORE_FREE_COMPLETION_PROOF",
        "real_labels_read": 0,
        "training": False,
        "tuning": False,
        "real_scoring": False,
        "secret_access": False,
        "formal_weight": 0.0,
        "current_change": False,
    }
    data = (json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    (output_root / "stage4_acquisition_status_v2.json").write_bytes(data)
    status["status_file_sha256"] = hashlib.sha256(data).hexdigest()
    return status

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(RESEARCH_DIR / "evidence" / "adaptive_latent_stage4"))
    args = parser.parse_args(argv)
    status = write_stop_status(Path(args.output_root))
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
