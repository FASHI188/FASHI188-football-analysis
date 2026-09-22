#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, gzip, hashlib, json, re, sqlite3, unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

EXPECTED=1826
BIG5={"EPL":"EPL","Bundesliga":"Bundesliga","La liga":"La_liga","La_liga":"La_liga","Ligue 1":"Ligue_1","Ligue_1":"Ligue_1","Serie A":"Serie_A","Serie_A":"Serie_A"}
class AuditError(RuntimeError): pass
def require(c:bool,m:str)->None:
    if not c: raise AuditError(m)
def canon(v:Any)->bytes: return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def sha256_bytes(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def sha256_file(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''): h.update(chunk)
    return h.hexdigest()
def utc(v:str)->datetime:
    d=datetime.fromisoformat(str(v).replace(' ','T').replace('Z','+00:00'))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def norm_name(s:str)->str:
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    x=x.replace('&',' and ')
    # Cross-source identity only: remove legal/entity wrappers and generic football-form words.
    # Geographic and brand-bearing tokens remain; no result/label fields participate.
    x=re.sub(r'\\b(football club|fussball club|fussballclub|club de futbol|club football|associazione sportiva|societa sportiva|societa|associazione|rasenballsport|olympique|stade|calcio)\\b',' ',x)
    x=re.sub(r'[^a-z0-9]+',' ',x)
    drop={'fc','cf','ac','ssc','afc','as','ogc','sv','vfb','rb','rc','sc','ss','us','uc','ud','ca','rcd','fsv','vfl','tsg','osc','bsc','bc','club','foot','spa','srl','sad','plc','ltd','de','del','di','da','the'}
    toks=[tok for tok in x.split() if tok not in drop and not tok.isdigit()]
    return ''.join(toks)
def sim(a:str,b:str)->float: return SequenceMatcher(None,norm_name(a),norm_name(b)).ratio()

def load_understat(db:Path, season:int)->list[dict[str,Any]]:
    con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
    q="SELECT id,date,h_id,a_id,team_h,team_a,league,season FROM general_game_stats WHERE season=? AND league IN ('EPL','Bundesliga','La liga','La_liga','Ligue 1','Ligue_1','Serie A','Serie_A') ORDER BY date,id"
    rows=con.execute(q,(season,)).fetchall(); con.close(); require(len(rows)==EXPECTED,f'UNDERSTAT_N:{len(rows)}')
    out=[]
    for mid,date,hid,aid,hn,an,league,ss in rows:
        ko=utc(str(date)); out.append({"fixture_id":f"understat:{int(mid)}","understat_match_id":int(mid),"kickoff":ko,"date":ko.date().isoformat(),"league":BIG5[str(league)],"season":int(ss),"home_team_id":f"understat-team:{int(hid)}","away_team_id":f"understat-team:{int(aid)}","home_name":str(hn),"away_name":str(an)})
    return out

def read_games(gz:Path, safe_columns:list[str])->tuple[list[dict[str,str]],list[str]]:
    out=[]
    with gzip.open(gz,'rt',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f); require(rd.fieldnames is not None,'NO_HEADER'); header=list(rd.fieldnames)
        missing=[c for c in safe_columns if c not in header]; require(not missing,f'MISSING_SAFE_COLUMNS:{missing}')
        for raw in rd: out.append({c:str(raw.get(c) or '').strip() for c in safe_columns})
    return out,header

def bind_targets(targets:list[dict[str,Any]], games:list[dict[str,str]], league_map:dict[str,str], cfg:dict[str,Any]):
    by=defaultdict(list)
    league_ids=set(league_map.values())
    for g in games:
        if g['competition_id'] in league_ids and g['season']=='2022': by[(g['competition_id'],g['date'])].append(g)
    bound=[]; diagnostics=[]
    min_side=float(cfg['minimum_side_similarity']); min_pair=float(cfg['minimum_pair_similarity']); margin=float(cfg['minimum_margin'])
    for t in targets:
        cand=by.get((league_map[t['league']],t['date']),[]); scored=[]
        for g in cand:
            hs,as_=sim(t['home_name'],g['home_club_name']),sim(t['away_name'],g['away_club_name']); scored.append((hs+as_,min(hs,as_),hs,as_,g))
        scored.sort(key=lambda z:(z[0],z[1],z[4]['game_id']),reverse=True)
        require(scored,f"NO_DATE_CANDIDATE:{t['fixture_id']}:{t['league']}:{t['date']}")
        best=scored[0]; second=scored[1][0] if len(scored)>1 else -1.0
        if best[1] < min_side or best[0] < min_pair or best[0]-second < margin:
            diagnostics.append({"fixture_id":t['fixture_id'],"target":[t['home_name'],t['away_name']],"best":[best[4]['home_club_name'],best[4]['away_club_name']],"scores":[best[2],best[3]],"pair":best[0],"margin":best[0]-second,"candidate_n":len(scored)})
            continue
        g=best[4]; x=dict(t); x.update({"tm_game_id":int(g['game_id']),"tm_home_club_id":int(g['home_club_id']),"tm_away_club_id":int(g['away_club_id']),"tm_home_name":g['home_club_name'],"tm_away_name":g['away_club_name'],"identity_home_similarity":best[2],"identity_away_similarity":best[3],"identity_margin":best[0]-second}); bound.append(x)
    return bound,diagnostics

def manager_events(games:list[dict[str,str]], lag_hours:int=48):
    out=defaultdict(list); invalid=0
    for g in games:
        try: d=datetime.fromisoformat(g['date']).replace(tzinfo=timezone.utc)
        except Exception: invalid+=1; continue
        available=d+timedelta(hours=lag_hours)
        for side in ('home','away'):
            cid=g[f'{side}_club_id']; mgr=g[f'{side}_club_manager_name']
            if not cid or not mgr: continue
            out[int(cid)].append((available,mgr,int(g['game_id']),g['date']))
    for cid in out: out[cid].sort(key=lambda x:(x[0],x[2]))
    return out,invalid

def state_at(events:list[tuple[datetime,str,int,str]], cutoff:datetime):
    usable=[e for e in events if e[0] <= cutoff]
    if not usable: return {"available":False,"manager":None,"available_at":None,"source_game_id":None,"history_n":0,"tenure_observation_n":0}
    last=usable[-1]; mgr=last[1]; tenure=0
    for e in reversed(usable):
        if e[1]!=mgr: break
        tenure+=1
    return {"available":True,"manager":mgr,"available_at":last[0].isoformat().replace('+00:00','Z'),"source_game_id":last[2],"history_n":len(usable),"tenure_observation_n":tenure}

def run(prereg:Path,db:Path,games_gz:Path,out:Path,exact_head:str)->dict[str,Any]:
    p=json.loads(prereg.read_text()); require(p['status']=='DESIGN_LOCKED_ZERO_LABEL','PREREG_STATUS')
    require(p['target_cohort']['labels_allowed'] is False and p['target_cohort']['training_allowed'] is False,'ZERO_LABEL_LOCK')
    data_sha=sha256_file(games_gz); expected=p['source'].get('expected_data_sha256')
    if expected: require(data_sha==expected,f'DATA_SHA_DRIFT:{data_sha}!={expected}')
    targets=load_understat(db,int(p['target_cohort']['season_start'])); games,header=read_games(games_gz,list(p['source']['safe_columns']))
    bound,diag=bind_targets(targets,games,p['league_map'],p['identity_binding'])
    require(len(bound)==int(p['identity_binding']['target_bind_required_n']),f"IDENTITY_BIND:{len(bound)}/{len(targets)}:diagnostics={diag[:8]}")
    tmids=[x['tm_game_id'] for x in bound]; require(len(tmids)==len(set(tmids)),'TM_GAME_REUSED')
    events,invalid_dates=manager_events(games,48); rows=[]; byleague=defaultdict(lambda:{'n':0,'both':0,'home':0,'away':0})
    unique_mgr=set(); direct_target_manager_used=0
    for t in bound:
        hs=state_at(events.get(t['tm_home_club_id'],[]),t['kickoff']); aas=state_at(events.get(t['tm_away_club_id'],[]),t['kickoff'])
        require(hs['source_game_id']!=t['tm_game_id'] and aas['source_game_id']!=t['tm_game_id'],'TARGET_MANAGER_DIRECT_USE')
        if hs['available']: unique_mgr.add(hs['manager'])
        if aas['available']: unique_mgr.add(aas['manager'])
        lr=byleague[t['league']]; lr['n']+=1; lr['home']+=int(hs['available']); lr['away']+=int(aas['available']); lr['both']+=int(hs['available'] and aas['available'])
        rows.append({"fixture_id":t['fixture_id'],"kickoff":t['kickoff'].isoformat().replace('+00:00','Z'),"league":t['league'],"season_start":2022,"home_team_id":t['home_team_id'],"away_team_id":t['away_team_id'],"tm_game_id":t['tm_game_id'],"tm_home_club_id":t['tm_home_club_id'],"tm_away_club_id":t['tm_away_club_id'],"home_manager_state":hs,"away_manager_state":aas})
    league_report={k:{**v,'home_coverage':v['home']/v['n'],'away_coverage':v['away']/v['n'],'both_coverage':v['both']/v['n']} for k,v in sorted(byleague.items())}
    both=sum(v['both'] for v in byleague.values()); pooled=both/len(rows)
    gate=p['data_ready_gate']; ready=(len(bound)/len(targets)>=float(gate['identity_binding_coverage_required']) and pooled>=float(gate['both_manager_state_coverage_pooled_gte']) and all(v['both_coverage']>=float(gate['both_manager_state_coverage_each_big5_gte']) for v in league_report.values()))
    out.mkdir(parents=True,exist_ok=True); fp=out/'coach_history_state_2022.jsonl'; fp.write_text(''.join(canon(r).decode()+'\n' for r in rows))
    receipt={"schema_version":"football3-nova-n9-coach-history-data-ready-receipt-v1","status":"N9_COACH_HISTORY_DATA_READY" if ready else "STOP_DATA_COVERAGE","classification":"DATA_READY" if ready else "STOP_DATA_COVERAGE","exact_head":exact_head,"source_repository":p['source']['repository'],"source_revision":p['source']['revision'],"source_license":p['source']['license'],"source_license_blob_sha":p['source']['license_blob_sha'],"source_schema_blob_sha":p['source']['schema_blob_sha'],"dataset_content_sha256":data_sha,"dataset_expected_sha256":expected,"dataset_header_sha256":sha256_bytes(canon(header)),"target_n":len(targets),"identity_bound_n":len(bound),"identity_coverage":len(bound)/len(targets),"identity_ambiguous_or_unmatched_n":len(diag),"manager_state_both_n":both,"manager_state_both_coverage":pooled,"league_report":league_report,"unique_manager_state_n":len(unique_mgr),"invalid_source_date_n":invalid_dates,"availability_rule":"calendar_date_00:00Z_plus_48h","target_match_manager_direct_feature_used":direct_target_manager_used,"result_labels_read":0,"score_values_read":0,"xg_values_read":0,"market_values_read":0,"training_performed":False,"scoring_performed":False,"candidate_weight":0,"matrix_delta":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"state_projection_sha256":sha256_file(fp)}
    (out/'data_ready_receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    return receipt

def main():
    a=argparse.ArgumentParser(); a.add_argument('--prereg',type=Path,required=True); a.add_argument('--db',type=Path,required=True); a.add_argument('--games-gz',type=Path,required=True); a.add_argument('--out',type=Path,required=True); a.add_argument('--exact-head',required=True); x=a.parse_args(); print(json.dumps(run(x.prereg,x.db,x.games_gz,x.out,x.exact_head),sort_keys=True))
if __name__=='__main__': main()
