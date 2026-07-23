#!/usr/bin/env python3
"""V6.6.9 validate current-roster overlays against the latest weekly team baseline.

A passing overlay is current, >=18 unique named players, and supported by either one official
current first-team/registered-squad source or two independent tier-2 sources. It may repair the
research strict-roster availability gap without overwriting the raw weekly snapshot. Provisional,
training-group and previous-season semantics are fail-closed.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "v6_current_roster_overlay_v669.json"
BASE = ROOT / "evidence" / "team_configuration_weekly"
EVIDENCE = ROOT / "evidence" / "team_current_roster_weekly"
OUT = ROOT / "manifests" / "v6_current_roster_overlay_v669_status.json"
SUFFIXES = {"fc", "sc", "cf", "afc", "ac", "fk", "bk"}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def ts(value: str):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def variants(value: str) -> set[str]:
    base = norm(value); tokens = base.split(); out = {base}
    while tokens and tokens[-1] in SUFFIXES:
        tokens = tokens[:-1]
        if tokens: out.add(" ".join(tokens))
    return out


def latest_team_baseline() -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    for path in BASE.glob("*.json") if BASE.exists() else []:
        try:
            payload = load(path)
            rows = payload.get("snapshots") if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list) else [payload]
            for row in rows:
                if not isinstance(row, dict): continue
                cid = str(row.get("competition_id") or ""); team = str(row.get("team_name") or "").strip(); observed = str(row.get("observed_at_utc") or "")
                if not cid or not team or not observed: continue
                stamp = ts(observed); key = (cid, team); prev = latest.get(key)
                if prev is None or stamp > prev[0]: latest[key] = (stamp, row)
        except Exception:
            continue
    return {k: v[1] for k, v in latest.items()}


def resolve(cid: str, team: str, keys: list[tuple[str, str]]) -> tuple[tuple[str, str] | None, str]:
    exact = (cid, team)
    if exact in keys: return exact, "EXACT"
    wanted = variants(team); matches = [k for k in keys if k[0] == cid and wanted & variants(k[1])]
    if len(matches) == 1: return matches[0], "NORMALIZED_SUFFIX_SAFE"
    return None, "AMBIGUOUS_FAIL_CLOSED" if len(matches) > 1 else "NO_MATCH_FAIL_CLOSED"


def iter_records(path: Path):
    payload = load(path)
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        for i, row in enumerate(payload["records"]):
            if isinstance(row, dict): yield row, f"{path.name}#{i}"
    elif isinstance(payload, dict): yield payload, path.name


def validate_record(row: dict[str, Any], cfg: dict[str, Any], now: datetime) -> list[str]:
    errors=[]
    if row.get("schema_version") != cfg.get("accepted_record_schema"): errors.append("wrong_schema")
    if not row.get("competition_id") or not row.get("team_name") or not row.get("observed_at_utc"): errors.append("missing_identity")
    try:
        observed=ts(row.get("observed_at_utc"))
        if observed.tzinfo is None: errors.append("observed_at_not_timezone_aware")
        if now-observed > timedelta(days=int(cfg.get("weekly_freshness_days",8))): errors.append("stale")
    except Exception: errors.append("invalid_observed_at"); observed=None
    semantics=str(row.get("roster_semantics") or "")
    if semantics not in set(cfg.get("accepted_roster_semantics") or []): errors.append(f"unaccepted_roster_semantics:{semantics}")
    if semantics in set(cfg.get("rejected_as_strict_semantics") or []): errors.append("provisional_semantics_forbidden")
    players=row.get("players") or []
    if not isinstance(players,list): errors.append("players_not_list"); players=[]
    names=[]
    for player in players:
        if not isinstance(player,dict) or not str(player.get("player_name") or "").strip(): errors.append("unnamed_player"); continue
        names.append(norm(player["player_name"]))
    unique=set(names)
    if len(unique) < int(cfg.get("minimum_named_players",18)): errors.append(f"named_players_below_min:{len(unique)}")
    if len(unique) != len(names): errors.append("duplicate_player_names")
    sources=row.get("sources") or []
    if not isinstance(sources,list) or not sources: errors.append("sources_missing"); sources=[]
    official=0; tier2=set()
    for source in sources:
        if not isinstance(source,dict): errors.append("invalid_source"); continue
        if not source.get("source_name") or not source.get("source_url") or not source.get("source_observed_at_utc"): errors.append("source_missing_fields"); continue
        try:
            st=ts(source["source_observed_at_utc"])
            if observed and st > observed: errors.append("source_after_record")
        except Exception: errors.append("invalid_source_time")
        tier=str(source.get("source_tier") or ""); group=str(source.get("provider_group") or source.get("source_name") or "").lower().strip()
        if tier=="tier_1_official": official+=1
        elif tier=="tier_2_independent" and group: tier2.add(group)
    gate=cfg.get("source_gate") or {}
    if not (official >= int(gate.get("official_sources_required",1)) or len(tier2) >= int(gate.get("or_independent_tier2_sources_required",2))): errors.append("source_gate_failed")
    gov=row.get("governance") or {}
    if gov.get("cross_source_union") is True: errors.append("cross_source_union_forbidden")
    return errors


def main() -> int:
    cfg=load(CFG); now=datetime.now(timezone.utc).replace(microsecond=0); base=latest_team_baseline(); keys=list(base.keys())
    files=sorted(EVIDENCE.glob("*.json")) if EVIDENCE.exists() else []; candidates=[]; invalid=[]
    for path in files:
        for row,virtual in iter_records(path):
            errs=validate_record(row,cfg,now)
            if errs: invalid.append({"file":virtual,"competition_id":row.get("competition_id"),"team_name":row.get("team_name"),"errors":errs}); continue
            cid=str(row["competition_id"]); team=str(row["team_name"]); resolved,method=resolve(cid,team,keys)
            if resolved is None: invalid.append({"file":virtual,"competition_id":cid,"team_name":team,"errors":[f"identity_resolution:{method}"]}); continue
            observed=ts(row["observed_at_utc"]); candidates.append((resolved,observed,row,virtual,method))
    latest_overlay={}
    for resolved,observed,row,virtual,method in candidates:
        prev=latest_overlay.get(resolved)
        if prev is None or observed>prev[0]: latest_overlay[resolved]=(observed,row,virtual,method)
    matched=[]; additions=0; already_strict=0
    for key,(observed,row,virtual,method) in sorted(latest_overlay.items()):
        base_row=base[key]; base_count=len(base_row.get("players") or []); base_health=base_row.get("source_health") or {}; base_strict=base_count>=18 and bool(base_health.get("roster_content_ok",True))
        if base_strict: already_strict+=1
        else: additions+=1
        matched.append({"competition_id":key[0],"source_team_name":row.get("team_name"),"resolved_team_name":key[1],"identity_method":method,"base_player_count":base_count,"overlay_player_count":len(row.get("players") or []),"base_strict_current_roster":base_strict,"strict_roster_addition":not base_strict,"evidence_file":virtual,"roster_semantics":row.get("roster_semantics")})
    base_strict_count=sum(1 for row in base.values() if len(row.get("players") or [])>=18 and bool((row.get("source_health") or {}).get("roster_content_ok",True)))
    payload={"schema_version":"V6.6.9-current-roster-overlay-status-r1","generated_at_utc":now.isoformat(),"status":"PASS" if not invalid else "WARN_INVALID_RECORDS","team_baseline_count":len(base),"base_strict_current_roster_count":base_strict_count,"evidence_file_count":len(files),"valid_overlay_count":len(latest_overlay),"strict_roster_additions":additions,"valid_overlays_on_already_strict_teams":already_strict,"effective_strict_current_roster_count":base_strict_count+additions,"invalid_records":invalid,"matched_overlays":matched,"governance":{"strict_current_only":True,"provisional_semantics_fail_closed":True,"no_cross_source_union":True,"raw_weekly_snapshot_not_overwritten":True,"formal_probability_change":False,"formal_weight_change":False}}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
