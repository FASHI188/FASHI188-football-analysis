from __future__ import annotations
import argparse, hashlib, json, sqlite3
from pathlib import Path
from typing import Any
BIG5={"EPL":"EPL","Bundesliga":"Bundesliga","La liga":"La_liga","La_liga":"La_liga","Ligue 1":"Ligue_1","Ligue_1":"Ligue_1","Serie A":"Serie_A","Serie_A":"Serie_A"}
EXPECTED=1826
class N9LabelError(RuntimeError): pass
def require(c,m):
    if not c: raise N9LabelError(m)
def canon(v:Any)->bytes: return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def sha256(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def normdt(v:str)->str:
    x=str(v).replace(" ","T").replace("+00:00","Z"); return x if x.endswith("Z") else x+"Z"
def run(db:Path,projection:Path,out:Path):
    targets=[json.loads(x) for x in projection.read_text().splitlines() if x.strip()]; require(len(targets)==EXPECTED,"TARGET_N"); require(all(int(r["season_start"])==2022 for r in targets),"NON_2022_TARGET")
    con=sqlite3.connect(f"file:{db}?mode=ro",uri=True); rows=con.execute("SELECT date,h_id,a_id,league,h_goals,a_goals FROM general_game_stats WHERE season=2022 AND league IN ('EPL','Bundesliga','La liga','La_liga','Ligue 1','Ligue_1','Serie A','Serie_A') ORDER BY date,h_id,a_id").fetchall(); con.close(); require(len(rows)==EXPECTED,f"DB_N:{len(rows)}")
    idx={}
    for r in rows:
        key=(normdt(r[0]),f"understat-team:{int(r[1])}",f"understat-team:{int(r[2])}",BIG5[str(r[3])]); require(key not in idx,"DB_DUP"); idx[key]=r
    labels=[]; binds=[]
    for t in targets:
        key=(str(t["kickoff"]),str(t["home_team_id"]),str(t["away_team_id"]),str(t["league"])); require(key in idx,f"UNMATCHED:{key}"); r=idx[key]; hg,ag=int(r[4]),int(r[5]); outcome="home" if hg>ag else "away" if ag>hg else "draw"; labels.append({"fixture_id":t["fixture_id"],"kickoff":t["kickoff"],"league":t["league"],"season_start":2022,"outcome":outcome}); binds.append(list(key))
    out.mkdir(parents=True,exist_ok=True); lp=out/"labels_2022.jsonl"; lp.write_text("".join(canon(r).decode()+"\n" for r in labels))
    receipt={"schema_version":"football3-nova-n9-label-vault-v1","status":"N9_DEVELOPMENT_2022_LABEL_VAULT_PASS","development_n":EXPECTED,"development_season":2022,"queried_season_predicate":2022,"n2_2023_isolated_labels_read":0,"n1_2025_isolated_labels_read":0,"n3_2021_isolated_labels_read":0,"label_sha256":sha256(lp.read_bytes()),"binding_sha256":sha256(canon(binds)),"score_fields_emitted":False,"raw_goals_emitted":False,"training_performed":False,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0}; (out/"label_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n"); return receipt

def main():
    p=argparse.ArgumentParser(); p.add_argument("--db",type=Path,required=True); p.add_argument("--projection",type=Path,required=True); p.add_argument("--out",type=Path,required=True); a=p.parse_args(); print(json.dumps(run(a.db,a.projection,a.out),sort_keys=True))
if __name__=="__main__": raise SystemExit(main() or 0)
