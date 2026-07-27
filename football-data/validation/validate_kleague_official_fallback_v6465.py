#!/usr/bin/env python3
"""V6.46.5 validate K League official AJAX result fallback against six unresolved forward fixtures.

This validation is read-only. It fetches the official K LEAGUE attendance-result AJAX API,
extracts 90-minute score rows, and verifies unique fixture identity for the six currently
unresolved K League 1 forward predictions. No prediction or settlement ledger is modified.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_kleague_official_fallback_v6465_status.json"
BASE_PAGE = "https://www.kleague.com/record/audienceDetail.do"
API = "https://www.kleague.com/record/audienceDetailList.do"

TARGETS = [
    {"date":"2026/07/25","home_en":"Gimcheon Sangmu","away_en":"Daejeon Hana Citizen","home_ko":"김천","away_ko":"대전"},
    {"date":"2026/07/25","home_en":"Pohang Steelers","away_en":"Jeonbuk Hyundai Motors","home_ko":"포항","away_ko":"전북"},
    {"date":"2026/07/26","home_en":"FC Anyang","away_en":"Gangwon FC","home_ko":"안양","away_ko":"강원"},
    {"date":"2026/07/26","home_en":"Gwangju FC","away_en":"Jeju SK","home_ko":"광주","away_ko":"제주"},
    {"date":"2026/07/26","home_en":"Incheon United","away_en":"Bucheon FC 1995","home_ko":"인천","away_ko":"부천"},
    {"date":"2026/07/26","home_en":"FC Seoul","away_en":"Ulsan HD","home_ko":"서울","away_ko":"울산"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def request_text(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent":"Mozilla/5.0 (compatible; FASHI188-football-audit/1.0)",
        "Accept-Language":"ko-KR,ko;q=0.9,en;q=0.5",
        "Accept":"application/json,text/plain,*/*",
        "Referer":BASE_PAGE,
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")


def discover_league_id() -> tuple[str, dict[str, Any]]:
    raw = request_text(BASE_PAGE)
    m = re.search(r"<select\b[^>]*id=['\"]leagueId['\"][^>]*>(.*?)</select>", raw, flags=re.I|re.S)
    options=[]
    if m:
        for om in re.finditer(r"<option\b([^>]*)>(.*?)</option>", m.group(1), flags=re.I|re.S):
            attrs,label=om.group(1),re.sub(r"<[^>]+>"," ",om.group(2))
            label=re.sub(r"\s+"," ",label).strip()
            vm=re.search(r"value=['\"]([^'\"]*)['\"]", attrs, flags=re.I)
            value=vm.group(1) if vm else ""
            options.append({"value":value,"label":label,"selected":bool(re.search(r"\bselected\b",attrs,re.I))})
    candidates=[x for x in options if "K리그1" in x["label"] or "K League 1" in x["label"]]
    if not candidates:
        # Fallback: selected non-empty option on the current K League 1 default page.
        candidates=[x for x in options if x["selected"] and x["value"]]
    if len(candidates)!=1:
        raise RuntimeError(f"could not uniquely discover K League 1 leagueId: {candidates!r}")
    return str(candidates[0]["value"]), {"options":options,"chosen":candidates[0]}


def fetch_page(league_id: str, page: int, limit: int = 300) -> dict[str, Any]:
    q=urllib.parse.urlencode({
        "leagueId":league_id,
        "year":"2026",
        "teamId":"",
        "type":"game",
        "page":str(page),
        "limit":str(limit),
    })
    raw=request_text(API+"?"+q)
    data=json.loads(raw)
    if not isinstance(data,dict) or not isinstance(data.get("data"),dict):
        raise RuntimeError(f"unexpected K League response shape page={page}")
    return data


def norm_team(value: Any) -> str:
    s=str(value or "")
    for token in ("FC","Utd","HD","SK","상무","현대","스틸러스","하나","시티즌"):
        s=s.replace(token,"")
    return re.sub(r"[^0-9A-Za-z가-힣]","",s).lower()


def main() -> int:
    league_id, discovery = discover_league_id()
    first=fetch_page(league_id,1)
    vo=(first.get("data") or {}).get("vo") or {}
    max_page=int(vo.get("maxPage") or 1)
    pages=[first]
    for page in range(2,max_page+1):
        pages.append(fetch_page(league_id,page))

    rows=[]
    for payload in pages:
        for item in (payload.get("data") or {}).get("audienceResultList") or []:
            if isinstance(item,dict):rows.append(item)

    audits=[]
    resolved=0
    for target in TARGETS:
        matches=[]
        for row in rows:
            date=str(row.get("gameDate") or "")
            home=str(row.get("homeTeamName") or "")
            away=str(row.get("awayTeamName") or "")
            if date != target["date"]:continue
            if norm_team(target["home_ko"]) not in norm_team(home) and norm_team(home) not in norm_team(target["home_ko"]):continue
            if norm_team(target["away_ko"]) not in norm_team(away) and norm_team(away) not in norm_team(target["away_ko"]):continue
            matches.append(row)
        status="resolved" if len(matches)==1 else "identity_not_found" if not matches else "identity_ambiguous"
        audit={"target":target,"status":status,"candidate_count":len(matches)}
        if len(matches)==1:
            row=matches[0]
            audit["official_row"]={k:row.get(k) for k in ("year","meetName","gameDate","homeTeamName","awayTeamName","homeGoal","awayGoal","fieldNameFull","roundId")}
            audit["score_90"]=[int(row["homeGoal"]),int(row["awayGoal"])]
            resolved+=1
        audits.append(audit)

    payload={
        "schema_version":"V6.46.5-kleague-official-fallback-validation-r1",
        "generated_at_utc":utc_now(),
        "status":"PASS" if resolved==len(TARGETS) else "PARTIAL",
        "official_base_page":BASE_PAGE,
        "official_api":API,
        "league_id":league_id,
        "league_id_discovery":discovery,
        "max_page":max_page,
        "official_result_rows_fetched":len(rows),
        "target_count":len(TARGETS),
        "resolved_count":resolved,
        "audits":audits,
        "governance":{
            "official_source_only":True,
            "settlement_written":False,
            "third_party_score_used":False,
            "prediction_generation":False,
            "formal_weight_change":False,
            "runtime_probability_change":False,
            "current_rule_change":False,
        },
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":payload["status"],"league_id":league_id,"max_page":max_page,"rows":len(rows),"resolved":resolved,"audits":audits},ensure_ascii=False,indent=2))
    return 0 if resolved==len(TARGETS) else 2


if __name__=="__main__":
    raise SystemExit(main())
