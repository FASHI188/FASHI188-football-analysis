#!/usr/bin/env python3
"""V6.31.1 compact context seeder.

Reuses the V6.31 PIT snapshot selection/source-time logic but stores strict rosters as immutable
snapshot references (player_count + snapshot_file + SHA-256) instead of copying the whole roster into
every fixture event. No network access; availability/XI remain UNKNOWN/UNAVAILABLE unless a dedicated
pre-kickoff source already exists in the frozen snapshot.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import v6_seed_match_team_context_v631 as base

OUT = base.ROOT / "manifests" / "v6_seed_match_team_context_v6311_status.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def empty_context(team: str) -> dict[str, Any]:
    return {
        "team_name": team,
        "roster": {"status": "UNAVAILABLE", "player_count": 0, "sources": []},
        "manager": {"status": "UNAVAILABLE", "head_coach": None, "sources": []},
        "availability": {"status": "UNKNOWN", "records": [], "sources": []},
        "predicted_xi": {"status": "UNAVAILABLE", "players": [], "sources": []},
    }


def compact_team_context(cid: str, team: str, snapshots, freeze: datetime, kickoff: datetime) -> dict[str, Any]:
    row = snapshots.get((cid, team))
    if not row:
        return empty_context(team)
    observed, snapshot, filename = row
    if observed > freeze or observed >= kickoff:
        return empty_context(team)
    sources = base.clean_sources(snapshot, freeze, kickoff)
    path = base.SNAPSHOT_ROOT / filename
    players = snapshot.get("players") if isinstance(snapshot.get("players"), list) else []
    if len(players) >= 18 and sources and path.exists():
        roster = {
            "status": "STRICT_CURRENT",
            "player_count": len(players),
            "snapshot_file": filename,
            "snapshot_sha256": sha256_file(path),
            "sources": sources,
        }
    else:
        roster = {"status": "UNAVAILABLE", "player_count": len(players), "snapshot_file": filename, "sources": []}

    coach = snapshot.get("head_coach")
    manager = (
        {"status": "VERIFIED", "head_coach": coach, "sources": sources}
        if coach and sources
        else {"status": "UNAVAILABLE", "head_coach": None, "sources": []}
    )

    availability_records = snapshot.get("availability") if isinstance(snapshot.get("availability"), list) else []
    availability_sources = [
        s for s in sources
        if any(t in str(s.get("source_role") or "").lower() for t in ("injury", "availability", "suspension", "fitness"))
    ]
    if availability_sources:
        availability = {
            "status": "PIT_VERIFIED_NONEMPTY" if availability_records else "PIT_VERIFIED_EMPTY",
            "records": availability_records,
            "sources": availability_sources,
        }
    else:
        availability = {"status": "UNKNOWN", "records": [], "sources": []}

    predicted = base.list_field(snapshot, ("predicted_xi", "expected_xi", "projected_xi", "probable_xi"))
    xi_sources = [
        s for s in sources
        if any(t in str(s.get("source_role") or "").lower() for t in (
            "predicted xi", "expected xi", "projected xi", "probable xi", "predicted lineup", "projected lineup"
        ))
    ]
    xi = (
        {"status": "PIT_PREDICTED_XI", "players": predicted, "sources": xi_sources}
        if len(predicted) >= 11 and xi_sources
        else {"status": "UNAVAILABLE", "players": [], "sources": []}
    )
    return {"team_name": team, "roster": roster, "manager": manager, "availability": availability, "predicted_xi": xi}


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ledger = base.load(base.LEDGER)
    events = list(ledger.get("events") or [])
    snapshots = base.latest_snapshots()
    existing = {str(e.get("match_id") or "") for e in events if e.get("event_type") == "MATCH_TEAM_CONTEXT_FROZEN"}
    previous_hash = str(events[-1].get("event_hash")) if events else "GENESIS"
    sequence = len(events) + 1
    added: list[str] = []
    skipped_started = 0
    skipped_existing = 0

    for item in base.market_predictions():
        mid = item["match_id"]
        fixture = item["fixture"]
        try:
            kickoff = base.parse_dt(fixture.get("kickoff_at"))
        except Exception:
            continue
        if kickoff <= now:
            skipped_started += 1
            continue
        if mid in existing:
            skipped_existing += 1
            continue
        cid = str(fixture.get("competition_id") or "")
        event = {
            "schema_version": "V6.31.1-match-team-context-event-r2",
            "sequence": sequence,
            "event_type": "MATCH_TEAM_CONTEXT_FROZEN",
            "event_timestamp_utc": now.isoformat(),
            "match_id": mid,
            "previous_event_hash": previous_hash,
            "payload": {
                "fixture_identity": fixture,
                "context_source_mode": "REPOSITORY_PIT_SNAPSHOT_SHA256_REFERENCE_NO_NETWORK",
                "home_context": compact_team_context(cid, str(fixture.get("home_team") or ""), snapshots, now, kickoff),
                "away_context": compact_team_context(cid, str(fixture.get("away_team") or ""), snapshots, now, kickoff),
            },
        }
        event["event_hash"] = base.hash_event(event)
        events.append(event)
        added.append(mid)
        existing.add(mid)
        previous_hash = event["event_hash"]
        sequence += 1

    ledger["schema_version"] = "V6.31.1-match-team-context-ledger-r2"
    ledger["events"] = events
    base.write(base.LEDGER, ledger)

    added_events = [e for e in events if str(e.get("match_id") or "") in set(added)]
    state_counts: dict[str, int] = {}
    axis_counts = {"strict_roster": 0, "verified_manager": 0, "verified_availability": 0, "predicted_xi": 0}
    for e in added_events:
        teams = [e["payload"]["home_context"], e["payload"]["away_context"]]
        for t in teams:
            axis_counts["strict_roster"] += int(str(t["roster"].get("status")) == "STRICT_CURRENT")
            axis_counts["verified_manager"] += int(str(t["manager"].get("status")) == "VERIFIED")
            axis_counts["verified_availability"] += int(str(t["availability"].get("status", "")).startswith("PIT_VERIFIED"))
            axis_counts["predicted_xi"] += int(str(t["predicted_xi"].get("status")) == "PIT_PREDICTED_XI")
        strict = sum(t["roster"].get("status") == "STRICT_CURRENT" for t in teams)
        state = "BOTH_ROSTERS_STRICT" if strict == 2 else "ONE_ROSTER_STRICT" if strict == 1 else "NO_STRICT_ROSTER"
        state_counts[state] = state_counts.get(state, 0) + 1

    status = {
        "schema_version": "V6.31.1-match-context-snapshot-seed-status-r2",
        "generated_at_utc": now.isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "added_context_events": len(added),
        "added_match_ids": added,
        "skipped_already_started_market_events": skipped_started,
        "skipped_existing_context_matches": skipped_existing,
        "seed_state_counts": state_counts,
        "seed_team_axis_counts": axis_counts,
        "ledger_event_count": len(events),
        "ledger_bytes": base.LEDGER.stat().st_size,
        "semantics": {
            "network_access": False,
            "full_roster_payload_duplicated_per_match": False,
            "roster_snapshot_sha256_bound": True,
            "availability_unknown_is_not_no_injuries": True,
            "predicted_xi_not_inferred_from_roster": True,
            "post_kickoff_backfill": False,
            "later_pre_kickoff_context_refresh_allowed": True,
        },
        "governance": {"research_only": True, "formal_weight": 0, "probability_mutation": False, "current_rule_change": False},
    }
    base.write(OUT, status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
