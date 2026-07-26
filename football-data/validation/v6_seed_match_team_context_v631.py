#!/usr/bin/env python3
"""Seed V6.31 future-match context events from already frozen repository evidence only.

This script performs NO network access. It reads the prospective market ledger plus the latest
physical V6.6 team snapshots already committed before the fixture kickoff. For every future market
match without a valid context event, it appends a MATCH_TEAM_CONTEXT_FROZEN event.

Conservative semantics:
- roster is STRICT_CURRENT only with >=18 named players and timestamped snapshot sources;
- manager is VERIFIED only when present in that same timestamped snapshot;
- availability is verified only when a dedicated injury/availability/suspension/fitness source role
  exists; otherwise UNKNOWN, including when availability=[];
- predicted XI is emitted only from an explicit predicted/expected/projected/probable XI field plus
  a dedicated predicted-lineup source role;
- no roster overlay or previous-season provisional source is silently promoted here; those may be
  added later by an explicitly source-bound context refresh;
- events are created only while kickoff is still in the future.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "forward" / "v6_market_first_events_v651.json"
LEDGER = ROOT / "forward" / "v6_match_team_context_events_v631.json"
SNAPSHOT_ROOT = ROOT / "evidence" / "team_configuration_weekly"
OUT = ROOT / "manifests" / "v6_seed_match_team_context_v631_status.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_dt(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return dt.astimezone(timezone.utc)


def hash_event(event_without_hash: dict[str, Any]) -> str:
    blob = json.dumps(event_without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def clean_sources(snapshot: dict[str, Any], freeze: datetime, kickoff: datetime) -> list[dict[str, Any]]:
    out = []
    for s in snapshot.get("sources") or []:
        if not isinstance(s, dict):
            continue
        if not s.get("source_name") or not s.get("source_url") or not s.get("source_role"):
            continue
        try:
            obs = parse_dt(s.get("source_observed_at_utc"))
        except Exception:
            continue
        if obs > freeze or obs >= kickoff:
            continue
        out.append({
            "source_name": s.get("source_name"),
            "source_url": s.get("source_url"),
            "source_observed_at_utc": obs.replace(microsecond=0).isoformat(),
            "source_role": s.get("source_role"),
            "source_tier": s.get("source_tier"),
        })
    return out


def latest_snapshots() -> dict[tuple[str, str], tuple[datetime, dict[str, Any], str]]:
    latest = {}
    for path in sorted(SNAPSHOT_ROOT.glob("*.json")) if SNAPSHOT_ROOT.exists() else []:
        if path.name.startswith("weekly_aggregate__"):
            continue
        try:
            x = load(path)
            if not isinstance(x, dict):
                continue
            cid = str(x.get("competition_id") or "")
            team = str(x.get("team_name") or "").strip()
            ts = parse_dt(x.get("observed_at_utc"))
            if not cid or not team:
                continue
            key = (cid, team)
            if key not in latest or ts > latest[key][0]:
                latest[key] = (ts, x, path.name)
        except Exception:
            continue
    return latest


def dedicated_role(sources: list[dict[str, Any]], tokens: tuple[str, ...]) -> bool:
    return any(any(t in str(s.get("source_role") or "").lower() for t in tokens) for s in sources)


def list_field(snapshot: dict[str, Any], names: tuple[str, ...]) -> list[Any]:
    for name in names:
        value = snapshot.get(name)
        if isinstance(value, list):
            return value
    return []


def team_context(cid: str, team: str, snapshots, freeze: datetime, kickoff: datetime) -> dict[str, Any]:
    row = snapshots.get((cid, team))
    if not row:
        return {
            "team_name": team,
            "roster": {"status": "UNAVAILABLE", "players": [], "sources": []},
            "manager": {"status": "UNAVAILABLE", "head_coach": None, "sources": []},
            "availability": {"status": "UNKNOWN", "records": [], "sources": []},
            "predicted_xi": {"status": "UNAVAILABLE", "players": [], "sources": []},
        }
    observed, snapshot, filename = row
    sources = clean_sources(snapshot, freeze, kickoff)
    # Do not use a snapshot observed after the context freeze or kickoff even if individual source
    # rows happen to be older: that composite snapshot did not exist yet.
    if observed > freeze or observed >= kickoff:
        return {
            "team_name": team,
            "roster": {"status": "UNAVAILABLE", "players": [], "sources": []},
            "manager": {"status": "UNAVAILABLE", "head_coach": None, "sources": []},
            "availability": {"status": "UNKNOWN", "records": [], "sources": []},
            "predicted_xi": {"status": "UNAVAILABLE", "players": [], "sources": []},
        }
    players = snapshot.get("players") if isinstance(snapshot.get("players"), list) else []
    if len(players) >= 18 and sources:
        roster = {"status": "STRICT_CURRENT", "players": players, "snapshot_file": filename, "sources": sources}
    else:
        roster = {"status": "UNAVAILABLE", "players": [], "snapshot_file": filename, "sources": []}
    coach = snapshot.get("head_coach")
    manager = {"status": "VERIFIED", "head_coach": coach, "sources": sources} if coach and sources else {"status": "UNAVAILABLE", "head_coach": None, "sources": []}

    availability_records = snapshot.get("availability") if isinstance(snapshot.get("availability"), list) else []
    availability_sources = [s for s in sources if any(t in str(s.get("source_role") or "").lower() for t in ("injury", "availability", "suspension", "fitness"))]
    if availability_sources:
        av_status = "PIT_VERIFIED_NONEMPTY" if availability_records else "PIT_VERIFIED_EMPTY"
        availability = {"status": av_status, "records": availability_records, "sources": availability_sources}
    else:
        availability = {"status": "UNKNOWN", "records": [], "sources": []}

    predicted = list_field(snapshot, ("predicted_xi", "expected_xi", "projected_xi", "probable_xi"))
    xi_sources = [s for s in sources if any(t in str(s.get("source_role") or "").lower() for t in ("predicted xi", "expected xi", "projected xi", "probable xi", "predicted lineup", "projected lineup"))]
    if len(predicted) >= 11 and xi_sources:
        xi = {"status": "PIT_PREDICTED_XI", "players": predicted, "sources": xi_sources}
    else:
        xi = {"status": "UNAVAILABLE", "players": [], "sources": []}
    return {"team_name": team, "roster": roster, "manager": manager, "availability": availability, "predicted_xi": xi}


def market_predictions() -> list[dict[str, Any]]:
    out = []
    for e in load(MARKET).get("events") or []:
        if e.get("event_type") != "MARKET_PREDICTION_FROZEN":
            continue
        fixture = ((e.get("payload") or {}).get("fixture_identity") or {})
        if e.get("match_id") and fixture:
            out.append({"match_id": str(e["match_id"]), "fixture": fixture})
    return out


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ledger = load(LEDGER)
    events = ledger.get("events") or []
    snapshots = latest_snapshots()
    context_matches = {str(e.get("match_id") or "") for e in events if e.get("event_type") == "MATCH_TEAM_CONTEXT_FROZEN"}
    previous_hash = str(events[-1].get("event_hash")) if events else "GENESIS"
    sequence = len(events) + 1
    added = []
    skipped_started = 0
    skipped_existing = 0
    for item in market_predictions():
        mid = item["match_id"]
        fixture = item["fixture"]
        try:
            kickoff = parse_dt(fixture.get("kickoff_at"))
        except Exception:
            continue
        if kickoff <= now:
            skipped_started += 1
            continue
        if mid in context_matches:
            skipped_existing += 1
            continue
        home = str(fixture.get("home_team") or "")
        away = str(fixture.get("away_team") or "")
        event = {
            "schema_version": "V6.31.0-match-team-context-event-r1",
            "sequence": sequence,
            "event_type": "MATCH_TEAM_CONTEXT_FROZEN",
            "event_timestamp_utc": now.isoformat(),
            "match_id": mid,
            "previous_event_hash": previous_hash,
            "payload": {
                "fixture_identity": fixture,
                "context_source_mode": "REPOSITORY_PIT_SNAPSHOT_SEED_NO_NETWORK",
                "home_context": team_context(str(fixture.get("competition_id") or ""), home, snapshots, now, kickoff),
                "away_context": team_context(str(fixture.get("competition_id") or ""), away, snapshots, now, kickoff),
            },
        }
        event["event_hash"] = hash_event(event)
        events.append(event)
        added.append(mid)
        previous_hash = event["event_hash"]
        sequence += 1
    ledger["events"] = events
    write(LEDGER, ledger)

    state_counts = {}
    roster_strict_teams = 0
    manager_verified_teams = 0
    availability_verified_teams = 0
    xi_verified_teams = 0
    for e in events:
        if e.get("match_id") not in added:
            continue
        teams = [e["payload"]["home_context"], e["payload"]["away_context"]]
        for t in teams:
            roster_strict_teams += int(t["roster"]["status"] == "STRICT_CURRENT")
            manager_verified_teams += int(t["manager"]["status"] == "VERIFIED")
            availability_verified_teams += int(t["availability"]["status"].startswith("PIT_VERIFIED"))
            xi_verified_teams += int(t["predicted_xi"]["status"] == "PIT_PREDICTED_XI")
        if all(t["roster"]["status"] == "STRICT_CURRENT" for t in teams):
            state = "BOTH_ROSTERS_STRICT"
        elif any(t["roster"]["status"] == "STRICT_CURRENT" for t in teams):
            state = "ONE_ROSTER_STRICT"
        else:
            state = "NO_STRICT_ROSTER"
        state_counts[state] = state_counts.get(state, 0) + 1

    status = {
        "schema_version": "V6.31.0-match-context-snapshot-seed-status-r1",
        "generated_at_utc": now.isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "added_context_events": len(added),
        "added_match_ids": added,
        "skipped_already_started_market_events": skipped_started,
        "skipped_existing_context_matches": skipped_existing,
        "seed_state_counts": state_counts,
        "seed_team_axis_counts": {
            "strict_roster": roster_strict_teams,
            "verified_manager": manager_verified_teams,
            "verified_availability": availability_verified_teams,
            "predicted_xi": xi_verified_teams,
        },
        "semantics": {
            "network_access": false,
            "availability_unknown_is_not_no_injuries": true,
            "predicted_xi_not_inferred_from_roster": true,
            "post_kickoff_backfill": false,
            "later_pre_kickoff_context_refresh_allowed": true,
        },
        "governance": {
            "research_only": true,
            "formal_weight": 0,
            "probability_mutation": false,
            "current_rule_change": false,
        },
    }
    write(OUT, status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
