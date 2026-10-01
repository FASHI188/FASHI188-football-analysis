#!/usr/bin/env python3
"""Audit the V5.1 R14 append-only strict-PIT pre-match capture ledger.

The auditor validates fixture identity, quote provenance, synchronized 1X2/AH/OU
markets, web-context availability timestamps, source-group identity and the ledger hash
chain. It never requests a provider, fits a model or mutates probabilities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v510_strict_pit_capture_r14.json"
DEFAULT_INBOX = ROOT / "forward" / "inbox" / "v510_strict_pit_capture_r14.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_strict_pit_capture_r14_status.json"


class CaptureError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CaptureError(f"missing input: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CaptureError(f"JSON root must be object: {path.relative_to(ROOT)}")
    return value


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_ts(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise CaptureError(f"missing {field}")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureError(f"invalid {field}: {text}") from exc
    if parsed.tzinfo is None:
        raise CaptureError(f"naive {field}: {text}")
    return parsed.astimezone(timezone.utc)


def require_fields(container: dict[str, Any], fields: list[str], scope: str) -> None:
    missing = [field for field in fields if field not in container]
    if missing:
        raise CaptureError(f"{scope} missing fields: {missing}")


def valid_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def event_hash(event: dict[str, Any]) -> str:
    body = {key: value for key, value in event.items() if key != "event_sha256"}
    return canonical_sha256(body)


def evidence_hash(item: dict[str, Any]) -> str:
    fields = (
        "category", "subject_team", "claim", "source_name", "source_tier",
        "provider_group", "source_url", "article_published_at_utc",
        "article_updated_at_utc", "observed_at_utc", "available_at_utc",
    )
    return canonical_sha256({field: item.get(field) for field in fields})


def positive_price(value: Any, minimum: float, field: str) -> float:
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise CaptureError(f"invalid decimal price {field}: {value}") from exc
    if not math.isfinite(price) or price <= minimum:
        raise CaptureError(f"invalid decimal price {field}: {price}")
    return price


def validate_market_block(
    name: str,
    block: dict[str, Any],
    sides: list[str],
    freeze: datetime,
    config: dict[str, Any],
    require_line: bool,
) -> dict[str, Any]:
    required = [
        "original_quote_at_utc", "observed_at_utc", "source_url",
        "provider_name", "provider_group", "executable_or_tradable", "prices",
    ]
    if require_line:
        required.append("line")
    require_fields(block, required, f"market.{name}")
    quote = parse_ts(block["original_quote_at_utc"], f"market.{name}.original_quote_at_utc")
    observed = parse_ts(block["observed_at_utc"], f"market.{name}.observed_at_utc")
    if quote > observed or observed > freeze:
        raise CaptureError(f"market.{name} chronology failure")
    maximum_age = float(config["market_contract"]["maximum_quote_age_minutes_at_freeze"])
    age_minutes = (freeze - quote).total_seconds() / 60.0
    if age_minutes < 0 or age_minutes > maximum_age:
        raise CaptureError(f"market.{name} quote age {age_minutes:.3f}m outside gate")
    if not str(block["source_url"]).startswith(("http://", "https://")):
        raise CaptureError(f"market.{name} source_url is not HTTP(S)")
    if not str(block["provider_name"]).strip() or not str(block["provider_group"]).strip():
        raise CaptureError(f"market.{name} provider identity missing")
    if block["executable_or_tradable"] is not True:
        raise CaptureError(f"market.{name} is not marked executable/tradable")
    prices = block["prices"]
    if not isinstance(prices, dict):
        raise CaptureError(f"market.{name}.prices must be object")
    minimum = float(config["market_contract"]["decimal_price_minimum_exclusive"])
    parsed_prices = {
        side: positive_price(prices.get(side), minimum, f"{name}.{side}")
        for side in sides
    }
    line = None
    if require_line:
        try:
            line = float(block["line"])
        except (TypeError, ValueError) as exc:
            raise CaptureError(f"market.{name}.line invalid") from exc
        if not math.isfinite(line):
            raise CaptureError(f"market.{name}.line non-finite")
    return {
        "quote": quote,
        "observed": observed,
        "age_minutes": age_minutes,
        "provider_group": str(block["provider_group"]).strip(),
        "prices": parsed_prices,
        "line": line,
    }


def validate_context_item(
    item: dict[str, Any], freeze: datetime, fixture: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise CaptureError("context evidence item is not object")
    required = list(config["context_contract"]["required_fields_per_item"])
    require_fields(item, required, "context_item")
    category = str(item["category"])
    if category not in set(config["context_contract"]["allowed_categories"]):
        raise CaptureError(f"context category not allowed: {category}")
    tier = str(item["source_tier"])
    if tier not in set(config["context_contract"]["allowed_source_tiers"]):
        raise CaptureError(f"context source tier not allowed: {tier}")
    subject = str(item["subject_team"]).strip()
    allowed_subjects = {
        "home", "away", "both", "unassigned",
        str(fixture.get("home_team") or "").strip(),
        str(fixture.get("away_team") or "").strip(),
    }
    if not subject or subject not in allowed_subjects:
        raise CaptureError(f"context subject_team invalid: {subject}")
    if not str(item["claim"]).strip():
        raise CaptureError("context claim empty")
    if not str(item["provider_group"]).strip():
        raise CaptureError("context provider_group empty")
    if not str(item["source_url"]).startswith(("http://", "https://")):
        raise CaptureError("context source_url is not HTTP(S)")
    published = parse_ts(item["article_published_at_utc"], "article_published_at_utc")
    updated = parse_ts(item["article_updated_at_utc"], "article_updated_at_utc")
    available = parse_ts(item["available_at_utc"], "available_at_utc")
    observed = parse_ts(item["observed_at_utc"], "observed_at_utc")
    if not (published <= updated <= available <= observed <= freeze):
        raise CaptureError("context chronology failure")
    if not valid_sha256(item["evidence_sha256"]):
        raise CaptureError("context evidence_sha256 malformed")
    if str(item["evidence_sha256"]) != evidence_hash(item):
        raise CaptureError("context evidence hash mismatch")
    return {
        "subject": subject,
        "provider_group": str(item["provider_group"]).strip(),
        "category": category,
        "observed": observed,
    }


def validate_event(
    event: dict[str, Any], expected_sequence: int, previous_hash: str | None,
    config: dict[str, Any], seen_fixture_freezes: set[tuple[str, str, str, str, str]],
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise CaptureError("event is not object")
    required = [
        "sequence", "event_id", "event_type", "fixture_identity", "freeze",
        "market_snapshot", "context_evidence", "missing_semantics", "governance",
        "previous_event_sha256", "event_sha256",
    ]
    require_fields(event, required, "event")
    if int(event["sequence"]) != expected_sequence:
        raise CaptureError(f"sequence mismatch: {event['sequence']} != {expected_sequence}")
    try:
        uuid.UUID(str(event["event_id"]))
    except ValueError as exc:
        raise CaptureError("event_id is not UUID") from exc
    required_type = config["append_only_ledger_contract"]["event_type_required_value"]
    if event["event_type"] != required_type:
        raise CaptureError(f"event_type mismatch: {event['event_type']}")
    supplied_previous = event.get("previous_event_sha256")
    if expected_sequence == 1:
        if supplied_previous not in (None, ""):
            raise CaptureError("first event previous_event_sha256 must be null")
    elif supplied_previous != previous_hash:
        raise CaptureError("previous_event_sha256 chain mismatch")
    if not valid_sha256(event["event_sha256"]):
        raise CaptureError("event_sha256 malformed")
    calculated = event_hash(event)
    if calculated != event["event_sha256"]:
        raise CaptureError("event_sha256 mismatch")

    fixture = event["fixture_identity"]
    if not isinstance(fixture, dict):
        raise CaptureError("fixture_identity must be object")
    require_fields(
        fixture, list(config["fixture_identity_contract"]["required_fields"]),
        "fixture_identity",
    )
    if fixture["settlement_scope"] != config["fixture_identity_contract"]["settlement_scope_required_value"]:
        raise CaptureError("settlement_scope mismatch")
    if not str(fixture["kickoff_timezone_source_url"]).startswith(("http://", "https://")):
        raise CaptureError("kickoff timezone provenance missing")
    kickoff = parse_ts(fixture["kickoff_at_utc"], "kickoff_at_utc")
    if not str(fixture["competition_id"]).strip() or not str(fixture["home_team"]).strip() or not str(fixture["away_team"]).strip():
        raise CaptureError("fixture identity incomplete")
    if str(fixture["home_team"]).strip() == str(fixture["away_team"]).strip():
        raise CaptureError("home and away teams identical")

    freeze_block = event["freeze"]
    require_fields(freeze_block, list(config["freeze_contract"]["required_fields"]), "freeze")
    freeze = parse_ts(freeze_block["freeze_at_utc"], "freeze_at_utc")
    collector = parse_ts(freeze_block["collector_observed_at_utc"], "collector_observed_at_utc")
    created = parse_ts(freeze_block["packet_created_at_utc"], "packet_created_at_utc")
    if not (collector <= freeze < kickoff and created >= collector):
        raise CaptureError("freeze chronology failure")
    maximum_created = float(config["freeze_contract"]["packet_created_max_seconds_after_freeze"])
    if (created - freeze).total_seconds() > maximum_created:
        raise CaptureError("packet created too long after freeze")

    freeze_key = (
        str(fixture["competition_id"]), kickoff.isoformat(),
        str(fixture["home_team"]).casefold().strip(),
        str(fixture["away_team"]).casefold().strip(), freeze.isoformat(),
    )
    if freeze_key in seen_fixture_freezes:
        raise CaptureError("duplicate fixture-freeze pair")
    seen_fixture_freezes.add(freeze_key)

    market = event["market_snapshot"]
    if not isinstance(market, dict):
        raise CaptureError("market_snapshot must be object")
    one_x_two = validate_market_block(
        "one_x_two", market.get("one_x_two") or {},
        list(config["market_contract"]["one_x_two_required_sides"]),
        freeze, config, False,
    )
    two_way = config["market_contract"]["two_way_required_sides"]
    asian = validate_market_block(
        "asian_handicap", market.get("asian_handicap") or {},
        list(two_way["asian_handicap"]), freeze, config, True,
    )
    total = validate_market_block(
        "over_under", market.get("over_under") or {},
        list(two_way["over_under"]), freeze, config, True,
    )
    quote_times = [one_x_two["quote"], asian["quote"], total["quote"]]
    sync_seconds = (max(quote_times) - min(quote_times)).total_seconds()
    if sync_seconds > float(config["market_contract"]["synchronization_window_seconds"]):
        raise CaptureError(f"three-market synchronization failure: {sync_seconds}s")

    evidence = event["context_evidence"]
    if not isinstance(evidence, list):
        raise CaptureError("context_evidence must be list")
    context_rows = [validate_context_item(item, freeze, fixture, config) for item in evidence]
    subjects = {row["subject"] for row in context_rows}
    home = str(fixture["home_team"]).strip()
    away = str(fixture["away_team"]).strip()
    both_team_context = (
        "both" in subjects
        or (("home" in subjects or home in subjects) and ("away" in subjects or away in subjects))
    )
    if not isinstance(event["missing_semantics"], dict):
        raise CaptureError("missing_semantics must be object")
    if not isinstance(event["governance"], dict):
        raise CaptureError("governance must be object")
    governance = event["governance"]
    forbidden_true = (
        "probability_mutated", "model_fitted", "provider_request_in_ci",
        "exact_score_generated", "ev_generated",
    )
    if any(governance.get(field) is True for field in forbidden_true):
        raise CaptureError("governance forbidden action marked true")

    inverse = {side: 1.0 / price for side, price in one_x_two["prices"].items()}
    overround = sum(inverse.values())
    devig = {side: value / overround for side, value in inverse.items()}
    return {
        "event_sha256": calculated,
        "fixture_freeze_key": list(freeze_key),
        "kickoff_at_utc": kickoff.isoformat(),
        "freeze_at_utc": freeze.isoformat(),
        "lead_hours": (kickoff - freeze).total_seconds() / 3600.0,
        "market_sync_seconds": sync_seconds,
        "market_provider_groups": sorted({
            one_x_two["provider_group"], asian["provider_group"], total["provider_group"]
        }),
        "context_item_count": len(context_rows),
        "context_provider_groups": sorted({row["provider_group"] for row in context_rows}),
        "both_team_context": both_team_context,
        "devig_1x2": devig,
    }


def run_audit(config: dict[str, Any], inbox: dict[str, Any]) -> dict[str, Any]:
    events = inbox.get("events")
    if not isinstance(events, list):
        raise CaptureError("inbox events must be list")
    valid_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    previous_hash: str | None = None
    for index, event in enumerate(events):
        try:
            row = validate_event(event, index + 1, previous_hash, config, seen)
            valid_rows.append(row)
            previous_hash = row["event_sha256"]
        except Exception as exc:
            failures.append({"event_index": index, "reason": f"{type(exc).__name__}: {exc}"})
            previous_hash = None

    counts = {
        "ledger_events": len(events),
        "strict_pit_valid_rows": len(valid_rows),
        "invalid_rows": len(failures),
        "complete_three_market_rows": len(valid_rows),
        "rows_with_both_team_context": sum(row["both_team_context"] for row in valid_rows),
        "settled_rows": 0,
        "settled_draws": 0,
        "unique_market_provider_groups": len({
            group for row in valid_rows for group in row["market_provider_groups"]
        }),
        "unique_context_provider_groups": len({
            group for row in valid_rows for group in row["context_provider_groups"]
        }),
    }
    gate = config["readiness_gate"]
    readiness = {
        "minimum_strict_pit_rows_for_context_residual_fit": counts["strict_pit_valid_rows"] >= int(gate["minimum_strict_pit_rows_for_context_residual_fit"]),
        "minimum_settled_rows": counts["settled_rows"] >= int(gate["minimum_settled_rows"]),
        "minimum_settled_draws": counts["settled_draws"] >= int(gate["minimum_settled_draws"]),
        "minimum_complete_three_market_rows": counts["complete_three_market_rows"] >= int(gate["minimum_complete_three_market_rows"]),
        "minimum_rows_with_both_team_context": counts["rows_with_both_team_context"] >= int(gate["minimum_rows_with_both_team_context"]),
        "identity_conflicts": 0 <= int(gate["identity_conflicts_allowed"]),
        "timestamp_failures": len(failures) <= int(gate["timestamp_failures_allowed"]),
        "market_failures": len(failures) <= int(gate["market_failures_allowed"]),
        "context_failures": len(failures) <= int(gate["context_failures_allowed"]),
        "hash_chain_failures": len(failures) <= int(gate["hash_chain_failures_allowed"]),
    }
    infrastructure_pass = len(failures) == 0
    ready_for_fit = infrastructure_pass and all(readiness.values())
    if ready_for_fit:
        status = "PASS_R14_READY_FOR_STRICT_PIT_CONTEXT_RESIDUAL_FIT"
    elif infrastructure_pass and not events:
        status = "PASS_R14_CAPTURE_INTERFACE_READY_NO_STRICT_PIT_ROWS"
    elif infrastructure_pass:
        status = "PASS_R14_CAPTURE_INTERFACE_READY_SAMPLE_GATE_NOT_MET"
    else:
        status = "FAIL_R14_STRICT_PIT_CAPTURE_AUDIT"

    return {
        "schema_version": "V5.1.0-strict-pit-capture-r14-status",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "classification": config["classification"],
        "counts": counts,
        "readiness_gates": readiness,
        "ready_for_context_residual_fit": ready_for_fit,
        "infrastructure_pass": infrastructure_pass,
        "valid_rows": valid_rows,
        "failures": failures,
        "ruling": {
            "capture_contract_executable": infrastructure_pass,
            "strict_pit_rows_available": counts["strict_pit_valid_rows"],
            "context_coefficient_allowed": ready_for_fit,
            "probability_mutation_allowed": False,
            "current_match_probability_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
            "formal_weight": 0,
            "fixed_outputs": ["总进球分布不可用。", "精确比分不可用。"],
        },
        "governance": config["hard_limits"],
    }


def make_context_item(freeze: datetime, subject: str) -> dict[str, Any]:
    item = {
        "category": "availability",
        "subject_team": subject,
        "claim": f"{subject} availability checked",
        "source_name": "official specimen",
        "source_tier": "tier_1_official",
        "provider_group": f"official-{subject}",
        "source_url": f"https://example.test/{subject}",
        "article_published_at_utc": "2026-08-05T10:00:00+00:00",
        "article_updated_at_utc": "2026-08-05T10:00:00+00:00",
        "available_at_utc": "2026-08-05T10:00:00+00:00",
        "observed_at_utc": freeze.isoformat(),
    }
    item["evidence_sha256"] = evidence_hash(item)
    return item


def make_valid_event(config: dict[str, Any]) -> dict[str, Any]:
    freeze = datetime(2026, 8, 5, 11, 0, tzinfo=timezone.utc)
    fixture = {
        "competition_id": "SPECIMEN_LEAGUE",
        "competition_name": "Specimen League",
        "round": "1",
        "kickoff_at_utc": "2026-08-05T12:00:00+00:00",
        "kickoff_timezone_source_url": "https://example.test/fixture",
        "home_team": "Home FC",
        "away_team": "Away FC",
        "venue_name": "Specimen Stadium",
        "neutral_venue": False,
        "settlement_scope": config["fixture_identity_contract"]["settlement_scope_required_value"],
        "two_legged_tie": False,
        "first_leg_status": "not_applicable",
    }
    common = {
        "original_quote_at_utc": "2026-08-05T10:58:00+00:00",
        "observed_at_utc": "2026-08-05T10:59:00+00:00",
        "source_url": "https://example.test/market",
        "provider_name": "Specimen Book",
        "provider_group": "specimen-book-group",
        "executable_or_tradable": True,
    }
    event = {
        "sequence": 1,
        "event_id": str(uuid.UUID("00000000-0000-4000-8000-000000000001")),
        "event_type": config["append_only_ledger_contract"]["event_type_required_value"],
        "fixture_identity": fixture,
        "freeze": {
            "freeze_at_utc": freeze.isoformat(),
            "collector_observed_at_utc": "2026-08-05T10:59:00+00:00",
            "packet_created_at_utc": "2026-08-05T11:00:05+00:00",
        },
        "market_snapshot": {
            "one_x_two": {**common, "prices": {"home": 2.10, "draw": 3.30, "away": 3.60}},
            "asian_handicap": {**common, "line": -0.25, "prices": {"home": 1.95, "away": 1.95}},
            "over_under": {**common, "line": 2.5, "prices": {"over": 1.90, "under": 2.00}},
        },
        "context_evidence": [
            make_context_item(freeze, "home"), make_context_item(freeze, "away")
        ],
        "missing_semantics": {
            "official_lineup": "unknown_not_observed",
            "injury": "observed_no_named_absence",
        },
        "governance": {
            "probability_mutated": False,
            "model_fitted": False,
            "provider_request_in_ci": False,
            "exact_score_generated": False,
            "ev_generated": False,
        },
        "previous_event_sha256": None,
    }
    event["event_sha256"] = event_hash(event)
    return event


def self_test(config: dict[str, Any]) -> None:
    valid = make_valid_event(config)
    receipt = run_audit(config, {"events": [valid]})
    assert receipt["infrastructure_pass"] is True
    assert receipt["counts"]["strict_pit_valid_rows"] == 1
    broken = json.loads(json.dumps(valid))
    broken["market_snapshot"]["one_x_two"]["observed_at_utc"] = "2026-08-05T11:01:00+00:00"
    broken["event_sha256"] = event_hash(broken)
    failure = run_audit(config, {"events": [broken]})
    assert failure["infrastructure_pass"] is False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--inbox", type=Path, default=DEFAULT_INBOX)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    config = load_json(args.config)
    if args.self_test:
        self_test(config)
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    result = run_audit(config, load_json(args.inbox))
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
