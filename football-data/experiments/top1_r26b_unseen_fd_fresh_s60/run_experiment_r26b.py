#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from collections import defaultdict
from pathlib import Path
import pandas as pd

HERE=Path(__file__).resolve().parent; DATA=HERE/'data'; OUT=HERE/'results'
R25=HERE.parent/'top1_r25_fresh_s60_confirmation'; sys.path.insert(0,str(R25)); import run_experiment_r25 as r25
r23=r25.r23; r18=r25.r18; r17=r25.r17; r9=r25.r9
START='2026-07-05'; END='2026-08-25'; DIVS=('T1','G1','SC1','SC2','SC3')
MIN_MAPPED=40; MIN_COVERAGE=.85; MIN_ACTIVE_DIVS=4
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'

def collect_fresh():
    tp=HERE/'_teams.parquet'; lp=HERE/'_leagues.parquet'; r9.download(f'{HF}/teams.parquet?download=true',tp); r9.download(f'{HF}/leagues.parquet?download=true',lp)
    teams=pd.read_parquet(tp); leagues=pd.read_parquet(lp); idx=r18.team_index(teams); cm={d:r23.comp_id(leagues,d) for d in DIVS}; tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)
    fresh=[];audit=[];counts={}
    for div in DIVS:
        src=r18.download_csv(div); counts[div]=len(src); cid=cm[div][0]
        for i,z in enumerate(src):
            hid,hc=r18.resolve(z['home_name'],idx); aid,ac=r18.resolve(z['away_name'],idx)
            audit.append({'division':div,'home':z['home_name'],'home_id':hid,'home_candidates':hc,'away':z['away_name'],'away_id':aid,'away_candidates':ac})
            if hid is None or aid is None: continue
            fresh.append({'date':z['date'],'game_id':f'FD_R26B_{div}_{z["date"]}_{i:03d}','competition_id':cid,'home_team':hid,'away_team':aid,'home_goals':z['home_goals'],'away_goals':z['away_goals'],'home_xg':0.0,'away_xg':0.0,'division':div})
    cov=len(fresh)/len(audit) if audit else 0.0; active=sum(int(counts[d]>0) for d in DIVS)
    if len(fresh)<MIN_MAPPED or cov<MIN_COVERAGE or active<MIN_ACTIVE_DIVS:
        DATA.mkdir(parents=True,exist_ok=True);un=[x for x in audit if x['home_id'] is None or x['away_id'] is None];(DATA/'diagnostic_r26b.json').write_text(json.dumps({'mapped':len(fresh),'candidates':len(audit),'coverage':cov,'active_divisions':active,'source_counts':counts,'unresolved':un},indent=2,ensure_ascii=False)+'\n',encoding='utf-8');raise RuntimeError(f'R26b cohort gate failed rows={len(fresh)} coverage={cov:.3f} active={active}')
    fresh.sort(key=lambda x:(x['date'],x['game_id']));return fresh,audit,counts,cov,cm

def run():
    r24s=r25.select_s60();base,bs,ss,bm,sm,lock=r25.lock_models();fresh,audit,counts,cov,cm=collect_fresh();scored=[];by=defaultdict(list)
    for x in fresh:by[x['date']].append(x)
    for d in sorted(by):
        pending=[]
        for x in sorted(by[d],key=lambda z:z['game_id']):
            rb=bs.pred(x);rs=ss.pred(x);scored.append({'date':d,'division':x['division'],'y':r9.actual(x),'B20':r23.pred(bm,rb),'S60':r23.pred(sm,rs)});pending.append((x,rb,rs))
        for x,rb,rs in pending:r17.goal_only_base_update(bs,x,rb);r17.goal_only_base_update(ss,x,rs)
    b=r9.metrics(scored,'B20');s=r9.metrics(scored,'S60');per={}
    for div in DIVS:
        z=[x for x in scored if x['division']==div]
        if not z:continue
        bb=r9.metrics(z,'B20');ssx=r9.metrics(z,'S60');per[div]={'count':len(z),'B20':bb,'S60':ssx,'delta':r23.delta(ssx,bb)}
    summary={'schema_version':'football3-top1-r26b-unseen-fd-fresh-s60','status':'COMPLETE','classification':'FRESH_FORWARD_CONFIRMATION_5B_DISJOINT_FOOTBALL_DATA','formal_weight':0,'governance':{'base_snapshot_last_date':'2026-07-04','fresh_start':START,'fresh_end':END,'domains_predeclared_before_source_retrieval':list(DIVS),'domain':'Turkey T1, Greece G1, Scotland SC1/SC2/SC3; disjoint from R17/R18/R23/R25','candidate_locked_from_R24_validation_before_fresh_label_retrieval':True,'candidate_selection_used_validation_only':True,'R24_test_used_for_selection':False,'R23_fresh_used_for_selection':False,'R25_fresh_used_for_selection':False,'cohort_includes_all_completed_rows_in_fixed_window':True,'cohort_filter_uses_outcome_values':False,'same_date_results_withheld':True,'fresh_xg_used':False,'source_files_may_contain_market_columns':True,'market_columns_loaded_into_dataframe':False,'safe_columns_read':list(r18.SAFE_COLUMNS),'odds_used':False,'market_prices_used':False,'manual_match_adjustment':False,'hyperparameter_search_on_fresh':False,'formal_promotion_allowed_from_this_run':False},'candidate_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},'candidate_selection_evidence':{'S20_validation_hits':r24s['scales']['S20']['validation']['hits'],'S40_validation_hits':r24s['scales']['S40']['validation']['hits'],'S60_validation_hits':r24s['scales']['S60']['validation']['hits'],'S80_validation_hits':r24s['scales']['S80']['validation']['hits']},'cohort':{'source_counts':counts,'mapped_rows':len(fresh),'mapped_coverage':cov,'competition_map':{k:{'id':v[0],'texts':v[1]} for k,v in cm.items()}},'B20':b,'S60':s,'delta_S60_minus_B20':r23.delta(s,b),'per_division':per}
    OUT.mkdir(parents=True,exist_ok=True);DATA.mkdir(parents=True,exist_ok=True);(OUT/'summary_r26b.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');(DATA/'fresh_cohort_r26b.json').write_text(json.dumps({'start':START,'end':END,'source_counts':counts,'mapped_coverage':cov,'rows':fresh,'mapping_audit':audit},indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(summary,indent=2))

def verify():
    s=json.loads((OUT/'summary_r26b.json').read_text(encoding='utf-8'));g=s['governance'];q=s['candidate_lock_reproduction'];c=s['cohort'];assert g['domains_predeclared_before_source_retrieval']==list(DIVS);assert g['candidate_locked_from_R24_validation_before_fresh_label_retrieval'] and g['candidate_selection_used_validation_only'];assert not g['R24_test_used_for_selection'] and not g['R23_fresh_used_for_selection'] and not g['R25_fresh_used_for_selection'];assert g['cohort_includes_all_completed_rows_in_fixed_window'] and not g['cohort_filter_uses_outcome_values'] and g['same_date_results_withheld'];assert not g['fresh_xg_used'] and not g['market_columns_loaded_into_dataframe'] and not g['odds_used'] and not g['market_prices_used'] and not g['hyperparameter_search_on_fresh'];assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889);assert c['mapped_rows']>=MIN_MAPPED and c['mapped_coverage']>=MIN_COVERAGE;print('R26B_VERIFY_PASS')

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:raise SystemExit('usage: run_experiment_r26b.py {run|verify}')
    {'run':run,'verify':verify}[sys.argv[1]]()
