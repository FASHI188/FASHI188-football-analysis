#!/usr/bin/env python3
from __future__ import annotations
import argparse, gzip, hashlib, json, math, pathlib, time, urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

class N4SourceError(RuntimeError): pass

def require(c: bool, m: str) -> None:
    if not c: raise N4SourceError(m)
def canon(v: Any) -> bytes: return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def sha256(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def finite(v: Any, f: str) -> float:
    if isinstance(v,bool): raise N4SourceError(f"{f}:BOOLEAN")
    try: x=float(v)
    except Exception as e: raise N4SourceError(f"{f}:NOT_NUMERIC") from e
    require(math.isfinite(x) and x>=0.0,f"{f}:INVALID")
    return x
def parse_dt(v: Any, f: str) -> datetime:
    t=str(v or "").strip().replace("T"," ").removesuffix("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M"):
        try: return datetime.strptime(t,fmt).replace(tzinfo=timezone.utc)
        except ValueError: pass
    raise N4SourceError(f"{f}:BAD_DATETIME:{v!r}")
def truthy(v: Any) -> bool: return v if isinstance(v,bool) else str(v).strip().lower() in {"1","true","yes"}
def tid(v: Any, f: str) -> str:
    x=str((v or {}).get("id") or "").strip(); require(x.isdigit(),f"{f}:BAD_TEAM_ID"); return x
def tname(v: Any, f: str) -> str:
    x=str((v or {}).get("title") or "").strip(); require(bool(x),f"{f}:MISSING_TEAM_NAME"); return x
def ppda_parts(v: Any, f: str) -> tuple[float,float]:
    require(isinstance(v,Mapping),f"{f}:NOT_OBJECT"); return finite(v.get("att"),f+".att"), finite(v.get("def"),f+".def")
def safe_hist(row: Any, team: str) -> dict[str,Any]:
    require(isinstance(row,Mapping),f"{team}:ROW_NOT_OBJECT")
    ha=str(row.get("h_a") or "").lower(); require(ha in {"h","a"},f"{team}:BAD_HA")
    pa,pd=ppda_parts(row.get("ppda"),f"{team}.ppda"); aa,ad=ppda_parts(row.get("ppda_allowed"),f"{team}.ppda_allowed")
    return {"kickoff":parse_dt(row.get("date"),f"{team}.date"),"h_a":ha,
            "deep":finite(row.get("deep"),f"{team}.deep"),"deep_allowed":finite(row.get("deep_allowed"),f"{team}.deep_allowed"),
            "ppda_att":pa,"ppda_def":pd,"ppda_allowed_att":aa,"ppda_allowed_def":ad,
            "ppda":0.0 if pd==0 else pa/pd}
def close(a: float,b: float) -> bool: return math.isclose(a,b,rel_tol=0,abs_tol=1e-9)
def reciprocal(h: Mapping[str,Any],a: Mapping[str,Any]) -> None:
    checks=[(h["deep_allowed"],a["deep"]),(a["deep_allowed"],h["deep"]),(h["ppda_allowed_att"],a["ppda_att"]),(h["ppda_allowed_def"],a["ppda_def"]),(a["ppda_allowed_att"],h["ppda_att"]),(a["ppda_allowed_def"],h["ppda_def"])]
    require(all(close(float(x),float(y)) for x,y in checks),"RECIPROCAL_MISMATCH")
def project(payload: Mapping[str,Any], league: str, season: int, expected: int, delay: int) -> list[dict[str,Any]]:
    dates,teams=payload.get("dates"),payload.get("teams"); require(isinstance(dates,list),"DATES_NOT_LIST"); require(isinstance(teams,Mapping),"TEAMS_NOT_OBJECT")
    hist={}
    for raw_id,team in teams.items():
        team_id=str(raw_id); require(team_id.isdigit() and isinstance(team,Mapping),"BAD_TEAM")
        rows=team.get("history"); require(isinstance(rows,list),"HISTORY_MISSING")
        for row in rows:
            s=safe_hist(row,team_id); key=(team_id,s["kickoff"]); require(key not in hist,"DUP_HISTORY"); hist[key]=s
    completed=[r for r in dates if isinstance(r,Mapping) and truthy(r.get("isResult"))]; require(len(completed)==expected,f"{league}:COUNT:{len(completed)}!={expected}")
    out=[]; seen=set(); used=set()
    for fx in completed:
        mid=str(fx.get("id") or ""); require(mid.isdigit(),"BAD_MATCH_ID"); fid=f"understat:{mid}"; require(fid not in seen,"DUP_FIXTURE"); seen.add(fid)
        ko=parse_dt(fx.get("datetime"),fid+".datetime"); hi,ai=tid(fx.get("h"),fid+".h"),tid(fx.get("a"),fid+".a")
        hk,ak=(hi,ko),(ai,ko); h,a=hist.get(hk),hist.get(ak); require(h is not None and a is not None,f"{fid}:HISTORY_JOIN_MISSING")
        require(h["h_a"]=="h" and a["h_a"]=="a",f"{fid}:HA_MISMATCH"); reciprocal(h,a); require(hk not in used and ak not in used,"HISTORY_REUSED"); used.update((hk,ak))
        out.append({"fixture_id":fid,"league":league,"season_start":season,"kickoff":ko.isoformat().replace("+00:00","Z"),"release_at":(ko+timedelta(hours=delay)).isoformat().replace("+00:00","Z"),
                    "home_team_id":f"understat-team:{hi}","away_team_id":f"understat-team:{ai}","home_team_name":tname(fx.get("h"),fid+".h"),"away_team_name":tname(fx.get("a"),fid+".a"),
                    "home_ppda":float(h["ppda"]),"away_ppda":float(a["ppda"]),"home_deep":float(h["deep"]),"away_deep":float(a["deep"])})
    out.sort(key=lambda r:(r["kickoff"],r["fixture_id"])); return out
def fetch_json(url: str, referer: str, tries: int=4):
    headers={"User-Agent":"Mozilla/5.0 (compatible; Football3-Nova-N4-Research/1.0; noncommercial-research)","Accept":"application/json","X-Requested-With":"XMLHttpRequest","Referer":referer}; last=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(req,timeout=60) as r: wire=r.read(); enc=str(r.headers.get("Content-Encoding") or "").lower()
            body=gzip.decompress(wire) if enc=="gzip" or wire[:2]==b"\x1f\x8b" else wire; obj=json.loads(body.decode()); require(isinstance(obj,dict),"ROOT_NOT_OBJECT"); return obj,{"sha256":sha256(body),"bytes":len(body)}
        except Exception as e:
            last=e
            if i+1<tries: time.sleep(2*(i+1))
    raise N4SourceError(f"FETCH_FAILED:{url}:{last}")
def run(prereg_path: pathlib.Path,out_dir: pathlib.Path) -> dict[str,Any]:
    p=json.loads(prereg_path.read_text()); require(p.get("status")=="DESIGN_LOCKED_PRELABEL","PREREG_STATUS"); season=int(p["data"]["development_season_start"]); require(season==2022,"SEASON_LOCK")
    rows=[]; sources={}; delay=int(p["source"]["release_delay_hours"])
    for league,spec in p["source"]["leagues"].items():
        url=p["source"]["endpoint_template"].format(slug=spec["slug"],season_start=season); payload,meta=fetch_json(url,f"https://understat.com/league/{spec['slug']}/{season}")
        part=project(payload,league,season,int(spec["expected_matches"]),delay); rows.extend(part); sources[league]={"url":url,"payload_sha256":meta["sha256"],"payload_bytes":meta["bytes"],"match_count":len(part)}
    rows.sort(key=lambda r:(r["kickoff"],r["fixture_id"])); require(len(rows)==int(p["data"]["development_n"]),f"TOTAL_COUNT:{len(rows)}"); ids=[r["fixture_id"] for r in rows]; require(len(ids)==len(set(ids)),"DUP_IDS")
    out_dir.mkdir(parents=True,exist_ok=True); proj=out_dir/"projection_2022.jsonl"
    with proj.open("w",encoding="utf-8") as f:
        for r in rows: f.write(canon(r).decode()+"\n")
    receipt={"schema_version":"football3-nova-n4-style-source-receipt-v1","status":"N4_STYLE_SOURCE_PRECHECK_PASS","match_count":len(rows),"development_season":2022,"fixture_id_unique_count":len(set(ids)),"fixture_identity_sha256":sha256(canon(ids)),"state_projection_sha256":sha256(proj.read_bytes()),"source_payloads":sources,"release_delay_hours":delay,"same_kickoff_atomic_required":True,"safe_features":p["source"]["projected_features"],"result_labels_read":0,"score_values_used":0,"xg_values_used":0,"market_values_used":0,"training_performed":False,"candidate_probabilities_generated":False,"candidate_weight":0,"matrix_delta":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False}
    (out_dir/"source_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n"); return receipt
def main():
    a=argparse.ArgumentParser(); a.add_argument("--prereg",required=True); a.add_argument("--out",required=True); x=a.parse_args(); print(json.dumps(run(pathlib.Path(x.prereg),pathlib.Path(x.out)),sort_keys=True))
if __name__=="__main__": main()
