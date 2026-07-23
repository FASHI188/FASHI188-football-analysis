#!/usr/bin/env python3
"""V6.6.12 build current K League active match pools from official match sheets.

This scraper scans 2026 K League 1 official `pdfDownload.do` match sheets, extracts the two
match squads, and for each registered project team unions only its three most recent completed
match squads from the SAME official provider. The result is ACTIVE_MATCH_POOL research evidence,
not a registered/current first-team roster and never satisfies the strict roster gate.

The latest sufficiently fresh official match sheet can also seed a V6.6.3 manager record because
it explicitly identifies the coach at that match. No post-observation or future match is used.
The run is a named no-formal-mutation evidence epoch under CURRENT V5.0.1.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "v6_kleague_active_pool_v6612.json"
OUTROOT = ROOT / "evidence" / "team_active_match_pool_weekly"
MANAGER_ROOT = ROOT / "evidence" / "team_manager_context_weekly"
STATUS = ROOT / "manifests" / "v6_kleague_active_pool_v6612_status.json"
BASE = "https://www.kleague.com/match/pdfDownload.do?gameId={game_id}&meetSeq=1&year=2026"
UA = "football-v6.6.12-kleague-active-pool/1.0"
POSITIONS = {"GK", "DF", "MF", "FW"}

TEAM_ALIASES = {
    "FC Seoul": ["FC서울", "서울"],
    "Jeonbuk Hyundai Motors": ["전북 현대", "전북"],
    "Pohang Steelers": ["포항 스틸러스", "포항"],
    "Ulsan HD": ["울산 HD", "울산"],
    "Gangwon FC": ["강원FC", "강원 FC", "강원"],
    "Incheon United": ["인천 Utd", "인천 UTD", "인천 유나이티드", "인천"],
    "FC Anyang": ["FC안양", "FC 안양", "안양"],
    "Jeju SK": ["제주SK FC", "제주 SK FC", "제주SK", "제주"],
    "Bucheon FC 1995": ["부천FC1995", "부천 FC1995", "부천"],
    "Daejeon Hana Citizen": ["대전 하나 시티즌", "대전하나시티즌", "대전"],
    "Gimcheon Sangmu": ["김천 상무", "김천상무", "김천"],
    "Gwangju FC": ["광주FC", "광주 FC", "광주"],
}


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_tr = False
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.in_tr = True; self.row = []
        elif tag in {"td", "th"} and self.in_tr:
            self.in_cell = True; self.cell_parts = []
        elif tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.in_cell:
            value = " ".join("".join(self.cell_parts).split())
            self.row.append(value)
            self.in_cell = False; self.cell_parts = []
        elif tag == "tr" and self.in_tr:
            if self.row: self.rows.append(self.row)
            self.in_tr = False; self.row = []
        elif tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_data(self, data):
        value = html.unescape(data)
        self.text_parts.append(value)
        if self.in_cell:
            self.cell_parts.append(value)

    def text(self) -> str:
        return re.sub(r"[ \t]+", " ", "".join(self.text_parts)).replace("\r", "")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def fetch(game_id: int) -> tuple[str | None, str | None]:
    url = BASE.format(game_id=game_id)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return raw, None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def find_teams_and_managers(text: str) -> list[tuple[str, str, str]]:
    found = []
    for canonical, aliases in TEAM_ALIASES.items():
        best = None
        for alias in sorted(aliases, key=len, reverse=True):
            pattern = re.compile(re.escape(alias) + r"\s*감독\s*:\s*([가-힣A-Za-zÀ-ÿ·.\- ]{2,40}?)(?=\s+(?:\d+승|\||FP|GK|배번|경기감독관|[가-힣A-Za-z]+\s*감독)|\n)")
            m = pattern.search(text)
            if m:
                manager = " ".join(m.group(1).split()).strip()
                best = (canonical, manager, alias); break
        if best: found.append(best)
    return sorted(found, key=lambda item: min((text.find(a) for a in TEAM_ALIASES[item[0]] if text.find(a) >= 0), default=10**9))[:2]


def parse_date(text: str) -> datetime | None:
    m = re.search(r"(2026)/(\d{2})/(\d{2})\([^)]*\)\s+(\d{2}):(\d{2})", text)
    if not m: return None
    kst = timezone(timedelta(hours=9))
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), tzinfo=kst).astimezone(timezone.utc)


def clean_player_name(value: str) -> str:
    value = re.sub(r"\(\d{2}\).*", "", value)
    value = re.sub(r"[★☆↓ⓗ]+", "", value)
    return " ".join(value.split()).strip()


def player_segments(rows: list[list[str]]) -> list[list[dict[str, str]]]:
    segments: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] | None = None
    for cells in rows:
        joined = " ".join(cells)
        if "배번" in joined and "위치" in joined and "성명" in joined:
            if current is not None and current:
                segments.append(current)
            current = []
            continue
        if current is None or len(cells) < 3:
            continue
        number = cells[0].strip(); pos = cells[1].strip().upper(); name = clean_player_name(cells[2])
        if re.fullmatch(r"\d{1,3}", number) and pos in POSITIONS and name:
            current.append({"shirt_number": number, "position": pos, "player_name": name})
    if current:
        segments.append(current)
    viable = [s for s in segments if len({p['player_name'] for p in s}) >= 15]
    return viable[:2]


def parse_match(game_id: int, raw: str, observed: datetime) -> dict[str, Any] | None:
    parser = TableParser(); parser.feed(raw); text = parser.text()
    if "K리그1 2026" not in text or "출전 선수 명단" not in text:
        return None
    kickoff = parse_date(text)
    if kickoff is None or kickoff >= observed:
        return None
    tm = find_teams_and_managers(text); segments = player_segments(parser.rows)
    if len(tm) != 2 or len(segments) != 2:
        return None
    teams = []
    for (canonical, manager, source_label), segment in zip(tm, segments):
        dedup = {}
        for p in segment: dedup.setdefault(p["player_name"], p)
        teams.append({"team_name": canonical, "source_label": source_label, "manager_name": manager, "players": list(dedup.values())})
    return {"game_id": game_id, "kickoff_utc": kickoff.replace(microsecond=0).isoformat(), "source_url": BASE.format(game_id=game_id), "teams": teams}


def main() -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8")); observed = utcnow(); matches = []; scan_errors = []
    for game_id in range(1, 181):
        raw, error = fetch(game_id)
        if raw:
            parsed = parse_match(game_id, raw, observed)
            if parsed: matches.append(parsed)
        elif error and game_id <= 130:
            scan_errors.append({"game_id": game_id, "error": error})
        time.sleep(0.03)
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        for team in match["teams"]:
            by_team[team["team_name"]].append({"game_id": match["game_id"], "kickoff_utc": match["kickoff_utc"], "source_url": match["source_url"], **team})
    lookback = int(cfg["lookback_matches_per_team"]); max_age = timedelta(days=int(cfg["maximum_latest_match_age_days"])); min_players = int(cfg["minimum_unique_active_players"])
    records = []; manager_records = []; team_audit = []
    for canonical in TEAM_ALIASES:
        appearances = sorted(by_team.get(canonical, []), key=lambda x: x["kickoff_utc"], reverse=True); recent = appearances[:lookback]
        if not recent: team_audit.append({"team_name": canonical, "status": "NO_OFFICIAL_MATCH_SHEET"}); continue
        latest_time = datetime.fromisoformat(recent[0]["kickoff_utc"])
        if observed - latest_time > max_age: team_audit.append({"team_name": canonical, "status": "LATEST_MATCH_TOO_OLD", "latest_match_utc": recent[0]["kickoff_utc"]}); continue
        pool = {}; sources = []
        for item in recent:
            for p in item["players"]: pool.setdefault(p["player_name"], p)
            sources.append({"source_name": "K League official 2026 match squad", "source_url": item["source_url"], "source_tier": "tier_1_official", "provider_group": "kleague_official", "source_observed_at_utc": observed.isoformat(), "match_kickoff_utc": item["kickoff_utc"], "game_id": item["game_id"]})
        status = "ACTIVE_MATCH_POOL_AVAILABLE" if len(pool) >= min_players else "ACTIVE_POOL_BELOW_MIN"
        records.append({"schema_version": "V6.6.12-kleague-active-match-pool-r1", "observed_at_utc": observed.isoformat(), "competition_id": "KOR_KLeague1", "team_name": canonical, "status": status, "source_semantics": "OFFICIAL_RECENT_MATCH_SQUAD", "lookback_match_count": len(recent), "latest_match_utc": recent[0]["kickoff_utc"], "active_players": list(pool.values()), "active_player_count": len(pool), "sources": sources, "governance": {"research_context_only": True, "active_match_pool_is_not_registered_roster": True, "strict_current_roster_eligible": False, "formal_probability_use": False}})
        team_audit.append({"team_name": canonical, "status": status, "active_player_count": len(pool), "latest_match_utc": recent[0]["kickoff_utc"], "matches_used": [x["game_id"] for x in recent]})
        latest_manager = recent[0].get("manager_name")
        if latest_manager and observed - latest_time <= timedelta(days=8):
            manager_records.append({"schema_version": "V6.6.3-team-manager-context-r1", "competition_id": "KOR_KLeague1", "team_name": canonical, "observed_at_utc": observed.isoformat(), "head_coach": {"name": latest_manager}, "manager_change": {"status": "UNKNOWN", "previous_manager": None, "changed_at_utc": None, "note": "Current manager verified from latest official K League match sheet; change status not inferred."}, "sources": [{"source_name": "K League official latest match squad", "source_url": recent[0]["source_url"], "source_tier": "tier_1_official", "provider_group": "kleague_official", "source_observed_at_utc": observed.isoformat()}], "governance": {"pit_current": True, "research_context_only": True, "formal_probability_use": False}})
    OUTROOT.mkdir(parents=True, exist_ok=True); MANAGER_ROOT.mkdir(parents=True, exist_ok=True); stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    evidence_path = OUTROOT / f"kleague_active_pool__{stamp}.json"; evidence_path.write_text(json.dumps({"schema_version": "V6.6.12-kleague-active-match-pool-weekly-r1", "observed_at_utc": observed.isoformat(), "record_count": len(records), "records": records, "governance": cfg["hard_rules"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    manager_path = None
    if manager_records:
        manager_path = MANAGER_ROOT / f"weekly_managers_kleague__{stamp}.json"; manager_path.write_text(json.dumps({"schema_version": "V6.6.3-team-manager-weekly-aggregate-r1", "observed_at_utc": observed.isoformat(), "records": manager_records, "governance": {"official_kleague_match_sheet_seed": True, "formal_probability_use": False}}, ensure_ascii=False, indent=2), encoding="utf-8")
    available = sum(1 for x in records if x["status"] == "ACTIVE_MATCH_POOL_AVAILABLE")
    payload = {"schema_version": "V6.6.12-kleague-active-match-pool-status-r1", "generated_at_utc": observed.isoformat(), "status": "PASS" if available >= 8 else "WARN_LOW_COVERAGE", "official_matches_parsed": len(matches), "teams_with_any_match_sheet": len(by_team), "active_match_pool_available_count": available, "manager_records_generated": len(manager_records), "evidence_path": str(evidence_path.relative_to(ROOT)), "manager_evidence_path": str(manager_path.relative_to(ROOT)) if manager_path else None, "team_audit": team_audit, "scan_error_count": len(scan_errors), "scan_errors_sample": scan_errors[:20], "governance": {"strict_current_roster_gate_unchanged": True, "same_official_provider_recent_match_union_only": True, "formal_probability_change": False, "formal_weight_change": False}}
    STATUS.parent.mkdir(parents=True, exist_ok=True); STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0

if __name__ == "__main__": raise SystemExit(main())
