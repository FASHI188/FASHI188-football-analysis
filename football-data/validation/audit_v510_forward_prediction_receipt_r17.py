#!/usr/bin/env python3
"""Audit and optionally append V5.1 R17 immutable pre-match receipts.

R17 prevents capture-event inflation by counting a fixture once across all snapshots.
A T-24h capture is a change baseline only; the deterministic T-45m-window capture is the
sole primary evaluation freeze. While current-match probabilities remain prohibited,
the sealed receipt must contain the fixed gate-blocked outputs and no probability.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import audit_v510_strict_pit_capture_r14 as r14
import append_v510_strict_pit_capture_r15 as r15

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_forward_prediction_receipt_r17.json"
DEFAULT_CAPTURE_CONFIG = ROOT / "config" / "v510_strict_pit_capture_r14.json"
DEFAULT_CAPTURE_LEDGER = ROOT / "forward" / "inbox" / "v510_strict_pit_capture_r14.json"
DEFAULT_PREDICTION_LEDGER = ROOT / "forward" / "inbox" / "v510_forward_prediction_receipt_r17.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_forward_prediction_receipt_r17_status.json"


class PredictionReceiptError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PredictionReceiptError(f"missing JSON input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PredictionReceiptError(f"JSON root must be object: {path}")
    return value


def parse_ts(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise PredictionReceiptError(f"missing {field}")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PredictionReceiptError(f"invalid {field}: {text}") from exc
    if parsed.tzinfo is None:
        raise PredictionReceiptError(f"naive {field}: {text}")
    return parsed.astimezone(timezone.utc)


def valid_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def prediction_hash(event: dict[str, Any]) -> str:
    return r14.canonical_sha256({
        key: value for key, value in event.items() if key != "prediction_sha256"
    })


def fixture_key(fixture: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(fixture.get("competition_id") or "").strip(),
        parse_ts(fixture.get("kickoff_at_utc"), "fixture.kickoff_at_utc").isoformat(),
        str(fixture.get("home_team") or "").strip().casefold(),
        str(fixture.get("away_team") or "").strip().casefold(),
    )


def fixture_key_list(fixture: dict[str, Any]) -> list[str]:
    return list(fixture_key(fixture))


def lead_minutes(event: dict[str, Any]) -> float:
    kickoff = parse_ts(event["fixture_identity"]["kickoff_at_utc"], "kickoff_at_utc")
    freeze = parse_ts(event["freeze"]["freeze_at_utc"], "freeze_at_utc")
    return (kickoff - freeze).total_seconds() / 60.0


def in_window(value: float, block: dict[str, Any]) -> bool:
    return (
        float(block["minimum_lead_minutes_inclusive"])
        <= value
        <= float(block["maximum_lead_minutes_inclusive"])
    )


def select_role_event(events: list[dict[str, Any]], block: dict[str, Any]) -> dict[str, Any] | None:
    target = float(block["target_lead_minutes"])
    eligible = [event for event in events if in_window(lead_minutes(event), block)]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda event: (
            abs(lead_minutes(event) - target),
            -parse_ts(event["freeze"]["freeze_at_utc"], "freeze_at_utc").timestamp(),
            str(event["event_sha256"]),
        ),
    )


def capture_index(
    capture_config: dict[str, Any], capture_ledger: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, str, str], list[dict[str, Any]]], dict[str, Any]]:
    receipt = r14.run_audit(capture_config, capture_ledger)
    if receipt["infrastructure_pass"] is not True:
        raise PredictionReceiptError("R14 capture ledger failed audit")
    events = capture_ledger.get("events")
    if not isinstance(events, list):
        raise PredictionReceiptError("capture ledger events must be list")
    by_hash: dict[str, dict[str, Any]] = {}
    by_fixture: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for event in events:
        event_sha = str(event.get("event_sha256") or "")
        if not valid_sha256(event_sha):
            raise PredictionReceiptError("capture event hash malformed")
        if event_sha in by_hash:
            raise PredictionReceiptError("duplicate capture event hash")
        key = fixture_key(event["fixture_identity"])
        by_hash[event_sha] = event
        by_fixture.setdefault(key, []).append(event)
    return by_hash, by_fixture, receipt


def role_index(
    by_fixture: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    config: dict[str, Any],
) -> dict[tuple[str, str, str, str], dict[str, dict[str, Any] | None]]:
    roles: dict[tuple[str, str, str, str], dict[str, dict[str, Any] | None]] = {}
    baseline_block = config["freeze_role_contract"]["baseline_t24"]
    primary_block = config["freeze_role_contract"]["primary_t45"]
    for key, events in by_fixture.items():
        roles[key] = {
            "baseline_t24": select_role_event(events, baseline_block),
            "primary_t45": select_role_event(events, primary_block),
        }
    return roles


def require_event_fields(event: dict[str, Any], fields: list[str]) -> None:
    missing = [field for field in fields if field not in event]
    if missing:
        raise PredictionReceiptError(f"prediction event missing fields: {missing}")


def validate_prediction_event(
    event: dict[str, Any],
    expected_sequence: int,
    previous_hash: str | None,
    by_hash: dict[str, dict[str, Any]],
    roles: dict[tuple[str, str, str, str], dict[str, dict[str, Any] | None]],
    config: dict[str, Any],
    seen_fixtures: set[tuple[str, str, str, str]],
) -> dict[str, Any]:
    contract = config["prediction_receipt_contract"]
    require_event_fields(event, list(contract["required_fields"]))
    if int(event["sequence"]) != expected_sequence:
        raise PredictionReceiptError("prediction sequence mismatch")
    try:
        uuid.UUID(str(event["prediction_id"]))
    except ValueError as exc:
        raise PredictionReceiptError("prediction_id is not UUID") from exc
    if event["event_type"] != contract["event_type_required_value"]:
        raise PredictionReceiptError("prediction event_type mismatch")
    supplied_previous = event.get("previous_prediction_sha256")
    if expected_sequence == 1:
        if supplied_previous not in (None, ""):
            raise PredictionReceiptError("first prediction previous hash must be null")
    elif supplied_previous != previous_hash:
        raise PredictionReceiptError("prediction hash chain mismatch")
    if not valid_sha256(event["prediction_sha256"]):
        raise PredictionReceiptError("prediction_sha256 malformed")
    calculated = prediction_hash(event)
    if calculated != event["prediction_sha256"]:
        raise PredictionReceiptError("prediction_sha256 mismatch")

    fixture = event["fixture_identity"]
    if not isinstance(fixture, dict):
        raise PredictionReceiptError("fixture_identity must be object")
    key = fixture_key(fixture)
    if list(key) != list(event["unique_fixture_key"]):
        raise PredictionReceiptError("unique_fixture_key mismatch")
    if key in seen_fixtures:
        raise PredictionReceiptError("duplicate prediction for unique fixture")
    seen_fixtures.add(key)

    primary_hash = str(event["primary_capture_event_sha256"])
    primary = by_hash.get(primary_hash)
    if primary is None:
        raise PredictionReceiptError("primary capture hash not found")
    if fixture_key(primary["fixture_identity"]) != key:
        raise PredictionReceiptError("primary capture fixture mismatch")
    selected_primary = roles.get(key, {}).get("primary_t45")
    if selected_primary is None:
        raise PredictionReceiptError("fixture lacks T-45m primary capture")
    if selected_primary["event_sha256"] != primary_hash:
        raise PredictionReceiptError("primary capture is not deterministic selected T-45m event")

    baseline_hash = event.get("baseline_capture_event_sha256")
    selected_baseline = roles.get(key, {}).get("baseline_t24")
    expected_baseline = selected_baseline["event_sha256"] if selected_baseline else None
    if baseline_hash != expected_baseline:
        raise PredictionReceiptError("baseline capture hash mismatch")

    primary_freeze = parse_ts(primary["freeze"]["freeze_at_utc"], "primary.freeze_at_utc")
    sealed = parse_ts(event["sealed_at_utc"], "sealed_at_utc")
    kickoff = parse_ts(fixture["kickoff_at_utc"], "kickoff_at_utc")
    stated_freeze = parse_ts(event["primary_freeze_at_utc"], "primary_freeze_at_utc")
    if stated_freeze != primary_freeze:
        raise PredictionReceiptError("primary_freeze_at_utc mismatch")
    if not (primary_freeze <= sealed < kickoff):
        raise PredictionReceiptError("prediction seal chronology failure")
    if (sealed - primary_freeze).total_seconds() > float(contract["maximum_seal_delay_seconds_after_primary_freeze"]):
        raise PredictionReceiptError("prediction sealed too long after primary freeze")

    frozen = config["frozen_model_identity"]
    for field in ("rules_version", "model_contract_id", "model_contract_sha256"):
        if event[field] != frozen[field]:
            raise PredictionReceiptError(f"frozen model identity mismatch: {field}")
    if not valid_sha256(event["model_contract_sha256"]):
        raise PredictionReceiptError("model_contract_sha256 malformed")

    output_gate = config["current_output_gate"]
    if event["output_status"] not in set(output_gate["allowed_output_statuses"]):
        raise PredictionReceiptError("output_status not allowed")
    if event["outputs"] != output_gate["required_blocked_outputs"]:
        raise PredictionReceiptError("blocked outputs do not exactly match contract")
    governance = event["governance"]
    if not isinstance(governance, dict):
        raise PredictionReceiptError("governance must be object")
    forbidden_true = (
        "current_match_probability_generated", "probability_mutated", "model_fitted",
        "result_seen_before_seal", "exact_score_generated", "ev_generated",
        "provider_request_in_ci",
    )
    if any(governance.get(field) is True for field in forbidden_true):
        raise PredictionReceiptError("prediction governance forbidden action marked true")
    return {
        "prediction_sha256": calculated,
        "unique_fixture_key": list(key),
        "primary_capture_event_sha256": primary_hash,
        "baseline_capture_event_sha256": baseline_hash,
        "primary_lead_minutes": lead_minutes(primary),
        "sealed_at_utc": sealed.isoformat(),
        "output_status": event["output_status"],
    }


def run_audit(
    config: dict[str, Any], capture_config: dict[str, Any],
    capture_ledger: dict[str, Any], prediction_ledger: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    valid_predictions: list[dict[str, Any]] = []
    try:
        by_hash, by_fixture, capture_receipt = capture_index(capture_config, capture_ledger)
        roles = role_index(by_fixture, config)
    except Exception as exc:
        by_hash, by_fixture, roles = {}, {}, {}
        capture_receipt = {"infrastructure_pass": False, "failures": [str(exc)]}
        failures.append(f"capture_audit: {exc}")

    events = prediction_ledger.get("events")
    if not isinstance(events, list):
        failures.append("prediction ledger events must be list")
        events = []
    previous: str | None = None
    seen: set[tuple[str, str, str, str]] = set()
    for index, event in enumerate(events, start=1):
        try:
            row = validate_prediction_event(
                event, index, previous, by_hash, roles, config, seen
            )
            valid_predictions.append(row)
            previous = str(event["prediction_sha256"])
        except Exception as exc:
            failures.append(f"prediction[{index}]: {exc}")

    unique_fixture_count = len(by_fixture)
    baseline_count = sum(
        values.get("baseline_t24") is not None for values in roles.values()
    )
    primary_count = sum(
        values.get("primary_t45") is not None for values in roles.values()
    )
    valid_prediction_count = len(valid_predictions)
    target = int(config["cohort_readiness"]["first_batch_target_unique_fixtures"])
    counts = {
        "capture_events": len(capture_ledger.get("events") or []),
        "unique_captured_fixtures": unique_fixture_count,
        "unique_fixtures_with_t24_baseline": baseline_count,
        "unique_fixtures_with_t45_primary": primary_count,
        "prediction_events": len(events),
        "valid_unique_fixture_predictions": valid_prediction_count,
        "capture_events_minus_unique_fixtures": max(
            0, len(capture_ledger.get("events") or []) - unique_fixture_count
        ),
        "first_batch_target_unique_fixtures": target,
        "first_batch_remaining_unique_predictions": max(0, target - valid_prediction_count),
    }
    infrastructure_pass = not failures and capture_receipt.get("infrastructure_pass") is True
    first_batch_complete = infrastructure_pass and valid_prediction_count >= target
    if first_batch_complete:
        status = "PASS_R17_FIRST_FORWARD_BATCH_CAPTURED"
    elif infrastructure_pass and valid_prediction_count == 0:
        status = "PASS_R17_INTERFACE_READY_NO_REAL_PREDICTION_RECEIPTS"
    elif infrastructure_pass:
        status = "PASS_R17_FORWARD_BATCH_IN_PROGRESS"
    else:
        status = "FAIL_R17_FORWARD_PREDICTION_RECEIPT_AUDIT"
    return {
        "schema_version": "V5.1.0-forward-prediction-receipt-r17-status",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "classification": config["classification"],
        "counts": counts,
        "infrastructure_pass": infrastructure_pass,
        "first_batch_complete": first_batch_complete,
        "capture_audit_status": capture_receipt.get("status"),
        "valid_predictions": valid_predictions,
        "failures": failures,
        "ruling": {
            "capture_event_inflation_blocked": True,
            "unique_fixture_is_sample_unit": True,
            "t24_is_baseline_only": True,
            "t45_is_sole_primary_evaluation_freeze": True,
            "current_match_probability_allowed": False,
            "model_fit_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
            "formal_weight": 0,
            "fixed_outputs": ["总进球分布不可用。", "精确比分不可用。"],
        },
        "governance": config["hard_limits"],
    }


def build_prediction_event(
    config: dict[str, Any], capture_config: dict[str, Any],
    capture_ledger: dict[str, Any], prediction_ledger: dict[str, Any],
    primary_capture_hash: str, sealed_at_utc: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing = run_audit(config, capture_config, capture_ledger, prediction_ledger)
    if existing["infrastructure_pass"] is not True:
        raise PredictionReceiptError("existing R17 ledgers failed audit")
    by_hash, by_fixture, _ = capture_index(capture_config, capture_ledger)
    roles = role_index(by_fixture, config)
    primary = by_hash.get(primary_capture_hash)
    if primary is None:
        raise PredictionReceiptError("primary capture hash not found")
    key = fixture_key(primary["fixture_identity"])
    selected_primary = roles.get(key, {}).get("primary_t45")
    if selected_primary is None or selected_primary["event_sha256"] != primary_capture_hash:
        raise PredictionReceiptError("supplied capture is not selected T-45m primary")
    baseline = roles.get(key, {}).get("baseline_t24")
    events = prediction_ledger.get("events") or []
    sequence = len(events) + 1
    previous = events[-1]["prediction_sha256"] if events else None
    prediction_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        "|".join([*key, primary_capture_hash, str(config["frozen_model_identity"]["model_contract_sha256"])])
    ))
    fixture = deepcopy(primary["fixture_identity"])
    event = {
        "sequence": sequence,
        "prediction_id": prediction_id,
        "event_type": config["prediction_receipt_contract"]["event_type_required_value"],
        "unique_fixture_key": list(key),
        "fixture_identity": fixture,
        "baseline_capture_event_sha256": baseline["event_sha256"] if baseline else None,
        "primary_capture_event_sha256": primary_capture_hash,
        "primary_freeze_at_utc": primary["freeze"]["freeze_at_utc"],
        "sealed_at_utc": parse_ts(sealed_at_utc, "sealed_at_utc").isoformat(),
        **deepcopy(config["frozen_model_identity"]),
        "output_status": config["current_output_gate"]["allowed_output_statuses"][0],
        "outputs": deepcopy(config["current_output_gate"]["required_blocked_outputs"]),
        "governance": {
            "current_match_probability_generated": False,
            "probability_mutated": False,
            "model_fitted": False,
            "result_seen_before_seal": False,
            "exact_score_generated": False,
            "ev_generated": False,
            "provider_request_in_ci": False,
        },
        "previous_prediction_sha256": previous,
    }
    event["prediction_sha256"] = prediction_hash(event)
    seen = {fixture_key(row["fixture_identity"]) for row in events}
    row = validate_prediction_event(
        event, sequence, previous, by_hash, roles, config, seen
    )
    trial = deepcopy(prediction_ledger)
    trial["events"] = [*events, event]
    full = run_audit(config, capture_config, capture_ledger, trial)
    if full["infrastructure_pass"] is not True:
        raise PredictionReceiptError("candidate passed isolated validation but full R17 audit failed")
    return event, {
        "schema_version": "V5.1.0-forward-prediction-append-r17-status",
        "status": "PASS_R17_PREDICTION_RECEIPT_READY_TO_APPEND",
        "sequence": sequence,
        "prediction_id": prediction_id,
        "prediction_sha256": event["prediction_sha256"],
        "unique_fixture_key": row["unique_fixture_key"],
        "primary_capture_event_sha256": primary_capture_hash,
        "baseline_capture_event_sha256": event["baseline_capture_event_sha256"],
        "output_status": event["output_status"],
        "pre_append_unique_predictions": existing["counts"]["valid_unique_fixture_predictions"],
        "post_append_unique_predictions": full["counts"]["valid_unique_fixture_predictions"],
        "provider_requests": 0,
        "probability_generation": False,
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


def append_prediction_event(
    config: dict[str, Any], capture_config: dict[str, Any],
    capture_ledger: dict[str, Any], prediction_path: Path,
    primary_capture_hash: str, sealed_at_utc: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prediction_ledger = load_json(prediction_path)
    event, receipt = build_prediction_event(
        config, capture_config, capture_ledger, prediction_ledger,
        primary_capture_hash, sealed_at_utc,
    )
    updated = deepcopy(prediction_ledger)
    updated["events"] = [*(prediction_ledger.get("events") or []), event]
    atomic_write_json(prediction_path, updated)
    persisted = run_audit(
        config, capture_config, capture_ledger, load_json(prediction_path)
    )
    if persisted["infrastructure_pass"] is not True:
        raise PredictionReceiptError("persisted R17 prediction ledger failed audit")
    receipt["status"] = "PASS_R17_PREDICTION_RECEIPT_APPENDED_AND_REAUDITED"
    receipt["persisted_ledger"] = str(prediction_path)
    return event, receipt


def make_capture_staging(
    capture_config: dict[str, Any], lead: timedelta,
) -> dict[str, Any]:
    staging = r15.make_staging(capture_config, 17)
    payload = staging["payload"]
    kickoff = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    freeze = kickoff - lead
    observed = freeze - timedelta(seconds=60)
    quote = freeze - timedelta(seconds=120)
    published = freeze - timedelta(hours=2)
    fixture = payload["fixture_identity"]
    fixture.update({
        "competition_id": "R17_SPECIMEN",
        "competition_name": "R17 Specimen League",
        "round": "1",
        "kickoff_at_utc": kickoff.isoformat(),
        "home_team": "R17 Home",
        "away_team": "R17 Away",
        "venue_name": "R17 Stadium",
    })
    payload["freeze"] = {
        "freeze_at_utc": freeze.isoformat(),
        "collector_observed_at_utc": observed.isoformat(),
        "packet_created_at_utc": (freeze + timedelta(seconds=5)).isoformat(),
    }
    for market in payload["market_snapshot"].values():
        market["original_quote_at_utc"] = quote.isoformat()
        market["observed_at_utc"] = observed.isoformat()
    for index, item in enumerate(payload["context_evidence"]):
        item["subject_team"] = "home" if index == 0 else "away"
        item["claim"] = f"R17 specimen context {index} at {freeze.isoformat()}"
        item["article_published_at_utc"] = published.isoformat()
        item["article_updated_at_utc"] = published.isoformat()
        item["available_at_utc"] = published.isoformat()
        item["observed_at_utc"] = observed.isoformat()
        item["evidence_sha256"] = r14.evidence_hash(item)
    return staging


def self_test(config: dict[str, Any], capture_config: dict[str, Any]) -> None:
    capture_ledger = {
        "schema_version": "V5.1.0-strict-pit-capture-ledger-r14",
        "classification": "APPEND_ONLY_RESEARCH_FORWARD_INPUT_LEDGER",
        "created_at_utc": "2026-08-05T05:49:00+00:00",
        "events": [],
    }
    t24, _ = r15.build_event(
        capture_config, capture_ledger,
        make_capture_staging(capture_config, timedelta(hours=24)),
    )
    capture_ledger["events"].append(t24)
    t45, _ = r15.build_event(
        capture_config, capture_ledger,
        make_capture_staging(capture_config, timedelta(minutes=45)),
    )
    capture_ledger["events"].append(t45)
    predictions = {
        "schema_version": "V5.1.0-forward-prediction-receipt-ledger-r17",
        "classification": "APPEND_ONLY_RESEARCH_FORWARD_PREMATCH_RECEIPT_LEDGER",
        "created_at_utc": "2026-08-05T05:49:00+00:00",
        "events": [],
    }
    pre = run_audit(config, capture_config, capture_ledger, predictions)
    assert pre["infrastructure_pass"] is True
    assert pre["counts"]["capture_events"] == 2
    assert pre["counts"]["unique_captured_fixtures"] == 1
    event, receipt = build_prediction_event(
        config, capture_config, capture_ledger, predictions,
        t45["event_sha256"], "2026-08-07T11:15:05+00:00",
    )
    assert receipt["post_append_unique_predictions"] == 1
    predictions["events"].append(event)
    post = run_audit(config, capture_config, capture_ledger, predictions)
    assert post["infrastructure_pass"] is True
    assert post["counts"]["valid_unique_fixture_predictions"] == 1
    duplicate_failed = False
    try:
        build_prediction_event(
            config, capture_config, capture_ledger, predictions,
            t45["event_sha256"], "2026-08-07T11:15:06+00:00",
        )
    except Exception:
        duplicate_failed = True
    assert duplicate_failed is True
    late_failed = False
    try:
        build_prediction_event(
            config, capture_config, capture_ledger,
            {**predictions, "events": []}, t45["event_sha256"],
            "2026-08-07T12:00:01+00:00",
        )
    except Exception:
        late_failed = True
    assert late_failed is True
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "predictions.json"
        empty = {**predictions, "events": []}
        atomic_write_json(path, empty)
        _, appended = append_prediction_event(
            config, capture_config, capture_ledger, path,
            t45["event_sha256"], "2026-08-07T11:15:05+00:00",
        )
        assert appended["status"] == "PASS_R17_PREDICTION_RECEIPT_APPENDED_AND_REAUDITED"
        assert len(load_json(path)["events"]) == 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--capture-config", type=Path, default=DEFAULT_CAPTURE_CONFIG)
    parser.add_argument("--capture-ledger", type=Path, default=DEFAULT_CAPTURE_LEDGER)
    parser.add_argument("--prediction-ledger", type=Path, default=DEFAULT_PREDICTION_LEDGER)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--primary-capture-hash")
    parser.add_argument("--sealed-at-utc")
    parser.add_argument("--event-out", type=Path)
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    config = load_json(args.config)
    capture_config = load_json(args.capture_config)
    if args.self_test:
        self_test(config, capture_config)
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    capture_ledger = load_json(args.capture_ledger)
    if args.primary_capture_hash or args.sealed_at_utc:
        if not args.primary_capture_hash or not args.sealed_at_utc:
            raise PredictionReceiptError(
                "--primary-capture-hash and --sealed-at-utc are required together"
            )
        if args.append:
            event, result = append_prediction_event(
                config, capture_config, capture_ledger, args.prediction_ledger,
                args.primary_capture_hash, args.sealed_at_utc,
            )
        else:
            event, result = build_prediction_event(
                config, capture_config, capture_ledger,
                load_json(args.prediction_ledger), args.primary_capture_hash,
                args.sealed_at_utc,
            )
            result["dry_run"] = True
            result["append_requested"] = False
        if args.event_out:
            args.event_out.parent.mkdir(parents=True, exist_ok=True)
            args.event_out.write_text(
                json.dumps(event, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    else:
        result = run_audit(
            config, capture_config, capture_ledger,
            load_json(args.prediction_ledger),
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
