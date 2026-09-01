from __future__ import annotations

import argparse
import hashlib
import io
import json
import pathlib
import sqlite3
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import historical_pit_replay as core
from player_strength import DIMS

KAGGLE_PAGE = "https://www.kaggle.com/datasets/codytipton/player-stats-per-game-understat"
KAGGLE_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download/codytipton/player-stats-per-game-understat"
TEAM_KEYS = {
    "brighton and hove albion": "brighton", "brighton": "brighton",
    "luton town": "luton", "luton": "luton",
    "tottenham hotspur": "tottenham", "tottenham": "tottenham",
    "west ham united": "west ham", "west ham": "west ham",
    "newcastle united": "newcastle", "newcastle": "newcastle",
    "wolverhampton wanderers": "wolverhampton", "wolves": "wolverhampton", "wolverhampton": "wolverhampton",
    "afc bournemouth": "bournemouth", "bournemouth": "bournemouth",
    "nottingham forest": "nottingham forest", "nottm forest": "nottingham forest",
}
_DB_PATH: pathlib.Path | None = None
_ARCHIVE_SHA: str | None = None
_ARCHIVE_BYTES: int = 0
_GAME_TABLE: str | None = None
_LINEUP_TABLE: str | None = None
_GAME_SIDE: dict[str, tuple[str, str]] = {}

class UnderstatCompatError(RuntimeError): pass

def _team_key(name:str)->str:
    n=core.norm(name);return TEAM_KEYS.get(n,n)
def _q(name:str)->str:return '"'+str(name).replace('"','""')+'"'
def _columns(con,table):return {str(r[1]).lower():str(r[1]) for r in con.execute(f"pragma table_info({_q(table)})")}
def _find_table(con,required):
    found=[]
    for (table,) in con.execute("select name from sqlite_master where type='table' order by name"):
        cols=_columns(con,str(table))
        if required<=set(cols):found.append((str(table),cols))
    if len(found)!=1:raise UnderstatCompatError(f"dataset table discovery expected exactly one {sorted(required)}; found={[x[0] for x in found]}")
    return found[0]

def _download_dataset(out:pathlib.Path):
    global _DB_PATH,_ARCHIVE_SHA,_ARCHIVE_BYTES,_GAME_TABLE,_LINEUP_TABLE
    if _DB_PATH is not None:return _DB_PATH,json.load(open(out/'understat_dataset_receipt.json'))
    req=urllib.request.Request(KAGGLE_DOWNLOAD,headers={**core.UA,"Accept":"application/zip,application/octet-stream;q=0.9,*/*;q=0.1"})
    try:
        with urllib.request.urlopen(req,timeout=180) as r:raw=r.read()
    except Exception as exc:raise UnderstatCompatError(f"public Kaggle Understat dataset download failed without credentials: {type(exc).__name__}: {exc}") from exc
    if len(raw)<1000:raise UnderstatCompatError(f"public dataset response too small: {len(raw)} bytes")
    _ARCHIVE_SHA=hashlib.sha256(raw).hexdigest();_ARCHIVE_BYTES=len(raw)
    try:z=zipfile.ZipFile(io.BytesIO(raw))
    except Exception as exc:raise UnderstatCompatError("public dataset response is not a ZIP archive") from exc
    bad=z.testzip()
    if bad:raise UnderstatCompatError(f"public dataset ZIP CRC failed: {bad}")
    members=[x for x in z.namelist() if not x.endswith('/')];dbs=[x for x in members if pathlib.Path(x).suffix.lower() in {'.db','.sqlite','.sqlite3'}]
    if not dbs:raise UnderstatCompatError(f"public dataset contains no SQLite database; members={members[:40]}")
    ranked=sorted(dbs,key=lambda x:(0 if pathlib.Path(x).name.lower()=='understat.db' else 1,len(x),x))
    if len(ranked)>1 and pathlib.Path(ranked[0]).name.lower()!='understat.db':raise UnderstatCompatError(f"ambiguous SQLite database members: {ranked}")
    tmp=pathlib.Path(tempfile.mkdtemp(prefix='football3_understat_'))/'understat.db'
    with z.open(ranked[0]) as src,tmp.open('wb') as dst:
        for b in iter(lambda:src.read(1<<20),b''):dst.write(b)
    con=sqlite3.connect(tmp)
    try:
        _GAME_TABLE,_=_find_table(con,{'id','date','team_h','team_a'});_LINEUP_TABLE,_=_find_table(con,{'match_id','player_id','team_id','position','player','time','xg','xa','xgchain','xgbuildup'})
    finally:con.close()
    _DB_PATH=tmp
    receipt={'schema_version':'football3-understat-public-dataset-v1','provider':'Cody Tipton player stats per game - Understat','source_page':KAGGLE_PAGE,'download_url':KAGGLE_DOWNLOAD,'license':'MIT (dataset page declaration)','archive_sha256':_ARCHIVE_SHA,'archive_bytes':_ARCHIVE_BYTES,'zip_crc':'PASS','database_member':ranked[0],'database_sha256':hashlib.sha256(tmp.read_bytes()).hexdigest(),'database_bytes':tmp.stat().st_size,'game_table':_GAME_TABLE,'lineup_table':_LINEUP_TABLE,'query_boundary':{'identity':'SELECT only id,date,team_h,team_a,h_id,a_id,league,season where present; no score/result/xG columns','prior_player_state':'SELECT only match_id,player_id,team_id,position,player,time,h_a,xG,xA,xGChain,xGBuildup after that match prediction batch is frozen and release_at is reached','target_match_player_rows_before_own_prediction':0,'season_aggregate_player_rows':0},'collected_at':core.iso(datetime.now(timezone.utc))}
    core.dump(out/'understat_dataset_receipt.json',receipt);return tmp,receipt

def understat_identity(v2_rows,out):
    global _GAME_SIDE
    db,receipt=_download_dataset(out);con=sqlite3.connect(db)
    try:
        cols=_columns(con,_GAME_TABLE or '');safe=['id','date','team_h','team_a']+[x for x in ('h_id','a_id','league','season') if x in cols];sql='select '+','.join(_q(cols[x]) for x in safe)+' from '+_q(_GAME_TABLE or '');rows=[dict(zip(safe,t)) for t in con.execute(sql)]
    finally:con.close()
    idx=defaultdict(list)
    for x in rows:
        date=str(x.get('date') or '')[:10];home=str(x.get('team_h') or '');away=str(x.get('team_a') or '')
        if date and home and away:
            rec={'understat_match_id':str(x['id']),'date':date,'home':home,'away':away,'source_home_id':None if x.get('h_id') is None else str(x.get('h_id')),'source_away_id':None if x.get('a_id') is None else str(x.get('a_id'))};idx[(date,_team_key(home),_team_key(away))].append(rec)
    mapping={};audit=[]
    for r in v2_rows:
        key=(str(r['cutoff'])[:10],_team_key(r['home_team']),_team_key(r['away_team']));cand=idx.get(key,[])
        if len(cand)!=1:raise UnderstatCompatError(f"public dataset identity collision/miss for {key}: {len(cand)}")
        z=cand[0];fid=str(r['fixture_id']);mapping[fid]=z['understat_match_id'];_GAME_SIDE[z['understat_match_id']]=(z.get('source_home_id') or '',z.get('source_away_id') or '');audit.append({'fixture_id':fid,**z,'canonical_home':key[1],'canonical_away':key[2]})
    if len(mapping)!=core.FULL_SEASON_N or len(set(mapping.values()))!=core.FULL_SEASON_N:raise UnderstatCompatError(f"public dataset full-season identity is not one-to-one {core.FULL_SEASON_N}: {len(mapping)}")
    rec={'schema_version':'football3-understat-epl-2023-24-identity-v4','transport':'PUBLIC_KAGGLE_DATASET_SQLITE_SAFE_COLUMN_PROJECTION','source_url':KAGGLE_PAGE,'archive_sha256':receipt['archive_sha256'],'database_sha256':receipt['database_sha256'],'collected_at':core.iso(datetime.now(timezone.utc)),'identity_rule':'date+frozen EPL cross-source team alias+home/away direction; SQL projection excludes score/result/xG columns','team_aliases':TEAM_KEYS,'mapped_n':len(mapping),'result_fields_read':False,'xg_fields_read_for_identity':False,'rows_sha256':core.canon(audit)};core.dump(out/'understat_identity_receipt.json',rec);return mapping,rec

def _num(x):
    if x is None or x=='':return 0.0
    try:return float(x)
    except Exception:return 0.0

def understat_roster(match_id,home_tid,away_tid,release_at):
    if _DB_PATH is None or _LINEUP_TABLE is None or _ARCHIVE_SHA is None:raise UnderstatCompatError('dataset not initialized before prior-match release')
    con=sqlite3.connect(_DB_PATH)
    try:
        cols=_columns(con,_LINEUP_TABLE);needed=['match_id','player_id','team_id','position','player','time','xg','xa','xgchain','xgbuildup'];optional=[x for x in ('h_a','positionorder') if x in cols];sql='select '+','.join(_q(cols[x]) for x in needed+optional)+' from '+_q(_LINEUP_TABLE)+' where '+_q(cols['match_id'])+'=?';rawrows=[dict(zip(needed+optional,t)) for t in con.execute(sql,(match_id,))]
    finally:con.close()
    if not rawrows:raise UnderstatCompatError(f'prior released match has no lineup rows: {match_id}')
    source_h,source_a=_GAME_SIDE.get(str(match_id),('',''));out_usage=defaultdict(list);events=[];aliases=defaultdict(list);safe_rows=[]
    for x in rawrows:
        name=str(x.get('player') or '').strip();pid0=str(x.get('player_id') or '').strip();source_tid=str(x.get('team_id') or '').strip();ha=str(x.get('h_a') or '').strip().lower()
        if not name or not pid0:continue
        if ha=='h' or (source_h and source_tid==source_h):tid=str(home_tid)
        elif ha=='a' or (source_a and source_tid==source_a):tid=str(away_tid)
        else:raise UnderstatCompatError(f'prior player side identity unresolved match={match_id} player={pid0} source_team={source_tid} h_a={ha}')
        pid='understat_player_'+pid0;position=str(x.get('position') or '');minutes=max(0.0,_num(x.get('time')));started=position.strip().lower() not in {'sub','substitute','bench'} and minutes>0;role=core.role_from_position(position);rec={'player_id':pid,'started':started,'appeared':minutes>0,'minutes':minutes,'role':role,'known_at':release_at,'player_name':name};out_usage[tid].append(rec);aliases[tid].append({'player_id':pid,'player_name':name})
        values={'shot_generation':_num(x.get('xg')),'finishing':0.0,'chance_creation':_num(x.get('xa')),'passing_progression':_num(x.get('xgbuildup')),'carrying_progression':0.0,'possession_retention_risk':0.0,'pressing':0.0,'tackling_interception':0.0,'defensive_position_protection':0.0,'aerial':0.0,'set_piece':0.0,'goalkeeper_shot_stopping':0.0,'goalkeeper_sweeping':0.0,'goalkeeper_cross_claiming':0.0,'goalkeeper_distribution':0.0,'on_ball_contribution':_num(x.get('xgchain')),'off_ball_contribution':0.0,'current_form':_num(x.get('xg'))+_num(x.get('xa'))}
        if set(values)!=set(DIMS):raise UnderstatCompatError('prior player dimension schema mismatch')
        if minutes>0:events.append({'player_id':pid,'team_id':tid,'league_id':core.LEAGUE,'role':role,'known_at':release_at,'minutes_exposure':minutes,'possession_opportunity':1.0,'values':values,'source_sha256':_ARCHIVE_SHA})
        safe_rows.append({'match_id':str(match_id),'player_id':pid0,'team_id':source_tid,'position':position,'player':name,'time':minutes,'h_a':ha,'xG':_num(x.get('xg')),'xA':_num(x.get('xa')),'xGChain':_num(x.get('xgchain')),'xGBuildup':_num(x.get('xgbuildup'))})
    for tid,rows in out_usage.items():
        if len([x for x in rows if x['started']])!=11:
            for x in rows:x['started']=False
    subset=core.canon(sorted(safe_rows,key=lambda x:(x['h_a'],x['player_id'])));return {'source_url':KAGGLE_PAGE+'#lineup_stats','source_sha256':subset,'source_archive_sha256':_ARCHIVE_SHA,'source_bytes':len(json.dumps(safe_rows,sort_keys=True,separators=(',',':')).encode()),'release_at':release_at,'usage':dict(out_usage),'events':events,'aliases':dict(aliases),'prohibited_target_fields_retained':False,'rating_field_used':False,'transport':'PUBLIC_KAGGLE_DATASET_SQLITE_SAFE_COLUMN_PROJECTION'}

def strict_resolve_source_name(name,tid,registry):
    n=core.norm(name)
    if not n:return None,'EMPTY'
    exact=registry.get(str(tid),{}).get(n)
    if exact:return exact,'PRIOR_HISTORY_EXACT'
    surname=n.split()[-1];candidates={pid for nm,pid in registry.get(str(tid),{}).items() if nm.split() and nm.split()[-1]==surname}
    if len(candidates)==1:return next(iter(candidates)),'PRIOR_HISTORY_UNIQUE_SURNAME'
    if len(candidates)>1:raise UnderstatCompatError(f'ambiguous player identity {tid} {name}: {sorted(candidates)}')
    return None,'NO_PRIOR_PERMANENT_IDENTITY_FAIL_CLOSED'

def install():core.understat_identity=understat_identity;core.understat_roster=understat_roster;core.resolve_source_name=strict_resolve_source_name
def main():
    install();ap=argparse.ArgumentParser();sp=ap.add_subparsers(dest='cmd',required=True);p=sp.add_parser('predict');p.add_argument('--v2',required=True);p.add_argument('--out',required=True);s=sp.add_parser('score');s.add_argument('--v2',required=True);s.add_argument('--label-vault',required=True);s.add_argument('--out',required=True);a=ap.parse_args();result=core.run_prediction(pathlib.Path(a.v2),pathlib.Path(a.out)) if a.cmd=='predict' else core.score(pathlib.Path(a.v2),pathlib.Path(a.label_vault),pathlib.Path(a.out));print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
