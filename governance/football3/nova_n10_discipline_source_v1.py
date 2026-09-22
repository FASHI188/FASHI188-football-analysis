#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,sqlite3
from pathlib import Path
from datetime import datetime,timedelta,timezone
from typing import Any
EXPECTED=1826
BIG5={"EPL":"EPL","Bundesliga":"Bundesliga","La liga":"La_liga","La_liga":"La_liga","Ligue 1":"Ligue_1","Ligue_1":"Ligue_1","Serie A":"Serie_A","Serie_A":"Serie_A"}
class N10SourceError(RuntimeError): pass
def require(c,m):
    if not c: raise N10SourceError(m)
def canon(v:Any)->bytes:return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def sha256(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def utc(v:str)->datetime:
    d=datetime.fromisoformat(str(v).replace(" ","T").replace("Z","+00:00"));return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def run(db:Path,prereg:Path,out:Path):
    p=json.loads(prereg.read_text());require(p["status"]=="DESIGN_LOCKED_PRELABEL","PREREG");season=int(p["data"]["development_season_start"]);delay=int(p["source"]["release_delay_hours"])
    con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
    gcols=[r[1] for r in con.execute("pragma table_info(general_game_stats)")];lcols=[r[1] for r in con.execute("pragma table_info(lineup_stats)")]
    require("referee" not in {x.lower() for x in gcols+lcols},"UNEXPECTED_REFEREE_FIELD_REQUIRES_NEW_DESIGN")
    q="""SELECT g.id,g.date,g.h_id,g.a_id,g.league,g.season,
      SUM(CASE WHEN l.team_id=g.h_id THEN COALESCE(l.yellow_card,0) ELSE 0 END),
      SUM(CASE WHEN l.team_id=g.a_id THEN COALESCE(l.yellow_card,0) ELSE 0 END),
      SUM(CASE WHEN l.team_id=g.h_id THEN COALESCE(l.red_card,0) ELSE 0 END),
      SUM(CASE WHEN l.team_id=g.a_id THEN COALESCE(l.red_card,0) ELSE 0 END),
      COUNT(l.player_id)
      FROM general_game_stats g LEFT JOIN lineup_stats l ON l.match_id=g.id
      WHERE g.season=? AND g.league IN ('EPL','Bundesliga','La liga','La_liga','Ligue 1','Ligue_1','Serie A','Serie_A')
      GROUP BY g.id ORDER BY g.date,g.id"""
    raw=con.execute(q,(season,)).fetchall();con.close();require(len(raw)==EXPECTED,f"N:{len(raw)}")
    rows=[];seen=set();ty=tr=nl=0
    for mid,date,hid,aid,league,ss,hy,ay,hr,ar,nlr in raw:
        require(nlr>0,f"NO_LINEUP:{mid}");vals=[hy,ay,hr,ar];require(all(v is not None and int(v)>=0 and float(v).is_integer() for v in vals),f"BAD_CARD:{mid}:{vals}")
        ko=utc(str(date));fid=f"understat:{int(mid)}";require(fid not in seen,"DUP");seen.add(fid)
        rows.append({"fixture_id":fid,"league":BIG5[str(league)],"season_start":int(ss),"kickoff":ko.isoformat().replace("+00:00","Z"),"release_at":(ko+timedelta(hours=delay)).isoformat().replace("+00:00","Z"),"home_team_id":f"understat-team:{int(hid)}","away_team_id":f"understat-team:{int(aid)}","home_yellow":int(hy),"away_yellow":int(ay),"home_red":int(hr),"away_red":int(ar)})
        ty+=int(hy)+int(ay);tr+=int(hr)+int(ar);nl+=int(nlr)
    out.mkdir(parents=True,exist_ok=True);fp=out/"discipline_state_2022.jsonl";fp.write_text("".join(canon(r).decode()+"\n" for r in rows))
    rec={"schema_version":"football3-nova-n10-discipline-source-receipt-v1","status":"N10_DISCIPLINE_SOURCE_PRECHECK_PASS","development_n":len(rows),"development_season":season,"fixture_id_unique_n":len(seen),"release_delay_hours":delay,"same_kickoff_atomic_required":True,"total_yellow_cards":ty,"total_red_cards":tr,"lineup_rows_aggregated":nl,"referee_status":"NOT_AVAILABLE","referee_field_available":False,"referee_weight":0,"referee_matrix_delta":0,"current_match_card_direct_feature_allowed":False,"result_labels_read":0,"score_values_read":0,"xg_values_read":0,"market_values_read":0,"training_performed":False,"candidate_weight":0,"matrix_delta":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"state_projection_sha256":sha256(fp.read_bytes())}
    (out/"source_receipt.json").write_text(json.dumps(rec,indent=2,sort_keys=True)+"\n");return rec
def main():
    a=argparse.ArgumentParser();a.add_argument("--db",type=Path,required=True);a.add_argument("--prereg",type=Path,required=True);a.add_argument("--out",type=Path,required=True);x=a.parse_args();print(json.dumps(run(x.db,x.prereg,x.out),sort_keys=True))
if __name__=="__main__":main()
