from __future__ import annotations

import argparse, collections, datetime as dt, hashlib, json, pathlib, re, sqlite3, unicodedata
import pyarrow.parquet as pq

DROP_TOKENS={"fc","afc","cf","calcio","club"}

def canon(x):
    if x is None: return None
    s=unicodedata.normalize('NFKD',str(x)).encode('ascii','ignore').decode('ascii').lower()
    toks=re.findall(r'[a-z0-9]+',s)
    toks=[t for t in toks if t not in DROP_TOKENS]
    return ' '.join(toks)

def sha256(p:pathlib.Path):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def date_only(x):
    if x is None: return None
    if isinstance(x,(dt.datetime,dt.date)): return x.date().isoformat() if isinstance(x,dt.datetime) else x.isoformat()
    return str(x)[:10]

def load_target(db:pathlib.Path,c):
    leagues=c['target']['leagues']; seasons=c['target']['seasons']
    q='select fid,date,league,season,team_h,team_a from general_game_stats where season in (%s) and league in (%s) order by date,fid' % (','.join('?'*len(seasons)),','.join('?'*len(leagues)))
    con=sqlite3.connect(db)
    rows=[]
    for fid,date,league,season,h,a in con.execute(q,[*seasons,*leagues]):
        rows.append({'fid':int(fid),'date':date_only(date),'league':league,'season':int(season),'team_h':h,'team_a':a,'h':canon(h),'a':canon(a)})
    con.close()
    if len(rows)!=int(c['target']['expected_n']): raise RuntimeError(f'target_n drift: {len(rows)}')
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--db',type=pathlib.Path,required=True)
    ap.add_argument('--fixtures',type=pathlib.Path,required=True); ap.add_argument('--match-stats',type=pathlib.Path,required=True); ap.add_argument('--teams',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c=json.loads(a.contract.read_text()); assert c['status']=='FROZEN_ZERO_LABEL_JOIN_AUDIT'
    if sha256(a.db)!=c['target']['db_sha256']: raise RuntimeError('db sha drift')
    for name,p in [('fixtures.parquet',a.fixtures),('match_stats.parquet',a.match_stats),('teams.parquet',a.teams)]:
        if sha256(p)!=c['external_source']['files'][name]: raise RuntimeError(f'{name} sha drift')
    target=load_target(a.db,c)

    teams=pq.read_table(a.teams,columns=['id','name','fd_name']).to_pylist()
    tmap={int(r['id']):tuple(dict.fromkeys(x for x in (canon(r.get('name')),canon(r.get('fd_name'))) if x)) for r in teams}
    stat=pq.read_table(a.match_stats,columns=['fixture_id','known_at']).to_pylist()
    stat_ids={int(r['fixture_id']) for r in stat}
    known_at_nonnull={int(r['fixture_id']) for r in stat if r.get('known_at') is not None}

    fixtures=pq.read_table(a.fixtures,columns=['id','date_utc','home_team_id','away_team_id']).to_pylist()
    idx=collections.defaultdict(list)
    source_missing_team=0
    for r in fixtures:
        fid=int(r['id'])
        if fid not in stat_ids: continue
        hn=tmap.get(int(r['home_team_id']) if r.get('home_team_id') is not None else -1,())
        an=tmap.get(int(r['away_team_id']) if r.get('away_team_id') is not None else -1,())
        if not hn or not an:
            source_missing_team+=1; continue
        d=date_only(r['date_utc'])
        for h in hn:
            for aa in an:
                idx[(d,h,aa)].append(fid)

    joined=[]; unmatched=[]; ambiguous=[]
    for r in target:
        ids=sorted(set(idx.get((r['date'],r['h'],r['a']),[])))
        if len(ids)==1:
            joined.append({'target_fid':r['fid'],'source_fixture_id':ids[0],'date':r['date'],'season':r['season'],'league':r['league'],'team_h':r['team_h'],'team_a':r['team_a'],'source_known_at_present':ids[0] in known_at_nonnull})
        elif len(ids)>1:
            ambiguous.append({'target_fid':r['fid'],'date':r['date'],'team_h':r['team_h'],'team_a':r['team_a'],'source_fixture_ids':ids})
        else:
            unmatched.append({'target_fid':r['fid'],'date':r['date'],'season':r['season'],'league':r['league'],'team_h':r['team_h'],'team_a':r['team_a'],'canon_h':r['h'],'canon_a':r['a']})

    n=len(target); coverage=len(joined)/n; amb=len(ambiguous)/n
    by_season={}
    by_league={}
    for key,field in [('season','season'),('league','league')]:
        vals=sorted({r[field] for r in target},key=str)
        out={}
        for v in vals:
            den=sum(r[field]==v for r in target); hit=sum(r[field]==v for r in joined); am=sum(r[field]==v for r in ambiguous)
            out[str(v)]={'target_n':den,'joined_n':hit,'coverage':hit/den if den else None,'ambiguous_n':am}
        if key=='season': by_season=out
        else: by_league=out
    gates={
        'target_n_exact':n==int(c['gates']['target_n_must_equal']),
        'minimum_unique_join_coverage':coverage>=float(c['gates']['minimum_unique_join_coverage']),
        'maximum_ambiguous_target_rate':amb<=float(c['gates']['maximum_ambiguous_target_rate']),
        'source_raw_sha_match':True,
    }
    passed=all(gates.values())
    out={
        'schema_version':'football3-discipline-frozen-join-audit-result-v1',
        'status':'DISCIPLINE_FROZEN_JOIN_AUDIT_PASS' if passed else 'DISCIPLINE_FROZEN_JOIN_AUDIT_FAIL',
        'research_only':True,'model_fits':0,'candidate_probabilities':0,'outcome_labels_read':False,'external_discipline_values_read':False,
        'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False,
        'target_n':n,'unique_joined_n':len(joined),'coverage':coverage,'ambiguous_n':len(ambiguous),'ambiguous_rate':amb,'unmatched_n':len(unmatched),
        'joined_known_at_nonnull_n':sum(x['source_known_at_present'] for x in joined),'source_match_stats_n':len(stat_ids),'source_fixture_rows_n':len(fixtures),'source_missing_team_identity_n':source_missing_team,
        'by_season':by_season,'by_league':by_league,'gates':gates,
        'source_sha256':c['external_source']['files'],'target_db_sha256':c['target']['db_sha256'],
        'unmatched_identity_sample':unmatched[:200],'ambiguous_sample':ambiguous[:50],
        'next_step':'DEDUP_OLD_DISCIPLINE_FAMILIES_THEN_PREREGISTER_STRICT_PRIOR_DISCIPLINE_RESIDUAL_SCREEN' if passed else 'STOP_OR_ZERO_LABEL_ALIAS_AUDIT_ONLY_NO_MODEL',
    }
    (a.out/'discipline_frozen_join_audit.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    with (a.out/'joined_identity.jsonl').open('w') as f:
        for r in joined: f.write(json.dumps(r,sort_keys=True)+'\n')
    print(json.dumps({k:out[k] for k in ['status','target_n','unique_joined_n','coverage','ambiguous_n','unmatched_n','by_season','by_league','gates','next_step']},sort_keys=True))
if __name__=='__main__': main()
