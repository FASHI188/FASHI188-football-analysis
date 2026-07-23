#!/usr/bin/env python3
"""V6.6.12 ingest current K League 1 roster and manager context from the official league site.

The official K League club pages expose the current squad and staff. Player cards are accepted only
when they carry a shirt-number marker (No.X), which excludes coaches/analysts/scouts. Duplicate
aliases sharing the same shirt number are collapsed fail-conservatively. The page's explicit
"YYYY.MM.DD 현재" as-of date must be fresh. One official page is sufficient for the existing tier-1
roster/manager evidence contracts. Research context only; no probability or formal-weight changes.
"""
from __future__ import annotations

import html
import json
import re
import time
import unicodedata
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROSTER_DIR = ROOT / "evidence" / "team_current_roster_weekly"
MANAGER_DIR = ROOT / "evidence" / "team_manager_context_weekly"
STATUS = ROOT / "manifests" / "v6_kleague_official_context_v6612_status.json"
BASE = "https://www.kleague.com/club/club.do?teamId={}"
UA = "football-v6.6.12-kleague-official-context/1.0"
MIN_PLAYERS = 18
MAX_PLAYERS = 60
FRESH_DAYS = 8

# Identity is intentionally tied to the project's existing 2026 K League 1 names.
TEAMS: dict[str, dict[str, str]] = {
    "FC Seoul": {"team_id": "K09", "short": "서울"},
    "Jeonbuk Hyundai Motors": {"team_id": "K05", "short": "전북"},
    "Pohang Steelers": {"team_id": "K03", "short": "포항"},
    "Ulsan HD": {"team_id": "K01", "short": "울산"},
    "Gangwon FC": {"team_id": "K21", "short": "강원"},
    "Incheon United": {"team_id": "K18", "short": "인천"},
    "FC Anyang": {"team_id": "K27", "short": "안양"},
    "Jeju SK": {"team_id": "K04", "short": "제주"},
    "Bucheon FC 1995": {"team_id": "K26", "short": "부천"},
    "Daejeon Hana Citizen": {"team_id": "K10", "short": "대전"},
    "Gimcheon Sangmu": {"team_id": "K24", "short": "김천"},
    "Gwangju FC": {"team_id": "K22", "short": "광주"},
}


class TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", html.unescape(data)).strip()
        if value:
            self.parts.append(value)


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip().casefold()


def fetch(url: str) -> tuple[str, datetime, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    try:
        text = raw.decode(charset, errors="strict")
    except Exception:
        try:
            text = raw.decode("utf-8", errors="strict")
        except Exception:
            text = raw.decode("cp949", errors="replace")
    observed = datetime.now(timezone.utc).replace(microsecond=0)
    return text, observed, charset


def page_tokens(markup: str) -> list[str]:
    parser = TextCollector(); parser.feed(markup); parser.close(); return parser.parts


def parse_as_of(joined: str) -> tuple[date | None, int | None]:
    match = re.search(r"(20\d{2})\.(\d{2})\.(\d{2})\s*현재", joined)
    if not match:
        return None, None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))), match.start()
    except Exception:
        return None, match.start()


def parse_roster_and_coach(markup: str, team_short: str, observed: datetime) -> dict[str, Any]:
    tokens = page_tokens(markup)
    joined = "\n".join(tokens)
    as_of, cutoff = parse_as_of(joined)
    prefix = joined[:cutoff] if cutoff is not None else joined
    short = re.escape(team_short)

    player_pattern = re.compile(
        rf"(?:^|\n)(?P<name>[^\n]{{1,80}}?)\s+(?:\n\s*)?{short}\s+(?:\n\s*)?No\.\s*(?P<number>\d{{1,3}})(?=\s|\n|$)",
        re.MULTILINE,
    )
    raw_players=[]
    for match in player_pattern.finditer(prefix):
        name=re.sub(r"\s+", " ", match.group("name")).strip(" -|\t")
        number=match.group("number")
        if name and 1 <= len(name) <= 80:
            raw_players.append({"player_name": name, "shirt_number": number, "positions": [], "squad_status": "official-current-roster"})

    # Official pages occasionally expose the same player twice (e.g. alias/duplicate card). One
    # active shirt number is counted once; names are also unique. This biases counts downward, not up.
    by_number: dict[str, dict[str, Any]] = {}
    seen_names=set(); duplicate_rows=[]
    for player in raw_players:
        nk=norm(player["player_name"]); num=str(player["shirt_number"])
        if nk in seen_names or num in by_number:
            duplicate_rows.append(player); continue
        seen_names.add(nk); by_number[num]=player
    players=list(by_number.values())
    players.sort(key=lambda p: int(str(p["shirt_number"])))

    coach_pattern = re.compile(
        rf"(?:^|\n)(?P<name>[^\n]{{1,80}}?)\s+(?:\n\s*)?{short}\s+(?:\n\s*)?감독(?=\s|\n|$)",
        re.MULTILINE,
    )
    coaches=[]
    for match in coach_pattern.finditer(prefix):
        name=re.sub(r"\s+", " ", match.group("name")).strip(" -|\t")
        if name and norm(name) not in {norm(v) for v in coaches}:
            coaches.append(name)

    fresh = as_of is not None and 0 <= (observed.date()-as_of).days <= FRESH_DAYS
    return {
        "tokens": len(tokens),
        "official_as_of_date": as_of.isoformat() if as_of else None,
        "as_of_fresh": fresh,
        "players": players,
        "raw_player_rows": len(raw_players),
        "duplicate_player_rows_collapsed": len(duplicate_rows),
        "coaches": coaches,
    }


def latest_prior_managers() -> dict[str, str]:
    latest: dict[str, tuple[datetime,str]] = {}
    if not MANAGER_DIR.exists(): return {}
    for path in MANAGER_DIR.glob("*.json"):
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        records=payload.get("records") if isinstance(payload,dict) else None
        records=records if isinstance(records,list) else [payload] if isinstance(payload,dict) else []
        for record in records:
            if not isinstance(record,dict) or record.get("competition_id")!="KOR_KLeague1": continue
            team=str(record.get("team_name") or ""); coach=((record.get("head_coach") or {}).get("name") if isinstance(record.get("head_coach"),dict) else None)
            if team not in TEAMS or not coach: continue
            try: ts=datetime.fromisoformat(str(record.get("observed_at_utc") or "").replace("Z","+00:00"))
            except Exception: continue
            prev=latest.get(team)
            if prev is None or ts>prev[0]: latest[team]=(ts,str(coach))
    return {team:value[1] for team,value in latest.items()}


def main() -> int:
    generated=datetime.now(timezone.utc).replace(microsecond=0); stamp=generated.strftime("%Y%m%dT%H%M%SZ")
    ROSTER_DIR.mkdir(parents=True,exist_ok=True); MANAGER_DIR.mkdir(parents=True,exist_ok=True); STATUS.parent.mkdir(parents=True,exist_ok=True)
    prior=latest_prior_managers(); roster_records=[]; manager_records=[]; audit=[]

    for team_name,meta in TEAMS.items():
        url=BASE.format(meta["team_id"]); row={"team_name":team_name,"team_id":meta["team_id"],"source_url":url,"status":"FETCH_FAILED"}
        try:
            markup,source_observed,charset=fetch(url); parsed=parse_roster_and_coach(markup,meta["short"],source_observed)
            row.update({"http_charset":charset,"official_as_of_date":parsed["official_as_of_date"],"as_of_fresh":parsed["as_of_fresh"],"raw_player_rows":parsed["raw_player_rows"],"player_count":len(parsed["players"]),"duplicate_player_rows_collapsed":parsed["duplicate_player_rows_collapsed"],"coach_candidates":parsed["coaches"]})
            roster_ok=parsed["as_of_fresh"] and MIN_PLAYERS<=len(parsed["players"])<=MAX_PLAYERS
            coach_ok=parsed["as_of_fresh"] and len(parsed["coaches"])==1
            if roster_ok:
                roster_records.append({
                    "schema_version":"V6.6.9-current-roster-overlay-r1","competition_id":"KOR_KLeague1","team_name":team_name,"observed_at_utc":generated.isoformat(),"roster_semantics":"CURRENT_REGISTERED_SQUAD","players":parsed["players"],
                    "sources":[{"source_name":"K League official club squad page","source_url":url,"source_tier":"tier_1_official","provider_group":"kleague_official","source_observed_at_utc":source_observed.isoformat(),"source_role":"current_registered_squad"}],
                    "source_metadata":{"kleague_team_id":meta["team_id"],"official_page_as_of_date":parsed["official_as_of_date"],"duplicate_player_rows_collapsed":parsed["duplicate_player_rows_collapsed"]},
                    "governance":{"current_at_observation_time":True,"single_source_player_list":True,"cross_source_union":False,"official_page_freshness_checked":True,"research_context_only":True,"formal_probability_use":False}
                })
            if coach_ok:
                coach=parsed["coaches"][0]; previous=prior.get(team_name)
                if previous is None: change={"status":"BASELINE_ESTABLISHED","previous_manager":None,"changed_at_utc":None,"note":"First verified official K League manager baseline in this evidence stream."}
                elif norm(previous)==norm(coach): change={"status":"UNCHANGED","previous_manager":previous,"changed_at_utc":None,"note":"Current official K League manager matches the previous verified weekly record."}
                else: change={"status":"CHANGED_CONFIRMED","previous_manager":previous,"changed_at_utc":None,"note":"Current official K League manager differs from the previous verified weekly record; exact appointment time is not inferred."}
                manager_records.append({
                    "schema_version":"V6.6.3-team-manager-context-r1","competition_id":"KOR_KLeague1","team_name":team_name,"observed_at_utc":generated.isoformat(),"head_coach":{"name":coach},"manager_change":change,
                    "sources":[{"source_name":"K League official club squad/staff page","source_url":url,"source_tier":"tier_1_official","provider_group":"kleague_official","source_observed_at_utc":source_observed.isoformat(),"source_role":"current_head_coach"}],
                    "source_metadata":{"kleague_team_id":meta["team_id"],"official_page_as_of_date":parsed["official_as_of_date"]},
                    "governance":{"pit_current":True,"official_page_freshness_checked":True,"research_context_only":True,"formal_probability_use":False}
                })
            row["roster_gate"]="PASS" if roster_ok else "FAIL"; row["manager_gate"]="PASS" if coach_ok else "FAIL"; row["status"]="PASS_BOTH" if roster_ok and coach_ok else "PARTIAL" if roster_ok or coach_ok else "FAIL_CLOSED"
        except Exception as exc:
            row["error"]=f"{type(exc).__name__}: {exc}"
        audit.append(row); time.sleep(0.20)

    roster_path=ROSTER_DIR/f"kleague_current_rosters__{stamp}.json"
    manager_path=MANAGER_DIR/f"kleague_managers__{stamp}.json"
    if roster_records:
        roster_payload={"schema_version":"V6.6.12-kleague-current-roster-weekly-aggregate-r1","observed_at_utc":generated.isoformat(),"records":roster_records,"governance":{"official_kleague_source":True,"strict_overlay_evidence":True,"formal_probability_use":False}}
        roster_path.write_text(json.dumps(roster_payload,ensure_ascii=False,indent=2),encoding="utf-8")
    if manager_records:
        manager_payload={"schema_version":"V6.6.12-kleague-manager-weekly-aggregate-r1","observed_at_utc":generated.isoformat(),"records":manager_records,"governance":{"official_kleague_source":True,"formal_probability_use":False}}
        manager_path.write_text(json.dumps(manager_payload,ensure_ascii=False,indent=2),encoding="utf-8")

    roster_n=len(roster_records); manager_n=len(manager_records)
    status="PASS_COMPLETE" if roster_n==len(TEAMS) and manager_n==len(TEAMS) else "WARN_PARTIAL" if roster_n or manager_n else "FAIL_NO_VALID_EVIDENCE"
    payload={
        "schema_version":"V6.6.12-kleague-official-context-status-r1","generated_at_utc":generated.isoformat(),"status":status,"formal_current_version":"V5.0.1","team_target_count":len(TEAMS),"valid_current_roster_count":roster_n,"valid_manager_count":manager_n,
        "roster_evidence_path":str(roster_path.relative_to(ROOT)) if roster_records else None,"manager_evidence_path":str(manager_path.relative_to(ROOT)) if manager_records else None,"audit":audit,
        "governance":{"official_source_only":True,"explicit_page_as_of_freshness_required":True,"shirt_number_required_for_player_card":True,"duplicate_shirt_numbers_collapsed":True,"staff_excluded_from_roster":True,"no_cross_source_union":True,"research_context_only":True,"formal_probability_change":False,"formal_weight_change":False}
    }
    STATUS.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2))
    return 0 if status!="FAIL_NO_VALID_EVIDENCE" else 2

if __name__=="__main__": raise SystemExit(main())