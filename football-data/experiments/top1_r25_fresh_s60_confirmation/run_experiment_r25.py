#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from collections import defaultdict
from pathlib import Path
import pandas as pd

HERE=Path(__file__).resolve().parent; DATA=HERE/'data'; OUT=HERE/'results'
R23=HERE.parent/'top1_r23_fresh_40k_confirmation'; R24=HERE.parent/'top1_r24_history_scale_curve'
for p in (R23,R24): sys.path.insert(0,str(p))
import run_experiment_r23 as r23
import run_experiment_r24 as r24
r18=r23.r18; r17=r23.r17; r9=r24.r9
START='2026-07-05'; END='2026-08-25'; DIVS=('D2','F2','I2','SP2')
EXTRA_N=40000; TRAIN_MULT=3
R24_RUN=32939928003; R24_DIGEST='sha256:f158db875f247bb0600892e66f2d5f0f8abaa46f1fb73afd1d62441ebf4914d9'; EXTRA_SHA='477d1a4f542850e4e2981b98acbbbba0c261b14f37fc0f5f618d5cb1234452bc'
EXTRA=HERE/'r24_artifact'/'data'/'extra_r24_xg_60000.csv'; HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'

def select_s60():
    s=json.loads((R24/'results'/'summary_r24.json').read_text(encoding='utf-8'))
    if s['status']!='COMPLETE' or s['governance']['fresh_R23_used_for_selection']:
        raise RuntimeError('R24 selection contract mismatch')
    vals={k:v['validation'] for k,v in s['scales'].items()}
    x=vals['S60']
    if x['hits']!=max(v['hits'] for v in vals.values()): raise RuntimeError('S60 not validation Top1 winner')
    for metric in ('logloss','brier','rps'):
        if x[metric]!=min(v[metric] for v in vals.values()): raise RuntimeError(f'S60 not validation {metric} winner')
    return s

def lock_models():
    if not EXTRA.exists(): raise RuntimeError(f'locked R24 artifact missing: {EXTRA}')
    if r9.fsha(EXTRA)!=EXTRA_SHA: raise RuntimeError('R24 extra artifact hash mismatch')
    base=r9.load(); ex60=r23.load_csv(EXTRA)
    if len(base)!=20000 or len(ex60)!=60000: raise RuntimeError(f'history size mismatch base={len(base)} extra={len(ex60)}')
    extra=ex60[-EXTRA_N:]
    if max(x['date'] for x in extra)>=min(x['date'] for x in base): raise RuntimeError('S60 extra history not strictly earlier')
    if {x['game_id'] for x in base}&{x['game_id'] for x in extra}: raise RuntimeError('S60 history overlap')
    bp,bs=r23.hist(base); b1=r9.boundary(bp,r9.TARGET_BURN); b2=r9.boundary(bp,b1+r9.TARGET_TRAIN); b3=r9.boundary(bp,b2+r9.TARGET_VAL); ntrain=b2-b1
    bm=r24.model(bp[b1:b2]); bv=[dict(x) for x in bp[b2:b3]]; bt=[dict(x) for x in bp[b3:]]; r24.attach(bv,bm,'B20'); r24.attach(bt,bm,'B20')
    hp,ss=r23.hist(extra+base); off=EXTRA_N; sm=r24.model(hp[off+b2-TRAIN_MULT*ntrain:off+b2]); sv=[dict(x) for x in hp[off+b2:off+b3]]; st=[dict(x) for x in hp[off+b3:]]; r24.attach(sv,sm,'S60'); r24.attach(st,sm,'S60')
    if [x['game_id'] for x in sv]!=[x['game_id'] for x in bv] or [x['game_id'] for x in st]!=[x['game_id'] for x in bt]: raise RuntimeError('S60 tail identity mismatch')
    lock=(r9.metrics(bv,'B20')['hits'],r9.metrics(bt,'B20')['hits'],r9.metrics(sv,'S60')['hits'],r9.metrics(st,'S60')['hits'])
    if lock!=(2064,1877,2071,1889): raise RuntimeError(f'R24 S60 lock failed {lock}')
    return base,bs,ss,bm,sm,lock

def collect_fresh():
    tp=HERE/'_teams.parquet'; lp=HERE/'_leagues.parquet'; r9.download(f'{HF}/teams.parquet?download=true',tp); r9.download(f'{HF}/leagues.parquet?download=true',lp)
    teams=pd.read_parquet(tp); leagues=pd.read_parquet(lp); idx=r18.team_index(teams); cm={d:r23.comp_id(leagues,d) for d in DIVS}; tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)
    fresh=[]; audit=[]; counts={}
    for div in DIVS:
        src=r18.download_csv(div); counts[div]=len(src); cid=cm[div][0]
        for i,z in enumerate(src):
            hid,hc=r18.resolve(z['home_name'],idx); aid,ac=r18.resolve(z['away_name'],idx)
            audit.append({'division':div,'home':z['home_name'],'home_id':hid,'home_candidates':hc,'away':z['away_name'],'away_id':aid,'away_candidates':ac})
            if hid is None or aid is None: continue
            fresh.append({'date':z['date'],'game_id':f'FD_R25_{div}_{z["date"]}_{i:03d}','competition_id':cid,'home_team':hid,'away_team':aid,'home_goals':z['home_goals'],'away_goals':z['away_goals'],'home_xg':0.0,'away_xg':0.0,'division':div})
    cov=len(fresh)/len(audit) if audit else 0.0
    if len(fresh)<60 or cov<.90:
        DATA.mkdir(parents=True,exist_ok=True); un=[x for x in audit if x['home_id'] is None or x['away_id'] is None]; (DATA/'diagnostic_r25.json').write_text(json.dumps({'mapped':len(fresh),'candidates':len(audit),'coverage':cov,'source_counts':counts,'unresolved':un},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        raise RuntimeError(f'R25 fresh cohort gate failed rows={len(fresh)} coverage={cov:.3f}')
    fresh.sort(key=lambda x:(x['date'],x['game_id']))
    return fresh,audit,counts,cov,cm

def run():
    r24s=select_s60(); base,bs,ss,bm,sm,lock=lock_models()
    fresh,audit,counts,cov,cm=collect_fresh(); scored=[]; by=defaultdict(list)
    for x in fresh: by[x['date']].append(x)
    for d in sorted(by):
        pending=[]
        for x in sorted(by[d],key=lambda z:z['game_id']):
            rb=bs.pred(x); rs=ss.pred(x); scored.append({'date':d,'division':x['division'],'y':r9.actual(x),'B20':r23.pred(bm,rb),'S60':r23.pred(sm,rs)}); pending.append((x,rb,rs))
        for x,rb,rs in pending: r17.goal_only_base_update(bs,x,rb); r17.goal_only_base_update(ss,x,rs)
    b=r9.metrics(scored,'B20'); s60=r9.metrics(scored,'S60'); per={}
    for div in DIVS:
        z=[x for x in scored if x['division']==div]; bb=r9.metrics(z,'B20'); ssx=r9.metrics(z,'S60'); per[div]={'count':len(z),'B20':bb,'S60':ssx,'delta':r23.delta(ssx,bb)}
    summary={'schema_version':'football3-top1-r25-fresh-s60-confirmation','status':'COMPLETE','classification':'FRESH_FORWARD_CONFIRMATION_4_DISJOINT_DOMAIN','formal_weight':0,'governance':{'base_snapshot_last_date':'2026-07-04','fresh_start':START,'fresh_end':END,'fresh_rows':len(scored),'mapped_coverage':cov,'domain':'Germany D2, France F2, Italy I2, Spain SP2; disjoint from R17/R18/R23 research domains','candidate_locked_from_R24_validation_before_fresh_label_retrieval':True,'candidate_selection_used_validation_only':True,'R24_test_used_for_selection':False,'R23_fresh_used_for_selection':False,'R24_run_id':R24_RUN,'R24_artifact_digest':R24_DIGEST,'R24_extra_sha256':EXTRA_SHA,'same_date_results_withheld':True,'fresh_xg_used':False,'compatible_historical_xg_state_frozen_after_base':True,'source_files_may_contain_market_columns':True,'market_columns_loaded_into_dataframe':False,'safe_columns_read':list(r18.SAFE_COLUMNS),'odds_used':False,'market_prices_used':False,'manual_match_adjustment':False,'hyperparameter_search_on_fresh':False,'formal_promotion_allowed_from_this_run':False},'candidate_selection_evidence':{'S20_validation_hits':r24s['scales']['S20']['validation']['hits'],'S40_validation_hits':r24s['scales']['S40']['validation']['hits'],'S60_validation_hits':r24s['scales']['S60']['validation']['hits'],'S80_validation_hits':r24s['scales']['S80']['validation']['hits'],'S60_validation_logloss':r24s['scales']['S60']['validation']['logloss'],'S60_validation_brier':r24s['scales']['S60']['validation']['brier'],'S60_validation_rps':r24s['scales']['S60']['validation']['rps']},'candidate_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},'source_counts':counts,'competition_map':{k:{'id':v[0],'texts':v[1]} for k,v in cm.items()},'B20':b,'S60':s60,'delta_S60_minus_B20':r23.delta(s60,b),'per_division':per}
    OUT.mkdir(parents=True,exist_ok=True); DATA.mkdir(parents=True,exist_ok=True); (OUT/'summary_r25.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); (DATA/'fresh_cohort_r25.json').write_text(json.dumps({'start':START,'end':END,'source_counts':counts,'mapped_coverage':cov,'rows':fresh,'mapping_audit':audit},indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(summary,indent=2))

def verify():
    s=json.loads((OUT/'summary_r25.json').read_text(encoding='utf-8')); g=s['governance']; q=s['candidate_lock_reproduction']; e=s['candidate_selection_evidence']
    assert g['fresh_rows']>=60 and g['mapped_coverage']>=.90 and g['candidate_locked_from_R24_validation_before_fresh_label_retrieval'] and g['candidate_selection_used_validation_only']
    assert not g['R24_test_used_for_selection'] and not g['R23_fresh_used_for_selection'] and g['same_date_results_withheld'] and not g['fresh_xg_used']
    assert g['source_files_may_contain_market_columns'] and not g['market_columns_loaded_into_dataframe'] and not g['odds_used'] and not g['market_prices_used'] and not g['hyperparameter_search_on_fresh']
    assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889)
    assert e['S60_validation_hits']==max(e['S20_validation_hits'],e['S40_validation_hits'],e['S60_validation_hits'],e['S80_validation_hits'])
    print('R25_VERIFY_PASS')

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: run_experiment_r25.py {run|verify}')
    {'run':run,'verify':verify}[sys.argv[1]]()
