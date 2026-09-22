#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BIG5={"EPL":"EPL","Bundesliga":"Bundesliga","La liga":"La_liga","La_liga":"La_liga","Ligue 1":"Ligue_1","Ligue_1":"Ligue_1","Serie A":"Serie_A","Serie_A":"Serie_A"}
EXPECTED=1826
class N8SourceError(RuntimeError): pass
def require(c:bool,m:str)->None:
    if not c: raise N8SourceError(m)
def canon(v:Any)->bytes: return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def sha256(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def normdt(v:str)->datetime:
    x=str(v).strip().replace("T"," ").replace("+00:00","").removesuffix("Z")
    for f in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M"):
        try: return datetime.strptime(x,f).replace(tzinfo=timezone.utc)
        except ValueError: pass
    raise N8SourceError(f"BAD_DATETIME:{v!r}")
def finite(v:Any,f:str)->float:
    try: x=float(v)
    except Exception as e: raise N8SourceError(f"{f}:NOT_NUMERIC") from e
    require(math.isfinite(x) and x>=0.0,f"{f}:INVALID"); return x

def summarize(xs:list[tuple[float,str]], setpieces:set[str], high_thr:float)->dict[str,float]:
    require(bool(xs),"NO_SHOTS_FOR_SIDE")
    np=[x for x in xs if x[1]!="Penalty"]; op=[x for x in xs if x[1]=="OpenPlay"]; sp=[x for x in xs if x[1] in setpieces]
    np_xg=sum(x for x,_ in np); op_xg=sum(x for x,_ in op); sp_xg=sum(x for x,_ in sp)
    np_vals=[x for x,_ in np]
    mean=np_xg/len(np_vals) if np_vals else 0.0
    sd=math.sqrt(sum((x-mean)**2 for x in np_vals)/len(np_vals)) if np_vals else 0.0
    return {
        "openplay_xg_per_shot": op_xg/len(op) if op else 0.0,
        "setpiece_xg_share": sp_xg/np_xg if np_xg>0 else 0.0,
        "highq_np_shot_share": sum(x>=high_thr for x in np_vals)/len(np_vals) if np_vals else 0.0,
        "np_shot_xg_sd": sd,
        "np_shots": float(len(np_vals)),
        "setpiece_shots": float(len(sp)),
        "openplay_shots": float(len(op))
    }

def run(prereg:Path,db:Path,out:Path)->dict[str,Any]:
    p=json.loads(prereg.read_text()); require(p.get("status")=="DESIGN_LOCKED_PRELABEL","PREREG_STATUS")
    require(sha256(db.read_bytes())==p["source"]["db_sha256"],"DB_SHA_MISMATCH")
    season=int(p["data"]["development_season_start"]); require(season==2022,"SEASON_LOCK")
    con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
    games=con.execute("SELECT id,date,h_id,a_id,league FROM general_game_stats WHERE season=? AND league IN ('EPL','Bundesliga','La liga','La_liga','Ligue 1','Ligue_1','Serie A','Serie_A') ORDER BY date,id",(season,)).fetchall()
    require(len(games)==EXPECTED,f"GAME_N:{len(games)}")
    events=con.execute("SELECT match_id,h_a,xG,situation FROM game_events WHERE season=? ORDER BY match_id,id",(season,)).fetchall(); con.close()
    allowed=set(p["source"]["allowed_situations"]); sp=set(p["source"]["setpiece_situations"]); high=float(p["source"]["high_quality_xg_threshold"])
    by=defaultdict(lambda:{"h":[],"a":[]}); event_n=0
    for mid,ha,xg,sit in events:
        require(ha in {"h","a"},f"BAD_HA:{mid}:{ha}"); require(sit in allowed,f"BAD_SITUATION:{mid}:{sit}")
        by[int(mid)][ha].append((finite(xg,f"xG:{mid}"),str(sit))); event_n+=1
    rows=[]; ids=[]; delay=int(p["source"]["release_delay_hours"])
    for mid,date,hid,aid,league_raw in games:
        league=BIG5[str(league_raw)]; require(int(mid) in by,f"EVENTS_MISSING:{mid}")
        hs=summarize(by[int(mid)]["h"],sp,high); aas=summarize(by[int(mid)]["a"],sp,high)
        ko=normdt(date); fid=f"understat:{int(mid)}"; ids.append(fid)
        rows.append({"fixture_id":fid,"league":league,"season_start":season,"kickoff":ko.isoformat().replace("+00:00","Z"),"release_at":(ko+timedelta(hours=delay)).isoformat().replace("+00:00","Z"),"home_team_id":f"understat-team:{int(hid)}","away_team_id":f"understat-team:{int(aid)}","home":hs,"away":aas})
    require(len(ids)==len(set(ids))==EXPECTED,"FIXTURE_ID_UNIQUENESS")
    rows.sort(key=lambda r:(r["kickoff"],r["fixture_id"])); out.mkdir(parents=True,exist_ok=True); proj=out/"shot_state_2022.jsonl"
    proj.write_text("".join(canon(r).decode()+"\n" for r in rows))
    rec={"schema_version":"football3-nova-n8-shot-quality-setpiece-source-receipt-v1","status":"N8_SHOT_SOURCE_PRECHECK_PASS","development_n":EXPECTED,"event_n":event_n,"fixture_id_unique_count":EXPECTED,"state_projection_sha256":sha256(proj.read_bytes()),"availability_mode":p["source"]["availability_mode"],"release_delay_hours":delay,"same_kickoff_atomic_required":True,"safe_match_fields":p["source"]["safe_match_fields"],"safe_event_fields":p["source"]["safe_event_fields"],"result_labels_read":0,"score_values_read":0,"market_values_read":0,"training_performed":False,"candidate_weight":0,"matrix_delta":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False}
    (out/"source_receipt.json").write_text(json.dumps(rec,indent=2,sort_keys=True)+"\n"); return rec

def main():
    a=argparse.ArgumentParser(); a.add_argument("--prereg",type=Path,required=True); a.add_argument("--db",type=Path,required=True); a.add_argument("--out",type=Path,required=True); x=a.parse_args(); print(json.dumps(run(x.prereg,x.db,x.out),sort_keys=True))
if __name__=="__main__": main()
