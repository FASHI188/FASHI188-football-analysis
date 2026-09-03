from __future__ import annotations
import argparse, hashlib, heapq, json, math, sqlite3, sys, time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
FOOTBALL_DATA=ROOT/'football-data'
if str(FOOTBALL_DATA) not in sys.path: sys.path.insert(0,str(FOOTBALL_DATA))
from new_engine_v1 import formal_fusion_v2 as formal
from historical_xg_challenger_v1 import historical_xg_challenger as hxg
import understat_source_preflight as source

CUTOFF=datetime.fromisoformat('2026-09-02T17:00:00+00:00')
BIG5=('Bundesliga','EPL','La liga','Ligue 1','Serie A')
LEAGUE_ID={'EPL':1,'Serie A':2,'Bundesliga':3,'La liga':4,'Ligue 1':5}
SEASON_SLUG={'EPL':'EPL','La liga':'La_Liga','Bundesliga':'Bundesliga','Serie A':'Serie_A','Ligue 1':'Ligue_1'}
BRIDGE_SEASONS=(2024,2025,2026)
HALF_LIFE=90.0
PRIOR_MATCHES=12.0
MIN_PROFILE_WEIGHT=3.0
SET_SITUATIONS={'SetPiece','FromCorner','DirectFreekick'}
CONTRACT_PATH=HERE/'PROSPECTIVE_CONFIRMATION_CONTRACT.json'
FROZEN_STATE_PATH=HERE/'FROZEN_CANDIDATE_STATE.json'

def canon(o:Any)->bytes:
    return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def sha(o:Any)->str: return hashlib.sha256(canon(o)).hexdigest()
def iso(t:datetime|None)->str|None: return None if t is None else t.astimezone(timezone.utc).isoformat()
def finite(x:Any,name:str)->float:
    v=float(x)
    if not math.isfinite(v): raise RuntimeError(f'{name} nonfinite')
    return v

@dataclass
class ProcessTeamState:
    last:datetime|None=None
    weight:float=0.0
    sums:dict[str,float]|None=None
    def snapshot(self,t:datetime)->tuple[float,dict[str,float]]:
        s={} if self.sums is None else dict(self.sums)
        if self.last is None: return 0.0,{k:0.0 for k in s}
        if t<self.last: raise RuntimeError('process state time reversal')
        days=(t-self.last).total_seconds()/86400.0
        fac=math.exp(-math.log(2.0)*days/HALF_LIFE)
        return self.weight*fac,{k:v*fac for k,v in s.items()}
    def add(self,t:datetime,stats:dict[str,float])->None:
        w,s=self.snapshot(t); keys=set(s)|set(stats)
        self.sums={k:s.get(k,0.0)+float(stats.get(k,0.0)) for k in keys}
        self.weight=w+1.0; self.last=t

def aggregate_shots(shots:list[dict[str,Any]])->dict[str,dict[str,float]]|None:
    side_rows={'h':[],'a':[]}
    for row in shots:
        if not isinstance(row,dict): raise RuntimeError('shot row not object')
        side=str(row.get('h_a') or '')
        sit=str(row.get('situation') or '')
        if side not in side_rows: raise RuntimeError('shot side invalid')
        if sit not in source.ALLOWED_SITUATIONS: raise RuntimeError(f'unknown shot situation {sit}')
        x=finite(row.get('xG'),'shot.xG')
        if x<0 or x>1: raise RuntimeError('shot xG invalid')
        side_rows[side].append((sit,x))
    out={}
    for side in ('h','a'):
        me=side_rows[side]; opp=side_rows['a' if side=='h' else 'h']
        me_np=[x for x in me if x[0]!='Penalty']; opp_np=[x for x in opp if x[0]!='Penalty']
        if not me_np or not opp_np: return None
        npxg=sum(x[1] for x in me_np); opp_npxg=sum(x[1] for x in opp_np)
        n=float(len(me_np)); on=float(len(opp_np))
        out[side]={
          'npxg_for':npxg,'npxg_against':opp_npxg,
          'npshots_for':n,'npshots_against':on,
          'npxg_per_shot':npxg/n,
          'open_share':sum(1 for sit,_ in me_np if sit=='OpenPlay')/n,
          'set_share':sum(1 for sit,_ in me_np if sit in SET_SITUATIONS)/n,
        }
    return out

def _side_from_db(ev:dict[tuple[str,str],dict[str,Any]],gid:str,side:str):
    me=ev.get((gid,side)); opp=ev.get((gid,'a' if side=='h' else 'h'))
    if not me or not opp or float(me['npshots'] or 0)<=0 or float(opp['npshots'] or 0)<=0: return None
    return {
      'npxg_for':float(me['npxg'] or 0.0),'npxg_against':float(opp['npxg'] or 0.0),
      'npshots_for':float(me['npshots']),'npshots_against':float(opp['npshots']),
      'npxg_per_shot':float(me['npxg'] or 0.0)/float(me['npshots']),
      'open_share':float(me['open_n'] or 0.0)/float(me['npshots']),
      'set_share':float(me['set_n'] or 0.0)/float(me['npshots']),
    }

def load_process_base(db:Path):
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row
    qs=','.join('?' for _ in BIG5)
    games=[dict(r) for r in con.execute(
      f"select id,fid,date,league,season,h_id,a_id from general_game_stats where league in ({qs}) and season between 2014 and 2023 order by date,id",BIG5)]
    ev={}
    q=f"""select e.match_id,e.h_a,
      sum(case when e.situation!='Penalty' then e.xG else 0 end) npxg,
      sum(case when e.situation!='Penalty' then 1 else 0 end) npshots,
      sum(case when e.situation='OpenPlay' then 1 else 0 end) open_n,
      sum(case when e.situation in ('SetPiece','FromCorner','DirectFreekick') then 1 else 0 end) set_n
      from game_events e join general_game_stats g on g.id=e.match_id
      where g.league in ({qs}) and g.season between 2014 and 2023
      group by e.match_id,e.h_a"""
    for r in con.execute(q,BIG5): ev[(str(r['match_id']),str(r['h_a']))]=dict(r)
    ids={int(r['fid']) for r in games}
    con.close()
    pools=defaultdict(lambda:defaultdict(list))
    for g in games:
        if int(g['season'])<=2017:
            for side in ('h','a'):
                s=_side_from_db(ev,str(g['id']),side)
                if s:
                    for k,v in s.items(): pools[g['league']][k].append(v)
    priors={l:{k:sum(v)/len(v) for k,v in d.items()} for l,d in pools.items()}
    if set(priors)!=set(BIG5): raise RuntimeError(f'process priors incomplete {set(priors)}')
    states=defaultdict(ProcessTeamState); qrel=[]; seq=0
    for g in games:
        ko=source.parse_dt(g['date'])
        while qrel and qrel[0][0]<=ko:
            _,_,updates=heapq.heappop(qrel)
            for team,stats,t in updates: states[str(team)].add(t,stats)
        updates=[]
        hs=_side_from_db(ev,str(g['id']),'h'); aa=_side_from_db(ev,str(g['id']),'a'); rel=ko+timedelta(hours=3)
        if hs: updates.append((str(g['h_id']),hs,rel))
        if aa: updates.append((str(g['a_id']),aa,rel))
        if updates: seq+=1; heapq.heappush(qrel,(rel,seq,updates))
    return priors,states,qrel,ids,len(games)

def team_id(obj:Any,label:str)->int:
    if not isinstance(obj,dict): raise RuntimeError(f'{label} team object missing')
    v=obj.get('id')
    try: n=int(v)
    except Exception as e: raise RuntimeError(f'{label} team id invalid') from e
    if n<=0: raise RuntimeError(f'{label} team id invalid')
    return n

def score_pair(row:dict[str,Any])->tuple[int,int,float,float]:
    goals=row.get('goals'); xg=row.get('xG')
    if not isinstance(goals,dict) or not isinstance(xg,dict): raise RuntimeError('league row goals/xG missing')
    try: hg=int(goals['h']); ag=int(goals['a']); hx=float(xg['h']); ax=float(xg['a'])
    except Exception as e: raise RuntimeError('league row goals/xG invalid') from e
    if hg<0 or ag<0 or hx<0 or ax<0: raise RuntimeError('league row negative labels')
    return hg,ag,hx,ax

def league_payload(league:str,season:int):
    slug=SEASON_SLUG[league]; url=f'https://understat.com/getLeagueData/{slug}/{season}'
    raw,obj,meta=source.fetch_ajax_json(url)
    return source.league_dates(obj),{'url':url,'decoded_sha256':source.sha256(raw),'transport':meta}

def make_row(league:str,season:int,r:dict[str,Any])->dict[str,Any]:
    mid=str(r.get('id') or '')
    if not mid.isdigit(): raise RuntimeError('match id invalid')
    ko=source.parse_dt(r.get('datetime'))
    h=team_id(r.get('h'),'home'); a=team_id(r.get('a'),'away')
    if h==a: raise RuntimeError('team identity collision')
    return {
      'mid':int(mid),'fixture_id':f'understat:{int(mid)}','league':league,
      'competition_id':f'understat-league:{LEAGUE_ID[league]}','season':str(season),'kickoff':ko,
      'home_team_id':f'understat-team:{h}','away_team_id':f'understat-team:{a}',
      'process_home_id':str(h),'process_away_id':str(a),
      'home_team':source.team_title(r.get('h')),'away_team':source.team_title(r.get('a')),
      'source_row':r,
    }

def fetch_bridge_and_future(existing_ids:set[int]):
    bridge=[]; future=[]; provenance={}
    for season in BRIDGE_SEASONS:
        for league in BIG5:
            dates,meta=league_payload(league,season); provenance[f'{league}|{season}']=meta
            for rawrow in dates:
                if not isinstance(rawrow,dict) or not rawrow.get('datetime'): continue
                row=make_row(league,season,rawrow)
                is_result=source.truthy(rawrow.get('isResult'))
                if is_result and row['kickoff']<CUTOFF and row['mid'] not in existing_ids:
                    hg,ag,hx,ax=score_pair(rawrow)
                    row.update({'home_goals':hg,'away_goals':ag,'home_xg':hx,'away_xg':ax})
                    bridge.append(row)
                if season==2026 and (not is_result) and row['kickoff']>CUTOFF:
                    future.append(row)
            time.sleep(0.15)
    bridge.sort(key=lambda r:(r['kickoff'],r['mid']))
    future.sort(key=lambda r:(r['kickoff'],r['mid']))
    return bridge,future,provenance

def fetch_bridge_process(bridge:list[dict[str,Any]],workers:int):
    def one(row):
        if row['kickoff']>=CUTOFF: raise RuntimeError('target match AJAX fetch forbidden in bootstrap')
        url=f"https://understat.com/getMatchData/{row['mid']}"
        raw,obj,meta=source.fetch_ajax_json(url)
        stats=aggregate_shots(source.match_shots(obj))
        time.sleep(0.12)
        return row['mid'],stats,{'decoded_sha256':source.sha256(raw),'wire_sha256':meta['wire_sha256'],'content_encoding':meta['content_encoding']}
    out={}; metas={}; done=0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs={ex.submit(one,r):r['mid'] for r in bridge}
        for fut in as_completed(futs):
            mid,stats,meta=fut.result(); out[mid]=stats; metas[mid]=meta; done+=1
            if done%250==0: print(f'bridge_shot_schema_processed_n={done}',flush=True)
    return out,metas

def replay_formal_base(db:Path,xg_identity:Path):
    fixtures,labels,_=hxg.load_universe(db,xg_identity)
    state=formal.new_candidate_state(); pending=deque()
    for batch in hxg.batches(fixtures):
        now=batch[0].kickoff
        hxg.release_ready(state,pending,labels,now,update_base=True)
        state.predict_batch(batch,include_matrix=False)
        pending.append((max(labels[f.fixture_id].release_at for f in batch),batch))
    return state,pending,labels,len(fixtures)

def groups(rows:list[dict[str,Any]]):
    out=[]; cur=[]; key=None
    for r in rows:
        if key is None or r['kickoff']==key: cur.append(r); key=r['kickoff']
        else: out.append(cur); cur=[r]; key=r['kickoff']
    if cur: out.append(cur)
    return out

def replay_bridge(formal_state,formal_pending,formal_hist_labels,proc_states,proc_queue,bridge,shot_stats):
    seq=max([x[1] for x in proc_queue],default=0)
    for rows in groups(bridge):
        now=rows[0]['kickoff']
        hxg.release_ready(formal_state,formal_pending,formal_hist_labels,now,update_base=True)
        while proc_queue and proc_queue[0][0]<=now:
            _,_,updates=heapq.heappop(proc_queue)
            for team,stats,t in updates: proc_states[str(team)].add(t,stats)
        fixtures=[hxg.FixtureRow(
          r['fixture_id'],r['competition_id'],r['season'],r['kickoff'],r['home_team_id'],r['away_team_id'],r['home_team'],r['away_team']) for r in rows]
        formal_state.predict_batch(fixtures,include_matrix=False)
        labs={}
        for f,r in zip(fixtures,rows):
            labs[f.fixture_id]=hxg.ReleasedLabel(r['home_goals'],r['away_goals'],r['home_xg'],r['away_xg'],r['kickoff']+timedelta(hours=3))
        formal_hist_labels.update(labs)
        formal_pending.append((max(x.release_at for x in labs.values()),fixtures))
        updates=[]
        for r in rows:
            st=shot_stats.get(r['mid'])
            if st:
                rel=r['kickoff']+timedelta(hours=3)
                updates.append((r['process_home_id'],st['h'],rel)); updates.append((r['process_away_id'],st['a'],rel))
        if updates: seq+=1; heapq.heappush(proc_queue,(rows[0]['kickoff']+timedelta(hours=3),seq,updates))
    hxg.release_ready(formal_state,formal_pending,formal_hist_labels,CUTOFF,update_base=True)
    while proc_queue and proc_queue[0][0]<=CUTOFF:
        _,_,updates=heapq.heappop(proc_queue)
        for team,stats,t in updates: proc_states[str(team)].add(t,stats)
    if formal_state.pending: raise RuntimeError(f'unreleased pre-cutoff formal pending at cutoff: {len(formal_state.pending)}')
    if proc_queue: raise RuntimeError(f'unreleased pre-cutoff process pending at cutoff: {len(proc_queue)}')

def dump_decayed_comp(x):
    return {'home_goals':x.home_goals,'away_goals':x.away_goals,'matches':x.matches,'last_time':iso(x.last_time)}
def dump_decayed_team(x):
    return {'goals_for':x.goals_for,'goals_against':x.goals_against,'weight':x.weight,'last_time':iso(x.last_time),'last_season':x.last_season}
def dump_residual(x):
    return {'signal_sum':x.signal_sum,'weight':x.weight,'last_time':iso(x.last_time),'last_season':x.last_season}

def serialize_formal(state):
    b=state.base
    return {
      'formal_state_digest':state.state_digest(),
      'params':state.p.__dict__,
      'base':{
        'params':b.params.__dict__,
        'global_comp':dump_decayed_comp(b.global_comp),
        'competitions':{k:dump_decayed_comp(v) for k,v in sorted(b.competitions.items())},
        'teams_local':[{'competition_id':k[0],'team_id':k[1],**dump_decayed_team(v)} for k,v in sorted(b.teams_local.items())],
        'teams_global':{k:dump_decayed_team(v) for k,v in sorted(b.teams_global.items())},
        'seen_fixtures':sorted(b.seen_fixtures),'last_update_time':iso(b.last_update_time),
      },
      'xg':{
        'venue_attack':[{'team_id':k[0],'venue':k[1],**dump_residual(v)} for k,v in sorted(state.venue_attack.items())],
        'venue_defence':[{'team_id':k[0],'venue':k[1],**dump_residual(v)} for k,v in sorted(state.venue_defence.items())],
        'pooled_attack':{k:dump_residual(v) for k,v in sorted(state.pooled_attack.items())},
        'pooled_defence':{k:dump_residual(v) for k,v in sorted(state.pooled_defence.items())},
        'seen':sorted(state.seen),'pending':[],
        'last_prediction_time':iso(state.last_prediction_time),'last_apply_time':iso(state.last_apply_time),
      }
    }

def serialize_process(priors,states):
    return {
      'half_life_days':HALF_LIFE,'prior_matches_league_shrinkage':PRIOR_MATCHES,'minimum_profile_weight':MIN_PROFILE_WEIGHT,
      'same_kickoff_release':'kickoff+3h after whole kickoff batch snapshot',
      'league_priors':priors,
      'teams':{k:{'last':iso(v.last),'weight':v.weight,'sums':{} if v.sums is None else v.sums} for k,v in sorted(states.items())}
    }

def profile_at(process,league,team_id,at=CUTOFF):
    row=process['teams'].get(str(team_id))
    if row is None: return None,0.0
    last=datetime.fromisoformat(row['last']) if row['last'] else None
    s=ProcessTeamState(last=last,weight=float(row['weight']),sums=dict(row['sums']))
    w,sums=s.snapshot(at); prior=process['league_priors'].get(league)
    if prior is None or w<MIN_PROFILE_WEIGHT: return None,w
    return {k:(sums.get(k,0.0)+PRIOR_MATCHES*prior[k])/(w+PRIOR_MATCHES) for k in prior},w

def public_future_row(r):
    return {
      'fixture_id':r['fixture_id'],'understat_match_id':r['mid'],'league':r['league'],'competition_id':r['competition_id'],
      'season':r['season'],'kickoff_utc':iso(r['kickoff']),'home_team_id':r['home_team_id'],'away_team_id':r['away_team_id'],
      'home_team':r['home_team'],'away_team':r['away_team']
    }

def run(old_db:Path,xg_identity:Path,out:Path,workers:int):
    contract=json.loads(CONTRACT_PATH.read_text()); frozen=json.loads(FROZEN_STATE_PATH.read_text())
    if contract['status']!='FROZEN_BEFORE_TARGET_ENROLLMENT' or contract['required_n']!=1603: raise RuntimeError('confirmation contract drift')
    if contract['target_cutoff_utc']!='2026-09-02T17:00:00Z': raise RuntimeError('cutoff drift')
    priors,proc_states,proc_queue,existing_ids,process_base_n=load_process_base(old_db)
    formal_state,formal_pending,formal_labels,formal_base_n=replay_formal_base(old_db,xg_identity)
    bridge,future,league_provenance=fetch_bridge_and_future(existing_ids)
    if len(future)<contract['required_n']: raise RuntimeError(f'future capacity below required_n: {len(future)}')
    shot_stats,shot_transport=fetch_bridge_process(bridge,workers)
    replay_bridge(formal_state,formal_pending,formal_labels,proc_states,proc_queue,bridge,shot_stats)
    formal_pack=serialize_formal(formal_state); process_pack=serialize_process(priors,proc_states)
    future_public=[public_future_row(r) for r in future]
    process_valid=0
    for r in future:
        hp,hw=profile_at(process_pack,r['league'],r['process_home_id'])
        ap,aw=profile_at(process_pack,r['league'],r['process_away_id'])
        process_valid+=int(hp is not None and ap is not None)
    package={
      'schema_version':'football3-v3-1-1-prospective-cutoff-state-v1',
      'status':'V3_1_1_PROSPECTIVE_CUTOFF_STATE_READY',
      'as_of_utc':CUTOFF.isoformat().replace('+00:00','Z'),
      'contract_blob_expected':'0dfbe39b7cbc97e09034871e427ff60895fffcdc',
      'frozen_candidate_state_payload_sha256':frozen['canonical_payload_sha256'],
      'formal_v2':formal_pack,'process_state':process_pack,
      'enrollment_universe_snapshot':{
        'n':len(future_public),'required_n':contract['required_n'],'margin':len(future_public)-contract['required_n'],
        'identity_sha256':sha(future_public),'fixtures':future_public,
        'process_profile_valid_n_at_cutoff':process_valid,
        'selection_rule':'chronological first 1603 eligible receipts; snapshot is discovery universe, not result-selected cohort'
      },
      'bootstrap_evidence':{
        'historical_formal_fixture_n':formal_base_n,'historical_process_fixture_n':process_base_n,
        'bridge_completed_pre_cutoff_n':len(bridge),'bridge_shot_ajax_n':len(shot_transport),
        'post_cutoff_match_ajax_fetched':0,'target_labels_read_for_scoring':False,'interim_scoring_run':False,
        'raw_ajax_payload_persisted':False,'raw_shot_rows_persisted':False,
        'league_ajax_provenance':league_provenance,
        'bridge_shot_transport_summary_sha256':sha({str(k):v for k,v in sorted(shot_transport.items())}),
      },
      'candidate_modified':False,'formal_v2_modified':False,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False
    }
    package['formal_state_sha256']=sha(formal_pack)
    package['process_state_sha256']=sha(process_pack)
    package['package_sha256']=sha({k:v for k,v in package.items() if k!='package_sha256'})
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(package,sort_keys=True,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({
      'status':package['status'],'formal_state_sha256':package['formal_state_sha256'],'process_state_sha256':package['process_state_sha256'],
      'future_n':len(future_public),'bridge_n':len(bridge),'process_profile_valid_n_at_cutoff':process_valid,'post_cutoff_match_ajax_fetched':0
    },sort_keys=True))

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--old-db',type=Path,required=True); ap.add_argument('--xg-identity',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True); ap.add_argument('--workers',type=int,default=3)
    a=ap.parse_args()
    if not 1<=a.workers<=6: raise SystemExit('workers must be 1..6')
    run(a.old_db,a.xg_identity,a.out,a.workers)
