#!/usr/bin/env python3
"""Football3 Formal Fast Prediction Runtime V1.

Runtime/governance layer only. Scientific predictions are delegated unchanged to:
- new_engine_v1.pure_engine
- historical_xg_challenger_v1.historical_xg_challenger
- new_engine_v1.formal_fusion_v2
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from historical_xg_challenger_v1 import historical_xg_challenger as hxg
from new_engine_v1 import formal_fusion_v2 as formal_v2
from new_engine_v1 import pure_engine as v1_engine

SCHEMA = "football3-formal-fast-runtime-v1"
BUNDLE_SCHEMA = "football3-production-state-bundle-v1"
INPUT_SCHEMA = "football3-fast-runtime-input-v1"
DELTA_SCHEMA = "football3-runtime-delta-v1"
RECEIPT_SCHEMA = "football3-prediction-receipt-v1"

FORMAL_BRANCH = "football3/historical-xg-fusion-v2-formal-activation-v1"
FORMAL_HEAD = "e12f5d1193be5d81f60301cf34ab2140e11712a9"
FORMAL_PARENT = "2ab29bbd7a301fc12a284afa1cc51c4c904e2888"
CURRENT_SHA256 = "ecf5fb99aaf2eb551c2c06bc5d37d3c656a2a1fcc280fd52045986f5894874f8"
BASE_HISTORY_CUTOFF = "2026-05-25T00:00:00+00:00"
MIN_CUTOFF_LEAD = timedelta(minutes=60)

FORMAL_SCOPE = (
    "ARG_Primera","BRA_SerieA","ENG_PremierLeague","ESP_LaLiga","FRA_Ligue1",
    "GER_Bundesliga","ITA_SerieA","JPN_J1","KOR_KLeague1","NED_Eredivisie",
    "NOR_Eliteserien","POR_PrimeiraLiga","SCO_Premiership","SUI_SuperLeague",
    "SWE_Allsvenskan","USA_MLS",
)
TRAINING_SEASONS = {
    "ARG_Primera":("2022","2023","2024","2025"),
    "BRA_SerieA":("2022","2023","2024","2025"),
    "ENG_PremierLeague":("2022/23","2023/24","2024/25","2025/26"),
    "ESP_LaLiga":("2022/23","2023/24","2024/25","2025/26"),
    "FRA_Ligue1":("2022/23","2023/24","2024/25","2025/26"),
    "GER_Bundesliga":("2022/23","2023/24","2024/25","2025/26"),
    "ITA_SerieA":("2022/23","2023/24","2024/25","2025/26"),
    "JPN_J1":("2022","2023","2024","2025"),
    "KOR_KLeague1":("2022","2023","2024","2025"),
    "NED_Eredivisie":("2022/23","2023/24","2024/25","2025/26"),
    "NOR_Eliteserien":("2022","2023","2024","2025"),
    "POR_PrimeiraLiga":("2022/23","2023/24","2024/25","2025/26"),
    "SCO_Premiership":("2022/23","2023/24","2024/25","2025/26"),
    "SUI_SuperLeague":("2022/23","2023/24","2024/25","2025/26"),
    "SWE_Allsvenskan":("2022","2023","2024","2025"),
    "USA_MLS":("2022","2023","2024","2025"),
}
BIG5 = {
    "Bundesliga":"GER_Bundesliga",
    "EPL":"ENG_PremierLeague",
    "La liga":"ESP_LaLiga",
    "Ligue 1":"FRA_Ligue1",
    "Serie A":"ITA_SerieA",
}

# Immutable model/source bindings. Git blob identities are independently checked in the exact-HEAD workflow.
BINDINGS = {
    "formal_fusion_v2.py":{
        "git_blob":"a5ed26d5ffd4a2875cb9c658cfaa28665a8b7871",
        "sha256":"ae318daec65deaedca15951cfec626d86881bb25ce3481fa4c66a0e6f374c37a",
    },
    "historical_xg_challenger.py":{
        "git_blob":"0db21270f6b1332936a6f18cce197afb948efbc7",
        "sha256":"588cad84f59e8a6b31297984c2c9015af5275f209dea8d4e3a3d71183e762ba2",
        "decompressed_sha256":"b220ebb20bf64c4b58fc8d3b40c3c9b6f51a649aab129abbb4a3b9984e9e5936",
    },
    "pure_engine.py":{
        "git_blob":"deae7ded3b36f2cf28dcfa7823a042c329f037d2",
        "sha256":"cc2c2c3eca421ad6d277107b8f1212656b2e943cc179e7f394ac53e916c3f318",
    },
    "model_lock.json":{"git_blob":"c9253391361b8a3a8c0dba6d674d5dae293d9949"},
    "fusion_data_identity.json":{"git_blob":"642b2a322e956b05c615ed5482d47479c1e88126"},
    "formal_wiring_contract.json":{"git_blob":"523b1a9062ad63d2cca3f713cde72e0d4abb659f"},
    "formal_pointer.json":{"git_blob":"f07743f9c873d415fad9612f3225f64c64b10542"},
    "platform_core.py":{"git_blob":"0a2f3d20ea2746245552653984c6092c253e8272"},
    "understat_frozen.db":{"sha256":"f102eae39b4036a4c24e5b75b9cee551064cf1e7d4fd028966cd62a5784d8681"},
    "confirmation_identity.jsonl":{"sha256":"c861032ea523cb921eebc7455941e2a71ecb2f3ecc3a8df6858eef0b7aaf8d95"},
    "confirmation_xg_result_vault.jsonl":{"sha256":"444edf8005d6dce3367701cd665947c3e6f852a27a4b6fb15ce0db430f476990"},
}
EXPECTED_V1_N = 20746
EXPECTED_V1_FIRST = "2022-02-18T00:00:00+00:00"
EXPECTED_V1_LAST = "2026-05-24T00:00:00+00:00"
EXPECTED_XG_OLD_N = 3578
EXPECTED_XG_JOIN_N = 5330

PAYLOAD_NAMES = (
    "bundle_meta.json","identity_map.json","source_identity.json",
    "state_v1.json","state_xg.json","state_build_receipt.json",
)

class RuntimeGateError(RuntimeError):
    pass

def _canon_bytes(obj: Any) -> bytes:
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")

def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _sha_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()

def _parse_dt(value: str, field: str) -> datetime:
    if not isinstance(value,str) or not value.strip():
        raise RuntimeGateError(f"{field}: empty datetime")
    try:
        d=datetime.fromisoformat(value.strip().replace("Z","+00:00"))
    except ValueError as exc:
        raise RuntimeGateError(f"{field}: invalid ISO datetime") from exc
    if d.tzinfo is None:
        raise RuntimeGateError(f"{field}: timezone required")
    return d.astimezone(timezone.utc)

def _iso(d: datetime | None) -> str | None:
    return None if d is None else d.astimezone(timezone.utc).isoformat()

def _normalize_team(value: str) -> str:
    # Frozen V1 historical identity tokenization, reproduced only as an identity adapter.
    import re, unicodedata
    text=unicodedata.normalize("NFKC",str(value or "")).casefold().strip()
    text=text.replace("&"," and ")
    text=re.sub(r"\b(fc|cf|afc|sc|ac|sv|fk|sk|club|football|calcio)\b"," ",text)
    text=re.sub(r"[^0-9a-z\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+","",text)
    return text

def _global_team_id(name: str) -> str:
    token=_normalize_team(name)
    if not token:
        raise RuntimeGateError("empty canonical team token")
    return "gteam_"+hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

def _fixture_id(comp: str, season: str, kickoff: datetime, home: str, away: str) -> str:
    raw="|".join((comp,season,kickoff.isoformat(),home,away))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

def _season_years(season: str) -> tuple[int|None,int|None]:
    import re
    token=str(season).strip()
    m=re.fullmatch(r"(20\d{2})[/-](\d{2})",token)
    if m:
        y=int(m.group(1)); return y,y+1
    if re.fullmatch(r"20\d{2}",token):
        y=int(token); return y,y
    return None,None

def _parse_match_date(value: str, season: str) -> datetime:
    raw=str(value or "").strip()
    if not raw:
        raise RuntimeGateError("empty match date")
    for fmt in ("%d/%m/%Y","%d/%m/%y","%Y-%m-%d","%Y.%m.%d","%d.%m.%Y","%a %b %d %Y","%A %b %d %Y","%b %d %Y"):
        try:
            return datetime.strptime(raw,fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    first,second=_season_years(season)
    for fmt in ("%a %b %d","%A %b %d","%b %d"):
        try:
            p=datetime.strptime(raw+" 2000",fmt+" %Y")
        except ValueError:
            continue
        if first is None:
            raise RuntimeGateError("date lacks resolvable year")
        return p.replace(year=first if p.month>=7 else (second or first),tzinfo=timezone.utc)
    raise RuntimeGateError(f"unsupported match date: {raw!r}")

@dataclass(frozen=True)
class HistoryFixture:
    fixture_id: str
    competition_id: str
    season: str
    kickoff: datetime
    home_team_id: str
    away_team_id: str
    home_team_name: str
    away_team_name: str
    home_goals: int
    away_goals: int
    source_path: str
    source_sha256: str

    def xg_fixture(self) -> hxg.FixtureRow:
        return hxg.FixtureRow(self.fixture_id,self.competition_id,self.season,self.kickoff,
                              self.home_team_id,self.away_team_id,self.home_team_name,self.away_team_name)
    def v1_fixture(self) -> v1_engine.Fixture:
        return v1_engine.Fixture(self.fixture_id,self.competition_id,self.season,self.kickoff,
                                 self.home_team_id,self.away_team_id)

@dataclass(frozen=True)
class XGLabel:
    label: hxg.ReleasedLabel
    source_fixture_id: str
    source_sha256: str
    source_kickoff: str

def _read_aliases(repo_root: Path) -> dict[str,dict[str,str]]:
    p=repo_root/"football-data/config/team_aliases.json"
    if not p.exists():
        return {}
    d=json.loads(p.read_text(encoding="utf-8"))
    x=d.get("competitions",{})
    if type(x) is not dict:
        raise RuntimeGateError("team aliases schema mismatch")
    return x

def _canonical_team(comp: str, raw: str, aliases: dict[str,dict[str,str]]) -> str:
    mapping=aliases.get(comp,{})
    if raw in mapping:
        return str(mapping[raw]).strip()
    n=_normalize_team(raw)
    hits=[str(v).strip() for k,v in mapping.items() if _normalize_team(k)==n]
    if len(set(hits))>1:
        raise RuntimeGateError(f"ambiguous alias: {comp} {raw}")
    return hits[0] if hits else str(raw).strip()

def load_frozen_v1_history(repo_root: Path) -> tuple[list[HistoryFixture],dict[str,Any]]:
    """Mechanically load the exact Frozen V1 training-universe rows from repository CSVs."""
    aliases=_read_aliases(repo_root)
    rows:list[HistoryFixture]=[]
    files:dict[str,dict[str,Any]]={}
    seen:set[str]=set()
    for comp in FORMAL_SCOPE:
        directory=repo_root/"football-data/processed"/comp
        if not directory.is_dir():
            raise RuntimeGateError(f"missing processed competition directory: {comp}")
        allowed=set(TRAINING_SEASONS[comp])
        for path in sorted(directory.glob("*.csv")):
            source_sha=_sha_file(path)
            used=0
            with path.open("r",encoding="utf-8-sig",newline="") as f:
                reader=csv.DictReader(f)
                for raw in reader:
                    season=str(raw.get("season") or "").strip()
                    if season not in allowed:
                        continue
                    kickoff=_parse_match_date(str(raw.get("Date") or raw.get("date") or ""),season)
                    # Read labels only after this row is mechanically accepted into the frozen universe.
                    home=_canonical_team(comp,str(raw.get("HomeTeam") or raw.get("home_team") or ""),aliases)
                    away=_canonical_team(comp,str(raw.get("AwayTeam") or raw.get("away_team") or ""),aliases)
                    try:
                        hg=int(str(raw.get("FTHG") or raw.get("home_goals") or "").strip())
                        ag=int(str(raw.get("FTAG") or raw.get("away_goals") or "").strip())
                    except ValueError as exc:
                        raise RuntimeGateError(f"invalid goals: {path}") from exc
                    if hg<0 or ag<0 or hg>30 or ag>30 or home==away:
                        raise RuntimeGateError(f"invalid historical row: {path}")
                    fid=_fixture_id(comp,season,kickoff,home,away)
                    if fid in seen:
                        raise RuntimeGateError(f"duplicate frozen V1 fixture: {fid}")
                    seen.add(fid); used+=1
                    rows.append(HistoryFixture(fid,comp,season,kickoff,_global_team_id(home),_global_team_id(away),
                                               home,away,hg,ag,str(path.relative_to(repo_root)),source_sha))
            if used:
                files[str(path.relative_to(repo_root))]={"sha256":source_sha,"bytes":path.stat().st_size,"used_rows":used}
    rows.sort(key=lambda r:(r.kickoff,r.competition_id,r.fixture_id))
    if len(rows)!=EXPECTED_V1_N:
        raise RuntimeGateError(f"Frozen V1 universe count mismatch: {len(rows)} != {EXPECTED_V1_N}")
    if not rows or rows[0].kickoff.isoformat()!=EXPECTED_V1_FIRST or rows[-1].kickoff.isoformat()!=EXPECTED_V1_LAST:
        raise RuntimeGateError("Frozen V1 universe boundary mismatch")
    return rows,{"files":files,"n":len(rows),"first":rows[0].kickoff.isoformat(),"last":rows[-1].kickoff.isoformat()}

def _xg_join_key(comp: str, dt: datetime, home: str, away: str) -> tuple[str,str,str,str]:
    return comp,dt.date().isoformat(),_normalize_team(home),_normalize_team(away)

def load_xg_labels(history: list[HistoryFixture], understat_db: Path, confirmation_dir: Path) -> tuple[dict[str,XGLabel],dict[str,Any]]:
    if _sha_file(understat_db)!=BINDINGS["understat_frozen.db"]["sha256"]:
        raise RuntimeGateError("Understat frozen database SHA mismatch")
    cident=confirmation_dir/"confirmation_identity.jsonl"
    cvault=confirmation_dir/"confirmation_xg_result_vault.jsonl"
    if _sha_file(cident)!=BINDINGS["confirmation_identity.jsonl"]["sha256"]:
        raise RuntimeGateError("confirmation identity SHA mismatch")
    if _sha_file(cvault)!=BINDINGS["confirmation_xg_result_vault.jsonl"]["sha256"]:
        raise RuntimeGateError("confirmation vault SHA mismatch")

    formal_index:dict[tuple[str,str,str,str],HistoryFixture]={}
    target_big5=0
    for r in history:
        if r.competition_id in set(BIG5.values()) and r.season in ("2022/23","2023/24","2024/25"):
            k=_xg_join_key(r.competition_id,r.kickoff,r.home_team_name,r.away_team_name)
            if k in formal_index:
                raise RuntimeGateError(f"formal xG join identity collision: {k}")
            formal_index[k]=r; target_big5+=1
    if target_big5!=EXPECTED_XG_JOIN_N:
        raise RuntimeGateError(f"formal Big5 join universe mismatch: {target_big5}")

    source:dict[tuple[str,str,str,str],tuple[str,int,int,float,float,datetime,str]]={}
    con=sqlite3.connect(str(understat_db)); con.row_factory=sqlite3.Row
    try:
        raw=[dict(r) for r in con.execute(
            "select fid,date,league,season,team_h,team_a,h_goals,a_goals,h_xg,a_xg "
            "from general_game_stats where league in ('Bundesliga','EPL','La liga','Ligue 1','Serie A') "
            "and season in (2022,2023) order by date,fid"
        )]
    finally:
        con.close()
    if len(raw)!=EXPECTED_XG_OLD_N:
        raise RuntimeGateError(f"old XG selected row count mismatch: {len(raw)}")
    old_sha=BINDINGS["understat_frozen.db"]["sha256"]
    for r in raw:
        dt=datetime.fromisoformat(str(r["date"])).replace(tzinfo=timezone.utc)
        comp=BIG5[str(r["league"])]
        k=_xg_join_key(comp,dt,str(r["team_h"]),str(r["team_a"]))
        if k in source:
            raise RuntimeGateError(f"duplicate old XG identity: {k}")
        source[k]=(f"understat:{int(r['fid'])}",int(r["h_goals"]),int(r["a_goals"]),
                   float(r["h_xg"]),float(r["a_xg"]),dt+timedelta(hours=3),old_sha)

    identities=[json.loads(x) for x in cident.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault_rows=[json.loads(x) for x in cvault.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(identities)!=1752 or len(vault_rows)!=1752:
        raise RuntimeGateError("confirmation row count mismatch")
    vault={str(r["fixture_id"]):r for r in vault_rows}
    if len(vault)!=1752:
        raise RuntimeGateError("confirmation vault duplicate id")
    conf_sha=_sha_file(cident)+"+"+_sha_file(cvault)
    for r in identities:
        sid=str(r["fixture_id"]); v=vault.get(sid)
        if v is None:
            raise RuntimeGateError("confirmation identity/vault mismatch")
        dt=_parse_dt(str(r["kickoff"]),"confirmation kickoff")
        if str(v.get("kickoff"))!=str(r["kickoff"]):
            raise RuntimeGateError("confirmation kickoff mismatch")
        comp=BIG5[str(r["league"])]
        k=_xg_join_key(comp,dt,str(r["home_team"]),str(r["away_team"]))
        if k in source:
            raise RuntimeGateError(f"duplicate combined XG identity: {k}")
        source[k]=(f"understat:{sid}",int(v["home_goals"]),int(v["away_goals"]),
                   float(v["home_xg"]),float(v["away_xg"]),_parse_dt(str(v["release_at"]),"release_at"),conf_sha)

    labels:dict[str,XGLabel]={}
    missing=[]
    for k,f in formal_index.items():
        s=source.get(k)
        if s is None:
            missing.append(k); continue
        sid,hg,ag,hx,ax,release,src_sha=s
        if (hg,ag)!=(f.home_goals,f.away_goals):
            raise RuntimeGateError(f"XG/formal result conflict: {f.fixture_id}")
        labels[f.fixture_id]=XGLabel(hxg.ReleasedLabel(hg,ag,hx,ax,release),sid,src_sha,release.isoformat())
    extra=[k for k in source if k not in formal_index]
    if missing or extra or len(labels)!=EXPECTED_XG_JOIN_N:
        raise RuntimeGateError(f"XG identity join incomplete missing={len(missing)} extra={len(extra)} joined={len(labels)}")
    return labels,{
        "joined_n":len(labels),"old_rows":EXPECTED_XG_OLD_N,"confirmation_rows":1752,
        "understat_db":{"sha256":_sha_file(understat_db),"bytes":understat_db.stat().st_size},
        "confirmation_identity":{"sha256":_sha_file(cident),"bytes":cident.stat().st_size},
        "confirmation_vault":{"sha256":_sha_file(cvault),"bytes":cvault.stat().st_size},
    }

def _groups(rows: Iterable[HistoryFixture]) -> list[list[HistoryFixture]]:
    groups=[]; cur=[]; key=None
    for r in rows:
        if key is None or r.kickoff==key:
            cur.append(r); key=r.kickoff
        else:
            groups.append(cur); cur=[r]; key=r.kickoff
    if cur: groups.append(cur)
    return groups

def _default_result_release(r: HistoryFixture, xg_labels: dict[str,XGLabel]) -> datetime:
    x=xg_labels.get(r.fixture_id)
    return x.label.release_at if x is not None else r.kickoff+timedelta(hours=3)

def _event_common(r: HistoryFixture, enters_xg: bool) -> dict[str,Any]:
    return {
        "fixture_id":r.fixture_id,"competition_id":r.competition_id,"season":r.season,
        "home_team_id":r.home_team_id,"away_team_id":r.away_team_id,
        "home_team_name":r.home_team_name,"away_team_name":r.away_team_name,
        "kickoff":r.kickoff.isoformat(),"source":r.source_path,"source_content_sha256":r.source_sha256,
        "enters_v1":True,"enters_xg":bool(enters_xg),
    }

def history_delta_events(history: list[HistoryFixture], xg_labels: dict[str,XGLabel], lower: datetime|None, upper: datetime,
                         target_fixture_id: str|None=None) -> list[dict[str,Any]]:
    """Create PIT delta events. Freeze events never carry target labels."""
    upper=upper.astimezone(timezone.utc); lower=None if lower is None else lower.astimezone(timezone.utc)
    events:list[dict[str,Any]]=[]
    for r in history:
        x=xg_labels.get(r.fixture_id)
        # XG challenger must freeze the fixture at kickoff even when its label is not yet released.
        if x is not None and r.kickoff<upper and (lower is None or r.kickoff>=lower):
            if r.fixture_id==target_fixture_id:
                raise RuntimeGateError("target fixture freeze entered pre-target delta")
            e=_event_common(r,True); e.update({"event_type":"FIXTURE_FREEZE","event_at":r.kickoff.isoformat()})
            events.append(e)
        release=_default_result_release(r,xg_labels)
        if release<=upper and (lower is None or release>lower):
            if r.fixture_id==target_fixture_id:
                raise RuntimeGateError("target label entered pre-target delta")
            e=_event_common(r,x is not None)
            e.update({"event_type":"LABEL_RELEASE","event_at":release.isoformat(),"result_available_at":release.isoformat(),
                      "home_goals":r.home_goals,"away_goals":r.away_goals})
            if x is not None:
                e["home_xg"]=x.label.home_xg; e["away_xg"]=x.label.away_xg
            events.append(e)
    order={"LABEL_RELEASE":0,"FIXTURE_FREEZE":1}
    events.sort(key=lambda e:(_parse_dt(str(e["event_at"]),"event_at"),order[e["event_type"]],
                              _parse_dt(str(e["kickoff"]),"kickoff"),str(e["competition_id"]),str(e["fixture_id"])))
    return events

def _apply_events(state: hxg.ChallengerState, events: list[dict[str,Any]], as_of: datetime) -> dict[str,Any]:
    """Apply verified PIT events in event-time order using only immutable formal components."""
    order={"LABEL_RELEASE":0,"FIXTURE_FREEZE":1}
    events=sorted(events,key=lambda e:(_parse_dt(str(e["event_at"]),"event_at"),order[str(e["event_type"])],
                                        _parse_dt(str(e["kickoff"]),"kickoff"),str(e["competition_id"]),str(e["fixture_id"])))
    applied_v1=applied_xg=frozen_xg=0
    i=0
    while i<len(events):
        at=_parse_dt(str(events[i]["event_at"]),"event_at")
        same=[]
        while i<len(events) and _parse_dt(str(events[i]["event_at"]),"event_at")==at:
            same.append(events[i]); i+=1
        releases=[e for e in same if e["event_type"]=="LABEL_RELEASE"]
        freezes=[e for e in same if e["event_type"]=="FIXTURE_FREEZE"]
        if releases:
            by_kickoff:dict[datetime,list[dict[str,Any]]]=defaultdict(list)
            for e in releases: by_kickoff[_parse_dt(str(e["kickoff"]),"kickoff")].append(e)
            for ko,rows in sorted(by_kickoff.items()):
                xrows=[r for r in rows if r["enters_xg"]]
                if xrows:
                    xf=[hxg.FixtureRow(str(r["fixture_id"]),str(r["competition_id"]),str(r["season"]),ko,
                                        str(r["home_team_id"]),str(r["away_team_id"]),str(r["home_team_name"]),str(r["away_team_name"])) for r in xrows]
                    for f in xf:
                        if f.fixture_id not in state.pending or state.pending[f.fixture_id]["fixture"]!=f:
                            raise RuntimeGateError("released XG label lacks exact cached fixture freeze")
                    labs={str(r["fixture_id"]):hxg.ReleasedLabel(int(r["home_goals"]),int(r["away_goals"]),float(r["home_xg"]),float(r["away_xg"]),
                                                                  _parse_dt(str(r["result_available_at"]),"result_available_at")) for r in xrows}
                    state.apply_released_batch(xf,labs,as_of=at,update_base=False); applied_xg+=len(xrows)
                vf=[v1_engine.Fixture(str(r["fixture_id"]),str(r["competition_id"]),str(r["season"]),ko,
                                      str(r["home_team_id"]),str(r["away_team_id"])) for r in rows]
                state.base.apply_batch(vf,{str(r["fixture_id"]):(int(r["home_goals"]),int(r["away_goals"])) for r in rows}); applied_v1+=len(rows)
        if freezes:
            by_kickoff:dict[datetime,list[dict[str,Any]]]=defaultdict(list)
            for e in freezes: by_kickoff[_parse_dt(str(e["kickoff"]),"kickoff")].append(e)
            for ko,rows in sorted(by_kickoff.items()):
                xf=[hxg.FixtureRow(str(r["fixture_id"]),str(r["competition_id"]),str(r["season"]),ko,
                                    str(r["home_team_id"]),str(r["away_team_id"]),str(r["home_team_name"]),str(r["away_team_name"])) for r in rows]
                if any(f.fixture_id in state.pending or f.fixture_id in state.seen for f in xf):
                    raise RuntimeGateError("duplicate XG fixture freeze event")
                state.predict_batch(xf,include_matrix=False,lightweight=True); frozen_xg+=len(xf)
    return {"applied_v1":applied_v1,"applied_xg":applied_xg,"frozen_xg":frozen_xg,"as_of":as_of.isoformat(),"pending_xg_n":len(state.pending)}

def replay_history_state(history: list[HistoryFixture], xg_labels: dict[str,XGLabel], cutoff: datetime) -> tuple[hxg.ChallengerState,dict[str,Any]]:
    """Replay trusted history to cutoff with same-kickoff and result-release isolation."""
    cutoff=cutoff.astimezone(timezone.utc)
    events=history_delta_events(history,xg_labels,None,cutoff,None)
    state=formal_v2.new_candidate_state()
    stats=_apply_events(state,events,cutoff)
    stats.update({
        "event_n":len(events),"historical_cutoff":cutoff.isoformat(),"last_v1_update":_iso(state.base.last_update_time),
        "last_xg_apply":_iso(state.last_apply_time),"v1_only_release_adapter":"kickoff_plus_3h_for_frozen_engineering_replay",
    })
    return state,stats

def _production_identity() -> dict[str,Any]:
    return {
        "competitions":list(FORMAL_SCOPE),
        "training_seasons":{k:list(v) for k,v in TRAINING_SEASONS.items()},
        "team_identity":"gteam_sha256(normalized_frozen_v1_canonical_name)[:16]",
        "xg_join":"competition+calendar_date+normalized_home+normalized_away",
        "v1_fixture_n":EXPECTED_V1_N,"xg_join_n":EXPECTED_XG_JOIN_N,
    }

def build_production_state_at_cutoff(repo_root: Path, understat_db: Path, confirmation_dir: Path, cutoff: datetime) -> tuple[hxg.ChallengerState,dict[str,Any],dict[str,Any]]:
    cutoff=cutoff.astimezone(timezone.utc)
    base_limit=_parse_dt(BASE_HISTORY_CUTOFF,"base history cutoff")
    if cutoff>base_limit:
        raise RuntimeGateError(f"FORMAL_INPUT_DATA_INCOMPLETE: frozen trusted sources end at {BASE_HISTORY_CUTOFF}")
    history,v1_source=load_frozen_v1_history(repo_root)
    xg_labels,xg_source=load_xg_labels(history,understat_db,confirmation_dir)
    state,replay=replay_history_state(history,xg_labels,cutoff)
    source={"v1":v1_source,"xg":xg_source,"replay":replay,"source_scope":"FROZEN_HISTORICAL_ENGINEERING"}
    return state,source,_production_identity()

def build_production_state(repo_root: Path, understat_db: Path, confirmation_dir: Path) -> tuple[hxg.ChallengerState,dict[str,Any],dict[str,Any]]:
    state,source,identity=build_production_state_at_cutoff(
        repo_root,understat_db,confirmation_dir,_parse_dt(BASE_HISTORY_CUTOFF,"base history cutoff")
    )
    if len(state.base.seen_fixtures)!=EXPECTED_V1_N or len(state.seen)!=EXPECTED_XG_JOIN_N or state.pending:
        raise RuntimeGateError("rebuilt production state cardinality mismatch")
    return state,source,identity

def _team_state_obj(s: v1_engine.DecayedTeam) -> dict[str,Any]:
    return {"goals_for":s.goals_for,"goals_against":s.goals_against,"weight":s.weight,
            "last_time":_iso(s.last_time),"last_season":s.last_season}
def _comp_state_obj(s: v1_engine.DecayedCompetition) -> dict[str,Any]:
    return {"home_goals":s.home_goals,"away_goals":s.away_goals,"matches":s.matches,"last_time":_iso(s.last_time)}
def serialize_v1_state(s: v1_engine.EngineState) -> dict[str,Any]:
    return {
        "params":asdict(s.params),
        "global_comp":_comp_state_obj(s.global_comp),
        "competitions":[{"competition_id":k,**_comp_state_obj(v)} for k,v in sorted(s.competitions.items())],
        "teams_local":[{"competition_id":k[0],"team_id":k[1],**_team_state_obj(v)} for k,v in sorted(s.teams_local.items())],
        "teams_global":[{"team_id":k,**_team_state_obj(v)} for k,v in sorted(s.teams_global.items())],
        "seen_fixtures":sorted(s.seen_fixtures),"last_update_time":_iso(s.last_update_time),
    }
def _residual_obj(r: hxg.ResidualState) -> dict[str,Any]:
    return {"signal_sum":r.signal_sum,"weight":r.weight,"last_time":_iso(r.last_time),"last_season":r.last_season}
def _fixture_obj(f: hxg.FixtureRow) -> dict[str,Any]:
    return {"fixture_id":f.fixture_id,"competition_id":f.competition_id,"season":f.season,"kickoff":f.kickoff.isoformat(),
            "home_team_id":f.home_team_id,"away_team_id":f.away_team_id,"home_team_name":f.home_team_name,"away_team_name":f.away_team_name}

def serialize_xg_state(s: hxg.ChallengerState) -> dict[str,Any]:
    pending=[]
    for fid,p in sorted(s.pending.items()):
        pending.append({"fixture_id":fid,"fixture":_fixture_obj(p["fixture"]),
                        "base_mu_home":p["base_mu_home"],"base_mu_away":p["base_mu_away"],
                        "base_prediction_hash":p["base_prediction_hash"],
                        "challenger_prediction_sha256":p.get("challenger_prediction_sha256")})
    return {
        "params":asdict(s.p),
        "venue_attack":[{"team_id":k[0],"venue":k[1],**_residual_obj(v)} for k,v in sorted(s.venue_attack.items())],
        "venue_defence":[{"team_id":k[0],"venue":k[1],**_residual_obj(v)} for k,v in sorted(s.venue_defence.items())],
        "pooled_attack":[{"team_id":k,**_residual_obj(v)} for k,v in sorted(s.pooled_attack.items())],
        "pooled_defence":[{"team_id":k,**_residual_obj(v)} for k,v in sorted(s.pooled_defence.items())],
        "seen":sorted(s.seen),"pending":pending,"last_prediction_time":_iso(s.last_prediction_time),"last_apply_time":_iso(s.last_apply_time),
    }

def _make_comp(d: dict[str,Any]) -> v1_engine.DecayedCompetition:
    return v1_engine.DecayedCompetition(float(d["home_goals"]),float(d["away_goals"]),float(d["matches"]),
                                        None if d["last_time"] is None else _parse_dt(d["last_time"],"competition last_time"))
def _make_team(d: dict[str,Any]) -> v1_engine.DecayedTeam:
    return v1_engine.DecayedTeam(float(d["goals_for"]),float(d["goals_against"]),float(d["weight"]),
                                 None if d["last_time"] is None else _parse_dt(d["last_time"],"team last_time"),d["last_season"])
def _make_residual(d: dict[str,Any]) -> hxg.ResidualState:
    return hxg.ResidualState(float(d["signal_sum"]),float(d["weight"]),
                             None if d["last_time"] is None else _parse_dt(d["last_time"],"xg last_time"),d["last_season"])

def deserialize_state(v1d: dict[str,Any],xgd: dict[str,Any]) -> hxg.ChallengerState:
    state=formal_v2.new_candidate_state()
    if v1d.get("params")!=asdict(state.base.params) or xgd.get("params")!=asdict(state.p):
        raise RuntimeGateError("state parameter identity mismatch")
    state.base.global_comp=_make_comp(v1d["global_comp"])
    state.base.competitions={str(r["competition_id"]):_make_comp(r) for r in v1d["competitions"]}
    state.base.teams_local={(str(r["competition_id"]),str(r["team_id"])):_make_team(r) for r in v1d["teams_local"]}
    state.base.teams_global={str(r["team_id"]):_make_team(r) for r in v1d["teams_global"]}
    state.base.seen_fixtures=set(map(str,v1d["seen_fixtures"]))
    state.base.last_update_time=None if v1d["last_update_time"] is None else _parse_dt(v1d["last_update_time"],"v1 last_update_time")
    state.venue_attack={(str(r["team_id"]),str(r["venue"])):_make_residual(r) for r in xgd["venue_attack"]}
    state.venue_defence={(str(r["team_id"]),str(r["venue"])):_make_residual(r) for r in xgd["venue_defence"]}
    state.pooled_attack={str(r["team_id"]):_make_residual(r) for r in xgd["pooled_attack"]}
    state.pooled_defence={str(r["team_id"]):_make_residual(r) for r in xgd["pooled_defence"]}
    state.pending={}
    pending=xgd.get("pending")
    if type(pending) is not list:
        raise RuntimeGateError("bundle pending state schema mismatch")
    for row in pending:
        if type(row) is not dict or type(row.get("fixture")) is not dict:
            raise RuntimeGateError("bundle pending fixture invalid")
        f=row["fixture"]
        fixture=hxg.FixtureRow(str(f["fixture_id"]),str(f["competition_id"]),str(f["season"]),_parse_dt(str(f["kickoff"]),"pending kickoff"),
                               str(f["home_team_id"]),str(f["away_team_id"]),str(f["home_team_name"]),str(f["away_team_name"]))
        fid=str(row["fixture_id"])
        if fid!=fixture.fixture_id or fid in state.pending:
            raise RuntimeGateError("bundle pending fixture identity mismatch")
        state.pending[fid]={"fixture":fixture,"base_mu_home":float(row["base_mu_home"]),"base_mu_away":float(row["base_mu_away"]),
                            "base_prediction_hash":row["base_prediction_hash"],"challenger_prediction_sha256":row.get("challenger_prediction_sha256")}
    state.seen=set(map(str,xgd["seen"]))
    state.last_prediction_time=None if xgd["last_prediction_time"] is None else _parse_dt(xgd["last_prediction_time"],"xg prediction time")
    state.last_apply_time=None if xgd["last_apply_time"] is None else _parse_dt(xgd["last_apply_time"],"xg apply time")
    return state

def _payload(path: Path,obj: Any) -> None:
    path.write_bytes(_canon_bytes(obj))

def seal_bundle(state: hxg.ChallengerState, bundle_dir: Path, source: dict[str,Any], identity: dict[str,Any],
                cutoff: str=BASE_HISTORY_CUTOFF, calculation_origin: str="FULL_REBUILD_PATH") -> dict[str,Any]:
    cutoff_dt=_parse_dt(cutoff,"bundle cutoff")
    if state.base.last_update_time is not None and state.base.last_update_time>=cutoff_dt:
        raise RuntimeGateError("bundle cutoff must be after last V1 update")
    bundle_dir.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix="football3-bundle-",dir=str(bundle_dir.parent)))
    try:
        v1d=serialize_v1_state(state.base); xgd=serialize_xg_state(state)
        source_sha=_sha_bytes(_canon_bytes(source)); identity_sha=_sha_bytes(_canon_bytes(identity))
        model_sha=_sha_bytes(_canon_bytes(BINDINGS))
        state_sha=_sha_bytes(_canon_bytes({
            "formal_head":FORMAL_HEAD,"cutoff":cutoff_dt.isoformat(),"model_bindings_sha256":model_sha,
            "source_identity_sha256":source_sha,"identity_map_sha256":identity_sha,"v1":v1d,"xg":xgd,
        }))
        meta={
            "schema_version":BUNDLE_SCHEMA,"formal_branch":FORMAL_BRANCH,"formal_head":FORMAL_HEAD,"formal_parent":FORMAL_PARENT,
            "current_sha256":CURRENT_SHA256,"historical_cutoff":cutoff_dt.isoformat(),"formal_scope":list(FORMAL_SCOPE),
            "fusion":{"xg_weight":0.75,"frozen_v1_weight":0.25,"xg_insufficient":"FROZEN_V1_EXACT_FALLBACK"},
            "same_kickoff":"PREDICT_BATCH_THEN_APPLY","calculation_origin":calculation_origin,
            "model_bindings":BINDINGS,"model_bindings_sha256":model_sha,
            "source_identity_sha256":source_sha,"identity_map_sha256":identity_sha,"state_sha256":state_sha,
        }
        receipt={"schema_version":"football3-state-build-receipt-v1","historical_cutoff":cutoff_dt.isoformat(),
                 "v1_seen_n":len(state.base.seen_fixtures),"xg_seen_n":len(state.seen),"xg_pending_n":len(state.pending),
                 "formal_head":FORMAL_HEAD,"current_sha256":CURRENT_SHA256,"calculation_origin":calculation_origin,
                 "state_sha256":state_sha,"source_identity_sha256":source_sha,"identity_map_sha256":identity_sha,
                 "state_payload_sha256":{"v1":_sha_bytes(_canon_bytes(v1d)),"xg":_sha_bytes(_canon_bytes(xgd))}}
        objects={
            "bundle_meta.json":meta,"identity_map.json":identity,"source_identity.json":source,
            "state_v1.json":v1d,"state_xg.json":xgd,"state_build_receipt.json":receipt,
        }
        for name in PAYLOAD_NAMES: _payload(tmp/name,objects[name])
        files={name:{"sha256":_sha_file(tmp/name),"bytes":(tmp/name).stat().st_size} for name in PAYLOAD_NAMES}
        core={"schema_version":"football3-production-state-manifest-v1","exact_payloads":list(PAYLOAD_NAMES),"files":files}
        bundle_sha=_sha_bytes(_canon_bytes(core))
        manifest={**core,"state_bundle_sha256":bundle_sha,"state_sha256":state_sha}
        _payload(tmp/"manifest.json",manifest)
        validate_bundle(tmp)
        backup=bundle_dir.with_name(bundle_dir.name+".previous")
        if backup.exists(): shutil.rmtree(backup)
        if bundle_dir.exists(): os.replace(bundle_dir,backup)
        os.replace(tmp,bundle_dir)
        if backup.exists(): shutil.rmtree(backup)
        return manifest
    except Exception:
        shutil.rmtree(tmp,ignore_errors=True)
        raise

def validate_bundle(bundle_dir: Path) -> dict[str,Any]:
    if not bundle_dir.is_dir():
        raise RuntimeGateError("bundle missing")
    actual={p.name for p in bundle_dir.iterdir() if p.is_file()}
    expected=set(PAYLOAD_NAMES)|{"manifest.json"}
    if actual!=expected:
        raise RuntimeGateError(f"bundle exact-set mismatch missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
    manifest=json.loads((bundle_dir/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version")!="football3-production-state-manifest-v1" or manifest.get("exact_payloads")!=list(PAYLOAD_NAMES):
        raise RuntimeGateError("bundle manifest schema mismatch")
    for name in PAYLOAD_NAMES:
        spec=manifest.get("files",{}).get(name)
        if type(spec) is not dict or _sha_file(bundle_dir/name)!=spec.get("sha256") or (bundle_dir/name).stat().st_size!=spec.get("bytes"):
            raise RuntimeGateError(f"bundle payload mismatch: {name}")
    core={"schema_version":manifest["schema_version"],"exact_payloads":manifest["exact_payloads"],"files":manifest["files"]}
    calc=_sha_bytes(_canon_bytes(core))
    if calc!=manifest.get("state_bundle_sha256"):
        raise RuntimeGateError("state bundle SHA mismatch")
    meta=json.loads((bundle_dir/"bundle_meta.json").read_text(encoding="utf-8"))
    source=json.loads((bundle_dir/"source_identity.json").read_text(encoding="utf-8"))
    identity=json.loads((bundle_dir/"identity_map.json").read_text(encoding="utf-8"))
    if meta.get("schema_version")!=BUNDLE_SCHEMA or meta.get("formal_head")!=FORMAL_HEAD or meta.get("current_sha256")!=CURRENT_SHA256:
        raise RuntimeGateError("bundle formal identity mismatch")
    if meta.get("formal_scope")!=list(FORMAL_SCOPE) or meta.get("model_bindings")!=BINDINGS:
        raise RuntimeGateError("bundle formal scope/model binding mismatch")
    if meta.get("model_bindings_sha256")!=_sha_bytes(_canon_bytes(BINDINGS)):
        raise RuntimeGateError("bundle model binding SHA mismatch")
    if meta.get("source_identity_sha256")!=_sha_bytes(_canon_bytes(source)) or meta.get("identity_map_sha256")!=_sha_bytes(_canon_bytes(identity)):
        raise RuntimeGateError("bundle data identity SHA mismatch")
    v1d=json.loads((bundle_dir/"state_v1.json").read_text(encoding="utf-8"))
    xgd=json.loads((bundle_dir/"state_xg.json").read_text(encoding="utf-8"))
    state=deserialize_state(v1d,xgd)
    if serialize_v1_state(state.base)!=v1d or serialize_xg_state(state)!=xgd:
        raise RuntimeGateError("bundle state round-trip mismatch")
    state_sha=_sha_bytes(_canon_bytes({
        "formal_head":FORMAL_HEAD,"cutoff":meta["historical_cutoff"],
        "model_bindings_sha256":meta["model_bindings_sha256"],
        "source_identity_sha256":meta["source_identity_sha256"],"identity_map_sha256":meta["identity_map_sha256"],
        "v1":v1d,"xg":xgd,
    }))
    if state_sha!=meta.get("state_sha256") or state_sha!=manifest.get("state_sha256"):
        raise RuntimeGateError("bundle deterministic state SHA mismatch")
    return {"manifest":manifest,"meta":meta,"state":state,"source":source,"identity":identity}

DELTA_STATUSES={"COMPLETE","INCOMPLETE","UNKNOWN"}

def _validate_delta_records(delta: list[dict[str,Any]], target_fixture_id: str, lower: datetime, upper: datetime) -> list[dict[str,Any]]:
    seen=set(); out=[]
    for r in delta:
        if type(r) is not dict:
            raise RuntimeGateError("invalid delta event")
        common=("event_type","event_at","fixture_id","competition_id","season","home_team_id","away_team_id","home_team_name","away_team_name",
                "kickoff","source","source_content_sha256","enters_v1","enters_xg")
        if any(k not in r for k in common):
            raise RuntimeGateError("delta event missing field")
        et=str(r["event_type"]); fid=str(r["fixture_id"]); key=(et,fid)
        if et not in {"FIXTURE_FREEZE","LABEL_RELEASE"} or not fid or key in seen or fid==target_fixture_id:
            raise RuntimeGateError("invalid/duplicate/target delta event")
        seen.add(key)
        if r["competition_id"] not in FORMAL_SCOPE:
            raise RuntimeGateError("delta competition outside formal scope")
        ko=_parse_dt(str(r["kickoff"]),"delta kickoff"); at=_parse_dt(str(r["event_at"]),"event_at")
        in_window=(lower<=at<upper) if et=="FIXTURE_FREEZE" else (lower<at<=upper)
        if not in_window or ko>=upper:
            raise RuntimeGateError("delta PIT/time continuity violation")
        if type(r["enters_v1"]) is not bool or type(r["enters_xg"]) is not bool or r["enters_v1"] is not True:
            raise RuntimeGateError("delta route flags invalid")
        if not str(r["source"]).strip() or len(str(r["source_content_sha256"]))<32:
            raise RuntimeGateError("delta source identity invalid")
        if et=="FIXTURE_FREEZE":
            if at!=ko or r["enters_xg"] is not True:
                raise RuntimeGateError("fixture-freeze event invalid")
            forbidden={"home_goals","away_goals","home_xg","away_xg","result_available_at"}
            if any(k in r for k in forbidden):
                raise RuntimeGateError("fixture-freeze carries forbidden target label fields")
        else:
            required=("result_available_at","home_goals","away_goals")
            if any(k not in r for k in required) or _parse_dt(str(r["result_available_at"]),"result_available_at")!=at or ko>=at:
                raise RuntimeGateError("label-release event invalid")
            for g in ("home_goals","away_goals"):
                if isinstance(r[g],bool) or not isinstance(r[g],int) or r[g]<0 or r[g]>30:
                    raise RuntimeGateError("delta goal invalid")
            if r["enters_xg"]:
                for x in ("home_xg","away_xg"):
                    if x not in r or isinstance(r[x],bool) or not math.isfinite(float(r[x])) or float(r[x])<0 or float(r[x])>20:
                        raise RuntimeGateError("delta XG invalid")
        out.append(r)
    order={"LABEL_RELEASE":0,"FIXTURE_FREEZE":1}
    return sorted(out,key=lambda e:(_parse_dt(str(e["event_at"]),"event_at"),order[e["event_type"]],
                                    _parse_dt(str(e["kickoff"]),"kickoff"),str(e["competition_id"]),str(e["fixture_id"])))

def validate_runtime_input(inp: dict[str,Any], fixture: dict[str,Any], bundle_cutoff: datetime, cutoff: datetime) -> dict[str,Any]:
    if inp.get("schema_version")!=INPUT_SCHEMA:
        raise RuntimeGateError("runtime input schema mismatch")
    if inp.get("fixture")!=fixture:
        raise RuntimeGateError("runtime input fixture identity mismatch")
    if _parse_dt(str(inp.get("cutoff")),"runtime input cutoff")!=cutoff:
        raise RuntimeGateError("runtime input cutoff mismatch")
    coverage=inp.get("delta_coverage")
    if type(coverage) is not dict or coverage.get("schema_version")!=DELTA_SCHEMA:
        raise RuntimeGateError("delta coverage schema mismatch")
    status=str(coverage.get("status") or "")
    if status not in DELTA_STATUSES:
        raise RuntimeGateError("delta coverage status invalid")
    delta=inp.get("model_delta")
    if type(delta) is not list:
        raise RuntimeGateError("model_delta must be list")
    if status!="COMPLETE":
        return {"fast_eligible":False,"status":status,"delta":[],"reason":"delta coverage not verified complete"}
    if coverage.get("verification")!="VERIFIED_COMPLETE" or coverage.get("v1_status")!="COMPLETE" or coverage.get("xg_status")!="COMPLETE":
        raise RuntimeGateError("complete delta coverage lacks verified V1/XG completeness")
    if _parse_dt(str(coverage.get("from")),"coverage from")!=bundle_cutoff or _parse_dt(str(coverage.get("to")),"coverage to")!=cutoff:
        raise RuntimeGateError("delta continuity mismatch")
    checked=_validate_delta_records(delta,str(fixture["fixture_id"]),bundle_cutoff,cutoff)
    if coverage.get("records_sha256")!=_sha_bytes(_canon_bytes(checked)):
        raise RuntimeGateError("delta records SHA mismatch")
    if not str(coverage.get("source_set_sha256") or ""):
        raise RuntimeGateError("delta source-set identity missing")
    return {"fast_eligible":True,"status":status,"delta":checked,"reason":None}

def apply_delta(state: hxg.ChallengerState, delta: list[dict[str,Any]], as_of: datetime) -> dict[str,Any]:
    return _apply_events(state,delta,as_of.astimezone(timezone.utc))

def _load_json(path: Path) -> dict[str,Any]:
    try: d=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise RuntimeGateError(f"invalid JSON: {path}") from exc
    if type(d) is not dict: raise RuntimeGateError(f"JSON object required: {path}")
    return d

def _fixture_payload(comp: str,home: str,away: str,kickoff: datetime) -> dict[str,Any]:
    if comp not in FORMAL_SCOPE:
        raise RuntimeGateError("competition outside Formal Fusion V2 scope")
    home=home.strip(); away=away.strip()
    if not home or not away or _normalize_team(home)==_normalize_team(away):
        raise RuntimeGateError("invalid home/away identity")
    # Season is resolved by acquisition, not guessed by the runtime.
    return {"competition":comp,"home":home,"away":away,"kickoff":kickoff.isoformat()}

def _matrix_marginals(matrix: list[dict[str,Any]]) -> dict[str,Any]:
    if type(matrix) is not list or len(matrix)!=(v1_engine.MAX_GOALS+1)**2:
        raise RuntimeGateError("score matrix exact size mismatch")
    total=home=draw=away=btts=0.0
    tg=defaultdict(float); scores=[]
    seen=set()
    for c in matrix:
        if type(c) is not dict or set(c)!={"home_goals","away_goals","probability"}:
            raise RuntimeGateError("score matrix cell schema mismatch")
        h=int(c["home_goals"]); a=int(c["away_goals"]); p=float(c["probability"])
        if (h,a) in seen or not math.isfinite(p) or p<0 or p>1:
            raise RuntimeGateError("invalid score matrix")
        seen.add((h,a)); total+=p
        if h>a: home+=p
        elif h==a: draw+=p
        else: away+=p
        tg[h+a]+=p
        if h>0 and a>0: btts+=p
        scores.append((p,h,a))
    if abs(total-1.0)>1e-12:
        raise RuntimeGateError(f"score matrix not normalized: {total}")
    top=sorted(scores,reverse=True)[:10]
    return {"p_home":home,"p_draw":draw,"p_away":away,
            "total_goals":{str(k):tg[k] for k in sorted(tg)},
            "btts":{"yes":btts,"no":1.0-btts},
            "top_scores":[{"score":f"{h}-{a}","probability":p} for p,h,a in top],
            "model_center":f"{top[0][1]}-{top[0][2]}"}

def _prediction_from_state(state: hxg.ChallengerState, fixture: hxg.FixtureRow) -> dict[str,Any]:
    # Auxiliary uncertainty/cold-start are disclosed from the immutable Frozen V1
    # component.  They never alter the formal Fusion probabilities.
    v1_fixture=v1_engine.Fixture(
        fixture.fixture_id,fixture.competition_id,fixture.season,fixture.kickoff,
        fixture.home_team_id,fixture.away_team_id,
    )
    v1_aux=state.base.predict(v1_fixture)
    rows=formal_v2.predict_formal_batch(state,[fixture])
    if len(rows)!=1: raise RuntimeGateError("formal runner output cardinality mismatch")
    row=rows[0]; pred=row["prediction"]; audit=row["audit"]
    matrix=pred["score_matrix"]; marg=_matrix_marginals(matrix)
    for key in ("p_home","p_draw","p_away"):
        if abs(float(pred[key])-float(marg[key]))>1e-12:
            raise RuntimeGateError(f"matrix/1X2 mismatch: {key}")
    aux={
        "uncertainty":v1_aux["uncertainty"],
        "cold_start_bucket":v1_aux["cold_start_bucket"],
        "mu_home_ci90":v1_aux["mu_home_ci90"],
        "mu_away_ci90":v1_aux["mu_away_ci90"],
        "effective_home_history":v1_aux["effective_home_history"],
        "effective_away_history":v1_aux["effective_away_history"],
        "effective_competition_history":v1_aux["effective_competition_history"],
        "prior_source":v1_aux["prior_source"],
        "source_component":"FROZEN_V1_DISCLOSURE_ONLY",
    }
    return {"row":row,"marginals":marg,"aux":aux}

def _prediction_equivalent(a: dict[str,Any],b: dict[str,Any]) -> dict[str,Any]:
    pa=a["row"]["prediction"]; pb=b["row"]["prediction"]
    d1=max(abs(float(pa[k])-float(pb[k])) for k in ("p_home","p_draw","p_away"))
    ma=pa["score_matrix"]; mb=pb["score_matrix"]
    if len(ma)!=len(mb): return {"passed":False,"max_1x2":d1,"max_matrix":math.inf}
    dm=max(abs(float(x["probability"])-float(y["probability"])) for x,y in zip(ma,mb))
    aa=a["row"]["audit"]; ab=b["row"]["audit"]
    meta=(aa["route"]==ab["route"] and aa["fallback_exact_v1"]==ab["fallback_exact_v1"]
          and a["aux"]["cold_start_bucket"]==b["aux"]["cold_start_bucket"]
          and a["aux"]["uncertainty"]==b["aux"]["uncertainty"])
    return {"passed":d1<=1e-12 and dm<=1e-12 and meta,"max_1x2":d1,"max_matrix":dm,"metadata_equal":meta}

def _full_rebuild_for_cutoff(cutoff: datetime, repo_root: Path|None, understat_db: Path|None, confirmation_dir: Path|None,
                             engineering_history: list[HistoryFixture]|None=None, engineering_xg_labels: dict[str,XGLabel]|None=None,
                             engineering_source: dict[str,Any]|None=None, engineering_identity: dict[str,Any]|None=None) -> tuple[hxg.ChallengerState,dict[str,Any],dict[str,Any]]:
    if engineering_history is not None and engineering_xg_labels is not None:
        state,replay=replay_history_state(engineering_history,engineering_xg_labels,cutoff)
        source=dict(engineering_source or {})
        source["replay"]=replay; source["source_scope"]="FROZEN_HISTORICAL_ENGINEERING_TEST"
        identity=dict(engineering_identity or {"competitions":sorted({r.competition_id for r in engineering_history}),"fixture_n":len(engineering_history)})
        return state,source,identity
    if repo_root is None or understat_db is None or confirmation_dir is None:
        raise RuntimeGateError("FORMAL_INPUT_DATA_INCOMPLETE: frozen full-rebuild sources unavailable")
    return build_production_state_at_cutoff(repo_root,understat_db,confirmation_dir,cutoff)

def resolve_state_for_cutoff(bundle_dir: Path, inp: dict[str,Any], fixture: dict[str,Any], cutoff: datetime,
                             repo_root: Path|None=None, understat_db: Path|None=None, confirmation_dir: Path|None=None,
                             engineering_history: list[HistoryFixture]|None=None, engineering_xg_labels: dict[str,XGLabel]|None=None,
                             engineering_source: dict[str,Any]|None=None, engineering_identity: dict[str,Any]|None=None) -> dict[str,Any]:
    """Automatic FAST/FULL routing. A bad cache is never used as a FULL rebuild input."""
    fast_failure=None; loaded=None; checked=None
    try:
        loaded=validate_bundle(bundle_dir)
        bundle_cutoff=_parse_dt(loaded["meta"]["historical_cutoff"],"bundle cutoff")
        if bundle_cutoff>cutoff:
            raise RuntimeGateError("cache cutoff is newer than target cutoff")
        checked=validate_runtime_input(inp,fixture,bundle_cutoff,cutoff)
        if not checked["fast_eligible"]:
            raise RuntimeGateError(checked["reason"] or "FAST_PATH delta unavailable")
        state=deserialize_state(serialize_v1_state(loaded["state"].base),serialize_xg_state(loaded["state"]))
        delta_result=apply_delta(state,checked["delta"],cutoff) if checked["delta"] else {"applied_v1":0,"applied_xg":0,"as_of":cutoff.isoformat()}
        source=dict(loaded["source"]); identity=dict(loaded["identity"])
        source["last_delta"]={"sha256":_sha_bytes(_canon_bytes(checked["delta"])),"records":len(checked["delta"]),
                              "from":bundle_cutoff.isoformat(),"to":cutoff.isoformat()}
        manifest=seal_bundle(state,bundle_dir,source,identity,cutoff.isoformat(),"FAST_PATH")
        return {"state":validate_bundle(bundle_dir)["state"],"path":"FAST_PATH","fast_failure":None,
                "delta_result":delta_result,"manifest":manifest,"input_check":checked}
    except RuntimeGateError as exc:
        fast_failure=str(exc)

    # FULL always starts from trusted raw frozen sources, never from a damaged/stale cache.
    state,source,identity=_full_rebuild_for_cutoff(
        cutoff,repo_root,understat_db,confirmation_dir,engineering_history,engineering_xg_labels,engineering_source,engineering_identity
    )
    manifest=seal_bundle(state,bundle_dir,source,identity,cutoff.isoformat(),"FULL_REBUILD_PATH")
    rebuilt=validate_bundle(bundle_dir)
    return {"state":rebuilt["state"],"path":"FULL_REBUILD_PATH","fast_failure":fast_failure,
            "delta_result":{"applied_v1":0,"applied_xg":0,"as_of":cutoff.isoformat()},"manifest":manifest,"input_check":checked}

def predict_match(comp: str,home: str,away: str,kickoff: datetime,cutoff: datetime,bundle_dir: Path,input_path: Path,
                  repo_root: Path|None=None,understat_db: Path|None=None,confirmation_dir: Path|None=None,
                  force_equivalence_sample: bool=False) -> dict[str,Any]:
    if cutoff>kickoff-MIN_CUTOFF_LEAD:
        raise RuntimeGateError("formal cutoff violates T_minus_60_minutes_or_earlier")
    base_fixture=_fixture_payload(comp,home,away,kickoff)
    inp=_load_json(input_path)
    target=inp.get("fixture")
    if type(target) is not dict:
        raise RuntimeGateError("runtime target missing")
    fixture_id=str(target.get("fixture_id") or ""); season=str(target.get("season") or "")
    expected={
        "fixture_id":fixture_id,"competition_id":comp,"season":season,"kickoff":kickoff.isoformat(),
        "home_team_id":str(target.get("home_team_id") or ""),"away_team_id":str(target.get("away_team_id") or ""),
        "home_team_name":home.strip(),"away_team_name":away.strip(),
    }
    if not fixture_id or not season or not expected["home_team_id"] or not expected["away_team_id"] or target!=expected:
        raise RuntimeGateError("canonical fixture identity not exact")
    routed=resolve_state_for_cutoff(bundle_dir,inp,expected,cutoff,repo_root,understat_db,confirmation_dir)
    state=routed["state"]
    f=hxg.FixtureRow(fixture_id,comp,season,kickoff,expected["home_team_id"],expected["away_team_id"],home,away)
    fast_pred=_prediction_from_state(state,f)
    eq=None
    sample=force_equivalence_sample or int(hashlib.sha256(fixture_id.encode()).hexdigest()[:8],16)%20==0
    if sample and repo_root is not None and understat_db is not None and confirmation_dir is not None:
        full_state,_,_=build_production_state_at_cutoff(repo_root,understat_db,confirmation_dir,cutoff)
        full_pred=_prediction_from_state(full_state,f)
        eq=_prediction_equivalent(fast_pred,full_pred)
        if not eq["passed"]:
            raise RuntimeGateError("FAST_RUNTIME_NOT_EQUIVALENT")
    pred=fast_pred["row"]["prediction"]; audit=fast_pred["row"]["audit"]; m=fast_pred["marginals"]
    bundle=validate_bundle(bundle_dir)
    prediction_core={
        "schema_version":RECEIPT_SCHEMA,"fixture_identity":expected,"cutoff":cutoff.isoformat(),
        "calculation_path":routed["path"],"route":"FORMAL_PRODUCTION",
        "model_route":audit["route"],"fallback_exact_v1":bool(audit["fallback_exact_v1"]),
        "p_home":float(pred["p_home"]),"p_draw":float(pred["p_draw"]),"p_away":float(pred["p_away"]),
        "score_matrix":pred["score_matrix"],"top_scores":m["top_scores"],"model_center":m["model_center"],
        "exact_score_gate":False,"total_goals":m["total_goals"],"btts":m["btts"],
        "uncertainty":fast_pred["aux"]["uncertainty"],"cold_start":fast_pred["aux"]["cold_start_bucket"],
        "uncertainty_metadata":fast_pred["aux"],"state_bundle_sha":bundle["manifest"]["state_bundle_sha256"],
        "state_sha256":bundle["manifest"]["state_sha256"],"runtime_input_sha":_sha_bytes(_canon_bytes(inp)),
        "formal_head":FORMAL_HEAD,"current_sha256":CURRENT_SHA256,"fusion_weights":{"xg":0.75,"v1":0.25},
        "source_scope":"FROZEN_HISTORICAL_OR_VERIFIED_DELTA_ONLY",
        "external_context":"NOT_IN_ENGINEERING_SCOPE","all_current_match_data_complete_claimed":False,
        "xg_status":"MODEL_XG_INSUFFICIENT_EXACT_V1_FALLBACK" if audit["fallback_exact_v1"] else "XG_ACTIVE",
        "equivalence_sample":eq,"fast_failure":routed["fast_failure"],"bet_status":"NO_BET",
    }
    prediction_sha=_sha_bytes(_canon_bytes(prediction_core))
    receipt={**prediction_core,"prediction_sha":prediction_sha}
    receipt["receipt_sha"]=_sha_bytes(_canon_bytes(receipt))
    return receipt

def command_build(args: argparse.Namespace) -> int:
    state,source,identity=build_production_state(Path(args.repo_root),Path(args.understat_db),Path(args.confirmation_dir))
    manifest=seal_bundle(state,Path(args.bundle),source,identity,BASE_HISTORY_CUTOFF,"FULL_REBUILD_PATH")
    print(json.dumps({"status":"OK","state_bundle_sha256":manifest["state_bundle_sha256"],"historical_cutoff":BASE_HISTORY_CUTOFF},sort_keys=True))
    return 0

def command_validate(args: argparse.Namespace) -> int:
    v=validate_bundle(Path(args.bundle))
    print(json.dumps({"status":"OK","state_bundle_sha256":v["manifest"]["state_bundle_sha256"],"historical_cutoff":v["meta"]["historical_cutoff"]},sort_keys=True))
    return 0

def command_update(args: argparse.Namespace) -> int:
    loaded=validate_bundle(Path(args.bundle))
    cutoff=_parse_dt(loaded["meta"]["historical_cutoff"],"bundle cutoff")
    as_of=_parse_dt(args.as_of,"as-of")
    inp=_load_json(Path(args.input))
    target=inp.get("fixture")
    if type(target) is not dict:
        raise RuntimeGateError("update runtime fixture context missing")
    checked=validate_runtime_input(inp,target,cutoff,as_of)
    if not checked["fast_eligible"]:
        raise RuntimeGateError("update requires verified complete delta")
    state=loaded["state"]; result=apply_delta(state,checked["delta"],as_of) if checked["delta"] else {"applied_v1":0,"applied_xg":0,"as_of":as_of.isoformat()}
    source=loaded["source"]; identity=loaded["identity"]
    source["last_delta"]={"sha256":_sha_bytes(_canon_bytes(checked["delta"])),"records":len(checked["delta"]),"from":cutoff.isoformat(),"to":as_of.isoformat()}
    manifest=seal_bundle(state,Path(args.bundle),source,identity,as_of.isoformat(),"FAST_PATH")
    print(json.dumps({"status":"OK","state_bundle_sha256":manifest["state_bundle_sha256"],**result},sort_keys=True))
    return 0

def command_predict(args: argparse.Namespace) -> int:
    repo_root=Path(os.environ["FOOTBALL3_REPO_ROOT"]) if "FOOTBALL3_REPO_ROOT" in os.environ else None
    understat=Path(os.environ["FOOTBALL3_UNDERSTAT_DB"]) if "FOOTBALL3_UNDERSTAT_DB" in os.environ else None
    conf=Path(os.environ["FOOTBALL3_CONFIRMATION_DIR"]) if "FOOTBALL3_CONFIRMATION_DIR" in os.environ else None
    r=predict_match(args.competition,args.home,args.away,_parse_dt(args.kickoff,"kickoff"),_parse_dt(args.cutoff,"cutoff"),
                    Path(args.bundle),Path(args.input),repo_root,understat,conf,args.equivalence_sample)
    if args.receipt:
        Path(args.receipt).write_bytes(_canon_bytes(r))
    print(json.dumps(r,ensure_ascii=False,sort_keys=True))
    return 0

def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser()
    sp=p.add_subparsers(dest="cmd",required=True)
    b=sp.add_parser("build_state"); b.add_argument("--repo-root",required=True); b.add_argument("--understat-db",required=True); b.add_argument("--confirmation-dir",required=True); b.add_argument("--bundle",required=True); b.set_defaults(fn=command_build)
    v=sp.add_parser("validate_bundle"); v.add_argument("--bundle",required=True); v.set_defaults(fn=command_validate)
    u=sp.add_parser("update_state"); u.add_argument("--bundle",required=True); u.add_argument("--as-of",required=True); u.add_argument("--input",required=True); u.set_defaults(fn=command_update)
    q=sp.add_parser("predict_match")
    q.add_argument("--competition",required=True); q.add_argument("--home",required=True); q.add_argument("--away",required=True)
    q.add_argument("--kickoff",required=True); q.add_argument("--cutoff",required=True); q.add_argument("--bundle",required=True)
    q.add_argument("--input",required=True); q.add_argument("--receipt"); q.add_argument("--equivalence-sample",action="store_true"); q.set_defaults(fn=command_predict)
    return p

def main() -> int:
    try:
        a=parser().parse_args()
        return int(a.fn(a))
    except RuntimeGateError as exc:
        print(json.dumps({"status":"FAIL_CLOSED","reason":str(exc)},ensure_ascii=False,sort_keys=True))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
