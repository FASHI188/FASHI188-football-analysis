#!/usr/bin/env python3
from __future__ import annotations
import json, urllib.request
from collections import Counter
from pathlib import Path
import pandas as pd
import pyarrow.dataset as ds

HERE=Path(__file__).resolve().parent; DATA=HERE/'data'; OUT=HERE/'results'
S2=HERE.parent/'batch001_stage2_historical_s60_replay'
PRED=S2/'results'/'batch001_s60_predictions_locked.json'
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
GRACE_HOURS=6; PRIOR_SCAN=20; FEATURE_MATCHES=5

def dl(name):
    DATA.mkdir(parents=True,exist_ok=True); p=DATA/f'{name}.parquet'
    req=urllib.request.Request(f'{HF}/{name}.parquet?download=true',headers={'User-Agent':'football3-stage3b/1.0'})
    with urllib.request.urlopen(req,timeout=300) as r,p.open('wb') as f:
        while True:
            b=r.read(1<<20)
            if not b: break
            f.write(b)
    return p

def mode_share(xs):
    xs=[str(x) for x in xs if x is not None and str(x) not in {'','nan','None'}]
    if not xs:return 0.0
    return max(Counter(xs).values())/len(xs)

def switches(xs):
    xs=[str(x) for x in xs if x is not None and str(x) not in {'','nan','None'}]
    return sum(a!=b for a,b in zip(xs,xs[1:]))

def jacc(a,b):
    a=set(a);b=set(b)
    return len(a&b)/len(a|b) if a|b else 0.0

def summarize(prior_ids,tid,lineups,players):
    # Keep chronological order supplied by prior_ids and use last FEATURE_MATCHES with available rows.
    formations=[]; coaches=[]; starter_sets=[]; all_rows=[]; captains=[]; used=0
    for fid in prior_ids:
        l=lineups[(lineups.fixture_id==int(fid))&(lineups.team_id==int(tid))]
        p=players[(players.fixture_id==int(fid))&(players.team_id==int(tid))]
        if len(l):
            formations.append(l.iloc[0].formation); coaches.append(l.iloc[0].coach_name)
        if len(p):
            used+=1; q=p.copy(); all_rows.append(q)
            starter_sets.append(set(q.loc[q.is_starter==True,'player_id'].dropna().astype(int).tolist()))
            c=q.loc[q.captain==True,'player_id'].dropna().astype(int).tolist(); captains.extend(c[:1])
    formations=formations[-FEATURE_MATCHES:]; coaches=coaches[-FEATURE_MATCHES:]; starter_sets=starter_sets[-FEATURE_MATCHES:]; all_rows=all_rows[-FEATURE_MATCHES:]
    if all_rows:
        z=pd.concat(all_rows,ignore_index=True); zmin=z[z.minutes.fillna(0)>0]
        starter=z[z.is_starter==True]
        sc=Counter(starter.player_id.dropna().astype(int).tolist()); mc=zmin.groupby('player_id').minutes.sum().sort_values(ascending=False)
        denom=11*len(all_rows)
        starter_core=sum(v for _,v in sc.most_common(11))/denom if denom else 0.0
        minute_share=float(mc.head(11).sum()/mc.sum()) if len(mc) and mc.sum()>0 else 0.0
        ratings=z.rating.dropna(); sr=starter.rating.dropna()
        avg_rating=float(ratings.mean()) if len(ratings) else 0.0; avg_starter_rating=float(sr.mean()) if len(sr) else 0.0
        distinct=int(zmin.player_id.nunique())
    else:
        starter_core=minute_share=avg_rating=avg_starter_rating=0.0;distinct=0
    last2=jacc(starter_sets[-2],starter_sets[-1]) if len(starter_sets)>=2 else 0.0
    return {
      'history_fixtures_with_player_rows':len(all_rows),'history_formations_available':len(formations),
      'formation_mode_share_5':mode_share(formations),'formation_switches_5':switches(formations),
      'coach_mode_share_5':mode_share(coaches),'coach_switches_5':switches(coaches),
      'starter_core_share_5':float(starter_core),'starter_jaccard_last2':float(last2),'top11_minutes_share_5':float(minute_share),
      'distinct_players_used_5':int(distinct),'avg_player_rating_5':avg_rating,'avg_starter_rating_5':avg_starter_rating,
      'captain_mode_share_5':mode_share(captains[-FEATURE_MATCHES:])
    }

def run():
    s=json.loads(PRED.read_text(encoding='utf-8'))
    if s['status']!='PREDICTIONS_LOCKED_BASELINE_ONLY' or s['rows']!=100: raise RuntimeError('Stage2 lock mismatch')
    fp=dl('fixtures'); fx=pd.read_parquet(fp,columns=['id','date_utc','home_team_id','away_team_id']); fx['date_utc']=pd.to_datetime(fx.date_utc,utc=True)
    byid={str(int(x.id)):x for x in fx.itertuples(index=False)}; target_ids={str(x['fixture_id']) for x in s['predictions']}
    per={}; union=set()
    for p in s['predictions']:
        x=byid[str(p['fixture_id'])]; cutoff=pd.to_datetime(p['effective_same_date_cutoff_utc'],utc=True)-pd.Timedelta(hours=GRACE_HOURS)
        d={}
        for side,tid in [('home',int(x.home_team_id)),('away',int(x.away_team_id))]:
            q=fx[((fx.home_team_id==tid)|(fx.away_team_id==tid))&(fx.date_utc<cutoff)].sort_values('date_utc').tail(PRIOR_SCAN)
            ids=[str(int(v)) for v in q.id.tolist()]
            if str(p['fixture_id']) in ids: raise RuntimeError(f'target self fixture leaked {p["fixture_id"]}')
            d[side]={'team_id':str(tid),'prior_ids':ids}; union.update(ids)
        per[p['batch_index']]=d
    lp=dl('fixture_lineups'); pp=dl('fixture_players')
    filt=ds.field('fixture_id').isin([int(x) for x in union])
    lineups=ds.dataset(lp,format='parquet').to_table(columns=['fixture_id','team_id','coach_name','formation'],filter=filt).to_pandas()
    players=ds.dataset(pp,format='parquet').to_table(columns=['fixture_id','team_id','player_id','is_starter','captain','minutes','rating'],filter=filt).to_pandas()
    rows=[]
    for p in s['predictions']:
        d=per[p['batch_index']]; h=summarize(d['home']['prior_ids'],d['home']['team_id'],lineups,players); a=summarize(d['away']['prior_ids'],d['away']['team_id'],lineups,players)
        delta={k:float(h[k]-a[k]) for k in h if isinstance(h[k],(int,float)) and k in a}
        rows.append({'batch_index':p['batch_index'],'date':p['date'],'division':p['division'],'home':p['home'],'away':p['away'],'fixture_id':str(p['fixture_id']),
          'effective_cutoff_utc':p['effective_same_date_cutoff_utc'],'home_team_id':d['home']['team_id'],'away_team_id':d['away']['team_id'],
          'home_history':h,'away_history':a,'home_minus_away':delta,'status':'LOCKED_HISTORICAL_LINEUP_PLAYER_ONLY'})
    for q in (fp,lp,pp): q.unlink(missing_ok=True)
    out={'schema_version':'football3-batch001-stage3b-lineup-history-v1','status':'HISTORICAL_LINEUP_PLAYER_FEATURES_LOCKED','rows':len(rows),'cohort_sha256':s['cohort_sha256'],
      'governance':{'target_fixture_lineup_rows_used':False,'target_fixture_player_rows_used':False,'target_outcomes_used':False,'prior_fixture_must_start_at_least_hours_before_cutoff':GRACE_HOURS,'chronologically_prior_cohort_matches_allowed':True,'odds_used':False,'market_used':False,'result_scoring_performed':False,'lineup_player_tables_have_known_at':False,'therefore_only_prior_completed_fixture_proxy_allowed':True},
      'coverage':{'lineup_rows_loaded':len(lineups),'player_rows_loaded':len(players),'prior_fixture_union':len(union),'target_fixture_ids_in_prior_union':len(target_ids & union)},'features':rows}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'batch001_stage3b_lineup_history_locked.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':out['status'],'rows':out['rows'],'coverage':out['coverage']},indent=2))

def verify():
    s=json.loads((OUT/'batch001_stage3b_lineup_history_locked.json').read_text(encoding='utf-8')); g=s['governance']
    assert s['status']=='HISTORICAL_LINEUP_PLAYER_FEATURES_LOCKED' and s['rows']==100 and len(s['features'])==100
    assert not g['target_fixture_lineup_rows_used'] and not g['target_fixture_player_rows_used'] and not g['target_outcomes_used']
    assert g['therefore_only_prior_completed_fixture_proxy_allowed'] and not g['odds_used'] and not g['market_used'] and not g['result_scoring_performed']
    assert [x['batch_index'] for x in s['features']]==list(range(1,101))
    print('BATCH001_STAGE3B_VERIFY_PASS')
if __name__=='__main__':
    import sys
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: build_stage3b.py {run|verify}')
    {'run':run,'verify':verify}[sys.argv[1]]()
