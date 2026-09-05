from __future__ import annotations
import argparse, collections, hashlib, json, math, pathlib, sqlite3

BIG5 = ('Bundesliga','EPL','La liga','Ligue 1','Serie A')
SETPIECE = {'SetPiece','FromCorner','DirectFreekick'}

def sha256_file(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def side_profile(rows):
    px=collections.defaultdict(float); shots=[]; time_xg=collections.defaultdict(float); types=collections.defaultdict(int)
    high=central=setp=lateral=0.0
    for r in rows:
        xg=float(r['xG']); X=float(r['X']); Y=float(r['Y']); pid=int(r['player_id'])
        px[pid]+=xg; shots.append(xg)
        if xg>=0.20: high+=xg
        if X>=0.83 and 0.33<=Y<=0.67: central+=xg
        if str(r['situation']) in SETPIECE: setp+=xg
        time_xg[min(max(int(r['minute'] or 0),0)//15,6)]+=xg
        types[str(r['shotType'])]+=1
        lateral+=abs(Y-0.5)
    return {'player_xg':dict(px),'shots':shots,'total_xg':sum(shots),'high':high,'central':central,'setp':setp,
            'time_xg':dict(time_xg),'types':dict(types),'lateral_sum':lateral,'nshots':len(shots)}

def aggregate(q):
    if len(q)<3: return None
    px=collections.defaultdict(float); shots=[]; time_xg=collections.defaultdict(float); types=collections.defaultdict(int)
    total=high=central=setp=lateral=0.0; nshots=0
    for p in q:
        total+=p['total_xg']; shots.extend(p['shots']); high+=p['high']; central+=p['central']; setp+=p['setp']; lateral+=p['lateral_sum']; nshots+=p['nshots']
        for k,v in p['player_xg'].items(): px[k]+=v
        for k,v in p['time_xg'].items(): time_xg[k]+=v
        for k,v in p['types'].items(): types[k]+=v
    if total<=0 or nshots<=0: return None
    return {
      'shooter_xg_hhi':sum((v/total)**2 for v in px.values()),
      'shot_xg_hhi':sum((x/total)**2 for x in shots),
      'high_xg_share':high/total,
      'central_xg_share':central/total,
      'setpiece_xg_share':setp/total,
      'temporal_xg_hhi':sum((v/total)**2 for v in time_xg.values()),
      'shotType_count_hhi':sum((v/nshots)**2 for v in types.values()),
      'lateral_width':lateral/nshots,
      'xg_per_shot':total/nshots,
    }

def pearson(xs,ys):
    n=len(xs)
    if n<2: return None
    mx=sum(xs)/n; my=sum(ys)/n
    vx=sum((x-mx)**2 for x in xs); vy=sum((y-my)**2 for y in ys)
    if vx<=0 or vy<=0: return None
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/math.sqrt(vx*vy)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--db',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text())
    assert c['status']=='FROZEN_BEFORE_ZERO_LABEL_SOURCE_AUDIT'
    dbsha=sha256_file(a.db); expected=c['source']['database_sha256']; db_sha_match=(dbsha==expected)
    con=sqlite3.connect(str(a.db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in BIG5)
    cols={r['name'] for r in con.execute('pragma table_info(game_events)')}
    required={'match_id','h_a','xG','X','Y','minute','situation','shotType','player_id'}
    assert required<=cols
    games=[dict(r) for r in con.execute(f'''select id,fid,h_id,a_id,date,league,season from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id''',BIG5)]
    target_counts={int(r['season']):int(r['n']) for r in con.execute(f'''select season,count(*) n from general_game_stats where league in ({qs}) and season between 2020 and 2022 group by season order by season''',BIG5)}
    targets=sum(target_counts.values())
    events=collections.defaultdict(lambda:{'h':[],'a':[]}); total_events=0; known_player=0
    for r in con.execute(f'''select e.match_id,e.h_a,e.xG,e.X,e.Y,e.minute,e.situation,e.shotType,e.player_id from game_events e join general_game_stats g on g.id=e.match_id where g.league in ({qs}) and g.season between 2014 and 2022 and e.situation!='Penalty' order by e.match_id,e.id''',BIG5):
        total_events+=1
        if r['player_id'] is not None: known_player+=1
        else: continue
        events[int(r['match_id'])][str(r['h_a'])].append(dict(r))
    con.close()
    profiles={}
    for g in games:
        profiles[int(g['id'])]={'h':side_profile(events[int(g['id'])]['h']),'a':side_profile(events[int(g['id'])]['a'])}
    states=collections.defaultdict(lambda:collections.deque(maxlen=int(c['feature']['lookback_completed_league_matches'])))
    bydate=collections.OrderedDict()
    for g in games: bydate.setdefault(str(g['date'])[:10],[]).append(g)
    comparators=['shot_xg_hhi','high_xg_share','central_xg_share','setpiece_xg_share','temporal_xg_hhi','shotType_count_hhi','lateral_width','xg_per_shot']
    rows=[]
    for d,batch in bydate.items():
        for g in sorted(batch,key=lambda z:int(z['id'])):
            if int(g['season']) not in (2020,2021,2022): continue
            hp=aggregate(states[(g['league'],int(g['h_id']))]); apf=aggregate(states[(g['league'],int(g['a_id']))])
            rec={'fixture_id':f"understat:{int(g['fid'])}",'season':int(g['season']),'available':False}
            if hp is not None and apf is not None:
                rec['available']=True; rec['shooter_xg_hhi']=0.5*(hp['shooter_xg_hhi']+apf['shooter_xg_hhi'])
                for k in comparators: rec[k]=0.5*(hp[k]+apf[k])
            rows.append(rec)
        for g in sorted(batch,key=lambda z:int(z['id'])):
            states[(g['league'],int(g['h_id']))].append(profiles[int(g['id'])]['h'])
            states[(g['league'],int(g['a_id']))].append(profiles[int(g['id'])]['a'])
    assert len(rows)==targets
    available=[r for r in rows if r['available']]
    coverage=len(available)/len(rows); player_rate=known_player/total_events if total_events else 0.0
    corr={}
    for k in comparators:
        xs=[float(r['shooter_xg_hhi']) for r in available]; ys=[float(r[k]) for r in available]
        corr[k]=pearson(xs,ys)
    max_corr=max(abs(v) for v in corr.values() if v is not None)
    vals=[float(r['shooter_xg_hhi']) for r in available]; vals_sorted=sorted(vals)
    def quantile(p):
        if not vals_sorted:return None
        j=(len(vals_sorted)-1)*p; lo=int(math.floor(j)); hi=int(math.ceil(j)); w=j-lo
        return vals_sorted[lo]*(1-w)+vals_sorted[hi]*w
    checks={
      'target_n_exact':len(rows)==int(c['population']['target_n']),
      'per_season_n_exact':all(target_counts.get(s)==int(c['population']['per_target_season_n']) for s in (2020,2021,2022)),
      'player_id_nonnull_rate':player_rate>=float(c['gates']['player_id_nonnull_rate_min']),
      'bilateral_feature_coverage':coverage>=float(c['gates']['bilateral_feature_coverage_min']),
      'max_abs_pearson_corr':max_corr<=float(c['gates']['max_abs_pearson_corr_vs_comparators_max']),
      'source_db_sha_match':db_sha_match,
    }
    passed=all(checks.values()); status=c['terminal']['pass'] if passed else c['terminal']['fail']
    out={
      'schema_version':'football3-shooter-xg-responsibility-source-audit-result-v1','status':status,'research_only':True,
      'history_n':len(games),'target_n':len(rows),'target_by_season':target_counts,'nonpenalty_event_n':total_events,'player_id_nonnull_n':known_player,'player_id_nonnull_rate':player_rate,
      'bilateral_feature_n':len(available),'bilateral_feature_coverage':coverage,'correlations_vs_consumed_comparators':corr,'max_abs_pearson_corr':max_corr,
      'feature_distribution':{'mean':sum(vals)/len(vals) if vals else None,'p10':quantile(.1),'p25':quantile(.25),'p50':quantile(.5),'p75':quantile(.75),'p90':quantile(.9)},
      'source_db_sha256':dbsha,'checks':checks,
      'outcome_labels_read':False,'home_away_goals_read':False,'model_fit':False,'candidate_probability':False,'historical_confirmation_2023_opened':False,'prospective_1335_touched':False,
      'next':'FREEZE_ONE_LOW_DIMENSIONAL_CANDIDATE_ONLY_IF_SOURCE_PASS' if passed else 'CLOSE_SHOOTER_XG_RESPONSIBILITY_SOURCE_NO_RESCUE'
    }
    (a.out/'shooter_xg_responsibility_source_audit.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps(out,sort_keys=True))
if __name__=='__main__': main()
