#!/usr/bin/env python3
from __future__ import annotations
import json, math, sys
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd

HERE=Path(__file__).resolve().parent
R25=HERE.parent/'top1_r25_fresh_s60_confirmation'; sys.path.insert(0,str(R25))
import run_experiment_r25 as r25
r23=r25.r23; r17=r25.r17; r9=r25.r9
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
OUT=HERE/'results'; TARGET_DAY='2026-08-26'

def string_cols(df): return [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype==object]

def resolve_team(df, names):
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
        if score>=100:
            hits.append((score,str(int(d['id'])),texts))
    hits.sort(key=lambda x:(-x[0],int(x[1])))
    if not hits: raise RuntimeError('K League 1 not found')
    top=hits[0]
    return {'league_id':top[1],'texts':top[2],'candidates':hits[:5]}

def probs(model, raw):
    pr=model.predict_proba([r9.feat_k1(raw)])[0]; cls=list(model[-1].classes_); v=np.zeros(3)
    for c,p in zip(cls,pr): v[int(c)]=float(p)
    v=np.clip(v,1e-12,None); v/=v.sum()
    return {'home':float(v[0]),'draw':float(v[1]),'away':float(v[2]),'top1':('home','draw','away')[int(np.argmax(v))]}

def scorelines(mu_h,mu_a,maxg=7):
    z=[]
    for h in range(maxg+1):
        ph=math.exp(-mu_h)*mu_h**h/math.factorial(h)
        for a in range(maxg+1):
            pa=math.exp(-mu_a)*mu_a**a/math.factorial(a); z.append({'score':f'{h}-{a}','probability':ph*pa})
    z.sort(key=lambda x:-x['probability']); return z[:10]

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
    names={
      'daejeon':('Daejeon Hana Citizen','Daejeon Citizen'), 'ulsan':('Ulsan HD','Ulsan Hyundai','Ulsan HD FC'),
      'gwangju':('Gwangju FC',), 'jeonbuk':('Jeonbuk Hyundai Motors','Jeonbuk Motors','Jeonbuk Hyundai Motors FC'), 'jeju':('Jeju SK','Jeju United'),
      'incheon':('Incheon United',), 'gimcheon':('Gimcheon Sangmu','Gimcheon Sangmu FC'), 'seoul':('FC Seoul','Seoul'), 'anyang':('FC Anyang','Anyang'),
      'pohang':('Pohang Steelers','Pohang'), 'gangwon':('Gangwon FC','Gangwon')}
    ids={k:resolve_team(teams,v) for k,v in names.items()}; league=resolve_league(leagues); cid=league['league_id']
    D=ids['daejeon']['team_id']; U=ids['ulsan']['team_id']
    target={'date':TARGET_DAY,'game_id':'WEB_DAEJEON_ULSAN_20260826','competition_id':cid,'home_team':D,'away_team':U,'home_goals':0,'away_goals':0,'home_xg':0.0,'away_xg':0.0}
    raw_frozen=ss.pred(target); frozen=probs(sm,raw_frozen)
    R=[
      ('2026-07-05','gwangju','ulsan',1,1),('2026-07-11','ulsan','jeonbuk',1,3),('2026-07-12','jeju','daejeon',0,0),('2026-07-18','daejeon','ulsan',2,2),
      ('2026-07-21','jeonbuk','daejeon',0,0),('2026-07-21','ulsan','incheon',1,2),('2026-07-25','gimcheon','daejeon',3,2),('2026-07-26','seoul','ulsan',1,3),
      ('2026-08-02','daejeon','gwangju',2,0),('2026-08-02','ulsan','anyang',3,1),('2026-08-08','anyang','daejeon',1,2),('2026-08-08','pohang','ulsan',0,2),
      ('2026-08-15','seoul','daejeon',4,2),('2026-08-16','ulsan','gangwon',2,3),('2026-08-22','jeonbuk','ulsan',1,0),('2026-08-23','daejeon','gangwon',3,0)]
    updates=[]
    for i,(ds,h,a,hg,ag) in enumerate(R):
        if not ds<TARGET_DAY: raise RuntimeError('target-date leakage in update list')
        row={'date':ds,'game_id':f'WEB_K1_UPDATE_{i:02d}','competition_id':cid,'home_team':ids[h]['team_id'],'away_team':ids[a]['team_id'],'home_goals':hg,'away_goals':ag,'home_xg':0.0,'away_xg':0.0}
        team_only_update(ss,row); updates.append({'date':ds,'home':h,'away':a,'score':f'{hg}-{ag}'})
    raw=ss.pred(target); updated=probs(sm,raw)
    out={'status':'COMPLETE','model':'S60 stage-primary candidate: 60k strict history / 24,123 train rows','target':{'display':'Daejeon Hana Citizen vs Ulsan HD','date':TARGET_DAY,'kickoff_kst':'2026-08-26 19:30','beijing_time':'2026-08-26 18:30','competition':'K League 1 Round 25','home_team_id':D,'away_team_id':U,'competition_id':cid},
      'governance':{'target_result_used':False,'target_live_data_used':False,'only_results_strictly_before_target_day_used':True,'recent_league_results_used':True,'fresh_xg_used':False,'cup_results_used':False,'odds_used':False,'market_prices_used':False,'lineups_used_as_model_features':False,'injuries_used_as_model_features':False,'manual_probability_adjustment':False,'competition_aggregate_left_frozen_after_2026_07_04':True,'reason':'only target-team post-Jul4 results were manually reconstructed; updating league-wide aggregate from a partial league sample would bias the state'},
      'lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},
      'resolution':{'league':league,**ids},'frozen_20260704':{'probabilities':frozen,'raw':raw_frozen,'scorelines':scorelines(float(raw_frozen['mu_home']),float(raw_frozen['mu_away']))},'prematch_team_updated':{'probabilities':updated,'raw':raw,'scorelines':scorelines(float(raw['mu_home']),float(raw['mu_away']))},'prematch_league_updates':updates,
      'context_audit_only':{'standings':'Daejeon 10th, 29 pts; Ulsan 3rd, 37 pts before kickoff','daejeon_form':'W Gangwon 3-0; W Chungnam Asan 3-2 cup; L Seoul 2-4; W Anyang 2-1; W Gwangju 2-0','ulsan_form':'L Jeonbuk 0-1; L Gangwon 2-3; W Pohang 2-0; W Anyang 3-1; L Ulsan Citizen 0-1 cup','h2h_2026':'Ulsan 1-4 Daejeon (Apr26); Daejeon 2-2 Ulsan (Jul18)','confirmed_lineups_observed_prematch':True,'weather_about_c':30}}
    OUT.mkdir(parents=True,exist_ok=True); p=OUT/'daejeon_ulsan_20260826_s60.json'; p.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
