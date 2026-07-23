#!/usr/bin/env python3
"""Validate weekly team-configuration evidence and produce feature-specific coverage gates.

V6.6.2 accepts both legacy single-team snapshots and the new one-file-per-week aggregate.
Roster/availability eligibility is separated from manager-context eligibility so a missing
coach field cannot silently mark good roster evidence as unusable, while manager features
remain fail-closed until independently sourced.
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
OUT = ROOT / "manifests" / "v6_team_configuration_weekly_v660_status.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ts(value: str):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iter_snapshots(path: Path):
    payload = load(path)
    if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list):
        for index, snapshot in enumerate(payload["snapshots"]):
            if isinstance(snapshot, dict):
                yield snapshot, f"{path.name}#{index}"
    elif isinstance(payload, dict):
        yield payload, path.name


def main() -> int:
    cfg = load(CONFIG)
    domains = set(cfg["domains"])
    files = sorted(SNAPSHOT_ROOT.glob("*.json")) if SNAPSHOT_ROOT.exists() else []
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any], str]] = {}
    errors = []
    source_tiers = Counter()
    domain_teams = defaultdict(set)
    aggregate_files = 0

    for path in files:
        try:
            root_payload = load(path)
            aggregate_files += int(isinstance(root_payload, dict) and isinstance(root_payload.get("snapshots"), list))
            for snapshot, virtual_name in iter_snapshots(path):
                cid = str(snapshot.get("competition_id") or "")
                team = str(snapshot.get("team_name") or "").strip()
                season = str(snapshot.get("season") or "").strip()
                observed = str(snapshot.get("observed_at_utc") or "")
                if cid not in domains:
                    raise ValueError(f"unknown competition_id: {cid}")
                if not team or not season or not observed:
                    raise ValueError("missing identity/season/observed_at")
                ts = parse_ts(observed)
                if ts.tzinfo is None:
                    raise ValueError("observed_at_utc must be timezone-aware")
                sources = snapshot.get("sources") or []
                if not isinstance(sources, list) or not sources:
                    raise ValueError("no sources")
                for source in sources:
                    if not source.get("source_name") or not source.get("source_url") or not source.get("source_observed_at_utc"):
                        raise ValueError("source missing name/url/timestamp")
                    source_tiers[str(source.get("source_tier") or "unspecified")] += 1
                players = snapshot.get("players") or []
                if not isinstance(players, list):
                    raise ValueError("players must be list")
                key = (cid, team)
                previous = latest.get(key)
                if previous is None or ts > previous[0]:
                    latest[key] = (ts, snapshot, virtual_name)
                domain_teams[cid].add(team)
        except Exception as exc:
            errors.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})

    roster_eligible = 0
    availability_eligible = 0
    transaction_eligible = 0
    depth_eligible = 0
    manager_eligible = 0
    full_context = 0
    latest_summary = []
    for (cid, team), (ts, snapshot, filename) in sorted(latest.items()):
        players = snapshot.get("players") or []
        coach = snapshot.get("head_coach")
        availability = snapshot.get("availability") or []
        source_health = snapshot.get("source_health") or {}
        sources = snapshot.get("sources") or []
        strong_source = any(str(source.get("source_tier")) in {"tier_1", "tier_1_identity", "tier_2"} and source.get("source_reached", True) for source in sources)
        roster_ok = len(players) >= 18 and bool(source_health.get("roster_content_ok", True))
        availability_ok = roster_ok and bool(source_health.get("injuries_endpoint_ok")) and strong_source
        transaction_ok = roster_ok and bool(source_health.get("transactions_endpoint_ok")) and strong_source
        depth_ok = roster_ok and bool(source_health.get("depthcharts_endpoint_ok")) and strong_source
        manager_ok = bool(coach) and strong_source
        complete = availability_ok and transaction_ok and manager_ok
        roster_eligible += int(roster_ok)
        availability_eligible += int(availability_ok)
        transaction_eligible += int(transaction_ok)
        depth_eligible += int(depth_ok)
        manager_eligible += int(manager_ok)
        full_context += int(complete)
        latest_summary.append({
            "competition_id": cid, "team_name": team, "season": snapshot.get("season"),
            "observed_at_utc": ts.isoformat(), "players": len(players),
            "availability_records": len(availability), "head_coach_present": bool(coach),
            "roster_research_eligible": roster_ok,
            "availability_research_eligible": availability_ok,
            "transaction_research_eligible": transaction_ok,
            "depth_research_eligible": depth_ok,
            "manager_research_eligible": manager_ok,
            "full_context_complete": complete,
            "snapshot_file": filename,
        })

    latest_count = len(latest)
    domain_count = len({cid for cid, _team in latest})
    if errors:
        status = "WARN_VALIDATION_ERRORS"
    elif domain_count == len(domains) and roster_eligible == latest_count and latest_count > 0:
        status = "PASS_ROSTER_BASELINE_CONTEXT_PARTIAL" if manager_eligible < latest_count else "PASS_COMPLETE"
    elif domain_count == len(domains):
        status = "WARN_ROSTER_GAPS"
    else:
        status = "WARN_DOMAIN_GAPS"
    payload = {
        "schema_version": "V6.6.2-weekly-team-configuration-status-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "physical_snapshot_files": len(files),
        "aggregate_snapshot_files": aggregate_files,
        "latest_team_snapshots": latest_count,
        "domains_with_snapshots": domain_count,
        "configured_domains": len(domains),
        "feature_eligibility": {
            "roster": roster_eligible,
            "availability": availability_eligible,
            "transactions": transaction_eligible,
            "depth_chart": depth_eligible,
            "manager": manager_eligible,
            "full_context": full_context,
        },
        "source_tier_counts": dict(source_tiers),
        "domain_team_counts": {key: len(value) for key, value in sorted(domain_teams.items())},
        "validation_errors": errors,
        "latest": latest_summary,
        "governance": {
            "configuration_data_is_research_context_only": True,
            "feature_specific_fail_closed_gates": True,
            "manager_missing_never_inferred_from_stale_prose": True,
            "no_probability_generation": True,
            "no_formal_weight_change": True,
            "no_runtime_probability_change": True,
            "no_current_rule_change": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("status", "latest_team_snapshots", "domains_with_snapshots", "feature_eligibility", "validation_errors")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
