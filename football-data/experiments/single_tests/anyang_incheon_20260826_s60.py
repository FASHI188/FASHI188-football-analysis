#!/usr/bin/env python3
from __future__ import annotations
import json, math, sys
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd

HERE=Path(__file__).resolve().parent
R25=HERE.parent/'top1_r25_fresh_s60_confirmation'; sys.path.insert(0,str(R25))
import run_experiment_r25 as r25
r9=r25.r9
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
OUT=HERE/'results'; TARGET_DAY='2026-08-26'

def string_cols(df): return [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype==object]

def resolve_team(df,names):
    cols=string_cols(df); hits=[]
    for row in df.itertuples(index=False):
        d=row._asdict(); texts=[str(d.get(c) or '').strip() for c in cols]; low=[x.lower() for x in texts]; blob=' | '.join(low); score=0
        for n in names:
            k=n.lower()
            if any(k==x for x in low): score=max(score,100)
            if k in blob: score=max(score,60+len(k))
        if score:
            try: tid=str(int(d['id']))
            except Exception: continue
            hits.append((score,tid,texts))
    hits.sort(key=lambda x:(-x[0],int(x[1])))
    if not hits: raise RuntimeError(f'team not found: {names}')
    return {'team_id':hits[0][1],'texts':hits[0][2],'candidates':hits[:5]}

def resolve_league(df):
    cols=string_cols(df); hits=[]
    for row in df.itertuples(index=False):
        d=row._asdict(); texts=[str(d.get(c) or '').strip() for c in cols]; blob=' | '.join(texts).lower(); score=0
        if 'k league 1' in blob or 'k-league 1' in blob: score+=100
        if 'south korea' in blob or 'korea' in blob: score+=20
        if score>=100: hits.append((score,str(int(d['id'])),texts))
    hits.sort(key=lambda x:(-x[0],int(x[1])))
    if not hits: raise RuntimeError('K League 1 not found')
    return {'league_id':hits[0][1],'texts':hits[0][2],'candidates':hits[:5]}

def probs(model,raw):
    pr=model.predict_proba([r9.feat_k1(raw)])[0]; cls=list(model[-1].classes_); v=np.zeros(3)
    for c,p in zip(cls,pr): v[int(c)]=float(p)
    v=np.clip(v,1e-12,None); v/=v.sum()
    return {'home':float(v[0]),'draw':float(v[1]),'away':float(v[2]),'top1':('home','draw','away')[int(np.argmax(v))]}

def scorelines(mh,ma,maxg=7):
    out=[]
    for h in range(maxg+1):
        ph=math.exp(-mh)*mh**h/math.factorial(h)
        for a in range(maxg+1):
            pa=math.exp(-ma)*ma**a/math.factorial(a); out.append({'score':f'{h}-{a}','probability':ph*pa})
    out.sort(key=lambda x:-x['probability']); return out[:10]

def team_only_update(st,row):
    p=st.pred(row); d=date.fromisoformat(row['date']); c=row['competition_id']; h=row['home_team']; a=row['away_team']; hg=int(row['home_goals']); ag=int(row['away_goals'])
    hs=st.v[(c,h,'H')]; av=st.v[(c,a,'A')]; hs.n+=1; hs.gf+=hg; hs.ga+=ag; av.n+=1; av.gf+=ag; av.ga+=hg
    H=st.touch(h,d); A=st.touch(a,d); eh=r9.clamp((hg-p['latent_h'])/(1+p['latent_h']),-2,2); ea=r9.clamp((ag-p['latent_a'])/(1+p['latent_a']),-2,2)
    H.attack=r9.clamp(H.attack+r9.LR*eh,-1.2,1.2); A.defence=r9.clamp(A.defence+r9.LR*eh,-1.2,1.2); A.attack=r9.clamp(A.attack+r9.LR*ea,-1.2,1.2); H.defence=r9.clamp(H.defence+r9.LR*ea,-1.2,1.2); H.matches+=1; A.matches+=1
    return p

def main():
    base,bs,ss,bm,sm,lock=r25.lock_models()
    if lock!=(2064,1877,2071,1889): raise RuntimeError(f'S60 lock mismatch {lock}')
    tp=HERE/'_teams.parquet'; lp=HERE/'_leagues.parquet'; r9.download(f'{HF}/teams.parquet?download=true',tp); r9.download(f'{HF}/leagues.parquet?download=true',lp)
    teams=pd.read_parquet(tp); leagues=pd.read_parquet(lp); tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)
    names={'anyang':('FC Anyang','Anyang'),'incheon':('Incheon United',),'seoul':('FC Seoul','Seoul'),'gwangju':('Gwangju FC',),'jeonbuk':('Jeonbuk Hyundai Motors','Jeonbuk Motors'),'ulsan':('Ulsan HD','Ulsan Hyundai'),'bucheon':('Bucheon FC 1995','Bucheon FC','Bucheon'),'gangwon':('Gangwon FC','Gangwon'),'daejeon':('Daejeon Hana Citizen','Daejeon Citizen'),'jeju':('Jeju SK','Jeju United'),'gimcheon':('Gimcheon Sangmu','Gimcheon Sangmu FC')}
    ids={k:resolve_team(teams,v) for k,v in names.items()}; league=resolve_league(leagues); cid=league['league_id']; A=ids['anyang']['team_id']; I=ids['incheon']['team_id']
    target={'date':TARGET_DAY,'game_id':'WEB_ANYANG_INCHEON_20260826','competition_id':cid,'home_team':A,'away_team':I,'home_goals':0,'away_goals':0,'home_xg':0.0,'away_xg':0.0}
    raw0=ss.pred(target); p0=probs(sm,raw0)
    R=[('2026-07-05','seoul','incheon',1,0),('2026-07-12','incheon','anyang',0,1),('2026-07-18','incheon','jeonbuk',1,0),('2026-07-19','anyang','gwangju',1,1),('2026-07-21','ulsan','incheon',1,2),('2026-07-22','bucheon','anyang',2,3),('2026-07-26','anyang','gangwon',2,1),('2026-07-26','incheon','bucheon',1,1),('2026-08-02','ulsan','anyang',3,1),('2026-08-02','jeju','incheon',3,3),('2026-08-08','anyang','daejeon',1,2),('2026-08-15','jeju','anyang',2,0),('2026-08-16','incheon','gimcheon',1,1),('2026-08-22','anyang','seoul',0,7),('2026-08-23','gwangju','incheon',0,1)]
    updates=[]
    for i,(ds,h,a,hg,ag) in enumerate(R):
        if not ds<TARGET_DAY: raise RuntimeError('target-date leakage')
        row={'date':ds,'game_id':f'WEB_K1_UPDATE_{i:02d}','competition_id':cid,'home_team':ids[h]['team_id'],'away_team':ids[a]['team_id'],'home_goals':hg,'away_goals':ag,'home_xg':0.0,'away_xg':0.0}
        team_only_update(ss,row); updates.append({'date':ds,'home':h,'away':a,'score':f'{hg}-{ag}'})
    raw=ss.pred(target); p=probs(sm,raw)
    out={'status':'COMPLETE','model':'S60 stage-primary candidate: 60k strict history / 24,123 train rows','target':{'display':'FC Anyang vs Incheon United','date':TARGET_DAY,'kickoff_kst':'2026-08-26 19:30','beijing_time':'2026-08-26 18:30','competition':'K League 1 Round 25','home_team_id':A,'away_team_id':I,'competition_id':cid},'governance':{'target_result_used':False,'target_live_data_used':False,'only_results_strictly_before_target_day_used':True,'recent_league_results_used':True,'fresh_xg_used':False,'cup_results_used':False,'odds_used':False,'market_prices_used':False,'lineups_used_as_model_features':False,'injuries_used_as_model_features':False,'manual_probability_adjustment':False,'competition_aggregate_left_frozen_after_2026_07_04':True,'reason':'only target-team post-Jul4 K League results reconstructed; partial league aggregate update would bias state'},'lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},'resolution':{'league':league,**ids},'frozen_20260704':{'probabilities':p0,'raw':raw0,'scorelines':scorelines(float(raw0['mu_home']),float(raw0['mu_away']))},'prematch_team_updated':{'probabilities':p,'raw':raw,'scorelines':scorelines(float(raw['mu_home']),float(raw['mu_away']))},'prematch_league_updates':updates,'context_audit_only':{'standings':'Anyang 8th, 30 pts from 24; Incheon 7th, 33 pts from 23 before kickoff','h2h_2026':'Anyang 0-1 Incheon (Mar22); Incheon 0-1 Anyang (Jul12)','anyang_recent':'L Seoul 0-7; L Jeju 0-2; L Daejeon 1-2; L Ulsan 1-3; W Gangwon 2-1','incheon_recent':'W Gwangju 1-0; D Gimcheon 1-1; D Jeju 3-3; D Bucheon 1-1; W Ulsan 2-1','cup_audit':'Anyang beat Chungbuk Cheongju 3-1 and Jeju 2-1; Incheon lost 0-1 to Gimpo; cup omitted from model','confirmed_lineups_observed_prematch':True}}
    OUT.mkdir(parents=True,exist_ok=True); path=OUT/'anyang_incheon_20260826_s60.json'; path.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
