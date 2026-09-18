#!/usr/bin/env python3
from __future__ import annotations
import argparse, gzip, hashlib, json, pathlib, time, urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

class N5SourceError(RuntimeError): pass

def require(c: bool, m: str) -> None:
    if not c: raise N5SourceError(m)
def canon(v: Any) -> bytes: return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
def sha256(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def parse_dt(v: Any, f: str) -> datetime:
    t=str(v or "").strip().replace("T", " ").removesuffix("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try: return datetime.strptime(t, fmt).replace(tzinfo=timezone.utc)
        except ValueError: pass
    raise N5SourceError(f"{f}:BAD_DATETIME:{v!r}")
def tid(v: Any, f: str) -> str:
    require(isinstance(v, Mapping), f"{f}:NOT_OBJECT")
    x=str(v.get("id") or "").strip(); require(x.isdigit(), f"{f}:BAD_TEAM_ID"); return x

def project(payload: Mapping[str, Any], league: str, season: int, expected: int, delay: int) -> list[dict[str, Any]]:
    dates=payload.get("dates"); require(isinstance(dates, list), "DATES_NOT_LIST")
    require(len(dates)==expected, f"{league}:COUNT:{len(dates)}!={expected}")
    out=[]; seen=set()
    for fx in dates:
        require(isinstance(fx, Mapping), f"{league}:ROW_NOT_OBJECT")
        mid=str(fx.get("id") or "").strip(); require(mid.isdigit(), f"{league}:BAD_MATCH_ID")
        fid=f"understat:{mid}"; require(fid not in seen, f"{league}:DUP_FIXTURE:{fid}"); seen.add(fid)
        ko=parse_dt(fx.get("datetime"), fid+".datetime")
        hi, ai=tid(fx.get("h"), fid+".h"), tid(fx.get("a"), fid+".a")
        require(hi != ai, f"{fid}:SAME_TEAM")
        out.append({
            "fixture_id": fid, "league": league, "season_start": season,
            "kickoff": ko.isoformat().replace("+00:00", "Z"),
            "release_at": (ko+timedelta(hours=delay)).isoformat().replace("+00:00", "Z"),
            "home_team_id": f"understat-team:{hi}", "away_team_id": f"understat-team:{ai}"
        })
    out.sort(key=lambda r:(r["kickoff"], r["fixture_id"])); return out

def fetch_json(url: str, referer: str, tries: int=4):
    headers={"User-Agent":"Mozilla/5.0 (compatible; Football3-Nova-N5-Research/1.0; noncommercial-research)","Accept":"application/json","X-Requested-With":"XMLHttpRequest","Referer":referer}; last=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(req,timeout=60) as r: wire=r.read(); enc=str(r.headers.get("Content-Encoding") or "").lower()
            body=gzip.decompress(wire) if enc=="gzip" or wire[:2]==b"\x1f\x8b" else wire
            obj=json.loads(body.decode()); require(isinstance(obj,dict),"ROOT_NOT_OBJECT"); return obj,{"sha256":sha256(body),"bytes":len(body)}
        except Exception as e:
            last=e
            if i+1<tries: time.sleep(2*(i+1))
    raise N5SourceError(f"FETCH_FAILED:{url}:{last}")

def run(prereg_path: pathlib.Path, out_dir: pathlib.Path) -> dict[str, Any]:
    p=json.loads(prereg_path.read_text()); require(p.get("status")=="DESIGN_LOCKED_PRELABEL","PREREG_STATUS")
    season=int(p["data"]["development_season_start"]); require(season==2022,"SEASON_LOCK")
    rows=[]; sources={}; delay=int(p["source"]["release_delay_hours"])
    for league,spec in p["source"]["leagues"].items():
        url=p["source"]["endpoint_template"].format(slug=spec["slug"],season_start=season)
        payload,meta=fetch_json(url,f"https://understat.com/league/{spec['slug']}/{season}")
        part=project(payload,league,season,int(spec["expected_matches"]),delay); rows.extend(part)
        sources[league]={"url":url,"payload_sha256":meta["sha256"],"payload_bytes":meta["bytes"],"match_count":len(part)}
    rows.sort(key=lambda r:(r["kickoff"],r["fixture_id"])); require(len(rows)==int(p["data"]["development_n"]),f"TOTAL_COUNT:{len(rows)}")
    ids=[r["fixture_id"] for r in rows]; require(len(ids)==len(set(ids)),"DUP_IDS")
    out_dir.mkdir(parents=True,exist_ok=True); proj=out_dir/"projection_2022.jsonl"
    with proj.open("w",encoding="utf-8") as f:
        for r in rows: f.write(canon(r).decode()+"\n")
    receipt={
        "schema_version":"football3-nova-n5-rest-congestion-source-receipt-v1","status":"N5_FIXTURE_IDENTITY_SOURCE_PRECHECK_PASS",
        "match_count":len(rows),"development_season":2022,"fixture_id_unique_count":len(set(ids)),
        "fixture_identity_sha256":sha256(canon(ids)),"state_projection_sha256":sha256(proj.read_bytes()),"source_payloads":sources,
        "release_delay_hours":delay,"same_kickoff_atomic_required":True,"projected_fields":p["source"]["projected_fields"],
        "result_labels_read":0,"result_status_read":0,"score_values_used":0,"xg_values_used":0,"market_values_used":0,
        "training_performed":False,"candidate_probabilities_generated":False,"candidate_weight":0,"matrix_delta":0,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False
    }
    (out_dir/"source_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n"); return receipt

def main():
    a=argparse.ArgumentParser(); a.add_argument("--prereg",required=True); a.add_argument("--out",required=True); x=a.parse_args()
    print(json.dumps(run(pathlib.Path(x.prereg),pathlib.Path(x.out)),sort_keys=True))
if __name__=="__main__": main()
