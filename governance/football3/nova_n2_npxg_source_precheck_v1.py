#!/usr/bin/env python3
from __future__ import annotations
import argparse, gzip, hashlib, json, math, pathlib, time, urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

class SourcePrecheckError(RuntimeError): pass

def canon(v: Any) -> bytes:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
def sha256(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def finite(v: Any, f: str) -> float:
    if isinstance(v, bool): raise SourcePrecheckError(f"{f}:BOOLEAN_NOT_NUMERIC")
    try: x=float(v)
    except Exception as e: raise SourcePrecheckError(f"{f}:NOT_NUMERIC") from e
    if not math.isfinite(x) or x < 0: raise SourcePrecheckError(f"{f}:INVALID")
    return x
def parse_dt(v: Any, f: str) -> datetime:
    t=str(v or "").strip().replace("T"," ").removesuffix("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M"):
        try: return datetime.strptime(t,fmt).replace(tzinfo=timezone.utc)
        except ValueError: pass
    raise SourcePrecheckError(f"{f}:UNSUPPORTED_DATETIME:{v!r}")
def truthy(v: Any) -> bool:
    return v if isinstance(v,bool) else str(v).strip().lower() in {"1","true","yes"}
def team_id(v: Any, f: str) -> str:
    t=str((v or {}).get("id") or "").strip()
    if not t.isdigit(): raise SourcePrecheckError(f"{f}:INVALID_TEAM_ID")
    return t
def team_name(v: Any, f: str) -> str:
    t=str((v or {}).get("title") or "").strip()
    if not t: raise SourcePrecheckError(f"{f}:MISSING_TEAM_NAME")
    return t

def safe_history_row(row: Any, team: str) -> dict[str,Any]:
    if not isinstance(row, Mapping): raise SourcePrecheckError(f"team:{team}:HISTORY_ROW_NOT_OBJECT")
    ha=str(row.get("h_a") or "").strip().lower()
    if ha not in {"h","a"}: raise SourcePrecheckError(f"team:{team}.h_a:INVALID")
    return {"kickoff":parse_dt(row.get("date"),f"team:{team}.date"),"h_a":ha,
            "npxg":finite(row.get("npxG"),f"team:{team}.npxG"),
            "npxga":finite(row.get("npxGA"),f"team:{team}.npxGA")}
def close(a: float,b: float) -> bool: return math.isclose(a,b,rel_tol=0,abs_tol=1e-8)
def reciprocal(h: Mapping[str,Any], a: Mapping[str,Any]) -> None:
    if not close(float(h["npxga"]),float(a["npxg"])): raise SourcePrecheckError("RECIPROCAL_MISMATCH:home.npxGA!=away.npxG")
    if not close(float(a["npxga"]),float(h["npxg"])): raise SourcePrecheckError("RECIPROCAL_MISMATCH:away.npxGA!=home.npxG")

def project_payload(payload: Mapping[str,Any], *, league:str, season:int, expected_matches:int, release_delay_hours:int) -> list[dict[str,Any]]:
    dates, teams = payload.get("dates"), payload.get("teams")
    if not isinstance(dates,list): raise SourcePrecheckError(f"{league}|{season}:DATES_NOT_LIST")
    if not isinstance(teams,Mapping): raise SourcePrecheckError(f"{league}|{season}:TEAMS_NOT_OBJECT")
    hist: dict[tuple[str,datetime],dict[str,Any]]={}
    for raw_id, team in teams.items():
        tid=str(raw_id).strip()
        if not tid.isdigit() or not isinstance(team,Mapping): raise SourcePrecheckError(f"{league}|{season}:BAD_TEAM_RECORD")
        rows=team.get("history")
        if not isinstance(rows,list): raise SourcePrecheckError(f"{league}|{season}:TEAM_HISTORY_MISSING:{tid}")
        for raw in rows:
            s=safe_history_row(raw,tid); key=(tid,s["kickoff"])
            if key in hist: raise SourcePrecheckError(f"{league}|{season}:DUPLICATE_HISTORY_KEY")
            hist[key]=s
    completed=[r for r in dates if isinstance(r,Mapping) and truthy(r.get("isResult"))]
    if len(completed)!=expected_matches: raise SourcePrecheckError(f"{league}|{season}:COMPLETED_COUNT:{len(completed)}!={expected_matches}")
    out=[]; seen=set(); used=set()
    for fx in completed:
        mid=str(fx.get("id") or "").strip()
        if not mid.isdigit(): raise SourcePrecheckError(f"{league}|{season}:BAD_MATCH_ID")
        fid=f"understat:{mid}"; kickoff=parse_dt(fx.get("datetime"),f"{fid}.datetime")
        hid,aid=team_id(fx.get("h"),f"{fid}.home"),team_id(fx.get("a"),f"{fid}.away")
        hk,ak=(hid,kickoff),(aid,kickoff); h,a=hist.get(hk),hist.get(ak)
        if fid in seen: raise SourcePrecheckError(f"{league}|{season}:DUPLICATE_FIXTURE:{fid}")
        seen.add(fid)
        if h is None or a is None: raise SourcePrecheckError(f"{fid}:HISTORY_JOIN_MISSING")
        if h["h_a"]!="h" or a["h_a"]!="a": raise SourcePrecheckError(f"{fid}:H_A_MISMATCH")
        reciprocal(h,a)
        if hk in used or ak in used: raise SourcePrecheckError(f"{fid}:HISTORY_ROW_REUSED")
        used.update((hk,ak))
        out.append({"fixture_id":fid,"league":league,"season_start":season,
                    "kickoff":kickoff.isoformat().replace("+00:00","Z"),
                    "release_at":(kickoff+timedelta(hours=release_delay_hours)).isoformat().replace("+00:00","Z"),
                    "home_team_id":f"understat-team:{hid}","away_team_id":f"understat-team:{aid}",
                    "home_team_name":team_name(fx.get("h"),f"{fid}.home"),"away_team_name":team_name(fx.get("a"),f"{fid}.away"),
                    "home_npxg":float(h["npxg"]),"away_npxg":float(a["npxg"]),
                    "home_npxga":float(h["npxga"]),"away_npxga":float(a["npxga"])})
    out.sort(key=lambda r:(r["kickoff"],r["fixture_id"]))
    return out

def fetch_json(url:str, referer:str, tries:int=4):
    headers={"User-Agent":"Mozilla/5.0 (compatible; Football3-Nova-N2-Research/1.0; noncommercial-research)","Accept":"application/json","X-Requested-With":"XMLHttpRequest","Referer":referer}
    last=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(req,timeout=60) as r:
                wire=r.read(); enc=str(r.headers.get("Content-Encoding") or "").lower()
            body=gzip.decompress(wire) if enc=="gzip" or wire[:2]==b"\x1f\x8b" else wire
            obj=json.loads(body.decode())
            if not isinstance(obj,dict): raise SourcePrecheckError("PAYLOAD_ROOT_NOT_OBJECT")
            return obj,{"sha256":sha256(body),"bytes":len(body)}
        except Exception as e:
            last=e
            if i+1<tries: time.sleep(2*(i+1))
    raise SourcePrecheckError(f"FETCH_FAILED:{url}:{last}")

def run(config_path:pathlib.Path, projection_out:pathlib.Path, receipt_out:pathlib.Path):
    cfg=json.loads(config_path.read_text())
    if cfg.get("status")!="PRECHECK_LOCKED_BEFORE_LABEL_READ_OR_FIT": raise SourcePrecheckError("CONFIG_STATUS_NOT_PRECHECK_LOCKED")
    rows=[]; sources={}
    for season in cfg["seasons"]:
        for league,spec in cfg["leagues"].items():
            expected=int(spec["expected_matches"][str(season)]); slug=spec["slug"]
            url=cfg["source"]["endpoint_template"].format(slug=slug,season_start=season)
            payload,meta=fetch_json(url,f"https://understat.com/league/{slug}/{season}")
            part=project_payload(payload,league=league,season=int(season),expected_matches=expected,release_delay_hours=int(cfg["release_delay_hours"]))
            sources[f"{league}|{season}"]={"url":url,"payload_sha256":meta["sha256"],"payload_bytes":meta["bytes"],"match_count":len(part)}
            rows.extend(part)
    rows.sort(key=lambda r:(r["kickoff"],r["fixture_id"]))
    if len(rows)!=int(cfg["expected_total_match_count"]): raise SourcePrecheckError(f"TOTAL_COUNT:{len(rows)}!={cfg['expected_total_match_count']}")
    ids=[r["fixture_id"] for r in rows]
    if len(ids)!=len(set(ids)): raise SourcePrecheckError("GLOBAL_FIXTURE_ID_DUPLICATE")
    projection_out.parent.mkdir(parents=True,exist_ok=True); receipt_out.parent.mkdir(parents=True,exist_ok=True)
    with projection_out.open("w",encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n")
    g=cfg["governance"]
    receipt={"schema_version":"football3-nova-n2-npxg-source-precheck-receipt-v1","status":"N2_NPXG_SOURCE_PRECHECK_PASS",
             "permission_class":cfg["source"]["permission_class"],"production_eligible":False,"match_count":len(rows),
             "development_2022_n":sum(r["season_start"]==2022 for r in rows),"isolated_2023_n":sum(r["season_start"]==2023 for r in rows),
             "fixture_id_unique_count":len(set(ids)),"fixture_identity_sha256":sha256(canon(ids)),"state_projection_sha256":sha256(projection_out.read_bytes()),
             "source_payloads":sources,"release_delay_hours":int(cfg["release_delay_hours"]),"same_kickoff_atomic_required":True,
             "safe_features":cfg["projected_features"],"result_labels_read":int(g["result_labels_read"]),"score_values_used":int(g["score_values_used"]),
             "market_values_used":int(g["market_values_used"]),"training_performed":False,"candidate_probabilities_generated":False,
             "candidate_weight":0,"matrix_delta":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False}
    receipt_out.write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return receipt

def main():
    p=argparse.ArgumentParser(); p.add_argument("--config",required=True); p.add_argument("--projection-out",required=True); p.add_argument("--receipt-out",required=True)
    a=p.parse_args(); print(json.dumps(run(pathlib.Path(a.config),pathlib.Path(a.projection_out),pathlib.Path(a.receipt_out)),indent=2,sort_keys=True))
if __name__=="__main__": main()
