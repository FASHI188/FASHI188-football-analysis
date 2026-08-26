#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from collections import defaultdict
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
EXP=HERE.parent
R14_DIR=EXP/'top1_r14_compact_draw'
sys.path.insert(0,str(R14_DIR))
import run_experiment_r14 as r14
r12=r14.r12; r9=r14.r9
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
OUT=HERE/'results'; TARGET_DAY='2026-08-26'; DRAW_GATE=0.08

def fit_locked_models_and_states():
    r12.freeze_gate(); rows=r9.load(); base=r9.S(); draw=r12.DrawState(); pred=[]; by=defaultdict(list)
    for row in rows: by[row['date']].append(row)
    for ds in sorted(by):
        pending=[]
        for row in sorted(by[ds], key=lambda x:x['game_id']):
            raw=base.pred(row); df=draw.features(row,raw); pred.append({'date':ds,'y':r9.actual(row),'raw':raw,'df':df}); pending.append((row,raw))
        for row,raw in pending: base.update(row,raw); draw.update(row)
    b1=r9.boundary(pred,r9.TARGET_BURN); b2=r9.boundary(pred,b1+r9.TARGET_TRAIN); train=pred[b1:b2]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y=[x['y'] for x in train]
    def mdl(): return make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0))
    k1=mdl(); k3=mdl(); k1.fit([r9.feat_k1(x['raw']) for x in train],y); k3.fit([r9.feat_k1(x['raw'])+r14.compact(x['df']) for x in train],y)
    return rows,base,draw,k1,k3

def download_table(name):
    p=HERE/f'_{name}.parquet'; r9.download(f'{HF}/{name}.parquet?download=true',p); return p

def string_columns(df): return [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype==object]

def resolve_team(teams, needle, alternates=()):
    id_col='id' if 'id' in teams.columns else 'team_id'; cols=string_columns(teams); keys=tuple(x.lower() for x in (needle,)+tuple(alternates)); cand=[]
    for row in teams.itertuples(index=False):
        d=row._asdict(); texts=[str(d.get(c) or '').strip() for c in cols]; blob=' | '.join(texts).lower(); score=0
        for k in keys:
            if any(k==t.lower() for t in texts): score=max(score,100)
            if k in blob: score=max(score,60+len(k))
        if score: cand.append((score,int(d[id_col]),texts))
    if not cand: raise RuntimeError(f'no team match for {needle}')
    cand.sort(key=lambda x:(-x[0],x[1])); top=cand[0]; return {'team_id':str(top[1]),'texts':top[2],'candidates':cand[:5]}

def resolve_ucl_league(leagues):
    id_col='id' if 'id' in leagues.columns else 'league_id'; cols=string_columns(leagues); hits=[]
    for row in leagues.itertuples(index=False):
        d=row._asdict(); blob=' | '.join(str(d.get(c) or '') for c in cols).lower()
        if 'champions league' in blob and 'women' not in blob and 'youth' not in blob: hits.append((int(d[id_col]),blob))
    if not hits: raise RuntimeError('UCL id not found')
    hits.sort(key=lambda x:(0 if x[0]==2 else 1,len(x[1]))); return str(hits[0][0])

def latest_comp_for_team(rows,tid,exclude):
    found=[(r['date'],r['competition_id']) for r in rows if r['competition_id']!=exclude and (r['home_team']==tid or r['away_team']==tid)]
    if not found: return f'DOMESTIC_{tid}'
    found.sort(); return found[-1][1]

def named(p): return {'home':p['p_home'],'draw':p['p_draw'],'away':p['p_away'],'top1':('home','draw','away')[int(p['top1'])]}
def predict(k1,k3,draw_state,target,raw):
    df=draw_state.features(target,raw); p1=r14.decorate_probs(k1,[r9.feat_k1(raw)])[0]; p3=r14.decorate_probs(k3,[r9.feat_k1(raw)+r14.compact(df)])[0]
    edge=float(p3['p_draw']-max(p3['p_home'],p3['p_away'])); gt=1 if int(p1['top1'])!=1 and int(p3['top1'])==1 and edge>=DRAW_GATE else int(p1['top1'])
    return {'K1':named(p1),'K3':named(p3),'R15_gate':{'top1':('home','draw','away')[gt],'draw_edge':edge,'activated':gt!=int(p1['top1'])}}
def goal_update(st,row,include_xg):
    p=st.pred(row); d=date.fromisoformat(row['date']); c=row['competition_id']; h=row['home_team']; a=row['away_team']; hg=int(row['home_goals']); ag=int(row['away_goals'])
    hs=st.v[(c,h,'H')]; av=st.v[(c,a,'A')]; hs.n+=1; hs.gf+=hg; hs.ga+=ag; av.n+=1; av.gf+=ag; av.ga+=hg; st.cn[c]+=1; st.ch[c]+=hg; st.ca[c]+=ag
    H=st.touch(h,d); A=st.touch(a,d); eh=r9.clamp((hg-p['latent_h'])/(1+p['latent_h']),-2,2); ea=r9.clamp((ag-p['latent_a'])/(1+p['latent_a']),-2,2)
    H.attack=r9.clamp(H.attack+r9.LR*eh,-1.2,1.2); A.defence=r9.clamp(A.defence+r9.LR*eh,-1.2,1.2); A.attack=r9.clamp(A.attack+r9.LR*ea,-1.2,1.2); H.defence=r9.clamp(H.defence+r9.LR*ea,-1.2,1.2); H.matches+=1; A.matches+=1
    if include_xg:
        hx=float(row['home_xg']); ax=float(row['away_xg']); st.xgn[c]+=1; st.xgh[c]+=hx; st.xga[c]+=ax; st.xhist[h].append((d,hx,ax)); st.xhist[a].append((d,ax,hx))
def score_peaks(raw,topn=8):
    mh=float(raw['mu_home']); ma=float(raw['mu_away']); arr=[]
    for h in range(8):
        ph=math.exp(-mh)*mh**h/math.factorial(h)
        for a in range(8): arr.append({'score':f'{h}-{a}','probability':ph*(math.exp(-ma)*ma**a/math.factorial(a))})
    arr.sort(key=lambda x:-x['probability']); return arr[:topn]

def main():
    rows,st,draw_state,k1,k3=fit_locked_models_and_states(); tp=download_table('teams'); lp=download_table('leagues'); teams=pd.read_parquet(tp); leagues=pd.read_parquet(lp)
    R=lambda n,a=(): resolve_team(teams,n,a)
    aek=R('AEK Athens',('AEK','Athens AEK')); lev=R('Levski Sofia',('Levski','PFC Levski Sofia'))
    borac=R('Borac Banja Luka',('Borac','Borac BL')); dunav=R('Dunav Ruse',('Dunav 2010','Dunav')); craiova=R('Universitatea Craiova',('CS Universitatea Craiova','U Craiova')); loko_sofia=R('Lokomotiv Sofia',('Lokomotiv Sofia 1929',)); sept=R('Septemvri Sofia',('Septemvri',)); kairat=R('Kairat Almaty',('Kairat',)); loko_plov=R('Lokomotiv Plovdiv',('Loko Plovdiv',)); spartak=R('Spartak Varna',('Spartak',)); iraklis=R('Iraklis',('Iraklis 1908',))
    ucl=resolve_ucl_league(leagues); tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)
    aid=aek['team_id']; lid=lev['team_id']; aek_dom=latest_comp_for_team(rows,aid,ucl); lev_dom=latest_comp_for_team(rows,lid,ucl)
    target={'date':TARGET_DAY,'game_id':'WEB_AEK_LEVSKI_20260826_K3','competition_id':ucl,'home_team':aid,'away_team':lid,'home_goals':0,'away_goals':0,'home_xg':0.0,'away_xg':0.0}
    raw0=st.pred(target); pred0=predict(k1,k3,draw_state,target,raw0)
    def row(dt,gid,comp,h,a,hg,ag,hx=0.0,ax=0.0): return {'date':dt,'game_id':gid,'competition_id':comp,'home_team':h,'away_team':a,'home_goals':hg,'away_goals':ag,'home_xg':hx,'away_xg':ax}
    updates=[
      {'label':'Borac Banja Luka 1-1 Levski Sofia, UCL Q1 first leg','include_xg':False,'row':row('2026-07-07','WEB_BOR_LEV_1',ucl,borac['team_id'],lid,1,1),'evidence':'UEFA official score; xG omitted'},
      {'label':'Levski Sofia 4-0 Borac Banja Luka, UCL Q1 second leg','include_xg':False,'row':row('2026-07-14','WEB_LEV_BOR_2',ucl,lid,borac['team_id'],4,0),'evidence':'UEFA official score; xG omitted'},
      {'label':'Levski Sofia 2-1 Dunav Ruse, First League','include_xg':False,'row':row('2026-07-17','WEB_LEV_DUN',lev_dom,lid,dunav['team_id'],2,1),'evidence':'FBref score; xG omitted'},
      {'label':'Levski Sofia 1-0 Universitatea Craiova, UCL Q2 first leg','include_xg':False,'row':row('2026-07-22','WEB_LEV_CRA_1',ucl,lid,craiova['team_id'],1,0),'evidence':'FBref/UEFA score; xG omitted'},
      {'label':'Lokomotiv Sofia 1-2 Levski Sofia, First League','include_xg':False,'row':row('2026-07-25','WEB_LOS_LEV',lev_dom,loko_sofia['team_id'],lid,1,2),'evidence':'FBref score; xG omitted'},
      {'label':'Universitatea Craiova 2-2 Levski Sofia, UCL Q2 second leg','include_xg':False,'row':row('2026-07-29','WEB_CRA_LEV_2',ucl,craiova['team_id'],lid,2,2),'evidence':'FBref score; xG omitted'},
      {'label':'Levski Sofia 3-0 Septemvri Sofia, First League','include_xg':False,'row':row('2026-08-01','WEB_LEV_SEP',lev_dom,lid,sept['team_id'],3,0),'evidence':'FBref score; xG omitted'},
      {'label':'Levski Sofia 1-0 Kairat Almaty, UCL Q3 first leg','include_xg':False,'row':row('2026-08-04','WEB_LEV_KAI_1',ucl,lid,kairat['team_id'],1,0),'evidence':'UEFA official score; xG omitted'},
      {'label':'Levski Sofia 2-0 Lokomotiv Plovdiv, First League','include_xg':False,'row':row('2026-08-07','WEB_LEV_LOP',lev_dom,lid,loko_plov['team_id'],2,0),'evidence':'AiScore score; xG omitted'},
      {'label':'Kairat Almaty 0-1 Levski Sofia, UCL Q3 second leg','include_xg':False,'row':row('2026-08-11','WEB_KAI_LEV_2',ucl,kairat['team_id'],lid,0,1),'evidence':'UEFA official score; xG omitted'},
      {'label':'Levski Sofia 0-0 AEK Athens, UCL playoff first leg','include_xg':True,'row':row('2026-08-18','WEB_LEV_AEK_1',ucl,lid,aid,0,0,1.08,1.10),'evidence':'Soccerzz/Playmaker post-match xG 1.08-1.10'},
      {'label':'AEK Athens 4-0 Iraklis, Greek Super League','include_xg':True,'row':row('2026-08-22','WEB_AEK_IRA',aek_dom,aid,iraklis['team_id'],4,0,2.06,0.38),'evidence':'FotMob/Opta post-match xG 2.06-0.38'},
      {'label':'Levski Sofia 6-0 Spartak Varna, First League','include_xg':False,'row':row('2026-08-22','WEB_LEV_SPA',lev_dom,lid,spartak['team_id'],6,0),'evidence':'Liga/FotMob score; no verified post-match xG used'},
    ]
    for u in updates: goal_update(st,u['row'],u['include_xg']); draw_state.update(u['row'])
    raw1=st.pred(target); pred1=predict(k1,k3,draw_state,target,raw1)
    out={'status':'COMPLETE','target':{'display':'AEK Athens vs Levski Sofia','uefa_day':TARGET_DAY,'beijing_time':'2026-08-27 03:00','competition_id':ucl,'home_team_id':aid,'away_team_id':lid},'governance':{'strict_prior_snapshot':True,'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False,'post_match_target_data_used':False,'same_day_leakage':False,'injury_or_lineup_manual_adjustment':False,'R15_gate_threshold':DRAW_GATE},'models':{'K1':'R9b retained baseline','K3':'R14 compact 9-feature challenger; R17 fresh winner 46/81 vs K1 45/81','R15_gate':'locked validation-selected draw gate'},'team_resolution':{'aek':aek,'levski':lev,'aek_domestic_comp':aek_dom,'levski_domestic_comp':lev_dom},'snapshot_only':{'prediction':pred0,'raw':raw0,'score_peaks':score_peaks(raw0)},'pre_match_updated':{'prediction':pred1,'raw':raw1,'score_peaks':score_peaks(raw1)},'pre_match_updates':updates,'external_context_not_model_inputs':['UEFA official squad lists checked','Levski injury reports checked; no manual personnel probability adjustment','No odds or market prices used']}
    OUT.mkdir(parents=True,exist_ok=True); p=OUT/'aek_levski_20260826_k3.json'; p.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
