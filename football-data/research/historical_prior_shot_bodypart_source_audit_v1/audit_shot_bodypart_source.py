from __future__ import annotations

import argparse, collections, json, math, pathlib, sqlite3, statistics

LEAGUES=("Bundesliga","EPL","La liga","Ligue 1","Serie A")
TARGET_SEASONS=(2020,2021,2022)
SETPIECE={"SetPiece","FromCorner","DirectFreekick"}
BINS=((0,14),(15,29),(30,44),(45,59),(60,74),(75,89),(90,120))
EPS=1e-12


def clean(x):
    if x is None: return None
    s=str(x).strip(); return s or None


def pearson(xs,ys):
    if len(xs)!=len(ys) or len(xs)<3: raise RuntimeError('invalid correlation sample')
    mx,my=statistics.fmean(xs),statistics.fmean(ys)
    dx=[x-mx for x in xs]; dy=[y-my for y in ys]
    sx=math.sqrt(sum(x*x for x in dx)); sy=math.sqrt(sum(y*y for y in dy))
    return 0.0 if sx==0 or sy==0 else sum(x*y for x,y in zip(dx,dy))/(sx*sy)


def side_profile(events):
    if not events: return None
    cats=collections.Counter(); xgs=[]; bins=[0.0]*len(BINS); sp=0.0; tx=0.0
    for e in events:
        c=clean(e['shotType'])
        if c is not None: cats[c]+=1
        x=max(0.0,float(e['xG'])); xgs.append(x); tx+=x
        if str(e['situation']) in SETPIECE: sp+=x
        m=int(e['minute'])
        for i,(lo,hi) in enumerate(BINS):
            if lo<=m<=hi: bins[i]+=x; break
    known=sum(cats.values())
    if known<=0: return None
    body=sum((n/known)**2 for n in cats.values())
    if tx>EPS:
        q=sum((x/tx)**2 for x in xgs); s=sp/tx; t=sum((x/tx)**2 for x in bins)
    else: q=s=t=0.0
    return {'body':float(body),'shotq':float(q),'sp':float(s),'time':float(t),'known_fraction':known/len(events)}


def avg(hist,min_n):
    if len(hist)<min_n: return None
    keys=('atk_body','def_body','atk_shotq','def_shotq','atk_sp','def_sp','atk_time','def_time')
    return {k:statistics.fmean(float(r[k]) for r in hist) for k in keys}


def matchup(h,a,stem):
    return abs(float(h['atk_'+stem]*a['def_'+stem]-a['atk_'+stem]*h['def_'+stem]))


def main():
    p=argparse.ArgumentParser(); p.add_argument('--contract',type=pathlib.Path,required=True); p.add_argument('--db',type=pathlib.Path,required=True); p.add_argument('--out',type=pathlib.Path,required=True); a=p.parse_args()
    c=json.loads(a.contract.read_text()); assert c['status']=='FROZEN_ZERO_MODEL_SOURCE_AUDIT'; assert c['boundaries']['outcome_label_access']==0
    lookback=int(c['feature']['lookback_completed_matches']); min_n=int(c['feature']['minimum_prior_matches']); assert (lookback,min_n)==(8,3)
    con=sqlite3.connect(f'file:{a.db}?mode=ro',uri=True); con.row_factory=sqlite3.Row
    tables={r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    if not {'game_events','general_game_stats'}<=tables: raise RuntimeError('required tables missing')
    cols={r[1] for r in con.execute('pragma table_info(game_events)')}; missing=sorted(set(c['source']['required_event_columns'])-cols)
    if missing: raise RuntimeError(f'required event columns missing: {missing}')
    qs=','.join('?' for _ in LEAGUES)
    fixtures=[dict(id=int(r['id']),fid=int(r['fid']),date=str(r['date']),day=str(r['date'])[:10],league=str(r['league']),season=int(r['season']),h_id=int(r['h_id']),a_id=int(r['a_id'])) for r in con.execute(f"select id,fid,date,league,season,h_id,a_id from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id",LEAGUES)]
    targets=[g for g in fixtures if g['season'] in TARGET_SEASONS]; counts={str(s):sum(g['season']==s for g in targets) for s in TARGET_SEASONS}
    if len(targets)!=5478: raise RuntimeError(f'target drift {len(targets)}')
    raw=collections.defaultdict(lambda:{'h':[],'a':[]}); cat_counts=collections.Counter(); n=blank=0
    for r in con.execute(f"select e.match_id,e.h_a,e.situation,e.shotType,e.xG,e.minute from game_events e join general_game_stats g on g.id=e.match_id where g.league in ({qs}) and g.season between 2014 and 2022 and e.situation!='Penalty' order by e.match_id,e.minute,e.id",LEAGUES):
        d=dict(r); side=str(d['h_a'])
        if side not in ('h','a'): raise RuntimeError(f'unexpected h_a {side!r}')
        raw[int(d['match_id'])][side].append(d); n+=1
        cc=clean(d['shotType'])
        if cc is None: blank+=1
        else: cat_counts[cc]+=1
    con.close()
    byday=collections.defaultdict(list)
    for g in fixtures: byday[g['day']].append(g)
    tids={g['id'] for g in targets}; hist=collections.defaultdict(lambda:collections.deque(maxlen=lookback)); recs=[]; valid=skipped=0
    for day in sorted(byday):
        games=sorted(byday[day],key=lambda z:z['id'])
        for g in games:
            if g['id'] not in tids: continue
            hp=avg(hist[(g['league'],g['h_id'])],min_n); ap=avg(hist[(g['league'],g['a_id'])],min_n)
            r={'fixture_id':f"understat:{g['fid']}",'season':g['season'],'league':g['league'],'day':day,'shot_bodypart_hhi_fit_abs':None,'shot_xg_hhi_fit_abs':None,'setpiece_xg_share_fit_abs':None,'temporal_xg_hhi_fit_abs':None,'target_current_event_access':False,'target_result_access':False,'target_score_access':False,'target_match_xg_access':False}
            if hp is not None and ap is not None:
                r['shot_bodypart_hhi_fit_abs']=matchup(hp,ap,'body'); r['shot_xg_hhi_fit_abs']=matchup(hp,ap,'shotq'); r['setpiece_xg_share_fit_abs']=matchup(hp,ap,'sp'); r['temporal_xg_hhi_fit_abs']=matchup(hp,ap,'time')
            recs.append(r)
        for g in games:
            h=side_profile(raw[g['id']]['h']); aa=side_profile(raw[g['id']]['a'])
            if h is None or aa is None: skipped+=2; continue
            hr={'atk_body':h['body'],'def_body':aa['body'],'atk_shotq':h['shotq'],'def_shotq':aa['shotq'],'atk_sp':h['sp'],'def_sp':aa['sp'],'atk_time':h['time'],'def_time':aa['time']}
            ar={'atk_body':aa['body'],'def_body':h['body'],'atk_shotq':aa['shotq'],'def_shotq':h['shotq'],'atk_sp':aa['sp'],'def_sp':h['sp'],'atk_time':aa['time'],'def_time':h['time']}
            hist[(g['league'],g['h_id'])].append(hr); hist[(g['league'],g['a_id'])].append(ar); valid+=2
    covered=[r for r in recs if r['shot_bodypart_hhi_fit_abs'] is not None]; body=[float(r['shot_bodypart_hhi_fit_abs']) for r in covered]
    comps={k:[float(r[k]) for r in covered] for k in ('shot_xg_hhi_fit_abs','setpiece_xg_share_fit_abs','temporal_xg_hhi_fit_abs')}
    cors={k:pearson(body,v) for k,v in comps.items()} if len(body)>=3 else {}; maxcorr=max((abs(v) for v in cors.values()),default=1.0)
    coverage=len(covered)/len(recs); nonblank=(n-blank)/n if n else 0.0; g=c['gates']
    checks={'target_n_exact':len(recs)==int(g['target_n_exact']),'per_season_n_exact':all(counts[str(s)]==int(g['per_season_n_exact']) for s in TARGET_SEASONS),'minimum_match_feature_coverage':coverage>=float(g['minimum_match_feature_coverage']),'minimum_shot_type_nonblank_fraction':nonblank>=float(g['minimum_shot_type_nonblank_fraction']),'minimum_distinct_shot_type_categories':len(cat_counts)>=int(g['minimum_distinct_shot_type_categories']),'maximum_absolute_redundancy_correlation':maxcorr<=float(g['maximum_absolute_redundancy_correlation'])}
    passed=all(checks.values()); out={'schema_version':'football3-prior-shot-bodypart-source-audit-result-v1','status':c['terminal']['pass'] if passed else c['terminal']['fail'],'source_only':True,'model_fit':0,'candidate_probability':0,'outcome_label_access':0,'target_n':len(recs),'target_counts':counts,'match_feature_covered_n':len(covered),'match_feature_coverage':coverage,'nonpenalty_event_n':n,'shot_type_blank_n':blank,'shot_type_nonblank_fraction':nonblank,'distinct_shot_type_categories':len(cat_counts),'shot_type_category_counts':dict(sorted(cat_counts.items(),key=lambda z:(-z[1],z[0]))),'valid_history_rowsides':valid,'skipped_history_rowsides':skipped,'feature_key':c['feature']['key'],'redundancy_correlations':cors,'maximum_absolute_redundancy_correlation':maxcorr,'checks':checks,'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False,'formal_weight':0,'next_step':'FREEZE_ONE_SHOT_BODYPART_HHI_CANDIDATE_NO_SEARCH' if passed else 'CLOSE_SHOT_BODYPART_AS_DUPLICATE_OR_INSUFFICIENT_SOURCE'}
    a.out.mkdir(parents=True,exist_ok=True); (a.out/'shot_bodypart_source_audit.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    with (a.out/'shot_bodypart_prematch_receipts.jsonl').open('w') as f:
        for r in recs: f.write(json.dumps(r,sort_keys=True)+'\n')
    print(json.dumps(out,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
