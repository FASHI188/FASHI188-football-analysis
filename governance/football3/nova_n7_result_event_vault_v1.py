from __future__ import annotations
import argparse,hashlib,json,sqlite3
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any
EXPECTED=1826
BIG5={'EPL':'EPL','Bundesliga':'Bundesliga','La liga':'La_liga','La_liga':'La_liga','Ligue 1':'Ligue_1','Ligue_1':'Ligue_1','Serie A':'Serie_A','Serie_A':'Serie_A'}
class E(RuntimeError):pass
def req(c,m):
    if not c:raise E(m)
def canon(v:Any)->bytes:return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def norm(v:str)->str:
    d=datetime.fromisoformat(str(v).replace(' ','T').replace('Z','+00:00'))
    if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def run(prereg:Path,db:Path,baseline:Path,out:Path):
    p=json.loads(prereg.read_text());req(p['status']=='DESIGN_LOCKED_PRELABEL','PREREG');req(int(p['data']['development_season_start'])==2022,'SEASON')
    br=[json.loads(x) for x in baseline.read_text().splitlines() if x.strip()];req(len(br)==EXPECTED,'BASE_N')
    bidx={(norm(r['kickoff']),str(r['home_team_id']),str(r['away_team_id']),str(r['league'])):r for r in br};req(len(bidx)==EXPECTED,'BASE_DUP')
    con=sqlite3.connect(f'file:{db}?mode=ro',uri=True);rows=con.execute("SELECT date,h_id,a_id,league,h_goals,a_goals FROM general_game_stats WHERE season=2022 AND league IN ('EPL','Bundesliga','La liga','Ligue 1','Serie A') ORDER BY date,h_id,a_id").fetchall();con.close();req(len(rows)==EXPECTED,f'DB_N:{len(rows)}')
    delay=int(p['source']['release_delay_hours']);events=[];labels=[];binds=[]
    for date,h,a,league,hg,ag in rows:
        key=(norm(date),f'understat-team:{int(h)}',f'understat-team:{int(a)}',BIG5[str(league)]);req(key in bidx,f'UNMATCHED:{key}');b=bidx[key];ko=key[0];kod=datetime.fromisoformat(ko.replace('Z','+00:00'));hg=int(hg);ag=int(ag);o='home' if hg>ag else 'away' if ag>hg else 'draw'
        events.append({'fixture_id':str(b['n2_fixture_id']),'league':key[3],'season_start':2022,'kickoff':ko,'release_at':(kod+timedelta(hours=delay)).isoformat().replace('+00:00','Z'),'home_team_id':key[1],'away_team_id':key[2],'outcome':o})
        labels.append({'fixture_id':str(b['n2_fixture_id']),'outcome':o});binds.append(list(key))
    events.sort(key=lambda r:(r['kickoff'],r['fixture_id']));lb={r['fixture_id']:r for r in labels};labels=[lb[r['fixture_id']] for r in events]
    req(len({r['fixture_id'] for r in events})==EXPECTED,'DUP');out.mkdir(parents=True,exist_ok=True);ep=out/'history_events_2022.jsonl';lp=out/'labels_2022.jsonl';ep.write_text(''.join(canon(r).decode()+'\n' for r in events));lp.write_text(''.join(canon(r).decode()+'\n' for r in labels))
    rec={'schema_version':'football3-nova-n7-result-event-vault-v1','status':'N7_2022_RESULT_EVENT_LABEL_VAULT_PASS','development_n':EXPECTED,'development_season':2022,'release_delay_hours':delay,'event_fields':['fixture_id','league','season_start','kickoff','release_at','home_team_id','away_team_id','outcome'],'event_sha256':sha(ep.read_bytes()),'label_sha256':sha(lp.read_bytes()),'binding_sha256':sha(canon(binds)),'raw_goals_emitted':False,'market_values_read':0,'xg_values_read':0,'isolated_2021_labels_read':0,'isolated_2023_labels_read':0,'isolated_2025_labels_read':0,'other_season_rows_read':0,'formal_v2_changed':False,'current_changed':False,'production_changed':False,'candidate_weight':0,'matrix_delta':0}
    (out/'event_vault_receipt.json').write_text(json.dumps(rec,indent=2,sort_keys=True)+'\n');return rec
def main():
    a=argparse.ArgumentParser();a.add_argument('--prereg',type=Path,required=True);a.add_argument('--db',type=Path,required=True);a.add_argument('--baseline',type=Path,required=True);a.add_argument('--out',type=Path,required=True);x=a.parse_args();print(json.dumps(run(x.prereg,x.db,x.baseline,x.out),sort_keys=True))
if __name__=='__main__':main()
