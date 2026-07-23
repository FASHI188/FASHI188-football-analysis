#!/usr/bin/env python3
"""V6.1.2 timezone-safe result resolver for the immutable pristine forward ledger.

This module settles ONLY already-frozen PREDICTION_FROZEN events. It never creates a prediction and
never changes an existing ledger event. A result is eligible only after kickoff+2h. ESPN public
scoreboards are queried for UTC kickoff calendar day -1/0/+1 because scoreboard date grouping can
follow the competition/local calendar date while the frozen fixture identity uses UTC timestamps.
The event must still match one unique home/away identity and be within the kickoff tolerance.

For ordinary league matches a completed final score is the 90-minute score. If ESPN indicates extra
time or penalties, the resolver uses only the first two regulation-period line scores when available;
otherwise it fails closed rather than treating an ET/penalty final as a 90-minute result.

Research settlement infrastructure only. V5.0.1 probabilities, weights, frozen predictions and their
hashes are never changed.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import (
    PlatformError,
    atomic_write_json,
    canonical_team_name,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
)

LEDGER = ROOT / "forward" / "v6_pristine_forward_events_v612.json"
RESULT_INBOX = ROOT / "forward" / "inbox" / "results_v612.json"
OUT = ROOT / "manifests" / "v6_pristine_forward_result_resolver_v612_status.json"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
UA = "football-v6.1.2-result-resolver/1.0"
MIN_RESULT_AGE = timedelta(hours=2)
KICKOFF_TOLERANCE = timedelta(hours=6)

DOMAINS = {
    "ARG_Primera": "arg.1",
    "BRA_SerieA": "bra.1",
    "ENG_PremierLeague": "eng.1",
    "ESP_LaLiga": "esp.1",
    "FRA_Ligue1": "fra.1",
    "GER_Bundesliga": "ger.1",
    "ITA_SerieA": "ita.1",
    "JPN_J1": "jpn.1",
    "KOR_KLeague1": "kor.1",
    "NED_Eredivisie": "ned.1",
    "NOR_Eliteserien": "nor.1",
    "POR_PrimeiraLiga": "por.1",
    "SCO_Premiership": "sco.1",
    "SUI_SuperLeague": "sui.1",
    "SWE_Allsvenskan": "swe.1",
    "UEFA_ChampionsLeague": "uefa.champions",
    "USA_MLS": "usa.1",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(value, dict):
        raise PlatformError("ESPN scoreboard returned non-object JSON")
    return value


def load_results() -> dict[str, Any]:
    if not RESULT_INBOX.exists():
        return {"schema_version": "V6.1.2-result-inbox-r1", "results": []}
    value = load_json(RESULT_INBOX)
    if value.get("schema_version") != "V6.1.2-result-inbox-r1" or not isinstance(value.get("results"), list):
        raise PlatformError("invalid V6.1.2 result inbox")
    return value


def open_predictions(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    predictions: dict[str, dict[str, Any]] = {}
    settled: set[str] = set()
    for event in ledger.get("events") or []:
        if not isinstance(event, dict):
            continue
        match_id = str(event.get("match_id") or "")
        if event.get("event_type") == "PREDICTION_FROZEN" and match_id:
            predictions[match_id] = event
        elif event.get("event_type") == "RESULT_SETTLED" and match_id:
            settled.add(match_id)
    return [event for match_id, event in predictions.items() if match_id not in settled]


def score_value(raw: Any) -> int | None:
    if isinstance(raw, dict):
        raw = raw.get("value") if raw.get("value") is not None else raw.get("displayValue")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or abs(value - round(value)) > 1e-9:
        return None
    return int(round(value))


def competitor_names(row: dict[str, Any]) -> list[str]:
    team = row.get("team") if isinstance(row.get("team"), dict) else {}
    values = [team.get("displayName"), team.get("shortDisplayName"), team.get("name"), team.get("location")]
    return [str(value).strip() for value in values if value]


def team_matches(cid: str, competitor: dict[str, Any], expected: str) -> bool:
    target = normalize_team_token(expected)
    for raw in competitor_names(competitor):
        try:
            canonical = canonical_team_name(cid, raw)
        except Exception:
            canonical = raw
        if normalize_team_token(canonical) == target or normalize_team_token(raw) == target:
            return True
    return False


def regulation_score(event: dict[str, Any], competition: dict[str, Any]) -> tuple[int, int, str] | None:
    competitors = competition.get("competitors") or []
    if not isinstance(competitors, list):
        return None
    home = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "home"), None)
    away = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "away"), None)
    if not isinstance(home, dict) or not isinstance(away, dict):
        return None
    status = competition.get("status") if isinstance(competition.get("status"), dict) else event.get("status") or {}
    type_block = status.get("type") if isinstance(status.get("type"), dict) else {}
    completed = bool(type_block.get("completed")) or str(type_block.get("state") or "").casefold() == "post"
    if not completed:
        return None
    try:
        period = int(status.get("period") or (event.get("status") or {}).get("period") or 0)
    except (TypeError, ValueError):
        period = 0
    label = " ".join(str(x or "").upper() for x in (type_block.get("name"), type_block.get("description"), type_block.get("detail")))
    extra = period > 2 or any(token in label for token in ("EXTRA", "PENALT", "SHOOTOUT"))
    if not extra:
        hs, as_ = score_value(home.get("score")), score_value(away.get("score"))
        return (hs, as_, "completed_final_score_regulation") if hs is not None and as_ is not None else None
    home_lines, away_lines = home.get("linescores") or [], away.get("linescores") or []
    if not isinstance(home_lines, list) or not isinstance(away_lines, list) or len(home_lines) < 2 or len(away_lines) < 2:
        return None
    h = [score_value(row) for row in home_lines[:2]]
    a = [score_value(row) for row in away_lines[:2]]
    if any(value is None for value in h + a):
        return None
    return int(sum(h)), int(sum(a)), "sum_first_two_period_linescores"


def scoreboard_dates(kickoff: datetime) -> list[str]:
    return [(kickoff + timedelta(days=delta)).strftime("%Y%m%d") for delta in (-1, 0, 1)]


def fetch_scoreboards(
    cid: str,
    kickoff: datetime,
    cache: dict[tuple[str, str], tuple[dict[str, Any] | None, str | None, str]],
) -> list[tuple[str, dict[str, Any], str]]:
    league = DOMAINS.get(cid)
    if not league:
        return []
    output=[]
    for date_token in scoreboard_dates(kickoff):
        key=(cid,date_token)
        if key not in cache:
            query=urllib.parse.urlencode({"dates":date_token,"limit":1000})
            url=f"{ESPN_BASE}/{league}/scoreboard?{query}"
            try:
                cache[key]=(get_json(url),None,url)
            except Exception as exc:
                cache[key]=(None,f"{type(exc).__name__}: {exc}",url)
        payload,error,url=cache[key]
        if payload is not None:
            output.append((date_token,payload,url))
    return output


def resolve_one(
    event: dict[str, Any],
    now: datetime,
    cache: dict[tuple[str, str], tuple[dict[str, Any] | None, str | None, str]],
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    identity=((event.get("payload") or {}).get("fixture_identity") or {})
    cid=str(identity.get("competition_id") or "")
    if cid not in DOMAINS:
        return None,"domain_unmapped",{}
    kickoff=parse_iso_datetime(str(identity.get("kickoff_at") or ""),"kickoff_at")
    matches=[]; pages=[]
    for date_token,payload,url in fetch_scoreboards(cid,kickoff,cache):
        pages.append({"date_token":date_token,"url":url})
        for raw_event in payload.get("events") or []:
            if not isinstance(raw_event,dict):
                continue
            try:
                event_kickoff=parse_iso_datetime(str(raw_event.get("date") or ""),"espn_event_date")
            except Exception:
                continue
            if abs(event_kickoff-kickoff)>KICKOFF_TOLERANCE:
                continue
            comps=raw_event.get("competitions") or []
            if not isinstance(comps,list) or not comps or not isinstance(comps[0],dict):
                continue
            comp=comps[0]; competitors=comp.get("competitors") or []
            if not isinstance(competitors,list):
                continue
            home=next((r for r in competitors if isinstance(r,dict) and r.get("homeAway")=="home"),None)
            away=next((r for r in competitors if isinstance(r,dict) and r.get("homeAway")=="away"),None)
            if not isinstance(home,dict) or not isinstance(away,dict):
                continue
            if team_matches(cid,home,str(identity.get("home_team") or "")) and team_matches(cid,away,str(identity.get("away_team") or "")):
                stable=(str(raw_event.get("id") or ""),event_kickoff.isoformat())
                matches.append((stable,raw_event,comp,event_kickoff,url,date_token))
    unique={item[0]:item for item in matches}
    if not unique:
        return None,"identity_not_found",{"pages":pages}
    if len(unique)>1:
        return None,"identity_ambiguous",{"pages":pages,"candidate_count":len(unique),"event_ids":[k[0] for k in unique]}
    _,raw_event,comp,event_kickoff,url,date_token=next(iter(unique.values()))
    score=regulation_score(raw_event,comp)
    if score is None:
        return None,"not_final_or_90m_score_unavailable",{"event_id":raw_event.get("id"),"page_date":date_token,"url":url}
    hg,ag,method=score
    result={
        "competition_id":cid,
        "source_fixture_id":str(identity.get("source_fixture_id") or ""),
        "status":"final_90",
        "settlement_scope":"90_minutes_including_stoppage",
        "home_goals_90":hg,
        "away_goals_90":ag,
        "source":{
            "name":"ESPN public soccer scoreboard API",
            "url":url,
            "observed_at":now.isoformat(),
            "source_record_id":str(raw_event.get("id") or "") or None,
        },
        "autofeed":{
            "schema_version":"V6.1.2-result-resolver-r1",
            "result_provider":"espn_public_site_api",
            "scoreboard_date_token":date_token,
            "event_kickoff_at":event_kickoff.isoformat(),
            "kickoff_difference_seconds":abs((event_kickoff-kickoff).total_seconds()),
            "regulation_score_extraction":method,
            "prediction_event_hash":event.get("event_hash"),
        },
    }
    return result,"resolved",{"event_id":raw_event.get("id"),"page_date":date_token,"url":url,"score":[hg,ag],"method":method}


def main() -> int:
    now=utcnow()
    ledger=load_json(LEDGER) if LEDGER.exists() else {"schema_version":"V6.1.2-forward-ledger-r1","events":[]}
    if ledger.get("schema_version")!="V6.1.2-forward-ledger-r1":
        raise PlatformError("invalid V6.1.2 forward ledger")
    envelope=load_results()
    existing={(str(r.get("competition_id") or ""),str(r.get("source_fixture_id") or "")):r for r in envelope["results"] if isinstance(r,dict)}
    cache:dict[tuple[str,str],tuple[dict[str,Any]|None,str|None,str]]={}
    stats:Counter=Counter(); audits=[]; generated=[]
    open_rows=open_predictions(ledger)
    for event in open_rows:
        identity=((event.get("payload") or {}).get("fixture_identity") or {})
        kickoff=parse_iso_datetime(str(identity.get("kickoff_at") or ""),"kickoff_at")
        if now<kickoff+MIN_RESULT_AGE:
            stats["not_old_enough"]+=1
            continue
        stats["eligible_for_resolution"]+=1
        key=(str(identity.get("competition_id") or ""),str(identity.get("source_fixture_id") or ""))
        if key in existing:
            stats["already_in_result_inbox"]+=1
            continue
        result,status,audit=resolve_one(event,now,cache)
        stats[status]+=1
        audits.append({"match_id":event.get("match_id"),"competition_id":key[0],"source_fixture_id":key[1],"home_team":identity.get("home_team"),"away_team":identity.get("away_team"),"kickoff_at":identity.get("kickoff_at"),"status":status,"audit":audit})
        if result is not None:
            generated.append(result)
            existing[key]=result
    if generated:
        envelope["results"].extend(generated)
        envelope["results"].sort(key=lambda r:(str(r.get("competition_id") or ""),str(r.get("source_fixture_id") or "")))
    atomic_write_json(RESULT_INBOX,envelope)
    eligible=int(stats.get("eligible_for_resolution",0))
    resolved=int(stats.get("resolved",0))+int(stats.get("already_in_result_inbox",0))
    status="PASS" if eligible==resolved else "WARN_UNRESOLVED_ELIGIBLE_RESULTS"
    payload={
        "schema_version":"V6.1.2-result-resolver-status-r1",
        "generated_at_utc":now.isoformat(),
        "status":status,
        "open_prediction_count":len(open_rows),
        "generated_result_count":len(generated),
        "result_inbox_count":len(envelope["results"]),
        "stats":dict(sorted(stats.items())),
        "audits":audits,
        "governance":{
            "existing_predictions_only":True,
            "no_prediction_generation":True,
            "no_ledger_event_mutation":True,
            "minimum_result_age_hours":2,
            "scoreboard_calendar_search":"UTC_KICKOFF_DAY_MINUS1_0_PLUS1",
            "unique_team_and_kickoff_identity_required":True,
            "extra_time_or_penalty_final_never_used_as_90m_without_regulation_linescores":True,
            "formal_probability_change":False,
            "formal_weight_change":False,
            "current_rule_change":False,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    atomic_write_json(OUT,payload)
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return 0 if status in {"PASS","WARN_UNRESOLVED_ELIGIBLE_RESULTS"} else 2


if __name__=="__main__":
    raise SystemExit(main())