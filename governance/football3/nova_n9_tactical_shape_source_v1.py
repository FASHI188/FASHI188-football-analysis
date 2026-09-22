#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BIG5={"EPL":"EPL","Bundesliga":"Bundesliga","La liga":"La_liga","La_liga":"La_liga","Ligue 1":"Ligue_1","Ligue_1":"Ligue_1","Serie A":"Serie_A","Serie_A":"Serie_A"}
EXPECTED=1826
class N9SourceError(RuntimeError): pass
def require(c,m):
    if not c: raise N9SourceError(m)
def canon(v:Any)->bytes: return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def sha256(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def normdt(v:str)->datetime:
    x=str(v).replace(" ","T").replace("Z","+00:00"); d=datetime.fromisoformat(x); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def zulu(d:datetime)->str: return d.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def shape(pos:list[str])->dict[str,Any]:
    if len(pos)!=11: return {"available":False,"starter_n":len(pos),"signature":None,"vector":None}
    fam={"back":0,"dm":0,"cm":0,"am":0,"front":0,"wide":0}
    for p in pos:
        if p in {"DC","DL","DR"}: fam["back"]+=1
        if p in {"DMC","DML","DMR"}: fam["dm"]+=1
        if p in {"MC","ML","MR"}: fam["cm"]+=1
        if p in {"AMC","AML","AMR"}: fam["am"]+=1
        if p in {"FW","FWL","FWR"}: fam["front"]+=1
        if p in {"DL","DR","DML","DMR","ML","MR","AML","AMR","FWL","FWR"}: fam["wide"]+=1
    sig="|".join(sorted(pos))
    vec=[float(fam[k]) for k in ("back","dm","cm","am","front","wide")]+[float(fam["back"]>=3),float(fam["front"]>=2)]
    return {"available":True,"starter_n":11,"signature":sig,"vector":vec}
def run(db:Path, prereg:Path, out:Path):
    p=json.loads(prereg.read_text()); require(p["status"]=="DESIGN_LOCKED_PRELABEL","PREREG")
    con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
    games=con.execute("SELECT id,date,h_id,a_id,league,season FROM general_game_stats WHERE season=2022 AND league IN ('EPL','Bundesliga','La liga','La_liga','Ligue 1','Ligue_1','Serie A','Serie_A') ORDER BY date,id").fetchall(); require(len(games)==EXPECTED,f"GAME_N:{len(games)}")
    ls=con.execute("SELECT l.match_id,l.team_id,l.h_a,l.position,l.positionOrder FROM lineup_stats l JOIN general_game_stats g ON g.id=l.match_id WHERE g.season=2022 AND g.league IN ('EPL','Bundesliga','La liga','La_liga','Ligue 1','Ligue_1','Serie A','Serie_A') ORDER BY l.match_id,l.team_id,l.positionOrder,l.position").fetchall(); con.close()
    by=defaultdict(list)
    for mid,tid,ha,pos,po in ls:
        if str(pos)!="Sub": by[(int(mid),int(tid),str(ha))].append(str(pos))
    rows=[]; invalid=0; signatures=set()
    delay=int(p["source"]["release_delay_hours"])
    for mid,date,hid,aid,league,season in games:
        ko=normdt(date); hs=shape(by[(int(mid),int(hid),"h")]); aas=shape(by[(int(mid),int(aid),"a")])
        if not hs["available"]: invalid+=1
        if not aas["available"]: invalid+=1
        if hs["signature"]: signatures.add(hs["signature"])
        if aas["signature"]: signatures.add(aas["signature"])
        rows.append({"fixture_id":f"understat:{int(mid)}","league":BIG5[str(league)],"season_start":2022,"kickoff":zulu(ko),"release_at":zulu(ko+timedelta(hours=delay)),"home_team_id":f"understat-team:{int(hid)}","away_team_id":f"understat-team:{int(aid)}","home_shape":hs,"away_shape":aas})
    rows.sort(key=lambda r:(r["kickoff"],r["fixture_id"])); ids=[r["fixture_id"] for r in rows]; require(len(ids)==len(set(ids)),"DUP_FIXTURE")
    out.mkdir(parents=True,exist_ok=True); fp=out/"shape_state_2022.jsonl"; fp.write_text("".join(canon(r).decode()+"\n" for r in rows))
    receipt={"schema_version":"football3-nova-n9-tactical-shape-source-receipt-v1","status":"N9_TACTICAL_SHAPE_SOURCE_PRECHECK_PASS","development_n":EXPECTED,"team_match_shape_n":EXPECTED*2,"invalid_shape_team_match_n":invalid,"valid_shape_team_match_n":EXPECTED*2-invalid,"unique_shape_signature_n":len(signatures),"state_projection_sha256":sha256(fp.read_bytes()),"availability_mode":p["source"]["availability_mode"],"release_delay_hours":delay,"safe_match_fields":p["source"]["safe_match_fields"],"safe_lineup_fields":p["source"]["safe_lineup_fields"],"result_labels_read":0,"score_values_read":0,"xg_values_read":0,"market_values_read":0,"coach_identity_status":"NOT_AVAILABLE","training_performed":False,"candidate_weight":0,"matrix_delta":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False}
    (out/"source_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n"); return receipt

def main():
    a=argparse.ArgumentParser(); a.add_argument("--db",type=Path,required=True); a.add_argument("--prereg",type=Path,required=True); a.add_argument("--out",type=Path,required=True); x=a.parse_args(); print(json.dumps(run(x.db,x.prereg,x.out),sort_keys=True))
if __name__=="__main__": main()
