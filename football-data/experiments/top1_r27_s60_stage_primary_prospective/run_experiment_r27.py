#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd

HERE=Path(__file__).resolve().parent; OUT=HERE/'results'; DATA=HERE/'data'
R25=HERE.parent/'top1_r25_fresh_s60_confirmation'; R24=HERE.parent/'top1_r24_history_scale_curve'; R26B=HERE.parent/'top1_r26b_unseen_fd_fresh_s60'
for p in (R25,R24,R26B): sys.path.insert(0,str(p))
import run_experiment_r25 as r25
r23=r25.r23; r17=r25.r17; r9=r25.r9
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
LOCK_UTC=pd.Timestamp('2026-08-26T08:00:00Z'); HORIZON_UTC=pd.Timestamp('2026-09-03T00:00:00Z'); POST_BASE_START=pd.Timestamp('2026-07-05T00:00:00Z')
LEAGUES=('EPL','La_liga','Bundesliga','Serie_A','Ligue_1','RFPL')
CODE={'EPL':'E0','La_liga':'SP1','Bundesliga':'D1','Serie_A':'I1','Ligue_1':'F1'}

def weighted(metric,parts):
    n=sum(x['count'] for x in parts); return sum(x['count']*x[metric] for x in parts)/n

def promotion_audit():
    r24s=json.loads((R24/'results'/'summary_r24.json').read_text(encoding='utf-8'))
    r25s=json.loads((R25/'results'/'summary_r25.json').read_text(encoding='utf-8'))
    r26s=json.loads((R26B/'results'/'summary_r26b.json').read_text(encoding='utf-8'))
    s20v=r24s['scales']['S20']['validation']; s60v=r24s['scales']['S60']['validation']; s20t=r24s['scales']['S20']['test']; s60t=r24s['scales']['S60']['test']
    def all_scores(a,b): return all(a[k]<b[k] for k in ('logloss','brier','rps'))
    def fresh_ok(s): return s['S60']['hits']>s['B20']['hits'] and all_scores(s['S60'],s['B20'])
    parts_b=[r25s['B20'],r26s['B20']];parts_s=[r25s['S60'],r26s['S60']]
    n=sum(x['count'] for x in parts_b); bh=sum(x['hits'] for x in parts_b); sh=sum(x['hits'] for x in parts_s)
    aggregate={'count':n,'B20_hits':bh,'S60_hits':sh,'B20_top1_accuracy':bh/n,'S60_top1_accuracy':sh/n,'gain_hits':sh-bh,'gain_top1_pp':(sh-bh)/n*100,'B20_logloss':weighted('logloss',parts_b),'S60_logloss':weighted('logloss',parts_s),'B20_brier':weighted('brier',parts_b),'S60_brier':weighted('brier',parts_s),'B20_rps':weighted('rps',parts_b),'S60_rps':weighted('rps',parts_s)}
    draws=r25s['B20']['actuals']['draw']+r26s['B20']['actuals']['draw']; drawp=r25s['S60']['top1_picks']['draw']+r26s['S60']['top1_picks']['draw']
    criteria={'R24_validation_top1_positive':s60v['hits']>s20v['hits'],'R24_validation_all_proper_scores_better':all_scores(s60v,s20v),'R24_test_top1_positive':s60t['hits']>s20t['hits'],'R24_test_all_proper_scores_better':all_scores(s60t,s20t),'R25_disjoint_fresh_top1_and_scores_positive':fresh_ok(r25s),'R26b_disjoint_fresh_top1_and_scores_positive':fresh_ok(r26s),'direct_S60_fresh_rows_at_least_100':n>=100,'direct_S60_fresh_net_hits_at_least_5':(sh-bh)>=5,'odds_market_free':not r25s['governance']['odds_used'] and not r25s['governance']['market_prices_used'] and not r26s['governance']['odds_used'] and not r26s['governance']['market_prices_used']}
    promoted=all(criteria.values())
    return {'schema_version':'football3-top1-r27-s60-stage-primary-prospective','status':'STAGE_PRIMARY_PROMOTED' if promoted else 'STAGE_PRIMARY_NOT_PROMOTED','classification':'ENGINEERING_STAGE_PRIMARY_AUDIT','formal_weight':0,'stage_primary_model':'S60 = 60k strict-prior state history + 24123 classifier training rows' if promoted else 'R9b K1 remains stage primary','previous_stage_primary':'R9b K1','stage_primary_promoted':promoted,'formal_scientific_promotion':False,'formal_scientific_claim':'NONE; prospective confirmation still required','selection_contract':'S60 selected on R24 validation only; R24 test and all fresh cohorts excluded from selection','criteria':criteria,'development':{'R24_validation':{'B20':s20v,'S60':s60v},'R24_test':{'B20':s20t,'S60':s60t}},'direct_disjoint_fresh':{'R25':{'B20':r25s['B20'],'S60':r25s['S60']},'R26b':{'B20':r26s['B20'],'S60':r26s['S60']},'aggregate':aggregate},'known_limitation':{'actual_draws_in_direct_S60_fresh':draws,'S60_draw_top1_picks':drawp,'draw_top1_unresolved':draws>0 and drawp==0},'governance':{'strict_prior':True,'same_date_results_withheld_in_evidence_runs':True,'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False,'no_new_hyperparameter_search_in_R27':True}}

def comp_ids(leagues):
    out={lg:r23.comp_id(leagues,CODE[lg])[0] for lg in CODE}; out['RFPL']=r17.league_id(leagues,'RFPL'); return out

def prospective_lock():
    now=pd.Timestamp(datetime.now(timezone.utc))
    if now>=LOCK_UTC: raise RuntimeError(f'prospective lock missed: now={now.isoformat()} cutoff={LOCK_UTC.isoformat()}')
    base,bs,ss,bm,sm,lock=r25.lock_models()
    tp=HERE/'_teams.parquet';lp=HERE/'_leagues.parquet';r9.download(f'{HF}/teams.parquet?download=true',tp);r9.download(f'{HF}/leagues.parquet?download=true',lp)
    teams=pd.read_parquet(tp);leagues=pd.read_parquet(lp);cids=comp_ids(leagues);fallback=r17.team_index(teams,set());tp.unlink(missing_ok=True);lp.unlink(missing_ok=True)
    past=[];future=[];mapping=[];candidate_future=0
    for lg in LEAGUES:
        cid=cids[lg]; compteams={x['home_team'] for x in base if x['competition_id']==cid}|{x['away_team'] for x in base if x['competition_id']==cid}; primary=r17.team_index(teams,compteams) if not teams.empty else []
        for z in r17.ajax(lg):
            dt=pd.to_datetime(z.get('datetime'),utc=True,errors='coerce')
            if pd.isna(dt) or dt<POST_BASE_START or dt>=HORIZON_UTC: continue
            goals=z.get('goals') or {}; ht=str((z.get('h') or {}).get('title') or '');at=str((z.get('a') or {}).get('title') or '')
            hid,hc=r17.resolve_team(ht,primary,fallback);aid,ac=r17.resolve_team(at,primary,fallback)
            if dt>=LOCK_UTC and goals.get('h') is None and goals.get('a') is None: candidate_future+=1
            mapping.append({'league':lg,'kickoff_utc':dt.isoformat(),'home':ht,'home_id':hid,'home_candidates':hc,'away':at,'away_id':aid,'away_candidates':ac})
            if hid is None or aid is None: continue
            row={'date':dt.date().isoformat(),'game_id':f'UNDERSTAT_R27_{lg}_{z.get("id")}','competition_id':cid,'home_team':hid,'away_team':aid,'home_goals':0,'away_goals':0,'home_xg':0.0,'away_xg':0.0,'league':lg,'kickoff_utc':dt.isoformat(),'home_name':ht,'away_name':at}
            if dt<LOCK_UTC and goals.get('h') is not None and goals.get('a') is not None:
                row['home_goals']=int(goals['h']);row['away_goals']=int(goals['a']);past.append(row)
            elif dt>=LOCK_UTC and goals.get('h') is None and goals.get('a') is None: future.append(row)
    past.sort(key=lambda x:(x['kickoff_utc'],x['game_id'])); by=defaultdict(list)
    for x in past:by[x['kickoff_utc']].append(x)
    for ts in sorted(by):
        pending=[]
        for x in sorted(by[ts],key=lambda z:z['game_id']): rb=bs.pred(x);rs=ss.pred(x);pending.append((x,rb,rs))
        for x,rb,rs in pending:r17.goal_only_base_update(bs,x,rb);r17.goal_only_base_update(ss,x,rs)
    rows=[]
    for x in sorted(future,key=lambda z:(z['kickoff_utc'],z['game_id'])):
        rb=bs.pred(x);rs=ss.pred(x);pb=r23.pred(bm,rb);ps=r23.pred(sm,rs);rows.append({'game_id':x['game_id'],'league':x['league'],'kickoff_utc':x['kickoff_utc'],'home_team_id':x['home_team'],'away_team_id':x['away_team'],'home':x['home_name'],'away':x['away_name'],'B20':pb,'S60':ps})
    coverage=len(rows)/candidate_future if candidate_future else 0; active=len({x['league'] for x in rows})
    if len(rows)<20 or coverage<.90 or active<4: raise RuntimeError(f'prospective cohort gate failed rows={len(rows)} candidates={candidate_future} coverage={coverage:.3f} leagues={active}')
    payload={'schema_version':'football3-r27-prospective-lock','status':'LOCKED_BEFORE_KICKOFF','generated_at_utc':now.isoformat(),'minimum_kickoff_utc':LOCK_UTC.isoformat(),'horizon_utc':HORIZON_UTC.isoformat(),'candidate_model':'S60 stage-primary candidate','baseline':'B20 / R9b K1','rules_committed_before_schedule_retrieval':True,'results_known_for_future_rows':False,'past_post_base_results_used_only_if_kickoff_before_lock':True,'fresh_xg_used':False,'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False,'candidate_future_rows':candidate_future,'mapped_rows':len(rows),'mapped_coverage':coverage,'active_leagues':active,'rows':rows,'mapping_audit':mapping}
    DATA.mkdir(parents=True,exist_ok=True);p=DATA/'prospective_predictions_r27.json';p.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');return {'generated_at_utc':payload['generated_at_utc'],'minimum_kickoff_utc':payload['minimum_kickoff_utc'],'horizon_utc':payload['horizon_utc'],'rows':len(rows),'coverage':coverage,'active_leagues':active,'S60_top1_picks':{'home':sum(x['S60']['top1']==0 for x in rows),'draw':sum(x['S60']['top1']==1 for x in rows),'away':sum(x['S60']['top1']==2 for x in rows)},'file_sha256':r9.fsha(p)}

def run():
    s=promotion_audit();s['prospective_lock']=prospective_lock();OUT.mkdir(parents=True,exist_ok=True);(OUT/'summary_r27.json').write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(s,indent=2))

def verify():
    s=json.loads((OUT/'summary_r27.json').read_text(encoding='utf-8'));p=json.loads((DATA/'prospective_predictions_r27.json').read_text(encoding='utf-8'));assert s['stage_primary_promoted'] and not s['formal_scientific_promotion'];assert all(s['criteria'].values());assert s['direct_disjoint_fresh']['aggregate']['gain_hits']>=5;assert s['known_limitation']['draw_top1_unresolved'];assert p['status']=='LOCKED_BEFORE_KICKOFF' and p['generated_at_utc']<p['minimum_kickoff_utc'];assert p['mapped_rows']>=20 and p['mapped_coverage']>=.90 and p['active_leagues']>=4;assert not p['fresh_xg_used'] and not p['odds_used'] and not p['market_prices_used'];print('R27_VERIFY_PASS')

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:raise SystemExit('usage: run_experiment_r27.py {run|verify}')
    {'run':run,'verify':verify}[sys.argv[1]]()
