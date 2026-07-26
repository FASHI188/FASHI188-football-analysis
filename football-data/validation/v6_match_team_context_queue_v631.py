#!/usr/bin/env python3
"""Validate V6.31 match-team-context events and build the unresolved future fixture queue.

The queue is derived from the existing prospective market ledger. It never fetches external web
content by itself. Question-time/manual research may append a context event only when the evidence
was observed before kickoff. This script checks event hash-chain integrity, exact match binding,
source timestamps and semantic gates for roster, manager, availability and predicted XI.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config" / "v6_match_team_context_contract_v631.json"
MARKET = ROOT / "forward" / "v6_market_first_events_v651.json"
LEDGER = ROOT / "forward" / "v6_match_team_context_events_v631.json"
OUT = ROOT / "manifests" / "v6_match_team_context_queue_v631_status.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_dt(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def canonical_hash(event_without_hash: dict[str, Any]) -> str:
    blob = json.dumps(event_without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def market_predictions() -> dict[str, dict[str, Any]]:
    payload = load(MARKET)
    out = {}
    for event in payload.get("events") or []:
        if event.get("event_type") != "MARKET_PREDICTION_FROZEN":
            continue
        mid = str(event.get("match_id") or "")
        fixture = ((event.get("payload") or {}).get("fixture_identity") or {})
        if mid and fixture:
            out[mid] = {"event": event, "fixture": fixture}
    return out


def validate_source(source: dict[str, Any], freeze: datetime, kickoff: datetime) -> list[str]:
    errors = []
    for key in ("source_name", "source_url", "source_observed_at_utc", "source_role"):
        if not source.get(key):
            errors.append(f"source_missing_{key}")
    try:
        obs = parse_dt(source.get("source_observed_at_utc"))
        if obs > freeze:
            errors.append("source_observed_after_freeze")
        if obs >= kickoff:
            errors.append("source_observed_at_or_after_kickoff")
    except Exception:
        errors.append("invalid_source_timestamp")
    return errors


def validate_axis(team: dict[str, Any], axis: str) -> list[str]:
    errors = []
    item = team.get(axis)
    if not isinstance(item, dict):
        return [f"missing_{axis}"]
    status = str(item.get("status") or "")
    if not status:
        errors.append(f"{axis}_missing_status")
    sources = item.get("sources") or []
    if not isinstance(sources, list):
        errors.append(f"{axis}_sources_not_list")
    if axis == "roster" and status == "STRICT_CURRENT":
        players = item.get("players") or []
        if not isinstance(players, list) or len(players) < 18:
            errors.append("strict_roster_below_18")
        if not sources:
            errors.append("strict_roster_without_source")
    if axis == "manager" and status == "VERIFIED" and not sources:
        errors.append("verified_manager_without_source")
    if axis == "availability" and status in {"PIT_VERIFIED_NONEMPTY", "PIT_VERIFIED_EMPTY"}:
        if not sources:
            errors.append("verified_availability_without_source")
        records = item.get("records") or []
        if status == "PIT_VERIFIED_NONEMPTY" and not records:
            errors.append("availability_nonempty_status_without_records")
        if status == "PIT_VERIFIED_EMPTY" and records:
            errors.append("availability_empty_status_with_records")
    if axis == "predicted_xi" and status == "PIT_PREDICTED_XI":
        players = item.get("players") or []
        if not isinstance(players, list) or len(players) < 11:
            errors.append("predicted_xi_below_11")
        if not sources:
            errors.append("predicted_xi_without_source")
    return errors


def validate_context_events(markets: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    payload = load(LEDGER)
    events = payload.get("events") or []
    errors = []
    latest_valid = {}
    previous_hash = "GENESIS"
    expected_sequence = 1
    for event in events:
        event_errors = []
        seq = int(event.get("sequence") or 0)
        mid = str(event.get("match_id") or "")
        if seq != expected_sequence:
            event_errors.append("sequence_mismatch")
        if str(event.get("previous_event_hash") or "") != previous_hash:
            event_errors.append("previous_event_hash_mismatch")
        if event.get("event_type") != "MATCH_TEAM_CONTEXT_FROZEN":
            event_errors.append("unexpected_event_type")
        if mid not in markets:
            event_errors.append("market_match_id_not_found")
        try:
            freeze = parse_dt(event.get("event_timestamp_utc"))
        except Exception:
            freeze = None
            event_errors.append("invalid_event_timestamp")
        payload0 = event.get("payload") or {}
        fixture = payload0.get("fixture_identity") or {}
        market_fixture = markets.get(mid, {}).get("fixture") or {}
        if market_fixture and fixture != market_fixture:
            event_errors.append("fixture_identity_not_exact_market_binding")
        try:
            kickoff = parse_dt(fixture.get("kickoff_at"))
            if freeze is not None and freeze >= kickoff:
                event_errors.append("context_freeze_at_or_after_kickoff")
        except Exception:
            kickoff = None
            event_errors.append("invalid_kickoff")
        for side in ("home_context", "away_context"):
            team = payload0.get(side)
            if not isinstance(team, dict):
                event_errors.append(f"missing_{side}")
                continue
            for axis in ("roster", "manager", "availability", "predicted_xi"):
                event_errors.extend(f"{side}:{x}" for x in validate_axis(team, axis))
                item = team.get(axis) if isinstance(team.get(axis), dict) else {}
                if freeze is not None and kickoff is not None:
                    for source in item.get("sources") or []:
                        if isinstance(source, dict):
                            event_errors.extend(f"{side}:{axis}:{x}" for x in validate_source(source, freeze, kickoff))
        stored_hash = str(event.get("event_hash") or "")
        without_hash = dict(event)
        without_hash.pop("event_hash", None)
        calculated = canonical_hash(without_hash)
        if stored_hash != calculated:
            event_errors.append("event_hash_mismatch")
        if not event_errors and freeze is not None:
            prior = latest_valid.get(mid)
            if prior is None or freeze > prior[0]:
                latest_valid[mid] = (freeze, event)
        else:
            errors.append({"sequence": seq, "match_id": mid, "errors": event_errors})
        previous_hash = stored_hash or calculated
        expected_sequence += 1
    return errors, {k: v[1] for k, v in latest_valid.items()}


def context_completeness(event: dict[str, Any] | None) -> str:
    if not event:
        return "NO_CONTEXT_EVENT"
    payload = event.get("payload") or {}
    teams = [payload.get("home_context") or {}, payload.get("away_context") or {}]
    roster = all((t.get("roster") or {}).get("status") == "STRICT_CURRENT" for t in teams)
    manager = all((t.get("manager") or {}).get("status") == "VERIFIED" for t in teams)
    availability = all((t.get("availability") or {}).get("status") in {"PIT_VERIFIED_NONEMPTY", "PIT_VERIFIED_EMPTY"} for t in teams)
    xi = all((t.get("predicted_xi") or {}).get("status") == "PIT_PREDICTED_XI" for t in teams)
    if roster and manager and availability and xi:
        return "FULL_PIT_CONTEXT"
    if roster and manager and availability:
        return "ROSTER_MANAGER_AVAILABILITY_NO_XI"
    if roster and manager:
        return "ROSTER_MANAGER_ONLY"
    if roster:
        return "ROSTER_ONLY"
    return "PARTIAL_CONTEXT"


def main() -> int:
    _ = load(CONTRACT)
    markets = market_predictions()
    errors, latest = validate_context_events(markets)
    now = datetime.now(timezone.utc)
    queue = []
    for mid, item in markets.items():
        fixture = item["fixture"]
        try:
            kickoff = parse_dt(fixture["kickoff_at"])
        except Exception:
            continue
        if kickoff <= now:
            continue
        event = latest.get(mid)
        queue.append({
            "match_id": mid,
            "competition_id": fixture.get("competition_id"),
            "season": fixture.get("season"),
            "kickoff_at": kickoff.isoformat(),
            "home_team": fixture.get("home_team"),
            "away_team": fixture.get("away_team"),
            "context_state": context_completeness(event),
            "latest_context_frozen_at_utc": event.get("event_timestamp_utc") if event else None,
        })
    queue.sort(key=lambda x: (x["kickoff_at"], x["competition_id"], x["match_id"]))
    states = {}
    for row in queue:
        states[row["context_state"]] = states.get(row["context_state"], 0) + 1
    out = {
        "schema_version": "V6.31.0-match-team-context-queue-status-r1",
        "generated_at_utc": now.replace(microsecond=0).isoformat(),
        "status": "PASS" if not errors else "WARN",
        "formal_current_version": "V5.0.1",
        "market_prediction_match_count": len(markets),
        "valid_context_match_count": len(latest),
        "future_market_match_count": len(queue),
        "future_context_state_counts": states,
        "future_queue": queue,
        "context_validation_errors": errors,
        "next_action": "For a user-requested future match, research PIT roster/manager/availability/predicted-XI evidence and append a hash-chained MATCH_TEAM_CONTEXT_FROZEN event before kickoff. Do not backfill after kickoff.",
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "probability_mutation": False,
            "post_kickoff_backfill": False,
            "current_rule_change": False
        }
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("status", "market_prediction_match_count", "valid_context_match_count", "future_market_match_count", "future_context_state_counts")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
