from __future__ import annotations
import argparse, hashlib, json, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
EXPECTED=1826
BIG5={"EPL":"EPL","Bundesliga":"Bundesliga","La liga":"La_liga","La_liga":"La_liga","Ligue 1":"Ligue_1","Ligue_1":"Ligue_1","Serie A":"Serie_A","Serie_A":"Serie_A"}
class N6DataError(RuntimeError):pass
def req(c,m):
    if not c:raise N6DataError(m)
def canon(v:Any)->bytes:return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def norm(v:str)->str:
    d=datetime.fromisoformat(str(v).replace(" ","T").replace("Z","+00:00"))
    if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def run(prereg:Path,db:Path,baseline:Path,out:Path):
    p=json.loads(prereg.read_text());req(p["status"]=="DESIGN_LOCKED_PRELABEL","PREREG");req(int(p["data"]["development_season_start"])==2020,"SEASON")
    br=[json.loads(x) for x in baseline.read_text().splitlines() if x.strip()];req(len(br)==EXPECTED,"BASE_N");bids={r["n6_fixture_id"] for r in br};req(len(bids)==EXPECTED,"BASE_DUP")
    con=sqlite3.connect(f"file:{db}?mode=ro",uri=True);rows=con.execute("SELECT fid,date,h_id,a_id,league,h_goals,a_goals FROM general_game_stats WHERE season=2020 AND league IN ('EPL','Bundesliga','La liga','Ligue 1','Serie A') ORDER BY date,fid").fetchall();con.close();req(len(rows)==EXPECTED,f"DB_N:{len(rows)}")
    events=[];labels=[];delay=int(p["source"]["release_delay_hours"])
    for fid,date,h,a,league,hg,ag in rows:
        fid=f"understat:{int(fid)}";req(fid in bids,f"BASE_MISSING:{fid}");ko=norm(date);kod=datetime.fromisoformat(ko.replace("Z","+00:00"));hg=int(hg);ag=int(ag)
        events.append({"fixture_id":fid,"league":BIG5[str(league)],"season_start":2020,"kickoff":ko,"release_at":(kod+timedelta(hours=delay)).isoformat().replace("+00:00","Z"),"home_team_id":f"understat-team:{int(h)}","away_team_id":f"understat-team:{int(a)}","is_draw":int(hg==ag),"low_total_le2":int(hg+ag<=2),"tight_margin_le1":int(abs(hg-ag)<=1)})
        labels.append({"fixture_id":fid,"outcome":"home" if hg>ag else "away" if ag>hg else "draw"})
    events.sort(key=lambda r:(r["kickoff"],r["fixture_id"])); labels_by={r["fixture_id"]:r for r in labels};labels=[labels_by[r["fixture_id"]] for r in events]
    req(len({r["fixture_id"] for r in events})==EXPECTED,"EVENT_DUP");req(all(set(r)=={"fixture_id","league","season_start","kickoff","release_at","home_team_id","away_team_id","is_draw","low_total_le2","tight_margin_le1"} for r in events),"EVENT_FIELDS")
    out.mkdir(parents=True,exist_ok=True);ep=out/"history_events_2020.jsonl";lp=out/"labels_2020.jsonl";ep.write_text("".join(canon(r).decode()+"\n" for r in events));lp.write_text("".join(canon(r).decode()+"\n" for r in labels))
    rec={"schema_version":"football3-nova-n6-draw-score-event-vault-v1","status":"N6_2020_EVENT_LABEL_VAULT_PASS","development_n":EXPECTED,"development_season":2020,"release_delay_hours":delay,"raw_goal_values_emitted":False,"event_sha256":sha(ep.read_bytes()),"label_sha256":sha(lp.read_bytes()),"isolated_2021_labels_read":0,"isolated_2023_labels_read":0,"isolated_2025_labels_read":0,"other_season_rows_read":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0}
    (out/"event_vault_receipt.json").write_text(json.dumps(rec,indent=2,sort_keys=True)+"\n");return rec
def main():
    a=argparse.ArgumentParser();a.add_argument("--prereg",type=Path,required=True);a.add_argument("--db",type=Path,required=True);a.add_argument("--baseline",type=Path,required=True);a.add_argument("--out",type=Path,required=True);x=a.parse_args();print(json.dumps(run(x.prereg,x.db,x.baseline,x.out),sort_keys=True))
if __name__=="__main__":main()
