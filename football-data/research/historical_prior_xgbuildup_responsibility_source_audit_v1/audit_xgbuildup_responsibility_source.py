from __future__ import annotations
import argparse, collections, hashlib, json, math, pathlib, sqlite3

BIG5 = ('Bundesliga','EPL','La liga','Ligue 1','Serie A')
FIELDS = ('xGBuildup','xGChain','xA','key_passes')

def sha256_file(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def lineup_profile(rows):
    out={k:collections.defaultdict(float) for k in FIELDS}
    for r in rows:
        pid=int(r['player_id'])
        for k in FIELDS:
            v=float(r[k])
            if not math.isfinite(v) or v < 0.0:
                raise RuntimeError(f'invalid {k}={v}')
            out[k][pid]+=v
    return {k:dict(v) for k,v in out.items()}

def shooter_profile(rows):
    d=collections.defaultdict(float)
    for r in rows:
        d[int(r['player_id'])]+=float(r['xG'])
    return dict(d)

def hhi_from_queue(q, key, minimum_prior):
    if len(q)<minimum_prior: return None
    d=collections.defaultdict(float)
    for p in q:
        for pid,v in p[key].items(): d[int(pid)]+=float(v)
    total=sum(d.values())
    if total<=0.0: return None
    value=sum((v/total)**2 for v in d.values())
    if not math.isfinite(value) or value<=0.0 or value>1.0+1e-12:
        raise RuntimeError(f'invalid HHI {key}={value}')
    return float(value)

def pearson(xs,ys):
    n=len(xs)
    if n<2:return None
    mx=sum(xs)/n; my=sum(ys)/n
    vx=sum((x-mx)**2 for x in xs); vy=sum((y-my)**2 for y in ys)
    if vx<=0 or vy<=0:return None
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/math.sqrt(vx*vy)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--db',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text())
    assert c['status']=='FROZEN_BEFORE_ZERO_LABEL_SOURCE_AUDIT'
    assert c['feature']['family']=='prior_xgbuildup_responsibility_concentration'
    assert c['semantic_dedup']['dedup_key']==['mechanism_definition','exact_formula','lookback','PIT_semantics','input_fields','model_action_axis']
    dbsha=sha256_file(a.db); db_sha_match=(dbsha==c['source']['database_sha256'])
    con=sqlite3.connect(str(a.db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in BIG5)
    lcols={r['name'] for r in con.execute('pragma table_info(lineup_stats)')}
    required={'match_id','player_id','team_id','xGBuildup','xGChain','xA','key_passes'}
    assert required<=lcols
    games=[dict(r) for r in con.execute(f'''select id,fid,h_id,a_id,date,league,season from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id''',BIG5)]
    target_counts={int(r['season']):int(r['n']) for r in con.execute(f'''select season,count(*) n from general_game_stats where league in ({qs}) and season between 2020 and 2022 group by season order by season''',BIG5)}
    lineup=collections.defaultdict(lambda:{'h':[],'a':[]}); lineup_n=pid_known=xgb_known=0
    for r in con.execute(f'''select l.match_id,l.h_a,l.player_id,l.team_id,l.xGBuildup,l.xGChain,l.xA,l.key_passes from lineup_stats l join general_game_stats g on g.id=l.match_id where g.league in ({qs}) and g.season between 2014 and 2022 order by l.match_id,l.player_id''',BIG5):
        lineup_n+=1
        if r['player_id'] is not None: pid_known+=1
        if r['xGBuildup'] is not None: xgb_known+=1
        if r['player_id'] is None or any(r[k] is None for k in FIELDS): continue
        side=str(r['h_a'])
        if side not in ('h','a'): raise RuntimeError(f'invalid lineup side {side}')
        lineup[int(r['match_id'])][side].append(dict(r))
    shots=collections.defaultdict(lambda:{'h':[],'a':[]})
    for r in con.execute(f'''select e.match_id,e.h_a,e.player_id,e.xG from game_events e join general_game_stats g on g.id=e.match_id where g.league in ({qs}) and g.season between 2014 and 2022 and e.situation!='Penalty' and e.player_id is not null order by e.match_id,e.id''',BIG5):
        shots[int(r['match_id'])][str(r['h_a'])].append(dict(r))
    con.close()
    if len(games)!=16332: raise RuntimeError(f'history n drift {len(games)}')
    profiles={}
    for g in games:
        mid=int(g['id'])
        profiles[mid]={}
        for side in ('h','a'):
            lp=lineup_profile(lineup[mid][side])
            lp['shooter_xG']=shooter_profile(shots[mid][side])
            profiles[mid][side]=lp
    lookback=int(c['feature']['lookback_completed_league_matches']); minimum=int(c['feature']['minimum_prior_matches'])
    states=collections.defaultdict(lambda:collections.deque(maxlen=lookback)); bydate=collections.OrderedDict()
    for g in games: bydate.setdefault(str(g['date'])[:10],[]).append(g)
    rows=[]
    for _,batch in bydate.items():
        batch=sorted(batch,key=lambda z:int(z['id']))
        for g in batch:
            if int(g['season']) not in (2020,2021,2022): continue
            hk=(str(g['league']),int(g['h_id'])); ak=(str(g['league']),int(g['a_id']))
            rec={'fixture_id':f"understat:{int(g['fid'])}",'season':int(g['season']),'available':False}
            vals={}
            for key,outkey in [('xGBuildup','xgbuildup_hhi'),('xGChain','xgchain_hhi'),('xA','xa_hhi'),('key_passes','keypasses_hhi'),('shooter_xG','shooter_xg_hhi')]:
                hv=hhi_from_queue(states[hk],key,minimum); av=hhi_from_queue(states[ak],key,minimum)
                vals[outkey]=None if hv is None or av is None else 0.5*(hv+av)
            if vals['xgbuildup_hhi'] is not None:
                rec['available']=True
                rec.update(vals)
            rows.append(rec)
        for g in batch:
            mid=int(g['id']); states[(str(g['league']),int(g['h_id']))].append(profiles[mid]['h']); states[(str(g['league']),int(g['a_id']))].append(profiles[mid]['a'])
    target_n=sum(target_counts.values())
    if len(rows)!=target_n: raise RuntimeError(f'target row drift {len(rows)}/{target_n}')
    available=[r for r in rows if r['available']]
    coverage=len(available)/len(rows)
    comparators=['xgchain_hhi','xa_hhi','keypasses_hhi','shooter_xg_hhi']
    corr={}; corr_n={}
    for k in comparators:
        rr=[r for r in available if r.get(k) is not None]
        corr[k]=pearson([float(r['xgbuildup_hhi']) for r in rr],[float(r[k]) for r in rr]); corr_n[k]=len(rr)
    max_corr=max(abs(v) for v in corr.values() if v is not None)
    vals=sorted(float(r['xgbuildup_hhi']) for r in available)
    def q(p):
        if not vals:return None
        j=(len(vals)-1)*p; lo=int(math.floor(j)); hi=int(math.ceil(j)); w=j-lo
        return vals[lo]*(1-w)+vals[hi]*w
    season_available=collections.Counter(r['season'] for r in available)
    player_rate=pid_known/lineup_n if lineup_n else 0.0; xgb_rate=xgb_known/lineup_n if lineup_n else 0.0
    g=c['gates']
    checks={
      'target_n_exact':len(rows)==int(c['population']['target_n']),
      'per_season_n_exact':all(target_counts.get(s)==int(c['population']['per_target_season_n']) for s in (2020,2021,2022)),
      'player_id_nonnull_rate':player_rate>=float(g['player_id_nonnull_rate_min']),
      'xgbuildup_nonnull_rate':xgb_rate>=float(g['xgbuildup_nonnull_rate_min']),
      'bilateral_feature_coverage':coverage>=float(g['bilateral_feature_coverage_min']),
      'max_abs_pearson_corr':max_corr<=float(g['max_abs_pearson_corr_vs_comparators_max']),
      'source_db_sha_match':db_sha_match,
    }
    passed=all(checks.values()); status=c['terminal']['pass'] if passed else c['terminal']['fail']
    out={
      'schema_version':'football3-xgbuildup-responsibility-source-audit-result-v1','status':status,'research_only':True,
      'history_n':len(games),'target_n':len(rows),'target_by_season':target_counts,'lineup_row_n':lineup_n,'player_id_nonnull_n':pid_known,'player_id_nonnull_rate':player_rate,'xgbuildup_nonnull_n':xgb_known,'xgbuildup_nonnull_rate':xgb_rate,
      'bilateral_feature_n':len(available),'bilateral_feature_coverage':coverage,'bilateral_feature_by_season':dict(sorted(season_available.items())),
      'correlations_vs_redundancy_comparators':corr,'correlation_n':corr_n,'max_abs_pearson_corr':max_corr,
      'feature_distribution':{'mean':sum(vals)/len(vals) if vals else None,'p10':q(.1),'p25':q(.25),'p50':q(.5),'p75':q(.75),'p90':q(.9)},
      'source_db_sha256':dbsha,'checks':checks,
      'outcome_labels_read':False,'home_away_goals_read':False,'model_fit':False,'candidate_probability':False,'historical_confirmation_2023_opened':False,'prospective_1335_touched':False,
      'next':'FREEZE_ONE_XGBUILDUP_RESPONSIBILITY_CANDIDATE' if passed else 'CLOSE_XGBUILDUP_RESPONSIBILITY_SOURCE_NO_RESCUE'
    }
    (a.out/'xgbuildup_responsibility_source_audit.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps(out,sort_keys=True))
if __name__=='__main__': main()
