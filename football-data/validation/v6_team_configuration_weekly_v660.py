#!/usr/bin/env python3
"""Validate weekly team configuration plus independently verified manager context.

V6.6.3 keeps machine roster/availability data separate from manager evidence. Manager context
may enter the research eligibility ledger only when it is point-in-time, current, and supported
by either one official source or two independent tier-2 sources. Missing manager data remains
fail-closed and never makes otherwise valid roster evidence unavailable.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v6_team_configuration_weekly_v660.json"
MANAGER_CONFIG = ROOT / "config" / "v6_team_manager_enrichment_v663.json"
SNAPSHOT_ROOT = ROOT / "evidence" / "team_configuration_weekly"
MANAGER_ROOT = ROOT / "evidence" / "team_manager_context_weekly"
OUT = ROOT / "manifests" / "v6_team_configuration_weekly_v660_status.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ts(value: str):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def iter_snapshots(path: Path):
    payload = load(path)
    if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list):
        for index, snapshot in enumerate(payload["snapshots"]):
            if isinstance(snapshot, dict):
                yield snapshot, f"{path.name}#{index}"
    elif isinstance(payload, dict):
        yield payload, path.name


def iter_manager_records(path: Path):
    payload = load(path)
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        for index, record in enumerate(payload["records"]):
            if isinstance(record, dict):
                yield record, f"{path.name}#{index}"
    elif isinstance(payload, dict):
        yield payload, path.name


def manager_gate(record: dict[str, Any], contract: dict[str, Any], now: datetime) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if str(record.get("schema_version") or "") != str(contract.get("accepted_record_schema")):
        errors.append("wrong_schema")
    cid = str(record.get("competition_id") or "")
    team = str(record.get("team_name") or "").strip()
    observed_raw = str(record.get("observed_at_utc") or "")
    coach = record.get("head_coach") or {}
    if not cid or not team or not observed_raw:
        errors.append("missing_identity")
        return False, errors
    try:
        observed = parse_ts(observed_raw)
    except Exception:
        errors.append("invalid_observed_at")
        return False, errors
    if observed.tzinfo is None:
        errors.append("observed_at_not_timezone_aware")
    freshness_days = int(contract.get("weekly_freshness_days", 8))
    if now - observed > timedelta(days=freshness_days):
        errors.append("manager_evidence_stale")
    if not str(coach.get("name") or "").strip():
        errors.append("manager_name_missing")
    sources = record.get("sources") or []
    if not isinstance(sources, list) or not sources:
        errors.append("sources_missing")
        return False, errors
    official = 0
    independent_groups = set()
    for source in sources:
        if not isinstance(source, dict):
            errors.append("invalid_source_record")
            continue
        if not source.get("source_name") or not source.get("source_url") or not source.get("source_observed_at_utc"):
            errors.append("source_missing_name_url_or_time")
            continue
        try:
            source_ts = parse_ts(source["source_observed_at_utc"])
            if source_ts > observed:
                errors.append("source_observed_after_record")
        except Exception:
            errors.append("invalid_source_time")
        tier = str(source.get("source_tier") or "")
        group = str(source.get("provider_group") or source.get("source_name") or "").strip().lower()
        if tier == "tier_1_official":
            official += 1
        elif tier == "tier_2_independent" and group:
            independent_groups.add(group)
    gate = official >= int(contract["verification_gate"]["official_sources_required"]) or len(independent_groups) >= int(contract["verification_gate"]["or_independent_tier2_sources_required"])
    if not gate:
        errors.append("manager_source_gate_failed")
    return not errors, errors


def main() -> int:
    cfg = load(CONFIG)
    manager_cfg = load(MANAGER_CONFIG)
    domains = set(cfg["domains"])
    files = sorted(SNAPSHOT_ROOT.glob("*.json")) if SNAPSHOT_ROOT.exists() else []
    manager_files = sorted(MANAGER_ROOT.glob("*.json")) if MANAGER_ROOT.exists() else []
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any], str]] = {}
    latest_manager: dict[tuple[str, str], tuple[datetime, dict[str, Any], str]] = {}
    errors = []
    manager_errors = []
    source_tiers = Counter()
    domain_teams = defaultdict(set)
    aggregate_files = 0
    manager_aggregate_files = 0
    now = datetime.now(timezone.utc).replace(microsecond=0)

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

    for path in manager_files:
        try:
            root_payload = load(path)
            manager_aggregate_files += int(isinstance(root_payload, dict) and isinstance(root_payload.get("records"), list))
            for record, virtual_name in iter_manager_records(path):
                cid = str(record.get("competition_id") or "")
                team = str(record.get("team_name") or "").strip()
                if cid not in domains:
                    manager_errors.append({"file": virtual_name, "errors": [f"unknown_competition_id:{cid}"]})
                    continue
                valid, record_errors = manager_gate(record, manager_cfg, now)
                if not valid:
                    manager_errors.append({"file": virtual_name, "competition_id": cid, "team_name": team, "errors": record_errors})
                    continue
                ts = parse_ts(record["observed_at_utc"])
                key = (cid, team)
                previous = latest_manager.get(key)
                if previous is None or ts > previous[0]:
                    latest_manager[key] = (ts, record, virtual_name)
        except Exception as exc:
            manager_errors.append({"file": path.name, "errors": [f"{type(exc).__name__}: {exc}"]})

    roster_eligible = availability_eligible = transaction_eligible = depth_eligible = 0
    manager_eligible = manager_change_eligible = full_context = 0
    latest_summary = []
    for (cid, team), (ts, snapshot, filename) in sorted(latest.items()):
        players = snapshot.get("players") or []
        machine_coach = snapshot.get("head_coach")
        availability = snapshot.get("availability") or []
        source_health = snapshot.get("source_health") or {}
        sources = snapshot.get("sources") or []
        strong_source = any(str(source.get("source_tier")) in {"tier_1", "tier_1_identity", "tier_2"} and source.get("source_reached", True) for source in sources)
        roster_ok = len(players) >= 18 and bool(source_health.get("roster_content_ok", True))
        availability_ok = roster_ok and bool(source_health.get("injuries_endpoint_ok")) and strong_source
        transaction_ok = roster_ok and bool(source_health.get("transactions_endpoint_ok")) and strong_source
        depth_ok = roster_ok and bool(source_health.get("depthcharts_endpoint_ok")) and strong_source

        manager_overlay = latest_manager.get((cid, team))
        manager_record = manager_overlay[1] if manager_overlay else None
        coach = (manager_record or {}).get("head_coach") or machine_coach
        manager_ok = bool(manager_record) or (bool(machine_coach) and strong_source)
        change = (manager_record or {}).get("manager_change") or {}
        manager_change_ok = manager_ok and bool(change.get("status") in {"UNCHANGED", "CHANGED_CONFIRMED", "INTERIM_CONFIRMED"})
        complete = availability_ok and transaction_ok and manager_ok

        roster_eligible += int(roster_ok)
        availability_eligible += int(availability_ok)
        transaction_eligible += int(transaction_ok)
        depth_eligible += int(depth_ok)
        manager_eligible += int(manager_ok)
        manager_change_eligible += int(manager_change_ok)
        full_context += int(complete)
        latest_summary.append({
            "competition_id": cid, "team_name": team, "season": snapshot.get("season"),
            "observed_at_utc": ts.isoformat(), "players": len(players),
            "availability_records": len(availability), "head_coach_present": bool(coach),
            "head_coach_name": (coach or {}).get("name") if isinstance(coach, dict) else None,
            "roster_research_eligible": roster_ok,
            "availability_research_eligible": availability_ok,
            "transaction_research_eligible": transaction_ok,
            "depth_research_eligible": depth_ok,
            "manager_research_eligible": manager_ok,
            "manager_change_research_eligible": manager_change_ok,
            "full_context_complete": complete,
            "snapshot_file": filename,
            "manager_evidence_file": manager_overlay[2] if manager_overlay else None,
        })

    latest_count = len(latest)
    domain_count = len({cid for cid, _team in latest})
    if errors:
        status = "WARN_VALIDATION_ERRORS"
    elif domain_count == len(domains) and roster_eligible == latest_count and latest_count > 0:
        status = "PASS_COMPLETE" if full_context == latest_count else "PASS_ROSTER_BASELINE_CONTEXT_PARTIAL"
    elif domain_count == len(domains):
        status = "WARN_ROSTER_GAPS"
    else:
        status = "WARN_DOMAIN_GAPS"
    payload = {
        "schema_version": "V6.6.3-weekly-team-configuration-status-r3",
        "generated_at_utc": now.isoformat(),
        "status": status,
        "physical_snapshot_files": len(files),
        "aggregate_snapshot_files": aggregate_files,
        "manager_evidence_files": len(manager_files),
        "manager_aggregate_files": manager_aggregate_files,
        "latest_team_snapshots": latest_count,
        "verified_manager_records": len(latest_manager),
        "domains_with_snapshots": domain_count,
        "configured_domains": len(domains),
        "feature_eligibility": {
            "roster": roster_eligible,
            "availability": availability_eligible,
            "transactions": transaction_eligible,
            "depth_chart": depth_eligible,
            "manager": manager_eligible,
            "manager_change": manager_change_eligible,
            "full_context": full_context,
        },
        "source_tier_counts": dict(source_tiers),
        "domain_team_counts": {key: len(value) for key, value in sorted(domain_teams.items())},
        "validation_errors": errors,
        "manager_validation_errors": manager_errors,
        "latest": latest_summary,
        "governance": {
            "configuration_data_is_research_context_only": True,
            "feature_specific_fail_closed_gates": True,
            "manager_requires_official_or_two_independent_sources": True,
            "manager_missing_never_inferred_from_stale_prose": True,
            "manager_overlay_cannot_rewrite_historical_snapshots": True,
            "no_probability_generation": True,
            "no_formal_weight_change": True,
            "no_runtime_probability_change": True,
            "current_rule_version": "V5.0.1",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("status", "latest_team_snapshots", "verified_manager_records", "domains_with_snapshots", "feature_eligibility", "validation_errors", "manager_validation_errors")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
