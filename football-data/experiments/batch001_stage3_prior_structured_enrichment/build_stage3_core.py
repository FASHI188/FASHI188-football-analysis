#!/usr/bin/env python3
from __future__ import annotations
import bisect,json,sys
from collections import defaultdict
from pathlib import Path
import pandas as pd

HERE=Path(__file__).resolve().parent; OUT=HERE/'results'; DATA=HERE/'data'
S2=HERE.parent/'batch001_stage2_historical_s60_replay'; sys.path.insert(0,str(S2))
import run_stage2 as s2
r9=s2.r9
PRED=S2/'results'/'batch001_s60_predictions_locked.json'

def avg(v): return sum(v)/len(v) if v else 0.0

def build_form_index(pool):
    idx=defaultdict(list)
    for x in pool:
        h=str(x['home_team']); a=str(x['away_team']); k=x['_known']
        idx[h].append({'gf':int(x['home_goals']),'ga':int(x['away_goals']),'xgf':float(x['home_xg']),'xga':float(x['away_xg']),'known':k})
        idx[a].append({'gf':int(x['away_goals']),'ga':int(x['home_goals']),'xgf':float(x['away_xg']),'xga':float(x['home_xg']),'known':k})
    for z in idx.values(): z.sort(key=lambda q:q['known'])
    return idx

def form_features(idx,tid,cutoff):
    z=idx.get(str(tid),[]); ks=[x['known'] for x in z]; z=z[:bisect.bisect_left(ks,cutoff)]; out={}
    for n in (5,10):
      q=z[-n:]
      out[f'prior_xg_matches_{n}']=len(q)
      out[f'gf_mean_{n}']=avg([x['gf'] for x in q]); out[f'ga_mean_{n}']=avg([x['ga'] for x in q])
      out[f'xgf_mean_{n}']=avg([x['xgf'] for x in q]); out[f'xga_mean_{n}']=avg([x['xga'] for x in q])
      out[f'xg_diff_mean_{n}']=avg([x['xgf']-x['xga'] for x in q])
      out[f'goal_diff_clipped_mean_{n}']=avg([max(-3,min(3,x['gf']-x['ga'])) for x in q])
      out[f'draw_rate_{n}']=avg([1.0 if x['gf']==x['ga'] else 0.0 for x in q])
      out[f'low2_rate_{n}']=avg([1.0 if x['gf']+x['ga']<=2 else 0.0 for x in q])
      out[f'blowout_rate_{n}']=avg([1.0 if abs(x['gf']-x['ga'])>=4 else 0.0 for x in q])
    return out

def build_schedule_index(fixtures):
    idx=defaultdict(list)
    for x in fixtures.itertuples(index=False):
        t=x.date_utc; idx[str(int(x.home_team_id))].append(t); idx[str(int(x.away_team_id))].append(t)
    for z in idx.values(): z.sort()
    return idx

def schedule_features(idx,tid,cutoff):
    z=idx.get(str(tid),[]); i=bisect.bisect_left(z,cutoff); prior=z[:i]
    rest=(cutoff-prior[-1]).total_seconds()/86400.0 if prior else 999.0
    o={'rest_days':float(rest)}
    for d in (7,14,30): o[f'matches_{d}d']=int(i-bisect.bisect_left(z,cutoff-pd.Timedelta(days=d),0,i))
    return o

def run():
    if not PRED.exists(): raise RuntimeError(f'missing locked Stage2 predictions {PRED}')
    s=json.loads(PRED.read_text(encoding='utf-8'))
    if s['status']!='PREDICTIONS_LOCKED_BASELINE_ONLY' or s['rows']!=100: raise RuntimeError('Stage2 lock mismatch')
    pool=s2.load_frozen_pool(); form_idx=build_form_index(pool)
    DATA.mkdir(parents=True,exist_ok=True); fp=DATA/'fixtures_safe_stage3.parquet'; r9.download(r9.FIX_URL,fp)
    fx=pd.read_parquet(fp,columns=['id','date_utc','league_id','home_team_id','away_team_id']); fx['date_utc']=pd.to_datetime(fx.date_utc,utc=True)
    byid={str(int(x.id)):x for x in fx.itertuples(index=False)}; sched_idx=build_schedule_index(fx); rows=[]
    for p in s['predictions']:
      fid=str(p['fixture_id']); x=byid.get(fid)
      if x is None: raise RuntimeError(f'missing target safe fixture {fid}')
      cutoff=pd.to_datetime(p['effective_same_date_cutoff_utc'],utc=True); hid=str(int(x.home_team_id)); aid=str(int(x.away_team_id))
      hf=form_features(form_idx,hid,cutoff); af=form_features(form_idx,aid,cutoff); hs=schedule_features(sched_idx,hid,cutoff); ass=schedule_features(sched_idx,aid,cutoff)
      diff={}
      for k in sorted(set(hf)&set(af)):
        if isinstance(hf[k],(int,float)) and isinstance(af[k],(int,float)): diff[f'delta_{k}']=float(hf[k]-af[k])
      for k in sorted(set(hs)&set(ass)): diff[f'delta_{k}']=float(hs[k]-ass[k])
      rows.append({'batch_index':p['batch_index'],'date':p['date'],'division':p['division'],'home':p['home'],'away':p['away'],'fixture_id':fid,
        'kickoff_utc':p['kickoff_utc'],'effective_cutoff_utc':cutoff.isoformat(),'home_team_id':hid,'away_team_id':aid,
        'baseline_S60_replay':p['S60_replay'],'home_prior':{**hs,**hf},'away_prior':{**ass,**af},'home_minus_away':diff,'enrichment_status':'LOCKED_PRIOR_ONLY_NO_TARGET_LABEL'})
    fp.unlink(missing_ok=True)
    out={'schema_version':'football3-batch001-stage3-core-enrichment-v1','status':'CORE_PRIOR_FEATURES_LOCKED','rows':len(rows),'cohort_sha256':s['cohort_sha256'],
      'governance':{'target_fixture_columns_read':['id','date_utc','league_id','home_team_id','away_team_id'],'target_outcome_columns_read':False,'target_postmatch_stats_read':False,'historical_form_requires_xg_known_at_before_effective_cutoff':True,'chronologically_prior_matches_allowed':True,'odds_used':False,'market_used':False,'result_scoring_performed':False,'player_lineup_target_data_used':False},'features':rows}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'batch001_stage3_core_features_locked.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':out['status'],'rows':len(rows),'cohort_sha256':out['cohort_sha256']},indent=2))

def verify():
    s=json.loads((OUT/'batch001_stage3_core_features_locked.json').read_text(encoding='utf-8')); g=s['governance']
    assert s['status']=='CORE_PRIOR_FEATURES_LOCKED' and s['rows']==100 and len(s['features'])==100
    assert not g['target_outcome_columns_read'] and not g['target_postmatch_stats_read'] and g['historical_form_requires_xg_known_at_before_effective_cutoff']
    assert not g['odds_used'] and not g['market_used'] and not g['result_scoring_performed'] and not g['player_lineup_target_data_used']
    assert [x['batch_index'] for x in s['features']]==list(range(1,101))
    print('BATCH001_STAGE3_CORE_VERIFY_PASS')
if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: build_stage3_core.py {run|verify}')
    {'run':run,'verify':verify}[sys.argv[1]]()
