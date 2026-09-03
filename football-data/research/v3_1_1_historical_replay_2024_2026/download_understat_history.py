from __future__ import annotations
import argparse, gzip, hashlib, json, math, pathlib, time, urllib.request
from datetime import datetime, timedelta, timezone

LEAGUES = {
    "EPL": {"slug":"EPL","league_id":1},
    "Serie A": {"slug":"Serie_A","league_id":2},
    "Bundesliga": {"slug":"Bundesliga","league_id":3},
    "La liga": {"slug":"La_Liga","league_id":4},
    "Ligue 1": {"slug":"Ligue_1","league_id":5},
}
SEASONS=(2024,2025)
ALLOWED_SITUATIONS={"OpenPlay","SetPiece","FromCorner","DirectFreekick","Penalty"}
SET_PIECE={"SetPiece","FromCorner","DirectFreekick"}
UA="Mozilla/5.0 (compatible; Football3HistoricalReplay/1.0; +noncommercial-research)"
HEADERS={"User-Agent":UA,"Accept":"application/json","X-Requested-With":"XMLHttpRequest"}

class DataError(RuntimeError): pass

def sha256(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def canon(o)->bytes: return json.dumps(o,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()

def decode_body(raw:bytes,enc:str)->bytes:
    if str(enc or "").lower().strip()=="gzip" or raw[:2]==b"\x1f\x8b":
        try:return gzip.decompress(raw)
        except Exception as e: raise DataError("gzip decode failed") from e
    return raw

def fetch_json(url:str, tries:int=4):
    err=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers=HEADERS)
            with urllib.request.urlopen(req,timeout=35) as r:
                if getattr(r,"status",200)!=200: raise DataError(f"HTTP {r.status}")
                wire=r.read(); body=decode_body(wire,str(r.headers.get("Content-Encoding") or ""))
            obj=json.loads(body)
            if not isinstance(obj,dict): raise DataError("JSON root not object")
            return obj,{"wire_sha256":sha256(wire),"decoded_sha256":sha256(body),"wire_bytes":len(wire),"decoded_bytes":len(body)}
        except Exception as e:
            err=e
            if i+1<tries: time.sleep(1.5*(i+1))
    raise DataError(f"fetch failed {url}: {err}")

def parse_dt(v)->datetime:
    s=str(v or "").strip().replace("T"," ")
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M"):
        try:return datetime.strptime(s,fmt).replace(tzinfo=timezone.utc)
        except ValueError: pass
    raise DataError(f"unsupported datetime {v!r}")

def truthy(v)->bool:
    if isinstance(v,bool): return v
    return str(v).lower() in {"true","1","yes"}

def _id(obj,label):
    v=str((obj or {}).get("id") or "").strip()
    if not v.isdigit(): raise DataError(f"{label} id invalid")
    return int(v)

def _title(obj,label):
    v=str((obj or {}).get("title") or "").strip()
    if not v: raise DataError(f"{label} title missing")
    return v

def _num(obj,key,label):
    try:x=float((obj or {})[key])
    except Exception as e: raise DataError(f"{label}.{key} nonnumeric") from e
    if not math.isfinite(x): raise DataError(f"{label}.{key} nonfinite")
    return x

def _goal(obj,key):
    try:x=int((obj or {})[key])
    except Exception as e: raise DataError(f"goals.{key} invalid") from e
    if x<0 or x>30: raise DataError("goal outside range")
    return x

def validate_shots(obj):
    shots=obj.get("shots")
    if not isinstance(shots,dict): raise DataError("match shots missing")
    out={"h":[],"a":[]}
    for side in ("h","a"):
        rows=shots.get(side)
        if not isinstance(rows,list): raise DataError(f"shots.{side} missing")
        for s in rows:
            if not isinstance(s,dict): raise DataError("shot row not object")
            for k in ("xG","situation","h_a"):
                if k not in s: raise DataError(f"shot field missing {k}")
            try:x=float(s["xG"])
            except Exception as e: raise DataError("shot xG nonnumeric") from e
            if not math.isfinite(x) or x<0 or x>1: raise DataError("shot xG outside [0,1]")
            sit=str(s["situation"]); ha=str(s["h_a"])
            if sit not in ALLOWED_SITUATIONS: raise DataError(f"unknown situation {sit}")
            if ha!=side: raise DataError("shot side mismatch")
            out[side].append((x,sit))
    if not out["h"] or not out["a"]: raise DataError("bilateral shots required")
    return out

def side_agg(rows):
    nps=[x for x,s in rows if s!="Penalty"]
    npxg=sum(nps); n=len(nps)
    if n<=0: raise DataError("zero nonpenalty shots")
    open_n=sum(1 for _,s in rows if s=="OpenPlay")
    set_n=sum(1 for _,s in rows if s in SET_PIECE)
    return {"npxg":npxg,"nonpenalty_shots":n,"npxg_per_shot":npxg/n,"open_play_share":open_n/n,"set_piece_share":set_n/n}

def outcome(hg,ag): return "home" if hg>ag else "draw" if hg==ag else "away"

def write_jsonl(path,rows):
    with pathlib.Path(path).open("w",encoding="utf-8") as f:
        for r in rows:f.write(json.dumps(r,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)+"\n")

def run(outdir:pathlib.Path):
    outdir.mkdir(parents=True,exist_ok=True)
    fixtures=[]; updates=[]; labels=[]; seen=set(); source_rows=[]; per={}; request_n=0
    for season in SEASONS:
        for league,info in LEAGUES.items():
            url=f"https://understat.com/getLeagueData/{info['slug']}/{season}"
            obj,meta=fetch_json(url); request_n+=1
            dates=obj.get("dates")
            if not isinstance(dates,list): raise DataError("league dates missing")
            done=[r for r in dates if isinstance(r,dict) and truthy(r.get("isResult"))]
            if not done: raise DataError(f"{league} {season}: no completed matches")
            key=f"{league}|{season}"
            per[key]={"league_rows":len(dates),"completed_n":len(done),"league_payload_sha256":meta["decoded_sha256"]}
            for idx,r in enumerate(done):
                mid=str(r.get("id") or "").strip()
                if not mid.isdigit(): raise DataError("match id invalid")
                fid=f"understat:{mid}"
                if fid in seen: raise DataError(f"duplicate fixture {fid}")
                seen.add(fid)
                ko=parse_dt(r.get("datetime"))
                hid=_id(r.get("h"),"home"); aid=_id(r.get("a"),"away")
                if hid==aid: raise DataError("team identity collision")
                hname=_title(r.get("h"),"home"); aname=_title(r.get("a"),"away")
                hg=_goal(r.get("goals"),"h"); ag=_goal(r.get("goals"),"a")
                hx=_num(r.get("xG"),"h","xG"); ax=_num(r.get("xG"),"a","xG")
                murl=f"https://understat.com/getMatchData/{mid}"
                mobj,mmeta=fetch_json(murl); request_n+=1
                sh=validate_shots(mobj); hs=side_agg(sh["h"]); aa=side_agg(sh["a"])
                fixtures.append({"fixture_id":fid,"understat_match_id":int(mid),"competition_id":f"understat-league:{info['league_id']}","league":league,"league_id":info["league_id"],"season":season,"season_label":f"{season}/{str(season+1)[-2:]}","kickoff":ko.isoformat(),"home_team_id":f"understat-team:{hid}","away_team_id":f"understat-team:{aid}","home_team_name":hname,"away_team_name":aname})
                updates.append({"fixture_id":fid,"release_at":(ko+timedelta(hours=3)).isoformat(),"home_xg":hx,"away_xg":ax,"home_npxg":hs["npxg"],"away_npxg":aa["npxg"],"home_nonpenalty_shots":hs["nonpenalty_shots"],"away_nonpenalty_shots":aa["nonpenalty_shots"],"home_npxg_per_shot":hs["npxg_per_shot"],"away_npxg_per_shot":aa["npxg_per_shot"],"home_open_play_share":hs["open_play_share"],"away_open_play_share":aa["open_play_share"],"home_set_piece_share":hs["set_piece_share"],"away_set_piece_share":aa["set_piece_share"]})
                labels.append({"fixture_id":fid,"home_goals":hg,"away_goals":ag,"outcome":outcome(hg,ag)})
                source_rows.append({"fixture_id":fid,"match_payload_sha256":mmeta["decoded_sha256"]})
                if (idx+1)%50==0: print(f"{key}: {idx+1}/{len(done)}",flush=True)
                time.sleep(0.05)
    order={r["fixture_id"]:(r["kickoff"],r["fixture_id"]) for r in fixtures}
    fixtures.sort(key=lambda r:(r["kickoff"],r["fixture_id"])); updates.sort(key=lambda r:order[r["fixture_id"]]); labels.sort(key=lambda r:order[r["fixture_id"]]); source_rows.sort(key=lambda r:order[r["fixture_id"]])
    if not (len(fixtures)==len(updates)==len(labels)==len(seen)): raise DataError("store count mismatch")
    write_jsonl(outdir/"fixtures.jsonl",fixtures); write_jsonl(outdir/"state_updates.jsonl",updates); write_jsonl(outdir/"label_vault.jsonl",labels)
    ids=[r["fixture_id"] for r in fixtures]
    manifest={"schema_version":"football3-v3-1-1-historical-replay-data-v1","status":"HISTORICAL_REPLAY_DATA_READY","post_view_historical_stress_test":True,"fresh_confirmation":False,"seasons":[2024,2025],"leagues":list(LEAGUES),"fixture_n":len(fixtures),"fixture_identity_sha256":sha256(canon(ids)),"fixture_store_sha256":sha256((outdir/"fixtures.jsonl").read_bytes()),"state_update_store_sha256":sha256((outdir/"state_updates.jsonl").read_bytes()),"label_vault_sha256":sha256((outdir/"label_vault.jsonl").read_bytes()),"raw_ajax_persisted":False,"raw_shot_rows_persisted":False,"request_n":request_n,"per_league_season":per,"source_match_payload_identity_sha256":sha256(canon(source_rows))}
    (outdir/"data_manifest.json").write_text(json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(manifest,sort_keys=True),flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--outdir",type=pathlib.Path,required=True); a=ap.parse_args(); run(a.outdir)
