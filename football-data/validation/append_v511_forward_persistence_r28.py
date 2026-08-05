#!/usr/bin/env python3
"""R28 fail-closed persistence entry for future football PIT events and results.

This is the only append-capable entry after R27. Candidate events must pass the
R27 validator before atomic persistence. Results must resolve to an existing event
by prediction_event_hash and pass exact identity and settlement checks.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v511_forward_persistence_entry_r28.json"
DEFAULT_EVENT_LEDGER = ROOT / "forward" / "inbox" / "v511_forward_freeze_events_r28.json"
DEFAULT_RESULT_LEDGER = ROOT / "forward" / "inbox" / "v511_forward_results_r28.json"
DEFAULT_RECEIPT = ROOT / "manifests" / "v511_forward_persistence_entry_r28_status.json"
R27_VALIDATOR = ROOT / "validation" / "validate_v511_forward_freeze_capture_contract_r27.py"
R27_CONFIG = ROOT / "config" / "v511_forward_freeze_capture_contract_r27.json"


class PersistenceError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PersistenceError(f"missing JSON file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PersistenceError(f"JSON root must be object: {path}")
    return value


def load_r27():
    spec = importlib.util.spec_from_file_location("r27_validator", R27_VALIDATOR)
    if spec is None or spec.loader is None:
        raise PersistenceError("unable to load R27 validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def fixture_freeze_key(event: dict[str, Any]) -> tuple[str, str, str, str, str]:
    payload = event.get("payload") or {}
    fixture = payload.get("fixture_identity") or {}
    return (
        str(fixture.get("competition_id") or "").strip(),
        str(fixture.get("kickoff_at_utc") or "").strip(),
        str(fixture.get("home_team") or "").strip().casefold(),
        str(fixture.get("away_team") or "").strip().casefold(),
        str(payload.get("decision_freeze_at_utc") or "").strip(),
    )


def validate_event_ledger(
    ledger: dict[str, Any], r27, r27_config: dict[str, Any]
) -> dict[str, Any]:
    if ledger.get("schema_version") != "v511_forward_freeze_event_ledger_r28.1":
        raise PersistenceError("event ledger schema invalid")
    events = ledger.get("events")
    if not isinstance(events, list):
        raise PersistenceError("event ledger events must be a list")

    expected_previous = "GENESIS"
    hashes: set[str] = set()
    fixture_keys: set[tuple[str, str, str, str, str]] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise PersistenceError(f"event ledger row {index + 1} is not an object")
        errors = r27.validate_event(event, r27_config)
        if errors:
            raise PersistenceError(f"event ledger row {index + 1} failed R27: {errors}")
        event_hash = str(event.get("event_hash") or "")
        if event.get("previous_event_hash") != expected_previous:
            raise PersistenceError(f"event ledger chain break at row {index + 1}")
        if event_hash in hashes:
            raise PersistenceError(f"duplicate event_hash at row {index + 1}")
        key = fixture_freeze_key(event)
        if key in fixture_keys:
            raise PersistenceError(f"duplicate fixture+freeze at row {index + 1}")
        hashes.add(event_hash)
        fixture_keys.add(key)
        expected_previous = event_hash
    return {
        "rows": len(events),
        "last_event_hash": expected_previous,
        "event_hashes": hashes,
        "fixture_freeze_keys": fixture_keys,
    }


def validate_result_ledger(
    ledger: dict[str, Any], event_ledger: dict[str, Any], r27
) -> dict[str, Any]:
    if ledger.get("schema_version") != "v511_forward_result_ledger_r28.1":
        raise PersistenceError("result ledger schema invalid")
    results = ledger.get("results")
    if not isinstance(results, list):
        raise PersistenceError("result ledger results must be a list")
    events = event_ledger.get("events") or []
    by_hash = {str(event.get("event_hash") or ""): event for event in events}
    seen: set[str] = set()
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise PersistenceError(f"result ledger row {index + 1} is not an object")
        event_hash = str(result.get("prediction_event_hash") or "")
        if event_hash in seen:
            raise PersistenceError(f"duplicate result link at row {index + 1}")
        event = by_hash.get(event_hash)
        if event is None:
            raise PersistenceError(f"result row {index + 1} references unknown event")
        errors = r27.validate_result_link(event, result)
        if errors:
            raise PersistenceError(f"result row {index + 1} failed R27: {errors}")
        seen.add(event_hash)
    return {"rows": len(results), "linked_event_hashes": seen}


def candidate_event_from_staging(
    staging: dict[str, Any], expected_previous: str, r27, r27_config: dict[str, Any]
) -> dict[str, Any]:
    raw = staging.get("event") if isinstance(staging.get("event"), dict) else staging
    if not isinstance(raw, dict):
        raise PersistenceError("event staging must contain an event object")
    candidate = copy.deepcopy(raw)

    supplied_previous = candidate.get("previous_event_hash")
    if supplied_previous in (None, "", "AUTO"):
        candidate["previous_event_hash"] = expected_previous
    elif supplied_previous != expected_previous:
        raise PersistenceError("candidate previous_event_hash does not match ledger tip")

    supplied_hash = candidate.get("event_hash")
    if supplied_hash in (None, "", "AUTO"):
        candidate.pop("event_hash", None)
        candidate = r27.seal_event(candidate)
    else:
        errors = r27.validate_event(candidate, r27_config)
        if "EVENT_HASH_MISMATCH" in errors:
            raise PersistenceError("supplied candidate event_hash is invalid")

    errors = r27.validate_event(candidate, r27_config)
    if errors:
        raise PersistenceError(f"candidate event failed R27: {errors}")
    return candidate


def build_event_append(
    config: dict[str, Any], event_ledger: dict[str, Any], staging: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    r27 = load_r27()
    r27_config = load_json(R27_CONFIG)
    existing = validate_event_ledger(event_ledger, r27, r27_config)
    candidate = candidate_event_from_staging(
        staging, str(existing["last_event_hash"]), r27, r27_config
    )
    event_hash = str(candidate["event_hash"])
    if event_hash in existing["event_hashes"]:
        raise PersistenceError("candidate event_hash already exists")
    if fixture_freeze_key(candidate) in existing["fixture_freeze_keys"]:
        raise PersistenceError("candidate fixture+freeze already exists")

    trial = copy.deepcopy(event_ledger)
    trial["events"] = [*(event_ledger.get("events") or []), candidate]
    after = validate_event_ledger(trial, r27, r27_config)
    receipt = {
        "schema_version": "v511_forward_persistence_entry_r28_status.1",
        "status": "PASS_R28_EVENT_READY_TO_APPEND",
        "operation": "event",
        "pre_rows": existing["rows"],
        "post_rows": after["rows"],
        "event_hash": event_hash,
        "previous_event_hash": candidate["previous_event_hash"],
        "fixture_freeze_key_sha256": sha256_json(fixture_freeze_key(candidate)),
        "r27_validation_passed": True,
        "full_ledger_pre_validation_passed": True,
        "full_ledger_post_validation_passed": True,
        "formal_weight": 0,
        "provider_requests": 0,
        "model_training_performed": False,
        "probabilities_generated": False,
    }
    return trial, receipt


def build_result_append(
    config: dict[str, Any],
    event_ledger: dict[str, Any],
    result_ledger: dict[str, Any],
    staging: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    r27 = load_r27()
    r27_config = load_json(R27_CONFIG)
    event_state = validate_event_ledger(event_ledger, r27, r27_config)
    result_state = validate_result_ledger(result_ledger, event_ledger, r27)
    raw = staging.get("result") if isinstance(staging.get("result"), dict) else staging
    if not isinstance(raw, dict):
        raise PersistenceError("result staging must contain a result object")
    candidate = copy.deepcopy(raw)
    event_hash = str(candidate.get("prediction_event_hash") or "")
    if event_hash in result_state["linked_event_hashes"]:
        raise PersistenceError("result for prediction_event_hash already exists")
    by_hash = {
        str(event.get("event_hash") or ""): event
        for event in (event_ledger.get("events") or [])
    }
    event = by_hash.get(event_hash)
    if event is None:
        raise PersistenceError("result references unknown prediction_event_hash")
    errors = r27.validate_result_link(event, candidate)
    if errors:
        raise PersistenceError(f"candidate result failed R27 link: {errors}")

    trial = copy.deepcopy(result_ledger)
    trial["results"] = [*(result_ledger.get("results") or []), candidate]
    after = validate_result_ledger(trial, event_ledger, r27)
    receipt = {
        "schema_version": "v511_forward_persistence_entry_r28_status.1",
        "status": "PASS_R28_RESULT_READY_TO_APPEND",
        "operation": "result",
        "event_rows": event_state["rows"],
        "pre_rows": result_state["rows"],
        "post_rows": after["rows"],
        "prediction_event_hash": event_hash,
        "result_receipt_sha256": sha256_json(candidate),
        "r27_result_link_passed": True,
        "full_ledger_pre_validation_passed": True,
        "full_ledger_post_validation_passed": True,
        "formal_weight": 0,
        "provider_requests": 0,
        "model_training_performed": False,
        "probabilities_generated": False,
    }
    return trial, receipt


def persist_event(
    config: dict[str, Any], ledger_path: Path, staging: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated, receipt = build_event_append(config, load_json(ledger_path), staging)
    atomic_write_json(ledger_path, updated)
    r27 = load_r27()
    validate_event_ledger(load_json(ledger_path), r27, load_json(R27_CONFIG))
    receipt["status"] = "PASS_R28_EVENT_APPENDED_AND_REAUDITED"
    receipt["persisted_ledger"] = str(ledger_path)
    return updated["events"][-1], receipt


def persist_result(
    config: dict[str, Any],
    event_ledger_path: Path,
    result_ledger_path: Path,
    staging: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    event_ledger = load_json(event_ledger_path)
    updated, receipt = build_result_append(
        config, event_ledger, load_json(result_ledger_path), staging
    )
    atomic_write_json(result_ledger_path, updated)
    validate_result_ledger(load_json(result_ledger_path), event_ledger, load_r27())
    receipt["status"] = "PASS_R28_RESULT_APPENDED_AND_REAUDITED"
    receipt["persisted_ledger"] = str(result_ledger_path)
    return updated["results"][-1], receipt


def self_test(config: dict[str, Any]) -> dict[str, Any]:
    r27 = load_r27()
    valid_event, valid_result = r27.valid_sample()
    event_ledger = {
        "schema_version": "v511_forward_freeze_event_ledger_r28.1",
        "classification": "APPEND_ONLY_RESEARCH_FORWARD_EVENT_LEDGER",
        "events": [],
    }
    result_ledger = {
        "schema_version": "v511_forward_result_ledger_r28.1",
        "classification": "APPEND_ONLY_RESEARCH_FORWARD_RESULT_LEDGER",
        "results": [],
    }

    trial_events, event_receipt = build_event_append(
        config, event_ledger, {"event": valid_event}
    )
    assert event_receipt["status"] == "PASS_R28_EVENT_READY_TO_APPEND"

    duplicate_rejected = False
    try:
        build_event_append(config, trial_events, {"event": valid_event})
    except PersistenceError:
        duplicate_rejected = True
    assert duplicate_rejected

    missing_total = copy.deepcopy(valid_event)
    missing_total["payload"]["market"].pop("over_under")
    missing_total = r27.seal_event(missing_total)
    missing_total_rejected = False
    try:
        build_event_append(config, event_ledger, {"event": missing_total})
    except PersistenceError:
        missing_total_rejected = True
    assert missing_total_rejected

    late_market = copy.deepcopy(valid_event)
    late_market["payload"]["market"]["observed_at_utc"] = "2026-08-05T10:01:00+00:00"
    late_market = r27.seal_event(late_market)
    late_market_rejected = False
    try:
        build_event_append(config, event_ledger, {"event": late_market})
    except PersistenceError:
        late_market_rejected = True
    assert late_market_rejected

    bad_hash = copy.deepcopy(valid_event)
    bad_hash["payload"]["fixture_identity"]["home_team"] = "Tampered"
    bad_hash_rejected = False
    try:
        build_event_append(config, event_ledger, {"event": bad_hash})
    except PersistenceError:
        bad_hash_rejected = True
    assert bad_hash_rejected

    trial_results, result_receipt = build_result_append(
        config, trial_events, result_ledger, {"result": valid_result}
    )
    assert result_receipt["status"] == "PASS_R28_RESULT_READY_TO_APPEND"

    bad_result = copy.deepcopy(valid_result)
    bad_result["prediction_event_hash"] = "0" * 64
    bad_result_rejected = False
    try:
        build_result_append(config, trial_events, result_ledger, {"result": bad_result})
    except PersistenceError:
        bad_result_rejected = True
    assert bad_result_rejected

    with tempfile.TemporaryDirectory() as directory:
        event_path = Path(directory) / "events.json"
        result_path = Path(directory) / "results.json"
        atomic_write_json(event_path, event_ledger)
        atomic_write_json(result_path, result_ledger)
        persisted_event, event_write = persist_event(config, event_path, {"event": valid_event})
        assert event_write["status"] == "PASS_R28_EVENT_APPENDED_AND_REAUDITED"
        linked_result = copy.deepcopy(valid_result)
        linked_result["prediction_event_hash"] = persisted_event["event_hash"]
        _, result_write = persist_result(
            config, event_path, result_path, {"result": linked_result}
        )
        assert result_write["status"] == "PASS_R28_RESULT_APPENDED_AND_REAUDITED"

    return {
        "self_test": "PASS",
        "valid_event_accepted": True,
        "valid_result_accepted": True,
        "duplicate_event_rejected": duplicate_rejected,
        "missing_over_under_rejected": missing_total_rejected,
        "post_freeze_market_rejected": late_market_rejected,
        "invalid_supplied_hash_rejected": bad_hash_rejected,
        "invalid_result_link_rejected": bad_result_rejected,
        "atomic_event_write_reaudited": True,
        "atomic_result_write_reaudited": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--event-ledger", type=Path, default=DEFAULT_EVENT_LEDGER)
    parser.add_argument("--result-ledger", type=Path, default=DEFAULT_RESULT_LEDGER)
    parser.add_argument("--event-staging", type=Path)
    parser.add_argument("--result-staging", type=Path)
    parser.add_argument("--append-event", action="store_true")
    parser.add_argument("--append-result", action="store_true")
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--candidate-out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    config = load_json(args.config)
    if args.self_test:
        print(json.dumps(self_test(config), ensure_ascii=False, indent=2))
        return

    modes = int(args.event_staging is not None) + int(args.result_staging is not None)
    if modes != 1:
        raise PersistenceError("provide exactly one staging input")
    if args.append_event and args.append_result:
        raise PersistenceError("event and result append flags are mutually exclusive")

    if args.event_staging is not None:
        staging = load_json(args.event_staging)
        if args.append_result:
            raise PersistenceError("--append-result cannot be used with event staging")
        if args.append_event:
            candidate, receipt = persist_event(config, args.event_ledger, staging)
        else:
            updated, receipt = build_event_append(
                config, load_json(args.event_ledger), staging
            )
            candidate = updated["events"][-1]
    else:
        staging = load_json(args.result_staging)
        if args.append_event:
            raise PersistenceError("--append-event cannot be used with result staging")
        if args.append_result:
            candidate, receipt = persist_result(
                config, args.event_ledger, args.result_ledger, staging
            )
        else:
            updated, receipt = build_result_append(
                config,
                load_json(args.event_ledger),
                load_json(args.result_ledger),
                staging,
            )
            candidate = updated["results"][-1]

    receipt["append_requested"] = bool(args.append_event or args.append_result)
    receipt["dry_run"] = not receipt["append_requested"]
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if args.candidate_out:
        args.candidate_out.parent.mkdir(parents=True, exist_ok=True)
        args.candidate_out.write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
