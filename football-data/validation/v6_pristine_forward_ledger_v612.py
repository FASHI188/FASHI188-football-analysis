#!/usr/bin/env python3
"""V6.1.2 immutable pre-match prediction and post-match settlement ledger.

The ledger is an append-only hash chain of two event types:
- PREDICTION_FROZEN: created strictly before kickoff from the frozen V6.1.0 bundle.
- RESULT_SETTLED: appended only after kickoff and linked to the frozen prediction event.

Existing events are never edited by this program. Repeated identical inbox items are
idempotent; identity conflicts, late predictions, result changes and chain tampering are
rejected and surfaced in the audit receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_direct_outcome_mvp_v600 as base
import v6_direct_outcome_draw_boundary_v601 as v601
from backtest_last_complete_season_all_domains_v470 import _predict_from_loaded_matches
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    atomic_write_json,
    canonical_team_name,
    derive_score_marginals,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    read_processed_matches,
    sha256_json,
    validate_probability_vector,
)

FREEZE = ROOT / "manifests" / "v6_pristine_forward_freeze_v610_status.json"
FIXTURE_INBOX = ROOT / "forward" / "inbox" / "fixtures_v612.json"
RESULT_INBOX = ROOT / "forward" / "inbox" / "results_v612.json"
LEDGER = ROOT / "forward" / "v6_pristine_forward_events_v612.json"
OUT = ROOT / "manifests" / "v6_pristine_forward_ledger_v612_status.json"
GENESIS = "GENESIS"
EVENT_SCHEMA = "V6.1.2-forward-event-r1"
LEDGER_SCHEMA = "V6.1.2-forward-ledger-r1"
RECEIPT_SCHEMA = "V6.1.2-forward-ledger-status-r1"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_or_initialize_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": LEDGER_SCHEMA, "events": []}
    data = load_json(path)
    if data.get("schema_version") != LEDGER_SCHEMA or not isinstance(data.get("events"), list):
        raise PlatformError("invalid V6.1.2 ledger envelope")
    return data


def _event_hash(event: dict[str, Any]) -> str:
    body = {key: value for key, value in event.items() if key != "event_hash"}
    return sha256_json(body)


def _append_event(ledger: dict[str, Any], event_type: str, match_id: str, payload: dict[str, Any], at: datetime) -> dict[str, Any]:
    events = ledger["events"]
    previous = events[-1]["event_hash"] if events else GENESIS
    event = {
        "schema_version": EVENT_SCHEMA,
        "sequence": len(events) + 1,
        "event_type": event_type,
        "event_timestamp_utc": at.isoformat(),
        "match_id": match_id,
        "previous_event_hash": previous,
        "payload": payload,
    }
    event["event_hash"] = _event_hash(event)
    events.append(event)
    return event


def _audit_chain(ledger: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    previous = GENESIS
    predictions: dict[str, dict[str, Any]] = {}
    settlements: dict[str, dict[str, Any]] = {}
    last_timestamp: datetime | None = None
    for index, event in enumerate(ledger.get("events", []), start=1):
        if event.get("sequence") != index:
            errors.append(f"sequence mismatch at {index}")
        if event.get("previous_event_hash") != previous:
            errors.append(f"previous hash mismatch at {index}")
        calculated = _event_hash(event)
        if event.get("event_hash") != calculated:
            errors.append(f"event hash mismatch at {index}")
        try:
            timestamp = parse_iso_datetime(str(event.get("event_timestamp_utc")), "event_timestamp_utc")
            if last_timestamp is not None and timestamp < last_timestamp:
                errors.append(f"event timestamp regression at {index}")
            last_timestamp = timestamp
        except PlatformError as exc:
            errors.append(f"invalid event timestamp at {index}: {exc}")
        match_id = str(event.get("match_id") or "")
        event_type = event.get("event_type")
        if event_type == "PREDICTION_FROZEN":
            if match_id in predictions:
                errors.append(f"duplicate prediction event for {match_id}")
            predictions[match_id] = event
        elif event_type == "RESULT_SETTLED":
            if match_id not in predictions:
                errors.append(f"settlement without prediction for {match_id}")
            if match_id in settlements:
                errors.append(f"duplicate settlement event for {match_id}")
            reference = ((event.get("payload") or {}).get("prediction_event_hash"))
            if match_id in predictions and reference != predictions[match_id].get("event_hash"):
                errors.append(f"settlement prediction reference mismatch for {match_id}")
            settlements[match_id] = event
        else:
            errors.append(f"unsupported event type at {index}: {event_type}")
        previous = str(event.get("event_hash") or "")
    return {
        "status": "PASS" if not errors else "FAIL",
        "event_count": len(ledger.get("events", [])),
        "prediction_count": len(predictions),
        "settlement_count": len(settlements),
        "open_prediction_count": len(set(predictions) - set(settlements)),
        "tip_hash": previous,
        "errors": errors,
    }


def _source_integrity(freeze: dict[str, Any]) -> dict[str, Any]:
    expected = freeze.get("source_integrity") or {}
    actual = {
        "v600_code_sha256": _sha256_file(VALIDATION / "v6_direct_outcome_mvp_v600.py"),
        "v601_code_sha256": _sha256_file(VALIDATION / "v6_direct_outcome_draw_boundary_v601.py"),
        "v604_code_sha256": _sha256_file(VALIDATION / "v6_selective_direction_lcb_v604.py"),
        "v605_code_sha256": _sha256_file(VALIDATION / "v6_selective_asymmetric_lcb_v605.py"),
    }
    mismatches = {
        key: {"expected": expected.get(key), "actual": value}
        for key, value in actual.items()
        if expected.get(key) != value
    }
    return {"status": "PASS" if not mismatches else "FAIL", "mismatches": mismatches, "actual": actual}


def _load_inbox(path: Path, key: str, expected_schema: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = load_json(path)
    if data.get("schema_version") != expected_schema:
        raise PlatformError(f"unexpected inbox schema in {path}")
    values = data.get(key)
    if not isinstance(values, list):
        raise PlatformError(f"{path} field {key} must be an array")
    return values


def _source_block(raw: Any, *, field: str) -> tuple[dict[str, Any], datetime]:
    if not isinstance(raw, dict):
        raise PlatformError(f"{field} must be an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise PlatformError(f"{field}.name is required")
    observed = parse_iso_datetime(str(raw.get("observed_at") or ""), f"{field}.observed_at")
    normalized = {
        "name": name,
        "observed_at": observed.isoformat(),
        "url": str(raw.get("url") or "").strip() or None,
        "source_record_id": str(raw.get("source_record_id") or "").strip() or None,
    }
    return normalized, observed


def _fixture_identity(fixture: dict[str, Any]) -> dict[str, Any]:
    cid = str(fixture.get("competition_id") or "").strip()
    source_fixture_id = str(fixture.get("source_fixture_id") or "").strip()
    season = str(fixture.get("season") or "").strip()
    stage = str(fixture.get("stage") or "stage_unverified").strip()
    kickoff = parse_iso_datetime(str(fixture.get("kickoff_at") or ""), "kickoff_at")
    home_raw = str(fixture.get("home_team") or "").strip()
    away_raw = str(fixture.get("away_team") or "").strip()
    if not cid or not source_fixture_id or not season or not home_raw or not away_raw:
        raise PlatformError("fixture requires competition_id, source_fixture_id, season, home_team and away_team")
    home = canonical_team_name(cid, home_raw)
    away = canonical_team_name(cid, away_raw)
    if normalize_team_token(home) == normalize_team_token(away):
        raise PlatformError("home and away team cannot be identical")
    return {
        "competition_id": cid,
        "source_fixture_id": source_fixture_id,
        "season": season,
        "stage": stage,
        "kickoff_at": kickoff.isoformat(),
        "home_team": home,
        "away_team": away,
    }


def _match_id(identity: dict[str, Any]) -> str:
    stable = {
        "competition_id": identity["competition_id"],
        "source_fixture_id": identity["source_fixture_id"],
    }
    return "match_" + sha256_json(stable)[:24]


def _history_digest(matches: list[Any]) -> str:
    rows = [
        {
            "competition_id": match.competition_id,
            "season": str(match.season),
            "date": match.date.isoformat(),
            "home_team": match.home_team,
            "away_team": match.away_team,
            "home_goals": int(match.home_goals),
            "away_goals": int(match.away_goals),
            "source_path": match.source_path,
        }
        for match in matches
    ]
    return sha256_json(rows)


def _selected_arms(item: dict[str, Any], arms: dict[str, Any]) -> dict[str, bool]:
    output: dict[str, bool] = {}
    for name, arm in arms.items():
        selected = int(item["agreement"]) == 1 and item["pick"] != "draw"
        direction = str(item["pick"])
        selected = selected and bool(arm.get(f"{direction}_enabled", False))
        if selected:
            if "pooled_confidence_threshold" in arm:
                threshold = float(arm["pooled_confidence_threshold"])
            else:
                threshold = float(arm[f"{direction}_confidence_threshold"])
            selected = float(item["confidence"]) >= threshold
        output[name] = bool(selected)
    return output


def _predict_fixture(identity: dict[str, Any], freeze: dict[str, Any], frozen_at: datetime) -> dict[str, Any]:
    cid = identity["competition_id"]
    domain = (freeze.get("domain_freeze") or {}).get(cid)
    if not isinstance(domain, dict):
        raise PlatformError(f"competition is not in frozen domain set: {cid}")
    kickoff = parse_iso_datetime(identity["kickoff_at"], "kickoff_at")
    all_matches = sorted(read_processed_matches(cid), key=lambda match: (match.date, match.home_team, match.away_team))
    # Processed historical rows are date-level rather than kickoff-time-level. Excluding the
    # entire fixture date prevents a later same-day result from entering a pre-match replay.
    history = [match for match in all_matches if match.date.date() < kickoff.date()]
    if not history:
        raise PlatformError(f"no leakage-safe historical rows before fixture date for {cid}")

    params = domain["formal_selected_parameters"]
    matrix = _predict_from_loaded_matches(
        history,
        identity["home_team"],
        identity["away_team"],
        kickoff,
        identity["season"],
        params,
    )
    temperature = float(domain["temperature"])
    if abs(temperature - 1.0) > 1e-15:
        matrix = temperature_scale_matrix(matrix, temperature)
    margins = derive_score_marginals(matrix)
    formal = validate_probability_vector(
        {key: float(margins["1x2"][key]) for key in base.CLASSES},
        base.CLASSES,
        field="formal_probabilities",
    )

    teams: dict[str, base.TeamState] = defaultdict(base.TeamState)
    competition = base.CompetitionState()
    for match in history:
        base._update_state(
            teams[base._team_key(match.home_team)],
            teams[base._team_key(match.away_team)],
            competition,
            match,
        )
    home_state = teams[base._team_key(identity["home_team"])]
    away_state = teams[base._team_key(identity["away_team"])]
    draw_x, side_x = base._features(formal, matrix, home_state, away_state, competition, kickoff)
    frozen_model = freeze["frozen_probability_model"]
    direct = validate_probability_vector(
        base._direct_probability({"formal": formal, "draw_x": draw_x, "side_x": side_x}, frozen_model["models"]),
        base.CLASSES,
        field="direct_probabilities",
    )
    pooled = validate_probability_vector(
        base._log_pool(formal, direct, float(frozen_model["pool_weight"])),
        base.CLASSES,
        field="pooled_probabilities",
    )
    pick = v601._pick(pooled, float(frozen_model["draw_ratio"]))
    formal_pick = max(base.CLASSES, key=lambda key: float(formal[key]))
    ordered = sorted((float(pooled[key]), key) for key in base.CLASSES)
    confidence = ordered[-1][0] - ordered[-2][0]
    item = {
        "pick": pick,
        "formal_pick": formal_pick,
        "agreement": int(pick == formal_pick),
        "confidence": confidence,
    }
    return {
        "formal_probabilities": formal,
        "direct_probabilities": direct,
        "pooled_probabilities": pooled,
        "pick": pick,
        "formal_pick": formal_pick,
        "agreement": bool(item["agreement"]),
        "confidence": confidence,
        "selected_arms": _selected_arms(item, freeze["frozen_arms"]),
        "formal_score_matrix_sha256": sha256_json(matrix),
        "formal_probability_sum": float(margins["probability_sum"]),
        "history": {
            "cutoff_policy": "processed rows with match date strictly before fixture UTC date",
            "row_count": len(history),
            "latest_match_datetime": history[-1].date.isoformat(),
            "history_sha256": _history_digest(history),
            "repository_commit": os.environ.get("GITHUB_SHA") or None,
            "prediction_generated_at_utc": frozen_at.isoformat(),
        },
    }


def _existing_maps(ledger: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[tuple[str, str], str]]:
    predictions: dict[str, dict[str, Any]] = {}
    settlements: dict[str, dict[str, Any]] = {}
    source_lookup: dict[tuple[str, str], str] = {}
    for event in ledger["events"]:
        match_id = str(event["match_id"])
        if event["event_type"] == "PREDICTION_FROZEN":
            predictions[match_id] = event
            identity = event["payload"]["fixture_identity"]
            source_lookup[(identity["competition_id"], identity["source_fixture_id"])] = match_id
        elif event["event_type"] == "RESULT_SETTLED":
            settlements[match_id] = event
    return predictions, settlements, source_lookup


def _process_fixture(
    raw: dict[str, Any],
    ledger: dict[str, Any],
    freeze: dict[str, Any],
    frozen_at: datetime,
) -> tuple[str, str | None]:
    if any(key in raw for key in ("home_goals", "away_goals", "home_goals_90", "away_goals_90", "result")):
        raise PlatformError("fixture inbox must not contain result fields")
    if str(raw.get("status") or "scheduled") not in {"scheduled", "confirmed"}:
        raise PlatformError("fixture status must be scheduled or confirmed")
    identity = _fixture_identity(raw)
    kickoff = parse_iso_datetime(identity["kickoff_at"], "kickoff_at")
    if kickoff.date().isoformat() < str(freeze["forward_start_date_utc"]):
        raise PlatformError("fixture precedes frozen forward start date")
    if frozen_at >= kickoff:
        raise PlatformError("prediction freeze must occur strictly before kickoff")
    source, source_observed_at = _source_block(raw.get("source"), field="source")
    if source_observed_at > frozen_at:
        raise PlatformError("fixture source observation cannot be later than prediction freeze")
    match_id = _match_id(identity)
    predictions, _, _ = _existing_maps(ledger)
    identity_hash = sha256_json(identity)
    if match_id in predictions:
        existing_hash = predictions[match_id]["payload"]["fixture_identity_sha256"]
        if existing_hash != identity_hash:
            raise PlatformError("source fixture identity conflicts with an existing frozen prediction")
        return "duplicate_skipped", match_id

    prediction = _predict_fixture(identity, freeze, frozen_at)
    payload = {
        "fixture_identity": identity,
        "fixture_identity_sha256": identity_hash,
        "fixture_source": source,
        "fixture_inbox_sha256": sha256_json(raw),
        "freeze": {
            "frozen_at_utc": frozen_at.isoformat(),
            "seconds_before_kickoff": (kickoff - frozen_at).total_seconds(),
            "freeze_receipt_sha256": _sha256_file(FREEZE),
            "frozen_model_sha256": sha256_json(freeze["frozen_probability_model"]),
            "frozen_arms_sha256": sha256_json(freeze["frozen_arms"]),
            "source_integrity": _source_integrity(freeze)["actual"],
        },
        "prediction": prediction,
    }
    event = _append_event(ledger, "PREDICTION_FROZEN", match_id, payload, frozen_at)
    return "prediction_frozen", event["event_hash"]


def _process_result(raw: dict[str, Any], ledger: dict[str, Any], settled_at: datetime) -> tuple[str, str | None]:
    cid = str(raw.get("competition_id") or "").strip()
    source_fixture_id = str(raw.get("source_fixture_id") or "").strip()
    if not cid or not source_fixture_id:
        raise PlatformError("result requires competition_id and source_fixture_id")
    if str(raw.get("status") or "") != "final_90":
        raise PlatformError("result status must be final_90")
    if str(raw.get("settlement_scope") or "90_minutes_including_stoppage") != "90_minutes_including_stoppage":
        raise PlatformError("result settlement_scope must be 90_minutes_including_stoppage")
    try:
        home_goals = int(raw["home_goals_90"])
        away_goals = int(raw["away_goals_90"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlatformError("result requires integer home_goals_90 and away_goals_90") from exc
    if home_goals < 0 or away_goals < 0:
        raise PlatformError("90-minute goals cannot be negative")
    source, observed_at = _source_block(raw.get("source"), field="source")
    predictions, settlements, source_lookup = _existing_maps(ledger)
    match_id = source_lookup.get((cid, source_fixture_id))
    if match_id is None or match_id not in predictions:
        raise PlatformError("result has no matching frozen prediction")
    prediction_event = predictions[match_id]
    identity = prediction_event["payload"]["fixture_identity"]
    kickoff = parse_iso_datetime(identity["kickoff_at"], "kickoff_at")
    if observed_at < kickoff or settled_at < kickoff:
        raise PlatformError("result cannot be observed or settled before kickoff")
    actual = "home" if home_goals > away_goals else "away" if home_goals < away_goals else "draw"
    prediction = prediction_event["payload"]["prediction"]
    selected_hits = {
        arm: (bool(selected) and str(prediction["pick"]) == actual)
        for arm, selected in prediction["selected_arms"].items()
    }
    payload = {
        "prediction_event_hash": prediction_event["event_hash"],
        "fixture_identity_sha256": prediction_event["payload"]["fixture_identity_sha256"],
        "result": {
            "home_goals_90": home_goals,
            "away_goals_90": away_goals,
            "actual_result": actual,
            "status": "final_90",
            "settlement_scope": "90_minutes_including_stoppage",
            "prediction_hit": str(prediction["pick"]) == actual,
            "selected_arm_hits": selected_hits,
        },
        "result_source": source,
        "result_inbox_sha256": sha256_json(raw),
        "settled_at_utc": settled_at.isoformat(),
    }
    if match_id in settlements:
        existing = settlements[match_id]["payload"]["result"]
        comparable = {
            "home_goals_90": existing["home_goals_90"],
            "away_goals_90": existing["away_goals_90"],
            "actual_result": existing["actual_result"],
        }
        proposed = {
            "home_goals_90": home_goals,
            "away_goals_90": away_goals,
            "actual_result": actual,
        }
        if comparable != proposed:
            raise PlatformError("settled result conflicts with existing immutable settlement")
        return "duplicate_skipped", match_id
    event = _append_event(ledger, "RESULT_SETTLED", match_id, payload, settled_at)
    return "result_settled", event["event_hash"]


def _write_receipt(
    *,
    status: str,
    generated: datetime,
    integrity: dict[str, Any],
    before_audit: dict[str, Any],
    after_audit: dict[str, Any],
    fixture_counts: Counter,
    result_counts: Counter,
    rejections: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "schema_version": RECEIPT_SCHEMA,
        "generated_at_utc": generated.isoformat(),
        "status": status,
        "frozen_source_integrity": integrity,
        "ledger_before": before_audit,
        "ledger_after": after_audit,
        "fixture_processing": dict(sorted(fixture_counts.items())),
        "result_processing": dict(sorted(result_counts.items())),
        "rejections": rejections,
        "governance": {
            "append_only_event_model": True,
            "prediction_must_precede_kickoff": True,
            "settlement_must_follow_kickoff": True,
            "historical_same_day_results_excluded": True,
            "existing_event_mutation_allowed": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    atomic_write_json(OUT, payload)
    return payload


def _run_self_test() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "ledger.json"
        ledger = _load_or_initialize_ledger(path)
        at = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)
        prediction = _append_event(
            ledger,
            "PREDICTION_FROZEN",
            "match_selftest",
            {
                "fixture_identity": {"competition_id": "TEST", "source_fixture_id": "1"},
                "fixture_identity_sha256": "fixture",
                "prediction": {"pick": "home", "selected_arms": {}},
            },
            at,
        )
        _append_event(
            ledger,
            "RESULT_SETTLED",
            "match_selftest",
            {"prediction_event_hash": prediction["event_hash"], "result": {"actual_result": "home"}},
            at,
        )
        audit = _audit_chain(ledger)
        if audit["status"] != "PASS" or audit["prediction_count"] != 1 or audit["settlement_count"] != 1:
            raise PlatformError(f"self-test valid chain failed: {audit}")
        tampered = json.loads(json.dumps(ledger))
        tampered["events"][0]["payload"]["prediction"]["pick"] = "away"
        if _audit_chain(tampered)["status"] != "FAIL":
            raise PlatformError("self-test tamper detection failed")
    print(json.dumps({"status": "PASS", "self_test": "V6.1.2 hash-chain and tamper audit"}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return _run_self_test()

    generated = _utc_now()
    freeze = load_json(FREEZE)
    if freeze.get("status") != "PASS":
        raise PlatformError("V6.1.0 freeze receipt must be PASS")
    integrity = _source_integrity(freeze)
    ledger = _load_or_initialize_ledger(LEDGER)
    before_audit = _audit_chain(ledger)
    if integrity["status"] != "PASS" or before_audit["status"] != "PASS":
        status = "FAIL_FROZEN_SOURCE_CHANGED" if integrity["status"] != "PASS" else "FAIL_LEDGER_TAMPERED"
        payload = _write_receipt(
            status=status,
            generated=generated,
            integrity=integrity,
            before_audit=before_audit,
            after_audit=before_audit,
            fixture_counts=Counter(),
            result_counts=Counter(),
            rejections=[],
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    fixtures = _load_inbox(FIXTURE_INBOX, "fixtures", "V6.1.2-fixture-inbox-r1")
    results = _load_inbox(RESULT_INBOX, "results", "V6.1.2-result-inbox-r1")
    fixture_counts: Counter = Counter()
    result_counts: Counter = Counter()
    rejections: list[dict[str, Any]] = []

    for index, raw in enumerate(fixtures):
        try:
            action, reference = _process_fixture(raw, ledger, freeze, generated)
            fixture_counts[action] += 1
            if reference:
                fixture_counts["referenced"] += 1
        except Exception as exc:
            fixture_counts["rejected"] += 1
            rejections.append({"kind": "fixture", "index": index, "error": f"{type(exc).__name__}: {exc}", "input_sha256": sha256_json(raw)})

    for index, raw in enumerate(results):
        try:
            action, reference = _process_result(raw, ledger, generated)
            result_counts[action] += 1
            if reference:
                result_counts["referenced"] += 1
        except Exception as exc:
            result_counts["rejected"] += 1
            rejections.append({"kind": "result", "index": index, "error": f"{type(exc).__name__}: {exc}", "input_sha256": sha256_json(raw)})

    after_audit = _audit_chain(ledger)
    status = "PASS"
    if after_audit["status"] != "PASS":
        status = "FAIL_LEDGER_AUDIT"
    elif rejections:
        status = "PARTIAL_REJECTED_INPUT"
    atomic_write_json(LEDGER, ledger)
    payload = _write_receipt(
        status=status,
        generated=generated,
        integrity=integrity,
        before_audit=before_audit,
        after_audit=after_audit,
        fixture_counts=fixture_counts,
        result_counts=result_counts,
        rejections=rejections,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
