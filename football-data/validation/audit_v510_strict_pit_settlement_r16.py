#!/usr/bin/env python3
"""Link R14 strict-PIT captures to post-match 90-minute result receipts.

The capture event remains immutable. Results are joined only by prediction_event_hash and
exact fixture identity, then audited for official-source chronology and score direction.
No result field is copied back into the pre-match event and no model is fitted here.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import audit_v510_strict_pit_capture_r14 as r14

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_strict_pit_settlement_r16.json"
DEFAULT_CAPTURE_CONFIG = ROOT / "config" / "v510_strict_pit_capture_r14.json"
DEFAULT_CAPTURE = ROOT / "forward" / "inbox" / "v510_strict_pit_capture_r14.json"
DEFAULT_RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_strict_pit_settlement_r16_status.json"


class SettlementError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SettlementError(f"missing JSON input: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SettlementError(f"JSON root must be object: {path.relative_to(ROOT)}")
    return value


def normalized(value: Any) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def result_direction(home: int, away: int) -> str:
    return "home" if home > away else "away" if away > home else "draw"


def fixture_signature_from_event(event: dict[str, Any]) -> tuple[str, str, str, str]:
    fixture = event.get("fixture_identity") or {}
    return (
        str(fixture.get("competition_id") or "").strip(),
        r14.parse_ts(fixture.get("kickoff_at_utc"), "capture.kickoff_at_utc").isoformat(),
        normalized(fixture.get("home_team")),
        normalized(fixture.get("away_team")),
    )


def fixture_signature_from_result(result: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(result.get("competition_id") or "").strip(),
        r14.parse_ts(result.get("kickoff_at"), "result.kickoff_at").isoformat(),
        normalized(result.get("home_team")),
        normalized(result.get("away_team")),
    )


def validate_result(
    result: dict[str, Any], event: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise SettlementError("result is not object")
    required = list(config["identity_contract"]["required_fields"])
    r14.require_fields(result, required, "result")
    if str(result["prediction_event_hash"]) != str(event["event_sha256"]):
        raise SettlementError("prediction_event_hash mismatch")
    if fixture_signature_from_result(result) != fixture_signature_from_event(event):
        raise SettlementError("result fixture identity mismatch")
    scope = str(result["settlement_scope"])
    if scope not in set(config["settlement_contract"]["accepted_settlement_scopes"]):
        raise SettlementError(f"settlement scope not accepted: {scope}")
    try:
        home = int(result["home_goals_90"])
        away = int(result["away_goals_90"])
    except (TypeError, ValueError) as exc:
        raise SettlementError("90-minute score is not integer") from exc
    if home < 0 or away < 0:
        raise SettlementError("negative 90-minute score")
    actual = str(result["actual_result"]).strip().casefold()
    aliases = {"h": "home", "home": "home", "d": "draw", "draw": "draw", "a": "away", "away": "away"}
    actual = aliases.get(actual, actual)
    expected = result_direction(home, away)
    if actual != expected:
        raise SettlementError(f"actual_result {actual} conflicts with {home}-{away}")
    source = result["source"]
    if not isinstance(source, dict):
        raise SettlementError("result source must be object")
    r14.require_fields(
        source, list(config["settlement_contract"]["source_required_fields"]),
        "result.source",
    )
    if not str(source["name"]).strip() or not str(source["source_record_id"]).strip():
        raise SettlementError("result source identity incomplete")
    if not str(source["url"]).startswith(("http://", "https://")):
        raise SettlementError("result source URL is not HTTP(S)")
    observed = r14.parse_ts(source["observed_at"], "result.source.observed_at")
    kickoff = r14.parse_ts(event["fixture_identity"]["kickoff_at_utc"], "capture.kickoff_at_utc")
    minimum = int(config["settlement_contract"]["minimum_minutes_after_kickoff_before_result_observation"])
    if observed < kickoff + timedelta(minutes=minimum):
        raise SettlementError("result observed before minimum settlement time")
    return {
        "prediction_event_hash": event["event_sha256"],
        "fixture_signature": list(fixture_signature_from_event(event)),
        "home_goals_90": home,
        "away_goals_90": away,
        "actual_result": expected,
        "zero_zero": home == 0 and away == 0,
        "source_name": str(source["name"]),
        "source_url": str(source["url"]),
        "source_record_id": str(source["source_record_id"]),
        "result_observed_at_utc": observed.isoformat(),
        "minutes_after_kickoff": (observed - kickoff).total_seconds() / 60.0,
    }


def run_audit(
    config: dict[str, Any], capture_config: dict[str, Any],
    capture_ledger: dict[str, Any], result_inbox: dict[str, Any],
) -> dict[str, Any]:
    capture_receipt = r14.run_audit(capture_config, capture_ledger)
    if capture_receipt["infrastructure_pass"] is not True:
        return {
            "schema_version": "V5.1.0-strict-pit-settlement-r16-status",
            "status": "FAIL_R16_CAPTURE_LEDGER_INVALID",
            "capture_receipt": capture_receipt,
            "settlement_failures": [],
            "ready_for_context_residual_fit": False,
            "ruling": {
                "probability_mutation_allowed": False,
                "model_fit_allowed": False,
                "formal_weight": 0,
            },
        }
    events = capture_ledger.get("events")
    results = result_inbox.get("results")
    if not isinstance(events, list) or not isinstance(results, list):
        raise SettlementError("capture events or result rows missing")

    captures_by_hash: dict[str, dict[str, Any]] = {}
    capture_rows_by_hash = {
        row["event_sha256"]: row for row in capture_receipt.get("valid_rows") or []
    }
    for event in events:
        capture_hash = str(event.get("event_sha256") or "")
        if capture_hash in captures_by_hash:
            raise SettlementError("duplicate capture event hash")
        captures_by_hash[capture_hash] = event

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    legacy_unmatched = 0
    malformed_hash_rows = 0
    for raw in results:
        if not isinstance(raw, dict):
            malformed_hash_rows += 1
            continue
        prediction_hash = str(raw.get("prediction_event_hash") or "")
        if prediction_hash in captures_by_hash:
            grouped[prediction_hash].append(raw)
        else:
            legacy_unmatched += 1

    settled_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    pending_hashes: list[str] = []
    for capture_hash, event in captures_by_hash.items():
        candidates = grouped.get(capture_hash, [])
        if not candidates:
            pending_hashes.append(capture_hash)
            continue
        if len(candidates) != 1:
            failures.append({
                "prediction_event_hash": capture_hash,
                "reason": "duplicate_or_conflicting_settlement_rows",
                "row_count": len(candidates),
            })
            continue
        try:
            row = validate_result(candidates[0], event, config)
            capture_row = capture_rows_by_hash[capture_hash]
            row["both_team_context"] = bool(capture_row["both_team_context"])
            row["complete_three_market_capture"] = True
            settled_rows.append(row)
        except Exception as exc:
            failures.append({
                "prediction_event_hash": capture_hash,
                "reason": f"{type(exc).__name__}: {exc}",
            })

    counts = {
        "capture_rows": len(events),
        "settled_strict_pit_rows": len(settled_rows),
        "pending_capture_rows": len(pending_hashes),
        "settled_draws": sum(row["actual_result"] == "draw" for row in settled_rows),
        "settled_zero_zero": sum(row["zero_zero"] for row in settled_rows),
        "settled_complete_three_market": sum(row["complete_three_market_capture"] for row in settled_rows),
        "settled_both_team_context": sum(row["both_team_context"] for row in settled_rows),
        "settlement_failures": len(failures),
        "legacy_unmatched_result_rows": legacy_unmatched,
        "malformed_result_rows_without_usable_hash": malformed_hash_rows,
    }
    gate = config["readiness_gate"]
    readiness = {
        "minimum_settled_strict_pit_rows": counts["settled_strict_pit_rows"] >= int(gate["minimum_settled_strict_pit_rows"]),
        "minimum_settled_draws": counts["settled_draws"] >= int(gate["minimum_settled_draws"]),
        "minimum_settled_zero_zero": counts["settled_zero_zero"] >= int(gate["minimum_settled_zero_zero"]),
        "minimum_rows_with_complete_three_market_capture": counts["settled_complete_three_market"] >= int(gate["minimum_rows_with_complete_three_market_capture"]),
        "minimum_rows_with_both_team_context": counts["settled_both_team_context"] >= int(gate["minimum_rows_with_both_team_context"]),
        "capture_failures": 0 <= int(gate["capture_failures_allowed"]),
        "identity_conflicts": len(failures) <= int(gate["identity_conflicts_allowed"]),
        "settlement_conflicts": len(failures) <= int(gate["settlement_conflicts_allowed"]),
        "unmatched_duplicate_results": 0 <= int(gate["unmatched_duplicate_results_allowed"]),
    }
    ready = not failures and all(readiness.values())
    if failures:
        status = "FAIL_R16_STRICT_PIT_SETTLEMENT_AUDIT"
    elif not events:
        status = "PASS_R16_SETTLEMENT_INTERFACE_READY_NO_CAPTURE_ROWS"
    elif not settled_rows:
        status = "PASS_R16_AWAITING_SETTLEMENT"
    elif ready:
        status = "PASS_R16_READY_FOR_STRICT_PIT_CONTEXT_RESIDUAL_FIT"
    else:
        status = "PASS_R16_SETTLEMENT_LINK_VALID_SAMPLE_GATE_NOT_MET"

    return {
        "schema_version": "V5.1.0-strict-pit-settlement-r16-status",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "classification": config["classification"],
        "counts": counts,
        "readiness_gates": readiness,
        "ready_for_context_residual_fit": ready,
        "settled_rows": settled_rows,
        "pending_capture_hashes": pending_hashes,
        "settlement_failures": failures,
        "capture_ledger_sha256": r14.canonical_sha256(capture_ledger),
        "result_inbox_sha256": r14.canonical_sha256(result_inbox),
        "ruling": {
            "pre_match_capture_immutable": True,
            "result_backfilled_into_capture": False,
            "model_fit_allowed": ready,
            "probability_mutation_allowed": False,
            "current_match_probability_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
            "formal_weight": 0,
            "fixed_outputs": ["总进球分布不可用。", "精确比分不可用。"],
        },
        "governance": config["hard_limits"],
    }


def make_result(event: dict[str, Any], home: int = 1, away: int = 1) -> dict[str, Any]:
    fixture = event["fixture_identity"]
    kickoff = r14.parse_ts(fixture["kickoff_at_utc"], "kickoff")
    return {
        "competition_id": fixture["competition_id"],
        "kickoff_at": fixture["kickoff_at_utc"],
        "home_team": fixture["home_team"],
        "away_team": fixture["away_team"],
        "home_goals_90": home,
        "away_goals_90": away,
        "actual_result": result_direction(home, away),
        "settlement_scope": "90_minutes_including_stoppage",
        "source": {
            "name": "official specimen scoreboard",
            "url": "https://example.test/result",
            "observed_at": (kickoff + timedelta(minutes=95)).isoformat(),
            "source_record_id": "specimen-result-1",
        },
        "prediction_event_hash": event["event_sha256"],
    }


def self_test(config: dict[str, Any], capture_config: dict[str, Any]) -> None:
    event = r14.make_valid_event(capture_config)
    capture = {"events": [event]}
    result = make_result(event, 0, 0)
    receipt = run_audit(config, capture_config, capture, {"results": [result]})
    assert receipt["counts"]["settled_strict_pit_rows"] == 1
    assert receipt["counts"]["settled_draws"] == 1
    assert receipt["counts"]["settled_zero_zero"] == 1
    assert receipt["ruling"]["result_backfilled_into_capture"] is False
    broken = json.loads(json.dumps(result))
    broken["actual_result"] = "home"
    failure = run_audit(config, capture_config, capture, {"results": [broken]})
    assert failure["status"] == "FAIL_R16_STRICT_PIT_SETTLEMENT_AUDIT"
    pending = run_audit(config, capture_config, capture, {"results": []})
    assert pending["status"] == "PASS_R16_AWAITING_SETTLEMENT"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--capture-config", type=Path, default=DEFAULT_CAPTURE_CONFIG)
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    config = load_json(args.config)
    capture_config = load_json(args.capture_config)
    if args.self_test:
        self_test(config, capture_config)
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    result = run_audit(
        config, capture_config, load_json(args.capture), load_json(args.results)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "counts": result["counts"],
        "readiness_gates": result["readiness_gates"],
        "ruling": result["ruling"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
