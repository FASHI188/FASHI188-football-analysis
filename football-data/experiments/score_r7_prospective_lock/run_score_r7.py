#!/usr/bin/env python3
from __future__ import annotations
import json, os, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
R5=HERE.parent/'score_r5_s60_marginal_anchor'; R25=HERE.parent/'top1_r25_fresh_s60_confirmation'; R17=HERE.parent/'top1_r17_fresh_forward'
for p in (R5,R25,R17): sys.path.insert(0,str(p))
import run_score_r5 as r5
import run_experiment_r25 as r25
import run_experiment_r17 as r17
r9=r25.r9; r23=r25.r23

OUT=HERE/'results'; DATA=HERE/'data'
UPDATE_START='2026-07-05'; LOCK_DAY='2026-08-26'; TARGET_START='2026-08-27'; TARGET_END='2026-09-02'
LEAGUES=('EPL','La_liga','Bundesliga','Serie_A','Ligue_1','RFPL')
LEAGUE_META={
    'EPL':('England','Premier League'),
    'La_liga':('Spain','La Liga'),
    'Bundesliga':('Germany','Bundesliga'),
    'Serie_A':('Italy','Serie A'),
    'Ligue_1':('France','Ligue 1'),
    'RFPL':('Russia','Premier League'),
}
LOCKED_RHO=-0.02250000000000002
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
TOPK=5


def complete(z):
    g=z.get('goals') or {}
    return g.get('h') is not None and g.get('a') is not None


def s60_prob(model,raw):
    p=r23.pred(model,raw)
    return {'home':p['p_home'],'draw':p['p_draw'],'away':p['p_away'],'top1':('home','draw','away')[int(p['top1'])]}


def league_id_exact(leagues,key):
    country,name=LEAGUE_META[key]
    hits=[]
    for row in leagues.itertuples(index=False):
        d=row._asdict()
        row_country=str(d.get('country') or '').strip().casefold()
        row_name=str(d.get('name') or '').strip().casefold()
        if row_country==country.casefold() and row_name==name.casefold():
            hits.append(str(int(d['id'])))
    if len(set(hits))!=1:
        raise RuntimeError(f'league map exact mismatch {key}: {hits}')
    return hits[0]


def collect(base):
    tp=HERE/'_teams.parquet'; lp=HERE/'_leagues.parquet'
    r9.download(f'{HF}/teams.parquet?download=true',tp); r9.download(f'{HF}/leagues.parquet?download=true',lp)
    teams=__import__('pandas').read_parquet(tp); leagues=__import__('pandas').read_parquet(lp)
    tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)
    active={x['home_team'] for x in base}|{x['away_team'] for x in base}; fallback=r17.team_index(teams,active)
    past=[]; future=[]; audit=[]; source_counts={}; compmap={}
    for lg in LEAGUES:
        cid=league_id_exact(leagues,lg); compmap[lg]=cid
        allowed={x['home_team'] for x in base if x['competition_id']==cid}|{x['away_team'] for x in base if x['competition_id']==cid}
        primary=r17.team_index(teams,allowed); rows=r17.ajax(lg); source_counts[lg]=len(rows)
        for z in rows:
            dt=str(z.get('datetime') or '')[:10]
            if not (UPDATE_START <= dt <= TARGET_END): continue
            ht=str((z.get('h') or {}).get('title') or ''); at=str((z.get('a') or {}).get('title') or '')
            hid,hc=r17.resolve_team(ht,primary,fallback); aid,ac=r17.resolve_team(at,primary,fallback)
            kind='past' if dt<=LOCK_DAY and complete(z) else 'future' if TARGET_START<=dt<=TARGET_END and not complete(z) else 'ignored'
            audit.append({'league':lg,'date':dt,'kind':kind,'understat_id':str(z.get('id')),'home':ht,'home_id':hid,'home_candidates':hc,'away':at,'away_id':aid,'away_candidates':ac})
            if hid is None or aid is None: continue
            if kind=='past':
                g=z.get('goals') or {}; past.append({'date':dt,'game_id':f'UNDERSTAT_R7_PAST_{lg}_{z.get("id")}','competition_id':cid,'home_team':hid,'away_team':aid,'home_goals':int(g['h']),'away_goals':int(g['a']),'home_xg':0.0,'away_xg':0.0,'league':lg})
            elif kind=='future':
                future.append({'date':dt,'game_id':f'UNDERSTAT_R7_FUTURE_{lg}_{z.get("id")}','understat_id':str(z.get('id')),'competition_id':cid,'home_team':hid,'away_team':aid,'home_name':ht,'away_name':at,'league':lg,'datetime':str(z.get('datetime') or '')})
    past.sort(key=lambda x:(x['date'],x['game_id'])); future.sort(key=lambda x:(x['date'],x['game_id']))
    pa=[x for x in audit if x['kind']=='past']; fa=[x for x in audit if x['kind']=='future']
    pcov=len(past)/len(pa) if pa else 0.; fcov=len(future)/len(fa) if fa else 0.
    if len(future)<20 or pcov<.90 or fcov<.90: raise RuntimeError(f'R7 cohort gate failed future={len(future)} past_cov={pcov:.3f} future_cov={fcov:.3f}')
    return past,future,audit,source_counts,compmap,pcov,fcov


def main():
    # Hard-lock score method before any future outcomes exist.
    r5s=json.loads((R5/'results'/'summary_score_r5.json').read_text(encoding='utf-8'))
    if r5s['status']!='COMPLETE' or abs(float(r5s['parameters']['dc_rho'])-LOCKED_RHO)>1e-12: raise RuntimeError('R5 lock mismatch')
    base,_,ss,_,sm,lock=r25.lock_models()
    if lock!=(2064,1877,2071,1889): raise RuntimeError(f'S60 lock mismatch {lock}')
    past,future,audit,source_counts,compmap,pcov,fcov=collect(base)

    # Update all mapped completed league matches through lock day. Same-day fixtures are predicted before any same-day result update.
    by=defaultdict(list)
    for x in past: by[x['date']].append(x)
    for d in sorted(by):
        pending=[]
        for x in sorted(by[d],key=lambda z:z['game_id']): pending.append((x,ss.pred(x)))
        for x,raw in pending: r17.goal_only_base_update(ss,x,raw)

    predictions=[]
    for x in future:
        target={'date':x['date'],'game_id':x['game_id'],'competition_id':x['competition_id'],'home_team':x['home_team'],'away_team':x['away_team'],'home_goals':0,'away_goals':0,'home_xg':0.0,'away_xg':0.0}
        raw=ss.pred(target); p=s60_prob(sm,raw); pdec={'p_home':p['home'],'p_draw':p['draw'],'p_away':p['away']}
        ranked,_=r5.ranked({'raw':raw},pdec,LOCKED_RHO)
        tops=[{'rank':i+1,'score':f'{h}-{a}','probability':float(prob)} for i,(prob,h,a) in enumerate(ranked[:TOPK])]
        predictions.append({'date':x['date'],'datetime':x['datetime'],'league':x['league'],'understat_id':x['understat_id'],'game_id':x['game_id'],'home_team_id':x['home_team'],'away_team_id':x['away_team'],'home_name':x['home_name'],'away_name':x['away_name'],'S60_1x2':p,'R5_score_top5':tops,'R5_score_top1':tops[0]['score'],'raw_mu_home':float(raw['mu_home']),'raw_mu_away':float(raw['mu_away'])})

    counts=Counter(x['league'] for x in predictions)
    now=datetime.now(timezone.utc).isoformat()
    out={'schema_version':'football3-score-r7-prospective-lock','status':'LOCKED','classification':'TRUE_PROSPECTIVE_PREDICTION_LOCK_NO_OUTCOMES','formal_weight':0,
      'lock':{'created_at_utc':now,'github_sha':os.environ.get('GITHUB_SHA','UNKNOWN'),'lock_day':LOCK_DAY,'target_start':TARGET_START,'target_end':TARGET_END,'future_rows':len(predictions)},
      'governance':{'R5_locked_before_future_evaluation':True,'R5_dc_rho':LOCKED_RHO,'S60_1x2_frozen_unchanged':True,'future_result_fields_read':False,'future_live_data_used':False,'completed_updates_end_no_later_than_lock_day':True,'same_date_results_withheld':True,'fresh_xg_used':False,'odds_used':False,'market_prices_used':False,'lineups_used_as_features':False,'injuries_used_as_features':False,'manual_probability_adjustment':False,'rerun_after_target_results_forbidden':True,'evaluation_must_use_this_committed_prediction_snapshot':True},
      'S60_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},
      'source':{'understat_season':2026,'leagues':list(LEAGUES),'source_row_counts':source_counts,'competition_map':compmap,'past_mapped_rows':len(past),'past_mapping_coverage':pcov,'future_mapping_coverage':fcov,'prediction_counts_by_league':dict(counts)},
      'predictions':predictions}
    DATA.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
    (DATA/'mapping_audit_score_r7.json').write_text(json.dumps({'audit':audit,'past_mapping_coverage':pcov,'future_mapping_coverage':fcov},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    (OUT/'prospective_predictions_score_r7.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':'LOCKED','future_rows':len(predictions),'counts':dict(counts),'created_at_utc':now},indent=2))


def verify():
    s=json.loads((OUT/'prospective_predictions_score_r7.json').read_text(encoding='utf-8')); g=s['governance']; q=s['S60_lock_reproduction']
    assert s['status']=='LOCKED' and s['classification']=='TRUE_PROSPECTIVE_PREDICTION_LOCK_NO_OUTCOMES' and s['lock']['future_rows']>=20
    assert g['R5_locked_before_future_evaluation'] and g['S60_1x2_frozen_unchanged'] and not g['future_result_fields_read'] and not g['future_live_data_used']
    assert g['completed_updates_end_no_later_than_lock_day'] and g['same_date_results_withheld'] and not g['fresh_xg_used'] and not g['odds_used'] and not g['market_prices_used'] and not g['manual_probability_adjustment']
    assert g['rerun_after_target_results_forbidden'] and g['evaluation_must_use_this_committed_prediction_snapshot']
    assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889)
    for x in s['predictions']:
        assert TARGET_START<=x['date']<=TARGET_END and len(x['R5_score_top5'])==5
    print('SCORE_R7_VERIFY_PASS')

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: run_score_r7.py {run|verify}')
    {'run':main,'verify':verify}[sys.argv[1]]()
