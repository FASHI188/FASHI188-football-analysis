#!/usr/bin/env python3
"""V6.30.0 point-in-time team-context evidence ledger.

Purpose
-------
The existing weekly team configuration pipeline deliberately gathers several different kinds of
information, but older aggregate receipts can make a complete roster look like complete match-level
team information. This ledger separates four evidence axes that must never be conflated:
  1) current first-team roster,
  2) current head coach,
  3) injury/suspension/availability status,
  4) predicted/expected starting XI.

Important semantics
-------------------
- availability=[] means VERIFIED EMPTY only when a timestamped source explicitly has an
  injury/availability/suspension role. Otherwise it means UNKNOWN, not "no injuries".
- a roster of >=18 players never implies a predicted XI.
- previous-season provisional rosters are continuity context only and never satisfy the current
  roster or predicted-XI gates.
- depth-chart context is recorded separately from a genuine predicted XI.
- no probabilities are generated and no CURRENT/formal weight is changed.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v6_team_configuration_weekly_v660.json"
SNAPSHOT_ROOT = ROOT / "evidence" / "team_configuration_weekly"
TEAM_STATUS = ROOT / "manifests" / "v6_team_configuration_weekly_v660_status.json"
ROSTER_OVERLAY = ROOT / "manifests" / "v6_current_roster_overlay_v669_status.json"
PROVISIONAL = ROOT / "manifests" / "v6_team_provisional_roster_v667_status.json"
OUT = ROOT / "manifests" / "v6_pit_team_availability_ledger_v6300_status.json"


def load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def source_roles(snapshot: dict[str, Any]) -> list[str]:
    return [str(s.get("source_role") or "").lower() for s in (snapshot.get("sources") or []) if isinstance(s, dict)]


def timestamped_sources(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in snapshot.get("sources") or []:
        if not isinstance(s, dict):
            continue
        if s.get("source_name") and s.get("source_url") and parse_ts(s.get("source_observed_at_utc")):
            out.append(s)
    return out


def latest_snapshots(domains: set[str]) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any], str]] = {}
    errors = []
    for path in sorted(SNAPSHOT_ROOT.glob("*.json")) if SNAPSHOT_ROOT.exists() else []:
        # Aggregate files are not one-team snapshots.
        if path.name.startswith("weekly_aggregate__"):
            continue
        try:
            x = load(path)
            if not isinstance(x, dict):
                raise ValueError("not an object")
            cid = str(x.get("competition_id") or "")
            team = str(x.get("team_name") or "").strip()
            ts = parse_ts(x.get("observed_at_utc"))
            if cid not in domains or not team or ts is None:
                raise ValueError("invalid competition/team/observed_at")
            key = (cid, team)
            prev = latest.get(key)
            if prev is None or ts > prev[0]:
                latest[key] = (ts, x, path.name)
        except Exception as exc:
            errors.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
    return {k: {"observed_at": v[0], "snapshot": v[1], "file": v[2]} for k, v in latest.items()}, errors


def current_roster_overlays() -> dict[tuple[str, str], dict[str, Any]]:
    payload = load(ROSTER_OVERLAY, {}) or {}
    out = {}
    for r in payload.get("matched_overlays") or []:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("competition_id") or "")
        team = str(r.get("resolved_team_name") or r.get("source_team_name") or "").strip()
        count = int(r.get("overlay_player_count") or 0)
        semantics = str(r.get("roster_semantics") or "")
        ts = parse_ts(r.get("observed_at_utc"))
        if cid and team and count >= 18 and semantics == "CURRENT_FIRST_TEAM" and ts:
            out[(cid, team)] = r
    return out


def manager_overlay_keys() -> set[tuple[str, str]]:
    status = load(TEAM_STATUS, {}) or {}
    out = set()
    for r in status.get("manager_identity_resolution") or []:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("competition_id") or "")
        team = str(r.get("resolved_team_name") or r.get("source_team_name") or "").strip()
        if cid and team:
            out.add((cid, team))
    return out


def provisional_keys() -> set[tuple[str, str]]:
    payload = load(PROVISIONAL, {}) or {}
    return {
        (str(r.get("competition_id") or ""), str(r.get("team_name") or "").strip())
        for r in payload.get("attempts") or []
        if isinstance(r, dict) and str(r.get("status") or "") == "PROVISIONAL_CONTINUITY_AVAILABLE"
    }


def named_list(snapshot: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    for key in keys:
        value = snapshot.get(key)
        if isinstance(value, list):
            return value
    return []


def grade_team(
    key: tuple[str, str],
    item: dict[str, Any] | None,
    roster_overlay: dict[tuple[str, str], dict[str, Any]],
    manager_keys: set[tuple[str, str]],
    provisional: set[tuple[str, str]],
) -> dict[str, Any]:
    cid, team = key
    snap = (item or {}).get("snapshot") or {}
    ts = (item or {}).get("observed_at")
    sources = timestamped_sources(snap)
    roles = source_roles(snap)
    tier12 = any(str(s.get("source_tier") or "") in {"tier_1", "tier_2", "tier_1_identity"} for s in sources)
    players = snap.get("players") if isinstance(snap.get("players"), list) else []

    if len(players) >= 18 and sources and tier12:
        roster_grade = "STRICT_CURRENT_SNAPSHOT"
        roster_count = len(players)
        roster_ts = ts.isoformat() if ts else None
    elif key in roster_overlay:
        r = roster_overlay[key]
        roster_grade = "STRICT_CURRENT_OVERLAY"
        roster_count = int(r.get("overlay_player_count") or 0)
        roster_ts = parse_ts(r.get("observed_at_utc")).isoformat()
    elif key in provisional:
        roster_grade = "PROVISIONAL_PREVIOUS_SEASON_CONTINUITY_ONLY"
        roster_count = len(players)
        roster_ts = ts.isoformat() if ts else None
    else:
        roster_grade = "INCOMPLETE_OR_UNAVAILABLE"
        roster_count = len(players)
        roster_ts = ts.isoformat() if ts else None

    coach = snap.get("head_coach")
    if coach and sources:
        manager_grade = "VERIFIED_SNAPSHOT"
    elif key in manager_keys:
        manager_grade = "VERIFIED_OVERLAY"
    else:
        manager_grade = "UNAVAILABLE"

    availability = snap.get("availability") if isinstance(snap.get("availability"), list) else []
    availability_source = any(any(token in role for token in ("injury", "availability", "suspension", "fitness")) for role in roles)
    if availability_source and availability:
        availability_grade = "PIT_VERIFIED_NONEMPTY"
    elif availability_source and not availability:
        availability_grade = "PIT_VERIFIED_EMPTY"
    elif availability and not availability_source:
        availability_grade = "RECORDS_PRESENT_SOURCE_ROLE_NOT_DEDICATED"
    else:
        availability_grade = "UNKNOWN_NOT_CHECKED"

    predicted = named_list(snap, ("predicted_xi", "expected_xi", "projected_xi", "probable_xi"))
    predicted_source = any(any(token in role for token in ("predicted xi", "expected xi", "projected xi", "probable xi", "predicted lineup", "projected lineup")) for role in roles)
    depth = named_list(snap, ("depth_chart", "position_depth"))
    depth_source = any("depth" in role for role in roles)
    if len(predicted) >= 11 and predicted_source:
        xi_grade = "PIT_PREDICTED_XI"
        xi_count = len(predicted)
    elif len(depth) >= 11 and depth_source:
        xi_grade = "PIT_DEPTH_CHART_CONTEXT_ONLY"
        xi_count = len(depth)
    else:
        xi_grade = "UNAVAILABLE"
        xi_count = len(predicted)

    strict_roster = roster_grade.startswith("STRICT_CURRENT")
    manager_ok = manager_grade.startswith("VERIFIED")
    availability_ok = availability_grade.startswith("PIT_VERIFIED")
    xi_ok = xi_grade == "PIT_PREDICTED_XI"
    if strict_roster and manager_ok and availability_ok and xi_ok:
        readiness = "FULL_PIT_XI_AVAILABILITY_CONTEXT"
    elif strict_roster and manager_ok and availability_ok:
        readiness = "PIT_ROSTER_MANAGER_AVAILABILITY_NO_XI"
    elif strict_roster and manager_ok:
        readiness = "PIT_ROSTER_MANAGER_ONLY"
    elif strict_roster:
        readiness = "PIT_ROSTER_ONLY"
    elif roster_grade.startswith("PROVISIONAL"):
        readiness = "PROVISIONAL_CONTINUITY_ONLY"
    else:
        readiness = "INSUFFICIENT"

    return {
        "competition_id": cid,
        "team_name": team,
        "latest_snapshot_file": (item or {}).get("file"),
        "latest_snapshot_observed_at_utc": ts.isoformat() if ts else None,
        "roster": {"grade": roster_grade, "named_player_count": roster_count, "evidence_at_utc": roster_ts},
        "manager": {"grade": manager_grade, "head_coach_in_snapshot": coach},
        "availability": {"grade": availability_grade, "record_count": len(availability), "dedicated_source_role": availability_source},
        "predicted_xi": {"grade": xi_grade, "player_count": xi_count, "depth_context_count": len(depth)},
        "model_context_readiness": readiness,
        "formal_probability_eligible": False,
    }


def main() -> int:
    cfg = load(CONFIG, {}) or {}
    domains = set(cfg.get("domains") or [])
    latest, errors = latest_snapshots(domains)
    overlays = current_roster_overlays()
    managers = manager_overlay_keys()
    provisional = provisional_keys()
    # Union lets authoritative overlays remain visible even if the machine snapshot is sparse.
    all_keys = set(latest) | set(overlays) | managers | provisional
    rows = [grade_team(k, latest.get(k), overlays, managers, provisional) for k in sorted(all_keys)]

    axis_counts = {
        "roster": dict(Counter(r["roster"]["grade"] for r in rows)),
        "manager": dict(Counter(r["manager"]["grade"] for r in rows)),
        "availability": dict(Counter(r["availability"]["grade"] for r in rows)),
        "predicted_xi": dict(Counter(r["predicted_xi"]["grade"] for r in rows)),
        "model_context_readiness": dict(Counter(r["model_context_readiness"] for r in rows)),
    }
    by_domain = {}
    for cid in sorted(domains):
        rs = [r for r in rows if r["competition_id"] == cid]
        by_domain[cid] = {
            "teams": len(rs),
            "strict_current_roster": sum(r["roster"]["grade"].startswith("STRICT_CURRENT") for r in rs),
            "manager_verified": sum(r["manager"]["grade"].startswith("VERIFIED") for r in rs),
            "availability_pit_verified": sum(r["availability"]["grade"].startswith("PIT_VERIFIED") for r in rs),
            "predicted_xi_pit": sum(r["predicted_xi"]["grade"] == "PIT_PREDICTED_XI" for r in rs),
            "full_context": sum(r["model_context_readiness"] == "FULL_PIT_XI_AVAILABILITY_CONTEXT" for r in rs),
        }

    payload = {
        "schema_version": "V6.30.0-pit-team-availability-ledger-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not errors else "WARN",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_PIT_INPUT_QUALITY_LEDGER_NO_PROBABILITY_MUTATION",
        "team_records": len(rows),
        "configured_domains": len(domains),
        "axis_counts": axis_counts,
        "by_domain": by_domain,
        "rows": rows,
        "validation_errors": errors,
        "hard_semantics": {
            "empty_availability_without_dedicated_timestamped_source_means_unknown": True,
            "complete_roster_does_not_imply_predicted_xi": True,
            "previous_season_provisional_roster_cannot_satisfy_current_roster_gate": True,
            "depth_chart_is_not_predicted_xi": True,
            "actual_match_lineup_may_not_backfill_pre_match_xi": True,
        },
        "next_data_priority": [
            "timestamped injury/suspension availability source per active team",
            "timestamped predicted/expected XI source near the user's question-time freeze",
            "retain strict current-roster and verified-manager evidence as context, not substitutes for XI/availability",
        ],
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "probability_generation": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "team_records": payload["team_records"],
        "axis_counts": axis_counts,
        "by_domain": by_domain,
        "validation_error_count": len(errors),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
