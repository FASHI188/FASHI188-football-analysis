#!/usr/bin/env python3
"""R27 forward-freeze capture contract and legacy readiness audit."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v511_forward_freeze_capture_contract_r27.json"
DEFAULT_SCHEMA = ROOT / "schemas" / "v511_forward_freeze_event_r27.schema.json"
DEFAULT_STATUS = ROOT / "manifests" / "v511_forward_freeze_capture_contract_r27_status.json"
DEFAULT_GAPS = ROOT / "manifests" / "v511_forward_freeze_capture_contract_r27_legacy_gaps.csv"
R26_SCRIPT = ROOT / "validation" / "build_v511_forward_pit_linkage_r26.py"
R26_CONFIG = ROOT / "config" / "v511_forward_pit_linkage_r26.json"
R26_RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
R26_CONTEXT = ROOT / "forward" / "v6_context_enriched_events_v6486.json"


class ContractError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise ContractError(f"missing required file: {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_price(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 1.0


def event_payload_for_hash(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "event_hash"}


def calculate_event_hash(event: dict[str, Any]) -> str:
    return sha256_text(canonical_json(event_payload_for_hash(event)))


def seal_event(event: dict[str, Any]) -> dict[str, Any]:
    sealed = copy.deepcopy(event)
    sealed.pop("event_hash", None)
    sealed["event_hash"] = calculate_event_hash(sealed)
    return sealed


def fixture_identity(fixture: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(fixture.get("competition_id") or "").strip(),
        str(fixture.get("kickoff_at_utc") or fixture.get("kickoff_at") or "").strip(),
        re.sub(r"\W+", "", str(fixture.get("home_team") or "").casefold()),
        re.sub(r"\W+", "", str(fixture.get("away_team") or "").casefold()),
    )


def validate_event(event: dict[str, Any], config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(event, dict):
        return ["EVENT_NOT_OBJECT"]
    if event.get("schema_version") != config["event_contract"]["schema_version"]:
        errors.append("SCHEMA_VERSION_INVALID")
    if event.get("event_type") != config["event_contract"]["event_type"]:
        errors.append("EVENT_TYPE_INVALID")
    if parse_ts(event.get("event_timestamp_utc")) is None:
        errors.append("TIMESTAMP_NOT_TIMEZONE_AWARE")
    previous_hash = str(event.get("previous_event_hash") or "")
    if previous_hash != "GENESIS" and not re.fullmatch(r"[0-9a-f]{64}", previous_hash):
        errors.append("PREVIOUS_EVENT_HASH_INVALID")
    if not re.fullmatch(r"[0-9a-f]{64}", str(event.get("event_hash") or "")) or event.get("event_hash") != calculate_event_hash(event):
        errors.append("EVENT_HASH_MISMATCH")

    payload = event.get("payload")
    if not isinstance(payload, dict):
        return sorted(set(errors + ["PAYLOAD_MISSING"]))
    fixture = payload.get("fixture_identity")
    if not isinstance(fixture, dict):
        errors.append("FIXTURE_IDENTITY_MISSING")
        fixture = {}
    kickoff = parse_ts(fixture.get("kickoff_at_utc"))
    freeze = parse_ts(payload.get("decision_freeze_at_utc"))
    if kickoff is None or freeze is None:
        errors.append("TIMESTAMP_NOT_TIMEZONE_AWARE")
    elif not freeze < kickoff:
        errors.append("FREEZE_NOT_BEFORE_KICKOFF")
    for field in ("competition_id", "round_or_stage", "home_team", "away_team", "venue_type", "settlement_scope"):
        if field not in fixture or fixture.get(field) in (None, ""):
            errors.append("FIXTURE_IDENTITY_INCOMPLETE")
    if fixture.get("settlement_scope") != "90_minutes_including_stoppage":
        errors.append("RESULT_SETTLEMENT_SCOPE_INVALID")
    if not isinstance(fixture.get("two_legged"), bool):
        errors.append("FIXTURE_IDENTITY_INCOMPLETE")

    market = payload.get("market")
    if not isinstance(market, dict):
        errors.extend(["MISSING_MARKET_OBSERVED_AT", "MISSING_MARKET_AVAILABLE_AT", "MISSING_1X2", "MISSING_ASIAN_HANDICAP", "MISSING_OVER_UNDER"])
        market = {}
    market_observed = parse_ts(market.get("observed_at_utc"))
    market_available = parse_ts(market.get("available_at_utc"))
    if market_observed is None:
        errors.append("MISSING_MARKET_OBSERVED_AT")
    if market_available is None:
        errors.append("MISSING_MARKET_AVAILABLE_AT")
    if market_observed and market_available and market_available < market_observed:
        errors.append("MARKET_AVAILABLE_BEFORE_OBSERVED")
    if freeze and market_observed and market_observed > freeze:
        errors.append("MARKET_AFTER_FREEZE")
    if freeze and market_available and market_available > freeze:
        errors.append("MARKET_AFTER_FREEZE")
    if not all(nonempty(market.get(key)) for key in ("provider_name", "provider_group", "source_identifier")):
        errors.append("PROVIDER_IDENTITY_INCOMPLETE")

    one = market.get("one_x_two")
    asian = market.get("asian_handicap")
    total = market.get("over_under")
    if not isinstance(one, dict) or not all(valid_price(one.get(key)) for key in ("home", "draw", "away")) or parse_ts(one.get("observed_at_utc")) is None:
        errors.append("MISSING_1X2")
    if not isinstance(asian, dict) or not all(valid_price(asian.get(key)) for key in ("home", "away")) or not isinstance(asian.get("line"), (int, float)) or parse_ts(asian.get("observed_at_utc")) is None:
        errors.append("MISSING_ASIAN_HANDICAP")
    if not isinstance(total, dict) or not all(valid_price(total.get(key)) for key in ("over", "under")) or not isinstance(total.get("line"), (int, float)) or parse_ts(total.get("observed_at_utc")) is None:
        errors.append("MISSING_OVER_UNDER")
    market_times = [parse_ts(value.get("observed_at_utc")) for value in (one, asian, total) if isinstance(value, dict)]
    if len(market_times) == 3 and all(market_times):
        gap = (max(market_times) - min(market_times)).total_seconds()
        if gap > float(config["timing_contract"]["maximum_three_market_sync_gap_seconds"]):
            errors.append("THREE_MARKETS_NOT_SYNCHRONIZED")
        if freeze and any(ts > freeze for ts in market_times):
            errors.append("MARKET_AFTER_FREEZE")

    context = payload.get("context")
    if not isinstance(context, dict):
        errors.extend(["MISSING_CONTEXT_TIMING", "CONTEXT_PAYLOAD_INCOMPLETE"])
        context = {}
    context_observed = parse_ts(context.get("observed_at_utc"))
    context_available = parse_ts(context.get("available_at_utc"))
    if context_observed is None or context_available is None:
        errors.append("MISSING_CONTEXT_TIMING")
    if context_observed and context_available and context_available < context_observed:
        errors.append("CONTEXT_AVAILABLE_BEFORE_OBSERVED")
    if freeze and ((context_observed and context_observed > freeze) or (context_available and context_available > freeze)):
        errors.append("CONTEXT_AFTER_FREEZE")
    source = context.get("source")
    availability = context.get("availability")
    predicted_xi = context.get("predicted_xi")
    task_state = context.get("task_state")
    if not isinstance(source, dict) or not all(nonempty(source.get(key)) for key in ("source_name", "source_identifier", "provider_group")):
        errors.append("CONTEXT_PAYLOAD_INCOMPLETE")
    for two_sides in (availability, predicted_xi):
        if not isinstance(two_sides, dict) or any(key not in two_sides for key in ("home", "away")):
            errors.append("CONTEXT_PAYLOAD_INCOMPLETE")
            continue
        for side in ("home", "away"):
            value = two_sides[side]
            if value != "UNKNOWN" and not isinstance(value, list):
                errors.append("CONTEXT_PAYLOAD_INCOMPLETE")
    if not isinstance(task_state, dict) or not all(nonempty(task_state.get(key)) for key in ("home_objective", "away_objective", "competition_state", "evidence")):
        errors.append("CONTEXT_PAYLOAD_INCOMPLETE")

    governance = payload.get("governance")
    expected_governance = {
        "market_observed_no_later_than_freeze": True,
        "context_observed_no_later_than_freeze": True,
        "freeze_before_kickoff": True,
        "three_markets_synchronized": True,
        "probability_mutation": False,
        "formal_weight": 0,
    }
    if not isinstance(governance, dict) or any(governance.get(key) != value for key, value in expected_governance.items()):
        errors.append("GOVERNANCE_ASSERTION_INVALID")
    return sorted(set(errors))


def validate_result_link(event: dict[str, Any], result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if result.get("prediction_event_hash") != event.get("event_hash"):
        errors.append("RESULT_LINK_HASH_MISMATCH")
    fixture = event.get("payload", {}).get("fixture_identity", {})
    result_fixture = {
        "competition_id": result.get("competition_id"),
        "kickoff_at_utc": result.get("kickoff_at"),
        "home_team": result.get("home_team"),
        "away_team": result.get("away_team"),
    }
    if fixture_identity(fixture) != fixture_identity(result_fixture):
        errors.append("RESULT_IDENTITY_MISMATCH")
    if result.get("settlement_scope") != "90_minutes_including_stoppage":
        errors.append("RESULT_SETTLEMENT_SCOPE_INVALID")
    kickoff = parse_ts(fixture.get("kickoff_at_utc"))
    observed = parse_ts(result.get("observed_at_utc") or result.get("source", {}).get("observed_at"))
    if kickoff is None or observed is None or observed <= kickoff:
        errors.append("RESULT_OBSERVED_TIME_INVALID")
    if not isinstance(result.get("home_goals_90"), int) or not isinstance(result.get("away_goals_90"), int):
        errors.append("RESULT_SCORE_INVALID")
    return sorted(set(errors))


def valid_sample() -> tuple[dict[str, Any], dict[str, Any]]:
    event = {
        "schema_version": "v511_forward_freeze_event_r27.1",
        "event_type": "PREMATCH_DECISION_FROZEN",
        "event_timestamp_utc": "2026-08-05T10:00:00+00:00",
        "previous_event_hash": "GENESIS",
        "payload": {
            "fixture_identity": {
                "competition_id": "TEST_League",
                "round_or_stage": "round_1",
                "kickoff_at_utc": "2026-08-05T12:00:00+00:00",
                "home_team": "Alpha",
                "away_team": "Beta",
                "venue_type": "home",
                "two_legged": False,
                "first_leg_state": None,
                "settlement_scope": "90_minutes_including_stoppage"
            },
            "decision_freeze_at_utc": "2026-08-05T10:00:00+00:00",
            "market": {
                "observed_at_utc": "2026-08-05T09:58:00+00:00",
                "available_at_utc": "2026-08-05T09:59:00+00:00",
                "provider_name": "Test Book",
                "provider_group": "independent_test",
                "source_identifier": "snapshot:test:001",
                "one_x_two": {"observed_at_utc": "2026-08-05T09:58:00+00:00", "home": 2.5, "draw": 3.1, "away": 2.9},
                "asian_handicap": {"observed_at_utc": "2026-08-05T09:58:30+00:00", "line": 0.0, "home": 1.91, "away": 1.95},
                "over_under": {"observed_at_utc": "2026-08-05T09:59:00+00:00", "line": 2.5, "over": 1.9, "under": 1.96}
            },
            "context": {
                "observed_at_utc": "2026-08-05T09:50:00+00:00",
                "available_at_utc": "2026-08-05T09:55:00+00:00",
                "source": {"source_name": "Test official preview", "source_identifier": "context:test:001", "provider_group": "official_test"},
                "availability": {"home": [], "away": []},
                "predicted_xi": {"home": ["H1"], "away": ["A1"]},
                "task_state": {"home_objective": "win", "away_objective": "avoid defeat", "competition_state": "league", "evidence": "official table"}
            },
            "governance": {
                "market_observed_no_later_than_freeze": True,
                "context_observed_no_later_than_freeze": True,
                "freeze_before_kickoff": True,
                "three_markets_synchronized": True,
                "probability_mutation": False,
                "formal_weight": 0
            }
        }
    }
    event = seal_event(event)
    result = {
        "prediction_event_hash": event["event_hash"],
        "competition_id": "TEST_League",
        "kickoff_at": "2026-08-05T12:00:00+00:00",
        "home_team": "Alpha",
        "away_team": "Beta",
        "home_goals_90": 1,
        "away_goals_90": 1,
        "actual_result": "draw",
        "settlement_scope": "90_minutes_including_stoppage",
        "observed_at_utc": "2026-08-05T14:00:00+00:00"
    }
    return event, result


def mutation_tests(config: dict[str, Any]) -> dict[str, Any]:
    base, result = valid_sample()
    tests: dict[str, tuple[dict[str, Any], str]] = {}

    def mutated(name: str, code: str, fn) -> None:
        item = copy.deepcopy(base)
        fn(item)
        if code != "EVENT_HASH_MISMATCH":
            item = seal_event(item)
        tests[name] = (item, code)

    mutated("missing_market_observed", "MISSING_MARKET_OBSERVED_AT", lambda e: e["payload"]["market"].pop("observed_at_utc"))
    mutated("missing_over_under", "MISSING_OVER_UNDER", lambda e: e["payload"]["market"].pop("over_under"))
    mutated("market_after_freeze", "MARKET_AFTER_FREEZE", lambda e: e["payload"]["market"].update(observed_at_utc="2026-08-05T10:01:00+00:00"))
    mutated("unsynchronized_markets", "THREE_MARKETS_NOT_SYNCHRONIZED", lambda e: e["payload"]["market"]["over_under"].update(observed_at_utc="2026-08-05T09:50:00+00:00"))
    mutated("context_after_freeze", "CONTEXT_AFTER_FREEZE", lambda e: e["payload"]["context"].update(available_at_utc="2026-08-05T10:01:00+00:00"))
    mutated("naive_kickoff", "TIMESTAMP_NOT_TIMEZONE_AWARE", lambda e: e["payload"]["fixture_identity"].update(kickoff_at_utc="2026-08-05T12:00:00"))
    mutated("bad_hash", "EVENT_HASH_MISMATCH", lambda e: e["payload"]["fixture_identity"].update(home_team="Tampered"))

    test_rows = []
    passed = validate_event(base, config) == [] and validate_result_link(base, result) == []
    for name, (item, expected) in tests.items():
        errors = validate_event(item, config)
        hit = expected in errors
        passed = passed and hit
        test_rows.append({"name": name, "expected_reject": expected, "errors": errors, "passed": hit})
    bad_result = copy.deepcopy(result)
    bad_result["prediction_event_hash"] = "0" * 64
    result_errors = validate_result_link(base, bad_result)
    result_hit = "RESULT_LINK_HASH_MISMATCH" in result_errors
    passed = passed and result_hit
    test_rows.append({"name": "bad_result_link", "expected_reject": "RESULT_LINK_HASH_MISMATCH", "errors": result_errors, "passed": result_hit})
    return {"passed": passed, "valid_sample_accepted": validate_event(base, config) == [], "valid_result_link_accepted": validate_result_link(base, result) == [], "tests": test_rows}


def load_r26_module():
    spec = importlib.util.spec_from_file_location("r26_builder", R26_SCRIPT)
    if spec is None or spec.loader is None:
        raise ContractError("unable to load R26 builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy_audit(config: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    r26 = load_r26_module()
    rows, summary = r26.build(load_json(R26_CONFIG), R26_RESULTS, R26_CONTEXT)
    gaps = []
    for row in rows:
        reasons = list(row.get("missing_reasons") or [])
        if not row.get("market_observed_at_utc") and "MARKET_TIMESTAMP_MISSING" not in reasons:
            reasons.append("MARKET_TIMESTAMP_MISSING")
        if not row.get("has_complete_over_under") and "COMPLETE_OVER_UNDER_MISSING" not in reasons:
            reasons.append("COMPLETE_OVER_UNDER_MISSING")
        if not row.get("context_pit_complete") and "TIMESTAMPED_CONTEXT_BEFORE_FREEZE_MISSING" not in reasons:
            reasons.append("TIMESTAMPED_CONTEXT_BEFORE_FREEZE_MISSING")
        for reason in sorted(set(reasons)):
            gaps.append({
                "prediction_event_hash": row.get("prediction_event_hash"),
                "competition_id": row.get("competition_id"),
                "kickoff_at": row.get("kickoff_at"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "actual_result": row.get("actual_result"),
                "gap": reason,
            })
    counts = summary["counts"]
    expected = config["legacy_audit"]
    invariant_ok = (
        counts["result_receipts"] == int(expected["expected_rows"])
        and counts["result_draws"] == int(expected["expected_draws"])
        and counts["result_zero_zero_draws"] == int(expected["expected_zero_zero_draws"])
        and summary["build_gate"]["passed"] is True
    )
    audit = {
        "r26_build_gate_passed": summary["build_gate"]["passed"],
        "legacy_invariants_passed": invariant_ok,
        "rows": counts["result_receipts"],
        "draws": counts["result_draws"],
        "zero_zero_draws": counts["result_zero_zero_draws"],
        "rows_with_market_timestamp": counts["rows_with_market_timestamp"],
        "rows_with_complete_1x2": counts["rows_with_complete_1x2"],
        "rows_with_complete_asian_handicap": counts["rows_with_complete_asian_handicap"],
        "rows_with_complete_over_under": counts["rows_with_complete_over_under"],
        "core_pit_complete_rows": counts["core_pit_complete_rows"],
        "core_pit_complete_draws": counts["core_pit_complete_draws"],
        "three_market_pit_complete_rows": counts["three_market_pit_complete_rows"],
        "three_market_pit_complete_draws": counts["three_market_pit_complete_draws"],
        "context_pit_complete_rows": counts["context_pit_complete_rows"],
        "context_pit_complete_draws": counts["context_pit_complete_draws"],
        "strict_screen10_ready": counts["context_pit_complete_draws"] >= int(config["screen10_readiness"]["minimum_strict_pit_draws"]),
        "legacy_rows_repaired_by_inference": False,
        "r26_status": summary["status"],
        "r26_final_row_hash": summary["ledger_integrity"]["final_row_hash"],
    }
    return audit, gaps


def write_gaps(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["prediction_event_hash", "competition_id", "kickoff_at", "home_team", "away_team", "actual_result", "gap"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(config_path: Path, schema_path: Path, status_path: Path, gaps_path: Path) -> dict[str, Any]:
    config = load_json(config_path)
    schema = load_json(schema_path)
    tests = mutation_tests(config)
    legacy, gaps = legacy_audit(config)
    schema_ok = schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema" and schema.get("properties", {}).get("event_hash") is not None
    passed = bool(tests["passed"] and legacy["legacy_invariants_passed"] and schema_ok and legacy["strict_screen10_ready"] is False)
    status = {
        "schema_version": "v511_forward_freeze_capture_contract_r27_status.1",
        "status": "PASS_R27_FUTURE_CAPTURE_CONTRACT_VALIDATED_LEGACY_REMAINS_INELIGIBLE" if passed else "FAIL_R27_CAPTURE_CONTRACT_OR_LEGACY_AUDIT",
        "classification": config["classification"],
        "formal_weight": 0,
        "contract_gate": {
            "passed": passed,
            "schema_present_and_parseable": schema_ok,
            "valid_event_accepted": tests["valid_sample_accepted"],
            "valid_result_link_accepted": tests["valid_result_link_accepted"],
            "all_required_negative_tests_rejected": tests["passed"]
        },
        "negative_tests": tests["tests"],
        "legacy_audit": legacy,
        "future_capture_ruling": {
            "new_r27_event_must_pass_before_persistence": True,
            "missing_market_timestamp_hard_reject": True,
            "missing_over_under_hard_reject": True,
            "three_market_sync_gap_seconds": config["timing_contract"]["maximum_three_market_sync_gap_seconds"],
            "post_freeze_information_hard_reject": True,
            "missing_context_timing_hard_reject": True,
            "event_hash_mismatch_hard_reject": True,
            "result_link_mismatch_hard_reject": True,
            "legacy_rows_grandfathered_as_strict_pit": False,
            "screen10_allowed_now": False,
            "next_action": "route every future freeze event through the R27 validator and accumulate at least ten strict PIT draw rows"
        },
        "hard_limits": config["hard_limits"],
        "governance_ruling": {
            "model_training_performed": False,
            "probabilities_generated": False,
            "provider_requests": 0,
            "new_external_data_collection": False,
            "formal_promotion_allowed": False,
            "current_or_main_mutation": False,
            "unified_matrix_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False
        }
    }
    write_gaps(gaps_path, gaps)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    config = load_json(args.config)
    if args.self_test:
        result = mutation_tests(config)
        if not result["passed"]:
            raise ContractError(f"R27 self-test failed: {result}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(json.dumps(run(args.config, args.schema, args.status, args.gaps), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
