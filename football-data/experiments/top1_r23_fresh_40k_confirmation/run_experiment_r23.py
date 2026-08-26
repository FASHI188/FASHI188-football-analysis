#!/usr/bin/env python3
from __future__ import annotations
import csv,json,sys
from collections import defaultdict
from pathlib import Path
import numpy as np,pandas as pd

HERE=Path(__file__).resolve().parent; DATA=HERE/'data'; OUT=HERE/'results'
R17=HERE.parent/'top1_r17_fresh_forward'; R18=HERE.parent/'top1_r18_fresh_efl'; R22=HERE.parent/'top1_r22_history_scale_40k'
for p in (R17,R18,R22): sys.path.insert(0,str(p))
import run_experiment_r17 as r17
import run_experiment_r18 as r18
import run_experiment_r22 as r22
r9=r22.r9
START='2026-07-05'; END='2026-08-25'; DIVS=('N1','B1','P1','SC0'); EXTRA_N=20000; TRAIN16=16082
R22_RUN=32939310382; R22_DIGEST='sha256:f19182964be4311a0c9ed888cfa79c5e91de528cfe0237bf0c0fae84c6e312de'
EXTRA=HERE/'r22_artifact'/'data'/'extra_r22_xg_20000.csv'; HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'

def load_csv(p):
    z=[]
    with p.open(encoding='utf-8') as f:
        for x in csv.DictReader(f):
            x['home_goals']=int(x['home_goals']);x['away_goals']=int(x['away_goals']);x['home_xg']=float(x['home_xg']);x['away_xg']=float(x['away_xg']);z.append(x)
    z.sort(key=lambda x:(x['date'],x['game_id']));return z

def hist(rows):
    st=r9.S(); pred=[]; by=defaultdict(list)
    for x in rows: by[x['date']].append(x)
    for d in sorted(by):
        q=[]
        for x in sorted(by[d],key=lambda z:z['game_id']):
            raw=st.pred(x);pred.append({'date':d,'game_id':x['game_id'],'y':r9.actual(x),'raw':raw});q.append((x,raw))
        for x,raw in q: st.update(x,raw)
    return pred,st

def pred(model,raw): return r17.probs(model,[r9.feat_k1(raw)])[0]

def comp_id(leagues,code):
    cols=r17.text_cols(leagues); hits=[]
    for row in leagues.itertuples(index=False):
        d=row._asdict(); texts=[str(d.get(c) or '').strip() for c in cols]
        if any(t.lower()==code.lower() for t in texts): hits.append((str(int(d['id'])),texts))
    u={x[0]:x[1] for x in hits}
    if len(u)!=1: raise RuntimeError(f'competition mapping ambiguous {code}: {list(u.items())}')
    k=next(iter(u));return k,u[k]

def delta(a,b):
    return {'hits':a['hits']-b['hits'],'top1_pp':100*(a['top1_accuracy']-b['top1_accuracy']),'logloss':a['logloss']-b['logloss'],'brier':a['brier']-b['brier'],'rps':a['rps']-b['rps']}

def run():
    if not EXTRA.exists(): raise RuntimeError(f'locked R22 extra artifact missing: {EXTRA}')
    base=r9.load(); extra=load_csv(EXTRA)
    if len(base)!=20000 or len(extra)!=EXTRA_N: raise RuntimeError(f'history lock mismatch base={len(base)} extra={len(extra)}')
    if max(x['date'] for x in extra)>=min(x['date'] for x in base): raise RuntimeError('extra history is not strictly earlier')
    if {x['game_id'] for x in base}&{x['game_id'] for x in extra}: raise RuntimeError('history overlap')

    bp,bs=hist(base); b1=r9.boundary(bp,r9.TARGET_BURN);b2=r9.boundary(bp,b1+r9.TARGET_TRAIN);b3=r9.boundary(bp,b2+r9.TARGET_VAL)
    bm=r22.fit_model(bp[b1:b2]); bv=[dict(x) for x in bp[b2:b3]];bt=[dict(x) for x in bp[b3:]];r22.attach(bv,bm,'B20');r22.attach(bt,bm,'B20')
    ep,hs=hist(extra+base); off=EXTRA_N; hm=r22.fit_model(ep[off+b2-TRAIN16:off+b2]);hv=[dict(x) for x in ep[off+b2:off+b3]];ht=[dict(x) for x in ep[off+b3:]];r22.attach(hv,hm,'H40');r22.attach(ht,hm,'H40')
    lock=(r9.metrics(bv,'B20')['hits'],r9.metrics(bt,'B20')['hits'],r9.metrics(hv,'H40')['hits'],r9.metrics(ht,'H40')['hits'])
    if lock!=(2064,1877,2067,1895): raise RuntimeError(f'R22 candidate lock failed {lock}')

    tp=HERE/'_teams.parquet';lp=HERE/'_leagues.parquet';r9.download(f'{HF}/teams.parquet?download=true',tp);r9.download(f'{HF}/leagues.parquet?download=true',lp)
    teams=pd.read_parquet(tp);leagues=pd.read_parquet(lp);idx=r18.team_index(teams);cm={d:comp_id(leagues,d) for d in DIVS};tp.unlink(missing_ok=True);lp.unlink(missing_ok=True)
    fresh=[];audit=[];counts={}
    for div in DIVS:
        src=r18.download_csv(div);counts[div]=len(src);cid=cm[div][0]
        for i,z in enumerate(src):
            hid,hc=r18.resolve(z['home_name'],idx);aid,ac=r18.resolve(z['away_name'],idx);audit.append({'division':div,'home':z['home_name'],'home_id':hid,'home_candidates':hc,'away':z['away_name'],'away_id':aid,'away_candidates':ac})
            if hid is None or aid is None: continue
            fresh.append({'date':z['date'],'game_id':f'FD_R23_{div}_{z["date"]}_{i:03d}','competition_id':cid,'home_team':hid,'away_team':aid,'home_goals':z['home_goals'],'away_goals':z['away_goals'],'home_xg':0.0,'away_xg':0.0,'division':div})
    cov=len(fresh)/len(audit) if audit else 0
    if len(fresh)<60 or cov<.90:
        DATA.mkdir(parents=True,exist_ok=True);un=[x for x in audit if x['home_id'] is None or x['away_id'] is None];(DATA/'diagnostic_r23.json').write_text(json.dumps({'mapped':len(fresh),'candidates':len(audit),'coverage':cov,'source_counts':counts,'unresolved':un},indent=2,ensure_ascii=False)+'\n')
        raise RuntimeError(f'R23 fresh cohort gate failed rows={len(fresh)} coverage={cov:.3f}')

    fresh.sort(key=lambda x:(x['date'],x['game_id'])); scored=[];by=defaultdict(list)
    for x in fresh:by[x['date']].append(x)
    for d in sorted(by):
        q=[]
        for x in sorted(by[d],key=lambda z:z['game_id']):
            rb=bs.pred(x);rh=hs.pred(x);scored.append({'date':d,'division':x['division'],'y':r9.actual(x),'B20':pred(bm,rb),'H40_T16':pred(hm,rh)});q.append((x,rb,rh))
        for x,rb,rh in q:r17.goal_only_base_update(bs,x,rb);r17.goal_only_base_update(hs,x,rh)
    b=r9.metrics(scored,'B20');h=r9.metrics(scored,'H40_T16');per={}
    for div in DIVS:
        z=[x for x in scored if x['division']==div];bb=r9.metrics(z,'B20');hh=r9.metrics(z,'H40_T16');per[div]={'count':len(z),'B20':bb,'H40_T16':hh,'delta':delta(hh,bb)}
    s={'schema_version':'football3-top1-r23-fresh-40k-confirmation','status':'COMPLETE','classification':'FRESH_FORWARD_CONFIRMATION_3_DISJOINT_DOMAIN','formal_weight':0,'governance':{'base_snapshot_last_date':'2026-07-04','fresh_start':START,'fresh_end':END,'fresh_rows':len(scored),'mapped_coverage':cov,'domain':'Netherlands N1, Belgium B1, Portugal P1, Scotland SC0; disjoint from R17/R18 research domains','candidate_locked_from_R22_before_fresh_label_retrieval':True,'R22_run_id':R22_RUN,'R22_artifact_digest':R22_DIGEST,'same_date_results_withheld':True,'fresh_xg_used':False,'compatible_historical_xg_state_frozen_after_base':True,'source_files_may_contain_market_columns':True,'market_columns_loaded_into_dataframe':False,'safe_columns_read':list(r18.SAFE_COLUMNS),'odds_used':False,'market_prices_used':False,'manual_match_adjustment':False,'hyperparameter_search_on_fresh':False,'formal_promotion_allowed_from_this_run':False},'candidate_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'H40_T16_validation_hits':lock[2],'H40_T16_test_hits':lock[3]},'source_counts':counts,'competition_map':{k:{'id':v[0],'texts':v[1]} for k,v in cm.items()},'B20':b,'H40_T16':h,'delta_H40_T16_minus_B20':delta(h,b),'per_division':per}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'summary_r23.json').write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n');DATA.mkdir(parents=True,exist_ok=True);(DATA/'fresh_cohort_r23.json').write_text(json.dumps({'start':START,'end':END,'source_counts':counts,'mapped_coverage':cov,'rows':fresh,'mapping_audit':audit},indent=2,ensure_ascii=False)+'\n');print(json.dumps(s,indent=2))

def verify():
    s=json.loads((OUT/'summary_r23.json').read_text());g=s['governance'];q=s['candidate_lock_reproduction'];assert g['fresh_rows']>=60 and g['mapped_coverage']>=.90;assert g['candidate_locked_from_R22_before_fresh_label_retrieval'] and g['same_date_results_withheld'];assert not g['fresh_xg_used'] and g['compatible_historical_xg_state_frozen_after_base'];assert g['source_files_may_contain_market_columns'] and not g['market_columns_loaded_into_dataframe'];assert not g['odds_used'] and not g['market_prices_used'] and not g['hyperparameter_search_on_fresh'];assert not g['formal_promotion_allowed_from_this_run'];assert (q['B20_validation_hits'],q['B20_test_hits'],q['H40_T16_validation_hits'],q['H40_T16_test_hits'])==(2064,1877,2067,1895);print('R23_VERIFY_PASS')

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:raise SystemExit('usage: run_experiment_r23.py {run|verify}')
    {'run':run,'verify':verify}[sys.argv[1]]()
