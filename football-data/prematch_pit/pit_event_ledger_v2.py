#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
LEDGER = HERE / "ledger" / "prematch_events_v2.jsonl"

EVENT_TYPES = {
    "confirmed_xi",
    "player_availability",
    "manager_status",
    "formation_observation",
}
AVAILABILITY = {"out", "suspended", "doubtful", "available", "unknown"}
MANAGER_STATUS = {"appointed", "departed", "interim", "confirmed_in_charge", "unknown"}
SOURCE_CLASSES = {"official_league", "official_club", "approved_api", "manual_official_browser"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime:
    x = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if x.tzinfo is None:
        x = x.replace(tzinfo=timezone.utc)
    return x.astimezone(timezone.utc)


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def fail(code: str, **extra):
    return {"eligible": False, "verdict": code, **extra}


def common_gate(event: dict):
    required = [
        "event_type", "source_name", "source_url", "source_class",
        "observed_at_utc", "kickoff_at_utc", "home_team", "away_team",
    ]
    missing = [x for x in required if event.get(x) in (None, "")]
    if missing:
        return fail("STOP_SCHEMA_MISSING_FIELDS", missing=missing)
    if event["event_type"] not in EVENT_TYPES:
        return fail("STOP_UNKNOWN_EVENT_TYPE")
    if event["source_class"] not in SOURCE_CLASSES:
        return fail("STOP_UNAPPROVED_SOURCE_CLASS")
    try:
        observed = parse_utc(event["observed_at_utc"])
        kickoff = parse_utc(event["kickoff_at_utc"])
    except Exception as exc:
        return fail("STOP_INVALID_TIMESTAMP", error=str(exc))
    if not observed < kickoff:
        return fail("STOP_NOT_OBSERVED_PREMATCH")
    return {
        "eligible": True,
        "verdict": "PASS_COMMON_PIT",
        "seconds_before_kickoff": int((kickoff - observed).total_seconds()),
    }


def identity(row: dict) -> str:
    for k in ("player_id", "player_name"):
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def confirmed_xi_gate(event: dict):
    common = common_gate(event)
    if not common["eligible"]:
        return common
    rows = event.get("lineup_rows") or []
    starters = {"home": [], "away": []}
    for row in rows:
        if bool(row.get("is_substitute", False)):
            continue
        side = str(row.get("side") or "").strip().lower()
        pid = identity(row)
        if side in starters and pid:
            starters[side].append(pid)
    uh = sorted(set(starters["home"]))
    ua = sorted(set(starters["away"]))
    if len(uh) != len(starters["home"]) or len(ua) != len(starters["away"]):
        return fail("STOP_DUPLICATE_STARTER_IDENTITY", home_unique=len(uh), away_unique=len(ua))
    if len(uh) != 11 or len(ua) != 11:
        return fail("STOP_XI_NOT_CONFIRMED", home_unique=len(uh), away_unique=len(ua))
    return {**common, "verdict": "PASS_CONFIRMED_XI_PIT", "home_unique": 11, "away_unique": 11}


def availability_gate(event: dict):
    common = common_gate(event)
    if not common["eligible"]:
        return common
    player = event.get("player") or {}
    if not identity(player):
        return fail("STOP_PLAYER_IDENTITY_MISSING")
    team_side = str(event.get("team_side") or "").strip().lower()
    if team_side not in {"home", "away"}:
        return fail("STOP_TEAM_SIDE_INVALID")
    status = str(event.get("availability_status") or "").strip().lower()
    if status not in AVAILABILITY:
        return fail("STOP_AVAILABILITY_STATUS_INVALID")
    reason = str(event.get("availability_reason") or "").strip()
    if status in {"out", "suspended", "doubtful"} and not reason:
        return fail("STOP_AVAILABILITY_REASON_MISSING")
    return {
        **common,
        "verdict": "PASS_PLAYER_AVAILABILITY_PIT",
        "availability_status": status,
        "player_identity": identity(player),
    }


def manager_gate(event: dict):
    common = common_gate(event)
    if not common["eligible"]:
        return common
    team_side = str(event.get("team_side") or "").strip().lower()
    if team_side not in {"home", "away"}:
        return fail("STOP_TEAM_SIDE_INVALID")
    if not str(event.get("manager_name") or "").strip():
        return fail("STOP_MANAGER_IDENTITY_MISSING")
    status = str(event.get("manager_status") or "").strip().lower()
    if status not in MANAGER_STATUS:
        return fail("STOP_MANAGER_STATUS_INVALID")
    return {**common, "verdict": "PASS_MANAGER_STATUS_PIT", "manager_status": status}


def formation_gate(event: dict):
    common = common_gate(event)
    if not common["eligible"]:
        return common
    team_side = str(event.get("team_side") or "").strip().lower()
    if team_side not in {"home", "away"}:
        return fail("STOP_TEAM_SIDE_INVALID")
    formation = str(event.get("formation") or "").strip()
    if not formation:
        return fail("STOP_FORMATION_MISSING")
    if event.get("formation_status") not in {"confirmed", "expected", "historical_tendency"}:
        return fail("STOP_FORMATION_STATUS_INVALID")
    return {**common, "verdict": "PASS_FORMATION_OBSERVATION_PIT", "formation": formation}


def evaluate(event: dict):
    typ = event.get("event_type")
    if typ == "confirmed_xi":
        return confirmed_xi_gate(event)
    if typ == "player_availability":
        return availability_gate(event)
    if typ == "manager_status":
        return manager_gate(event)
    if typ == "formation_observation":
        return formation_gate(event)
    return fail("STOP_UNKNOWN_EVENT_TYPE")


def freeze_record(event: dict):
    gate = evaluate(event)
    body = {
        "schema_version": "football3-prematch-pit-event-v2",
        "event": event,
        "gate": gate,
        "frozen_at_utc": utc_now(),
        "immutability": "APPEND_ONLY_NEVER_OVERWRITE",
    }
    body["record_id"] = digest({"event": event, "gate": gate})
    return body


def append_record(record: dict, path: Path = LEDGER):
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            existing.add(json.loads(line)["record_id"])
    if record["record_id"] in existing:
        return {"appended": False, "reason": "DUPLICATE_RECORD_ID"}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return {"appended": True, "reason": "APPENDED"}


def selftest():
    OUT.mkdir(parents=True, exist_ok=True)
    base = {
        "source_name": "OFFICIAL_TEST",
        "source_url": "https://example.invalid/official",
        "source_class": "official_league",
        "observed_at_utc": "2026-08-27T10:00:00Z",
        "kickoff_at_utc": "2026-08-27T11:00:00Z",
        "home_team": "HOME",
        "away_team": "AWAY",
    }
    rows = []
    for side in ("home", "away"):
        for i in range(11):
            rows.append({"side": side, "player_id": f"{side}-{i}", "is_substitute": False})

    xi = {**base, "event_type": "confirmed_xi", "lineup_rows": rows}
    inj = {
        **base,
        "event_type": "player_availability",
        "team_side": "home",
        "player": {"player_id": "p1", "player_name": "Player One"},
        "availability_status": "out",
        "availability_reason": "hamstring injury",
    }
    susp = {
        **base,
        "event_type": "player_availability",
        "team_side": "away",
        "player": {"player_id": "p2"},
        "availability_status": "suspended",
        "availability_reason": "disciplinary suspension",
    }
    mgr = {
        **base,
        "event_type": "manager_status",
        "team_side": "home",
        "manager_name": "Manager A",
        "manager_status": "confirmed_in_charge",
    }
    form = {
        **base,
        "event_type": "formation_observation",
        "team_side": "away",
        "formation": "4-2-3-1",
        "formation_status": "expected",
    }
    late = dict(inj)
    late["observed_at_utc"] = "2026-08-27T11:01:00Z"
    weak = dict(inj)
    weak["source_class"] = "unverified_blog"
    bad_susp = dict(susp)
    bad_susp["availability_reason"] = ""

    cases = {
        "confirmed_xi": evaluate(xi),
        "injury_out": evaluate(inj),
        "suspension": evaluate(susp),
        "manager": evaluate(mgr),
        "formation": evaluate(form),
        "late_injury": evaluate(late),
        "unapproved_source": evaluate(weak),
        "suspension_without_reason": evaluate(bad_susp),
    }
    assert all(cases[k]["eligible"] for k in ("confirmed_xi", "injury_out", "suspension", "manager", "formation"))
    assert cases["late_injury"]["verdict"] == "STOP_NOT_OBSERVED_PREMATCH"
    assert cases["unapproved_source"]["verdict"] == "STOP_UNAPPROVED_SOURCE_CLASS"
    assert cases["suspension_without_reason"]["verdict"] == "STOP_AVAILABILITY_REASON_MISSING"

    temp = OUT / "_ledger_selftest.jsonl"
    if temp.exists():
        temp.unlink()
    record = freeze_record(inj)
    first = append_record(record, temp)
    second = append_record(record, temp)
    assert first["appended"] is True and second["appended"] is False
    lines = [x for x in temp.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    temp.unlink()

    result = {
        "schema_version": "football3-prematch-pit-event-ledger-v2-selftest",
        "status": "PASS",
        "supported_event_types": sorted(EVENT_TYPES),
        "cases": cases,
        "append_only_duplicate_test": {"first": first, "second": second, "rows_after": 1},
        "model_rule": "Only gate.eligible=true records may become prematch model inputs; semantic status is retained and never inferred beyond the source observation.",
    }
    (OUT / "event_ledger_selftest_v2.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def schema():
    OUT.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": "football3-prematch-pit-event-schema-v2",
        "event_types": {
            "confirmed_xi": {"required_extra": ["lineup_rows"], "gate": "exactly 11 unique non-substitute starters per side"},
            "player_availability": {"required_extra": ["team_side", "player", "availability_status"], "statuses": sorted(AVAILABILITY)},
            "manager_status": {"required_extra": ["team_side", "manager_name", "manager_status"], "statuses": sorted(MANAGER_STATUS)},
            "formation_observation": {"required_extra": ["team_side", "formation", "formation_status"], "statuses": ["confirmed", "expected", "historical_tendency"]},
        },
        "common_required": [
            "event_type", "source_name", "source_url", "source_class", "observed_at_utc",
            "kickoff_at_utc", "home_team", "away_team",
        ],
        "approved_source_classes": sorted(SOURCE_CLASSES),
        "time_rule": "observed_at_utc must be strictly earlier than kickoff_at_utc",
        "provenance_rule": "observed_at is our evidence time; optional source publication time may be stored separately but never substituted when unknown",
        "immutability": "append-only; corrections and later observations create new record_ids",
        "research_rule": "Historical rows without auditable observed_at remain retrospective-only and cannot be converted into formal prematch records.",
    }
    (OUT / "pit_event_schema_v2.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    t = json.loads((OUT / "event_ledger_selftest_v2.json").read_text(encoding="utf-8"))
    s = json.loads((OUT / "pit_event_schema_v2.json").read_text(encoding="utf-8"))
    assert t["status"] == "PASS"
    assert set(t["supported_event_types"]) == EVENT_TYPES
    assert t["append_only_duplicate_test"]["rows_after"] == 1
    assert set(s["approved_source_classes"]) == SOURCE_CLASSES
    print("PIT_EVENT_LEDGER_V2_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"selftest", "schema", "verify"}:
        raise SystemExit("usage: pit_event_ledger_v2.py {selftest|schema|verify}")
    {"selftest": selftest, "schema": schema, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
