#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from collections import Counter,defaultdict
from pathlib import Path
import pandas as pd

HERE=Path(__file__).resolve().parent; DATA=HERE/'data'; OUT=HERE/'results'
R25=HERE.parent/'top1_r25_fresh_s60_confirmation'; sys.path.insert(0,str(R25)); import run_experiment_r25 as r25
r23=r25.r23; r18=r25.r18; r17=r25.r17; r9=r25.r9
START='2026-07-05'; END='2026-08-25'; MIN_FRESH_PER_COMP=20
FIX_SHA='7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7'
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
EXCLUDED_CODES=('E0','E1','E2','E3','SP1','SP2','I1','I2','F1','F2','D1','D2','N1','B1','P1','SC0')

def comp_label(leagues,cid):
    cols=r17.text_cols(leagues); row=leagues[leagues['id'].astype(str)==str(cid)]
    if row.empty: return {'id':str(cid),'texts':[]}
    d=row.iloc[0].to_dict(); texts=[]
    for c in cols:
        v=d.get(c)
        if v is not None and str(v).lower()!='nan' and str(v).strip(): texts.append(str(v).strip())
    return {'id':str(cid),'texts':texts[:8]}

def collect_fresh():
    lp=HERE/'_leagues.parquet'; fp=HERE/'_fixtures.parquet'; r9.download(f'{HF}/leagues.parquet?download=true',lp); r9.download(f'{HF}/fixtures.parquet?download=true',fp)
    if r9.fsha(fp)!=FIX_SHA: raise RuntimeError('R26 fixtures hash drift')
    leagues=pd.read_parquet(lp)
    excluded=set(r23.comp_id(leagues,code)[0] for code in EXCLUDED_CODES)
    excluded.add(r17.league_id(leagues,'RFPL'))
    fx=pd.read_parquet(fp,columns=['id','date_utc','league_id','home_team_id','away_team_id','goals_home','goals_away','status_norm','is_played'])
    fx=fx[(fx.is_played==True)&(fx.status_norm=='FT')&fx.goals_home.notna()&fx.goals_away.notna()&fx.league_id.notna()&fx.home_team_id.notna()&fx.away_team_id.notna()].copy()
    fx['date']=pd.to_datetime(fx.date_utc,utc=True).dt.date.astype(str); fx=fx[(fx.date>=START)&(fx.date<=END)].sort_values(['date','id']).drop_duplicates('id')
    fx['cid']=fx.league_id.astype(int).astype(str); pool=fx[~fx.cid.isin(excluded)].copy(); counts=Counter(pool.cid); eligible=sorted([c for c,n in counts.items() if n>=MIN_FRESH_PER_COMP],key=lambda c:int(c))
    use=pool[pool.cid.isin(eligible)].copy(); fresh=[]
    for z in use.itertuples(index=False): fresh.append({'date':z.date,'game_id':f'HF_R26_{int(z.id)}','competition_id':str(int(z.league_id)),'home_team':str(int(z.home_team_id)),'away_team':str(int(z.away_team_id)),'home_goals':int(z.goals_home),'away_goals':int(z.goals_away),'home_xg':0.0,'away_xg':0.0})
    if len(fresh)<200 or len(eligible)<5: raise RuntimeError(f'R26 cohort gate failed rows={len(fresh)} competitions={len(eligible)}')
    fresh.sort(key=lambda x:(x['date'],x['game_id'])); meta={c:{'fresh_count':counts[c],**comp_label(leagues,c)} for c in eligible}; lp.unlink(missing_ok=True); fp.unlink(missing_ok=True)
    return fresh,eligible,meta,sorted(excluded,key=lambda x:int(x)),len(fx),len(pool)

def run():
    r24s=r25.select_s60(); base,bs,ss,bm,sm,lock=r25.lock_models()
    fresh,eligible,meta,excluded,total_fresh,pool_rows=collect_fresh(); scored=[]; by=defaultdict(list)
    for x in fresh: by[x['date']].append(x)
    for d in sorted(by):
        pending=[]
        for x in sorted(by[d],key=lambda z:z['game_id']):
            rb=bs.pred(x); rs=ss.pred(x); scored.append({'date':d,'competition_id':x['competition_id'],'y':r9.actual(x),'B20':r23.pred(bm,rb),'S60':r23.pred(sm,rs),'b20_comp_history':rb['comp_history'],'s60_comp_history':rs['comp_history']}); pending.append((x,rb,rs))
        for x,rb,rs in pending: r17.goal_only_base_update(bs,x,rb); r17.goal_only_base_update(ss,x,rs)
    b=r9.metrics(scored,'B20'); s=r9.metrics(scored,'S60'); per={}
    for cid in eligible:
        z=[x for x in scored if x['competition_id']==cid]; bb=r9.metrics(z,'B20'); ssx=r9.metrics(z,'S60'); per[cid]={'meta':meta[cid],'count':len(z),'B20':bb,'S60':ssx,'delta':r23.delta(ssx,bb)}
    mature=[x for x in scored if x['s60_comp_history']>=20]; cold=[x for x in scored if x['s60_comp_history']<20]
    strat={}
    for name,z in (('mature',mature),('cold',cold)):
        if z:
            bb=r9.metrics(z,'B20'); ssx=r9.metrics(z,'S60'); strat[name]={'count':len(z),'B20':bb,'S60':ssx,'delta':r23.delta(ssx,bb)}
    summary={'schema_version':'football3-top1-r26-global-fresh-s60','status':'COMPLETE','classification':'FRESH_FORWARD_CONFIRMATION_5_GLOBAL_UNSEEN_COMPETITIONS','formal_weight':0,'governance':{'base_snapshot_last_date':'2026-07-04','fresh_start':START,'fresh_end':END,'candidate_locked_from_R24_validation_before_fresh_label_retrieval':True,'candidate_selection_used_validation_only':True,'R24_test_used_for_selection':False,'R23_fresh_used_for_selection':False,'R25_fresh_used_for_selection':False,'eligible_rule':f'unseen competition with at least {MIN_FRESH_PER_COMP} completed FT fixtures in fresh window','eligible_rule_uses_outcome_values':False,'previous_fresh_domains_excluded':True,'same_date_results_withheld':True,'fresh_xg_used':False,'odds_used':False,'market_prices_used':False,'manual_match_adjustment':False,'hyperparameter_search_on_fresh':False,'fixtures_sha256':FIX_SHA,'formal_promotion_allowed_from_this_run':False},'candidate_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},'candidate_selection_evidence':{'S20_validation_hits':r24s['scales']['S20']['validation']['hits'],'S40_validation_hits':r24s['scales']['S40']['validation']['hits'],'S60_validation_hits':r24s['scales']['S60']['validation']['hits'],'S80_validation_hits':r24s['scales']['S80']['validation']['hits']},'cohort':{'all_completed_fresh_rows_before_exclusion':total_fresh,'rows_after_previous_domain_exclusion_before_min_count':pool_rows,'eligible_competitions':len(eligible),'scored_rows':len(scored),'excluded_competition_ids':excluded,'eligible_competition_meta':meta},'B20':b,'S60':s,'delta_S60_minus_B20':r23.delta(s,b),'history_strata':strat,'per_competition':per}
    OUT.mkdir(parents=True,exist_ok=True); DATA.mkdir(parents=True,exist_ok=True); (OUT/'summary_r26.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); (DATA/'fresh_cohort_r26.json').write_text(json.dumps({'start':START,'end':END,'eligible_competitions':eligible,'meta':meta,'rows':fresh},indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(summary,indent=2))

def verify():
    s=json.loads((OUT/'summary_r26.json').read_text(encoding='utf-8')); g=s['governance']; q=s['candidate_lock_reproduction']; c=s['cohort']
    assert g['candidate_locked_from_R24_validation_before_fresh_label_retrieval'] and g['candidate_selection_used_validation_only']; assert not g['R24_test_used_for_selection'] and not g['R23_fresh_used_for_selection'] and not g['R25_fresh_used_for_selection']; assert g['eligible_rule_uses_outcome_values'] is False and g['previous_fresh_domains_excluded'] and g['same_date_results_withheld']; assert not g['fresh_xg_used'] and not g['odds_used'] and not g['market_prices_used'] and not g['hyperparameter_search_on_fresh']; assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889); assert c['scored_rows']>=200 and c['eligible_competitions']>=5; print('R26_VERIFY_PASS')

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: run_experiment_r26.py {run|verify}')
    {'run':run,'verify':verify}[sys.argv[1]]()
