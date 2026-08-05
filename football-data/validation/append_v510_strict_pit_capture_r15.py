#!/usr/bin/env python3
"""Build and optionally append one V5.1 strict-PIT capture event.

The command accepts a staging payload containing fixture, freeze, synchronized market and
web-context evidence. It derives the append-only administrative fields, validates the
entire existing ledger plus the candidate through R14, and writes only with --append.
CI exercises self-tests and dry-run behavior; it never appends or requests providers.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import audit_v510_strict_pit_capture_r14 as r14

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_strict_pit_capture_r14.json"
DEFAULT_LEDGER = ROOT / "forward" / "inbox" / "v510_strict_pit_capture_r14.json"
DEFAULT_RECEIPT = ROOT / "manifests" / "v510_strict_pit_append_r15_status.json"
REQUIRED_PAYLOAD_FIELDS = (
    "fixture_identity", "freeze", "market_snapshot", "context_evidence",
    "missing_semantics", "governance",
)


class AppendError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AppendError(f"missing JSON input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AppendError(f"JSON root must be object: {path}")
    return value


def candidate_identity_material(payload: dict[str, Any]) -> str:
    fixture = payload.get("fixture_identity") or {}
    freeze = payload.get("freeze") or {}
    return "|".join([
        str(fixture.get("competition_id") or "").strip(),
        str(fixture.get("kickoff_at_utc") or "").strip(),
        str(fixture.get("home_team") or "").strip().casefold(),
        str(fixture.get("away_team") or "").strip().casefold(),
        str(freeze.get("freeze_at_utc") or "").strip(),
        r14.canonical_sha256(payload),
    ])


def normalize_payload(staging: dict[str, Any]) -> dict[str, Any]:
    payload = staging.get("payload") if isinstance(staging.get("payload"), dict) else staging
    if not isinstance(payload, dict):
        raise AppendError("staging payload must be object")
    missing = [field for field in REQUIRED_PAYLOAD_FIELDS if field not in payload]
    if missing:
        raise AppendError(f"staging payload missing fields: {missing}")
    forbidden = {
        "sequence", "event_id", "event_type", "previous_event_sha256", "event_sha256"
    }
    overlap = sorted(forbidden.intersection(payload))
    if overlap:
        raise AppendError(f"staging payload contains derived fields: {overlap}")
    return deepcopy(payload)


def validate_existing_ledger(config: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    receipt = r14.run_audit(config, ledger)
    if receipt["infrastructure_pass"] is not True:
        raise AppendError("existing strict-PIT ledger failed R14 audit")
    if receipt["counts"]["strict_pit_valid_rows"] != len(ledger.get("events") or []):
        raise AppendError("existing ledger valid-row count mismatch")
    return receipt


def build_event(
    config: dict[str, Any], ledger: dict[str, Any], staging: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing = validate_existing_ledger(config, ledger)
    events = ledger.get("events")
    if not isinstance(events, list):
        raise AppendError("ledger events must be list")
    payload = normalize_payload(staging)
    sequence = len(events) + 1
    previous = events[-1].get("event_sha256") if events else None
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, candidate_identity_material(payload)))
    event = {
        "sequence": sequence,
        "event_id": event_id,
        "event_type": config["append_only_ledger_contract"]["event_type_required_value"],
        **payload,
        "previous_event_sha256": previous,
    }
    event["event_sha256"] = r14.event_hash(event)

    seen = {
        tuple(row["fixture_freeze_key"])
        for row in existing.get("valid_rows") or []
    }
    previous_hash = str(previous) if previous else None
    candidate_row = r14.validate_event(
        event, sequence, previous_hash, config, seen
    )
    trial = deepcopy(ledger)
    trial["events"] = [*events, event]
    full_receipt = r14.run_audit(config, trial)
    if full_receipt["infrastructure_pass"] is not True:
        raise AppendError("candidate passed isolated validation but full ledger audit failed")
    if full_receipt["counts"]["strict_pit_valid_rows"] != sequence:
        raise AppendError("post-append valid-row count mismatch")
    return event, {
        "schema_version": "V5.1.0-strict-pit-append-r15-status",
        "status": "PASS_R15_CANDIDATE_READY_TO_APPEND",
        "sequence": sequence,
        "event_id": event_id,
        "event_sha256": event["event_sha256"],
        "previous_event_sha256": previous,
        "fixture_freeze_key": candidate_row["fixture_freeze_key"],
        "market_sync_seconds": candidate_row["market_sync_seconds"],
        "both_team_context": candidate_row["both_team_context"],
        "pre_append_rows": len(events),
        "post_append_rows": sequence,
        "provider_requests": 0,
        "probability_mutation": False,
        "model_fit": False,
        "formal_weight": 0,
    }


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False,
        prefix=f".{path.name}.", suffix=".tmp",
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def append_event(
    config: dict[str, Any], ledger_path: Path, staging: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    ledger = load_json(ledger_path)
    event, receipt = build_event(config, ledger, staging)
    updated = deepcopy(ledger)
    updated["events"] = [*(ledger.get("events") or []), event]
    atomic_write_json(ledger_path, updated)
    persisted = r14.run_audit(config, load_json(ledger_path))
    if persisted["infrastructure_pass"] is not True:
        raise AppendError("persisted ledger failed post-write audit")
    receipt["status"] = "PASS_R15_EVENT_APPENDED_AND_REAUDITED"
    receipt["persisted_ledger"] = str(ledger_path)
    return event, receipt


def make_staging(config: dict[str, Any], suffix: int = 1) -> dict[str, Any]:
    event = r14.make_valid_event(config)
    payload = {
        field: deepcopy(event[field])
        for field in REQUIRED_PAYLOAD_FIELDS
    }
    payload["fixture_identity"]["competition_id"] = f"SPECIMEN_{suffix}"
    payload["fixture_identity"]["home_team"] = f"Home FC {suffix}"
    payload["fixture_identity"]["away_team"] = f"Away FC {suffix}"
    for index, item in enumerate(payload["context_evidence"]):
        item["subject_team"] = "home" if index == 0 else "away"
        item["claim"] = f"specimen {suffix} context {index}"
        item["evidence_sha256"] = r14.evidence_hash(item)
    return {"payload": payload}


def self_test(config: dict[str, Any]) -> None:
    empty = {
        "schema_version": "V5.1.0-strict-pit-capture-ledger-r14",
        "classification": "APPEND_ONLY_RESEARCH_FORWARD_INPUT_LEDGER",
        "created_at_utc": "2026-08-05T05:20:00+00:00",
        "events": [],
    }
    event_one, receipt_one = build_event(config, empty, make_staging(config, 1))
    assert receipt_one["sequence"] == 1
    ledger_one = deepcopy(empty)
    ledger_one["events"].append(event_one)
    event_two, receipt_two = build_event(config, ledger_one, make_staging(config, 2))
    assert receipt_two["sequence"] == 2
    assert event_two["previous_event_sha256"] == event_one["event_sha256"]
    ledger_two = deepcopy(ledger_one)
    ledger_two["events"].append(event_two)
    assert r14.run_audit(config, ledger_two)["infrastructure_pass"] is True
    duplicate_failed = False
    try:
        build_event(config, ledger_one, make_staging(config, 1))
    except Exception:
        duplicate_failed = True
    assert duplicate_failed is True

    with tempfile.TemporaryDirectory() as directory:
        ledger_path = Path(directory) / "ledger.json"
        atomic_write_json(ledger_path, empty)
        _, persisted = append_event(config, ledger_path, make_staging(config, 3))
        assert persisted["status"] == "PASS_R15_EVENT_APPENDED_AND_REAUDITED"
        assert len(load_json(ledger_path)["events"]) == 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--staging", type=Path)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--event-out", type=Path)
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    config = load_json(args.config)
    if args.self_test:
        self_test(config)
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    if args.staging is None:
        raise AppendError("--staging is required unless --self-test is used")
    staging = load_json(args.staging)
    if args.append:
        event, receipt = append_event(config, args.ledger, staging)
    else:
        event, receipt = build_event(config, load_json(args.ledger), staging)
    receipt["append_requested"] = bool(args.append)
    receipt["dry_run"] = not args.append
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.event_out:
        args.event_out.parent.mkdir(parents=True, exist_ok=True)
        args.event_out.write_text(
            json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
