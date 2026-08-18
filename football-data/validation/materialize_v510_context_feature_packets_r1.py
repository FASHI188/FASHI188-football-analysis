#!/usr/bin/env python3
"""Materialize audited V5.1 context feature packets from existing frozen web evidence.

This is the input-interface layer that the old route lacked. It converts every existing
CONTEXT_DECISION_FROZEN event into a deterministic numeric/categorical packet with explicit
missing semantics, source timing, market features and an integrity hash. It does not learn
coefficients and does not mutate probabilities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import audit_v510_pit_context_readiness_r1 as audit

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v510_context_feature_packet_r1.json"
CONTEXT = ROOT / "forward" / "v6_context_enriched_events_v6486.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_context_feature_packets_r1_status.json"
DIRECTIONS = ("home", "draw", "away")


class PacketError(RuntimeError):
    pass


def canonical_sha256(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_optional_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalized_list(container: dict[str, Any], key: str) -> tuple[list[str] | None, str]:
    if key not in container:
        return None, "unknown_not_observed"
    raw = container.get(key)
    if not isinstance(raw, list):
        return None, "invalid_non_list"
    values = [str(value).strip() for value in raw if str(value).strip()]
    if not values:
        return [], "observed_explicit_empty"
    return values, "observed_nonempty"


def xi_state(values: list[str] | None, explicit_marker: bool, expected: int) -> str:
    if values is None:
        return "unknown_not_observed"
    if not values:
        return "observed_no_predicted_xi"
    if len(values) == expected and explicit_marker:
        return "complete_predicted_xi"
    return "partial_or_malformed_predicted_xi"


def availability_state(values: list[str] | None) -> str:
    if values is None:
        return "unknown_not_observed"
    if not values:
        return "observed_no_named_absence_or_unavailability"
    return "named_availability_items_present"


def devig_with_overround(odds: dict[str, Any]) -> tuple[dict[str, float], float]:
    inverse: dict[str, float] = {}
    for direction in DIRECTIONS:
        try:
            price = float(odds[direction])
        except (KeyError, TypeError, ValueError) as exc:
            raise PacketError(f"invalid 1X2 price {direction}: {odds}") from exc
        if not math.isfinite(price) or price <= 1.0:
            raise PacketError(f"invalid decimal price {direction}={price}")
        inverse[direction] = 1.0 / price
    overround = sum(inverse.values())
    probabilities = {direction: inverse[direction] / overround for direction in DIRECTIONS}
    if abs(sum(probabilities.values()) - 1.0) > 1e-12:
        raise PacketError("devig probability conservation failure")
    return probabilities, overround


def entropy(probabilities: dict[str, float]) -> float:
    return -sum(value * math.log(value) for value in probabilities.values() if value > 0)


def two_way_market(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"available": False, "line": None, "side_a_price": None, "side_b_price": None}
    line = raw.get("line")
    side_pairs = (("home", "away"), ("over", "under"))
    side_a = side_b = None
    for left, right in side_pairs:
        if left in raw or right in raw:
            side_a, side_b = raw.get(left), raw.get(right)
            break
    try:
        a = float(side_a)
        b = float(side_b)
        valid_prices = math.isfinite(a) and math.isfinite(b) and a > 1.0 and b > 1.0
    except (TypeError, ValueError):
        a = b = None
        valid_prices = False
    try:
        parsed_line = float(line)
        line_valid = math.isfinite(parsed_line)
    except (TypeError, ValueError):
        parsed_line = None
        line_valid = False
    return {
        "available": bool(valid_prices and line_valid),
        "line": parsed_line,
        "side_a_price": a,
        "side_b_price": b,
    }


def source_weight(tier: str, weights: dict[str, Any]) -> float:
    if tier in weights:
        return float(weights[tier])
    if tier.startswith("tier_1"):
        return 1.0
    if tier.startswith("tier_2"):
        return 0.75
    if tier.startswith("tier_3"):
        return 0.5
    return float(weights.get("unknown", 0.25))


def build_packet(event: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    fixture = payload.get("fixture_identity") or {}
    identity = audit.identity_key(fixture)
    freeze = audit.parse_ts(payload.get("decision_freeze_at_utc") or event.get("event_timestamp_utc"), "decision_freeze_at_utc")
    kickoff = audit.parse_ts(fixture.get("kickoff_at"), "kickoff_at")
    market = payload.get("market") or {}
    market_observed = audit.parse_ts(market.get("observed_at_utc"), "market.observed_at_utc")
    context = payload.get("context_evidence") or {}
    context_observed = audit.parse_ts(context.get("observed_at_utc") or freeze.isoformat(), "context.observed_at_utc")
    if market_observed > freeze or context_observed > freeze or freeze >= kickoff:
        raise PacketError("PIT chronology failure")

    probabilities, overround = devig_with_overround(market.get("one_x_two") or {})
    pick = max(DIRECTIONS, key=lambda direction: probabilities[direction])
    sorted_probs = sorted(probabilities.values(), reverse=True)
    asian = two_way_market(market.get("asian_handicap"))
    total = two_way_market(market.get("over_under"))

    availability = context.get("availability") if isinstance(context.get("availability"), dict) else {}
    predicted_xi = context.get("predicted_xi") if isinstance(context.get("predicted_xi"), dict) else {}
    markers = context.get("predicted_xi_verified_by_explicit_marker") if isinstance(context.get("predicted_xi_verified_by_explicit_marker"), dict) else {}
    expected_xi = int(config.get("predicted_xi_expected_count", 11))

    home_availability, _ = normalized_list(availability, "home")
    away_availability, _ = normalized_list(availability, "away")
    unassigned_availability, _ = normalized_list(availability, "unassigned")
    home_xi, _ = normalized_list(predicted_xi, "home")
    away_xi, _ = normalized_list(predicted_xi, "away")
    home_xi_marker = markers.get("home") is True
    away_xi_marker = markers.get("away") is True

    source = context.get("source") if isinstance(context.get("source"), dict) else {}
    tier = str(source.get("source_tier") or "unknown")
    article_ts = parse_optional_ts(source.get("article_last_updated") or source.get("article_published"))
    article_age_hours = (context_observed - article_ts).total_seconds() / 3600.0 if article_ts else None

    core = {
        "schema_version": "V5.1.0-context-feature-packet-r1",
        "fixture_identity": {
            "competition_id": identity[0],
            "kickoff_at_utc": identity[1],
            "home_team": fixture.get("home_team"),
            "away_team": fixture.get("away_team"),
            "normalized_home_team": identity[2],
            "normalized_away_team": identity[3],
        },
        "freeze": {
            "decision_freeze_at_utc": freeze.isoformat(),
            "market_observed_at_utc": market_observed.isoformat(),
            "context_observed_at_utc": context_observed.isoformat(),
            "market_age_minutes_at_freeze": (freeze - market_observed).total_seconds() / 60.0,
            "context_age_minutes_at_freeze": (freeze - context_observed).total_seconds() / 60.0,
            "lead_hours_to_kickoff": (kickoff - freeze).total_seconds() / 3600.0,
        },
        "market_features": {
            "raw_1x2_odds": {direction: float((market.get("one_x_two") or {})[direction]) for direction in DIRECTIONS},
            "devig_probabilities": probabilities,
            "overround": overround,
            "market_pick": pick,
            "pmax": probabilities[pick],
            "pdraw": probabilities["draw"],
            "top1_top2_gap": sorted_probs[0] - sorted_probs[1],
            "home_away_probability_gap": probabilities["home"] - probabilities["away"],
            "entropy_nats": entropy(probabilities),
            "asian_handicap": asian,
            "over_under": total,
            "synchronized_three_market_complete": bool(asian["available"] and total["available"]),
            "provider_name": market.get("provider_name"),
            "provider_group": market.get("provider_group"),
        },
        "context_features": {
            "source_tier": tier,
            "source_tier_weight": source_weight(tier, config.get("source_tier_weights") or {}),
            "provider_group": source.get("provider_group"),
            "source_name": source.get("source_name"),
            "source_url": source.get("source_url"),
            "article_timestamp_utc": article_ts.isoformat() if article_ts else None,
            "article_age_hours_at_observation": article_age_hours,
            "home_availability_count": len(home_availability) if home_availability is not None else None,
            "away_availability_count": len(away_availability) if away_availability is not None else None,
            "unassigned_availability_count": len(unassigned_availability) if unassigned_availability is not None else None,
            "home_availability_state": availability_state(home_availability),
            "away_availability_state": availability_state(away_availability),
            "unassigned_availability_state": availability_state(unassigned_availability),
            "home_predicted_xi_count": len(home_xi) if home_xi is not None else None,
            "away_predicted_xi_count": len(away_xi) if away_xi is not None else None,
            "home_predicted_xi_state": xi_state(home_xi, home_xi_marker, expected_xi),
            "away_predicted_xi_state": xi_state(away_xi, away_xi_marker, expected_xi),
            "home_predicted_xi_explicit_marker": home_xi_marker,
            "away_predicted_xi_explicit_marker": away_xi_marker,
            "both_predicted_xi_complete": bool(
                xi_state(home_xi, home_xi_marker, expected_xi) == "complete_predicted_xi"
                and xi_state(away_xi, away_xi_marker, expected_xi) == "complete_predicted_xi"
            ),
            "both_availability_observed": home_availability is not None and away_availability is not None,
        },
        "missing_semantics": {
            "availability": config.get("availability_semantics"),
            "predicted_xi": config.get("predicted_xi_semantics"),
        },
        "governance": {
            "formal_weight": 0,
            "context_effect_coefficient_applied": False,
            "probability_mutation": False,
            "training_run": False,
            "provider_requests": 0,
            "new_data_collection": False,
        },
    }
    packet = dict(core)
    packet["packet_sha256"] = canonical_sha256(core)
    return packet


def materialize(config: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    events = ledger.get("events")
    if not isinstance(events, list):
        raise PacketError("context ledger has no events list")
    packets: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    identities: Counter[tuple[str, str, str, str]] = Counter()
    tiers: Counter[str] = Counter()
    xi_states: Counter[str] = Counter()
    availability_states: Counter[str] = Counter()
    synchronized_market_count = 0

    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("event_type") != "CONTEXT_DECISION_FROZEN":
            continue
        try:
            packet = build_packet(event, config)
            identity = packet["fixture_identity"]
            key = (
                str(identity["competition_id"]), str(identity["kickoff_at_utc"]),
                str(identity["normalized_home_team"]), str(identity["normalized_away_team"]),
            )
            identities[key] += 1
            tiers[str(packet["context_features"]["source_tier"])] += 1
            xi_states[str(packet["context_features"]["home_predicted_xi_state"])] += 1
            xi_states[str(packet["context_features"]["away_predicted_xi_state"])] += 1
            availability_states[str(packet["context_features"]["home_availability_state"])] += 1
            availability_states[str(packet["context_features"]["away_availability_state"])] += 1
            synchronized_market_count += int(packet["market_features"]["synchronized_three_market_complete"])
            packets.append(packet)
        except Exception as exc:
            errors.append({"event_index": index, "reason": f"{type(exc).__name__}: {exc}"})

    duplicates = [list(key) for key, count in identities.items() if count != 1]
    hashes = [packet["packet_sha256"] for packet in packets]
    duplicate_hashes = [value for value, count in Counter(hashes).items() if count != 1]
    checks = {
        "at_least_one_packet": len(packets) > 0,
        "all_context_events_materialized": len(packets) + len(errors) == sum(
            isinstance(event, dict) and event.get("event_type") == "CONTEXT_DECISION_FROZEN" for event in events
        ),
        "no_materialization_errors": not errors,
        "unique_fixture_identity": not duplicates,
        "unique_packet_hash": not duplicate_hashes,
        "all_packet_hashes_present": all(len(value) == 64 for value in hashes),
        "all_probability_sums_valid": all(
            abs(sum(packet["market_features"]["devig_probabilities"].values()) - 1.0) <= 1e-12 for packet in packets
        ),
    }
    passed = all(checks.values())
    return {
        "schema_version": "V5.1.0-context-feature-packet-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS_MODEL_INPUT_PACKETS_READY" if passed else "FAIL_CONTEXT_PACKET_AUDIT",
        "classification": "MODEL_READY_INPUT_INTERFACE_NO_CONTEXT_COEFFICIENT",
        "counts": {
            "packet_count": len(packets),
            "materialization_errors": len(errors),
            "duplicate_fixture_identities": len(duplicates),
            "duplicate_packet_hashes": len(duplicate_hashes),
            "complete_predicted_xi_both_sides": sum(packet["context_features"]["both_predicted_xi_complete"] for packet in packets),
            "both_availability_observed": sum(packet["context_features"]["both_availability_observed"] for packet in packets),
            "synchronized_1x2_ah_ou_complete": synchronized_market_count,
        },
        "distributions": {
            "source_tiers": dict(sorted(tiers.items())),
            "side_predicted_xi_states": dict(sorted(xi_states.items())),
            "side_availability_states": dict(sorted(availability_states.items())),
        },
        "audit": {"passed": passed, "checks": checks, "errors": errors, "duplicate_identities": duplicates, "duplicate_hashes": duplicate_hashes},
        "packets": packets,
        "governance": {
            "formal_weight": 0,
            "context_effect_coefficient_applied": False,
            "probability_generation": False,
            "training_run": False,
            "provider_requests": 0,
            "new_data_collection": False,
            "current_rule_change": False,
        },
        "next_action": "join packets to settled results only after the frozen context fit gate is satisfied",
    }


def self_test() -> None:
    config = audit.load_json(CONFIG)
    event = {
        "event_type": "CONTEXT_DECISION_FROZEN",
        "event_timestamp_utc": "2026-01-01T10:00:00+00:00",
        "payload": {
            "fixture_identity": {"competition_id": "X", "kickoff_at": "2026-01-01T12:00:00+00:00", "home_team": "Home", "away_team": "Away"},
            "decision_freeze_at_utc": "2026-01-01T10:00:00+00:00",
            "market": {
                "observed_at_utc": "2026-01-01T09:55:00+00:00",
                "one_x_two": {"home": 2.0, "draw": 3.5, "away": 4.0},
                "asian_handicap": {"line": -0.5, "home": 1.9, "away": 1.9},
                "over_under": {"line": 2.5, "over": 1.9, "under": 1.9},
            },
            "context_evidence": {
                "observed_at_utc": "2026-01-01T10:00:00+00:00",
                "source": {"source_tier": "tier_2_editorial_preview"},
                "availability": {"home": [], "away": ["Player"], "unassigned": []},
                "predicted_xi": {"home": [str(i) for i in range(11)], "away": [str(i) for i in range(11)]},
                "predicted_xi_verified_by_explicit_marker": {"home": True, "away": True},
            },
        },
    }
    packet = build_packet(event, config)
    assert packet["context_features"]["both_predicted_xi_complete"] is True
    assert packet["context_features"]["home_availability_state"] == "observed_no_named_absence_or_unavailability"
    assert abs(sum(packet["market_features"]["devig_probabilities"].values()) - 1.0) < 1e-12
    assert len(packet["packet_sha256"]) == 64


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--context", type=Path, default=CONTEXT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "PASS", "self_test": True}, ensure_ascii=False))
        return 0
    payload = materialize(audit.load_json(args.config), audit.load_json(args.context))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "counts": payload["counts"],
        "distributions": payload["distributions"],
        "audit": payload["audit"],
    }, ensure_ascii=False, indent=2))
    return 0 if payload["audit"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
