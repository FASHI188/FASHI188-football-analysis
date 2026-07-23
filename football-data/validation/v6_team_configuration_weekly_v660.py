#!/usr/bin/env python3
"""V6.6.10 weekly team-context validator.

Strict current-roster, provisional prior-season continuity, and verified manager evidence are three
separate feature classes. Manager records are matched to team snapshots only within the same
competition using deterministic normalized identity variants; terminal club designators such as
FC/SC/CF/AFC may be stripped, but fuzzy cross-club substitution is prohibited. Machine-observed
coach fields remain descriptive only and cannot bypass the verified manager evidence gate.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v6_team_configuration_weekly_v660.json"
MANAGER_CONFIG = ROOT / "config" / "v6_team_manager_enrichment_v663.json"
SNAPSHOT_ROOT = ROOT / "evidence" / "team_configuration_weekly"
MANAGER_ROOT = ROOT / "evidence" / "team_manager_context_weekly"
PROVISIONAL_ROOT = ROOT / "evidence" / "team_provisional_roster_weekly"
OUT = ROOT / "manifests" / "v6_team_configuration_weekly_v660_status.json"
CLUB_SUFFIXES = {"fc", "sc", "cf", "afc", "ac", "fk", "bk"}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ts(value: str):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def identity_variants(value: str) -> set[str]:
    base = norm(value)
    tokens = base.split()
    variants = {base}
    while tokens and tokens[-1] in CLUB_SUFFIXES:
        tokens = tokens[:-1]
        if tokens:
            variants.add(" ".join(tokens))
    return variants


def iter_snapshots(path: Path):
    payload = load(path)
    if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list):
        for index, snapshot in enumerate(payload["snapshots"]):
            if isinstance(snapshot, dict):
                yield snapshot, f"{path.name}#{index}"
    elif isinstance(payload, dict):
        yield payload, path.name


def iter_records(path: Path):
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
        return False, ["missing_identity"]
    try:
        observed = parse_ts(observed_raw)
    except Exception:
        return False, ["invalid_observed_at"]
    if observed.tzinfo is None:
        errors.append("observed_at_not_timezone_aware")
    if now - observed > timedelta(days=int(contract.get("weekly_freshness_days", 8))):
        errors.append("manager_evidence_stale")
    if not str(coach.get("name") or "").strip():
        errors.append("manager_name_missing")
    sources = record.get("sources") or []
    if not isinstance(sources, list) or not sources:
        return False, errors + ["sources_missing"]
    official = 0
    tier2_groups = set()
    for source in sources:
        if not isinstance(source, dict):
            errors.append("invalid_source_record"); continue
        if not source.get("source_name") or not source.get("source_url") or not source.get("source_observed_at_utc"):
            errors.append("source_missing_name_url_or_time"); continue
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
            tier2_groups.add(group)
    if not (official >= int(contract["verification_gate"]["official_sources_required"]) or len(tier2_groups) >= int(contract["verification_gate"]["or_independent_tier2_sources_required"])):
        errors.append("manager_source_gate_failed")
    return not errors, errors


def resolve_manager_key(cid: str, team: str, team_keys: list[tuple[str, str]]) -> tuple[tuple[str, str] | None, str]:
    exact = (cid, team)
    if exact in team_keys:
        return exact, "EXACT"
    wanted = identity_variants(team)
    matches = []
    for key in team_keys:
        if key[0] != cid:
            continue
        if wanted & identity_variants(key[1]):
            matches.append(key)
    if len(matches) == 1:
        return matches[0], "NORMALIZED_SUFFIX_SAFE"
    if len(matches) > 1:
        return None, "AMBIGUOUS_FAIL_CLOSED"
    return None, "NO_MATCH_FAIL_CLOSED"


def main() -> int:
    cfg = load(CONFIG); manager_cfg = load(MANAGER_CONFIG); domains = set(cfg["domains"])
    files = sorted(SNAPSHOT_ROOT.glob("*.json")) if SNAPSHOT_ROOT.exists() else []
    manager_files = sorted(MANAGER_ROOT.glob("*.json")) if MANAGER_ROOT.exists() else []
    provisional_files = sorted(PROVISIONAL_ROOT.glob("*.json")) if PROVISIONAL_ROOT.exists() else []
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any], str]] = {}
    latest_manager_raw: list[tuple[datetime, dict[str, Any], str]] = []
    latest_provisional: dict[tuple[str, str], tuple[datetime, dict[str, Any], str]] = {}
    errors=[]; manager_errors=[]; source_tiers=Counter(); domain_teams=defaultdict(set); aggregate_files=manager_aggregate_files=provisional_aggregate_files=0
    now = datetime.now(timezone.utc).replace(microsecond=0)

    for path in files:
        try:
            root = load(path); aggregate_files += int(isinstance(root,dict) and isinstance(root.get("snapshots"),list))
            for snapshot, virtual in iter_snapshots(path):
                cid=str(snapshot.get("competition_id") or ""); team=str(snapshot.get("team_name") or "").strip(); season=str(snapshot.get("season") or "").strip(); observed=str(snapshot.get("observed_at_utc") or "")
                if cid not in domains: raise ValueError(f"unknown competition_id: {cid}")
                if not team or not season or not observed: raise ValueError("missing identity/season/observed_at")
                ts=parse_ts(observed)
                if ts.tzinfo is None: raise ValueError("observed_at_utc must be timezone-aware")
                sources=snapshot.get("sources") or []
                if not isinstance(sources,list) or not sources: raise ValueError("no sources")
                for source in sources:
                    if not source.get("source_name") or not source.get("source_url") or not source.get("source_observed_at_utc"): raise ValueError("source missing name/url/timestamp")
                    source_tiers[str(source.get("source_tier") or "unspecified")]+=1
                if not isinstance(snapshot.get("players") or [],list): raise ValueError("players must be list")
                key=(cid,team); prev=latest.get(key)
                if prev is None or ts>prev[0]: latest[key]=(ts,snapshot,virtual)
                domain_teams[cid].add(team)
        except Exception as exc: errors.append({"file":path.name,"error":f"{type(exc).__name__}: {exc}"})

    team_keys=list(latest.keys())
    manager_resolution=[]
    for path in manager_files:
        try:
            root=load(path); manager_aggregate_files += int(isinstance(root,dict) and isinstance(root.get("records"),list))
            for record,virtual in iter_records(path):
                cid=str(record.get("competition_id") or ""); team=str(record.get("team_name") or "").strip()
                if cid not in domains: manager_errors.append({"file":virtual,"errors":[f"unknown_competition_id:{cid}"]}); continue
                valid,record_errors=manager_gate(record,manager_cfg,now)
                if not valid: manager_errors.append({"file":virtual,"competition_id":cid,"team_name":team,"errors":record_errors}); continue
                latest_manager_raw.append((parse_ts(record["observed_at_utc"]),record,virtual))
        except Exception as exc: manager_errors.append({"file":path.name,"errors":[f"{type(exc).__name__}: {exc}"]})

    latest_manager: dict[tuple[str,str],tuple[datetime,dict[str,Any],str]]={}
    for ts,record,virtual in latest_manager_raw:
        cid=str(record["competition_id"]); source_team=str(record["team_name"]); resolved,method=resolve_manager_key(cid,source_team,team_keys)
        manager_resolution.append({"competition_id":cid,"source_team_name":source_team,"resolved_team_name":resolved[1] if resolved else None,"method":method,"file":virtual})
        if resolved is None:
            manager_errors.append({"file":virtual,"competition_id":cid,"team_name":source_team,"errors":[f"identity_resolution:{method}"]}); continue
        prev=latest_manager.get(resolved)
        if prev is None or ts>prev[0]: latest_manager[resolved]=(ts,record,virtual)

    for path in provisional_files:
        try:
            root=load(path); provisional_aggregate_files += int(isinstance(root,dict) and isinstance(root.get("records"),list))
            for record,virtual in iter_records(path):
                if record.get("status")!="PROVISIONAL_PREVIOUS_SEASON_CONTINUITY" or record.get("strict_current_roster_eligible") is not False: continue
                cid=str(record.get("competition_id") or ""); team=str(record.get("team_name") or "").strip(); ts=parse_ts(str(record.get("observed_at_utc") or ""))
                if (cid,team) not in latest: continue
                if len(record.get("previous_season_players") or [])<18: continue
                key=(cid,team); prev=latest_provisional.get(key)
                if prev is None or ts>prev[0]: latest_provisional[key]=(ts,record,virtual)
        except Exception as exc: errors.append({"file":path.name,"error":f"provisional:{type(exc).__name__}: {exc}"})

    roster_eligible=availability_eligible=transaction_eligible=depth_eligible=manager_eligible=manager_change_eligible=full_context=0
    provisional_eligible=0; latest_summary=[]
    for (cid,team),(ts,snapshot,filename) in sorted(latest.items()):
        players=snapshot.get("players") or []; machine_coach=snapshot.get("head_coach"); availability=snapshot.get("availability") or []; health=snapshot.get("source_health") or {}; sources=snapshot.get("sources") or []
        strong=any(str(s.get("source_tier")) in {"tier_1","tier_1_identity","tier_2"} and s.get("source_reached",True) for s in sources)
        roster_ok=len(players)>=18 and bool(health.get("roster_content_ok",True)); availability_ok=roster_ok and bool(health.get("injuries_endpoint_ok")) and strong; transaction_ok=roster_ok and bool(health.get("transactions_endpoint_ok")) and strong; depth_ok=roster_ok and bool(health.get("depthcharts_endpoint_ok")) and strong
        provisional=latest_provisional.get((cid,team)); provisional_ok=not roster_ok and provisional is not None
        manager_overlay=latest_manager.get((cid,team)); manager_record=manager_overlay[1] if manager_overlay else None; coach=(manager_record or {}).get("head_coach") or machine_coach; manager_ok=bool(manager_record); change=(manager_record or {}).get("manager_change") or {}; manager_change_ok=manager_ok and change.get("status") in {"UNCHANGED","CHANGED_CONFIRMED","INTERIM_CONFIRMED"}; complete=availability_ok and transaction_ok and manager_ok
        roster_eligible+=int(roster_ok); availability_eligible+=int(availability_ok); transaction_eligible+=int(transaction_ok); depth_eligible+=int(depth_ok); provisional_eligible+=int(provisional_ok); manager_eligible+=int(manager_ok); manager_change_eligible+=int(manager_change_ok); full_context+=int(complete)
        latest_summary.append({"competition_id":cid,"team_name":team,"season":snapshot.get("season"),"observed_at_utc":ts.isoformat(),"players":len(players),"availability_records":len(availability),"head_coach_present":bool(coach),"head_coach_name":(coach or {}).get("name") if isinstance(coach,dict) else None,"machine_coach_descriptive_only":bool(machine_coach) and not bool(manager_record),"roster_research_eligible":roster_ok,"provisional_roster_continuity_eligible":provisional_ok,"availability_research_eligible":availability_ok,"transaction_research_eligible":transaction_ok,"depth_research_eligible":depth_ok,"manager_research_eligible":manager_ok,"manager_change_research_eligible":manager_change_ok,"full_context_complete":complete,"snapshot_file":filename,"provisional_evidence_file":provisional[2] if provisional else None,"manager_evidence_file":manager_overlay[2] if manager_overlay else None})

    latest_count=len(latest); domain_count=len({cid for cid,_ in latest})
    status="WARN_VALIDATION_ERRORS" if errors else "PASS_COMPLETE" if domain_count==len(domains) and roster_eligible==latest_count and full_context==latest_count else "PASS_ROSTER_BASELINE_CONTEXT_PARTIAL" if domain_count==len(domains) and roster_eligible==latest_count else "WARN_ROSTER_GAPS" if domain_count==len(domains) else "WARN_DOMAIN_GAPS"
    payload={"schema_version":"V6.6.10-weekly-team-configuration-status-r5","generated_at_utc":now.isoformat(),"status":status,"physical_snapshot_files":len(files),"aggregate_snapshot_files":aggregate_files,"manager_evidence_files":len(manager_files),"manager_aggregate_files":manager_aggregate_files,"provisional_evidence_files":len(provisional_files),"provisional_aggregate_files":provisional_aggregate_files,"latest_team_snapshots":latest_count,"verified_manager_records":len(latest_manager_raw),"resolved_manager_records":len(latest_manager),"domains_with_snapshots":domain_count,"configured_domains":len(domains),"feature_eligibility":{"roster":roster_eligible,"provisional_roster_continuity":provisional_eligible,"availability":availability_eligible,"transactions":transaction_eligible,"depth_chart":depth_eligible,"manager":manager_eligible,"manager_change":manager_change_eligible,"full_context":full_context},"source_tier_counts":dict(source_tiers),"domain_team_counts":{k:len(v) for k,v in sorted(domain_teams.items())},"validation_errors":errors,"manager_validation_errors":manager_errors,"manager_identity_resolution":manager_resolution,"latest":latest_summary,"governance":{"configuration_data_is_research_context_only":True,"strict_current_roster_and_provisional_continuity_are_separate":True,"provisional_roster_never_satisfies_strict_current_gate":True,"manager_requires_official_or_two_independent_sources":True,"machine_coach_is_descriptive_only":True,"manager_eligibility_requires_verified_overlay":True,"manager_identity_matching_is_deterministic_same_competition_only":True,"fuzzy_cross_club_substitution":False,"no_probability_generation":True,"no_formal_weight_change":True,"no_runtime_probability_change":True,"current_rule_version":"V5.0.1"}}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps({k:payload[k] for k in ("status","latest_team_snapshots","verified_manager_records","resolved_manager_records","domains_with_snapshots","feature_eligibility","manager_identity_resolution","validation_errors","manager_validation_errors")},ensure_ascii=False,indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())