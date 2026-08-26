#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
R9B_DIR=HERE.parents[0]/"top1_r9b_xg_hf"
sys.path.insert(0,str(R9B_DIR))
import run_experiment_r9b as r9
HF="https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
OUT=HERE/"results"
TARGET_DAY="2026-08-26"

def fit_k1_and_state():
    r9.freeze(); rows=r9.load(); st=r9.S(); pred=[]; by=defaultdict(list)
    for row in rows: by[row['date']].append(row)
    for ds in sorted(by):
        pending=[]
        for row in sorted(by[ds],key=lambda x:x['game_id']):
            p=st.pred(row); pred.append({'date':ds,'y':r9.actual(row),'raw':p}); pending.append((row,p))
        for row,p in pending: st.update(row,p)
    b1=r9.boundary(pred,r9.TARGET_BURN); b2=r9.boundary(pred,b1+r9.TARGET_TRAIN); train=pred[b1:b2]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y=[x['y'] for x in train]
    k1=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0))
    k1.fit([r9.feat_k1(x['raw']) for x in train],y)
    return rows,st,k1

def download_table(name):
    p=HERE/f"_{name}.parquet"; r9.download(f"{HF}/{name}.parquet?download=true",p); return p

def text_cols(df):
    return [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype==object]

def resolve_team(teams,needle,alts=()):
    id_col='id' if 'id' in teams.columns else 'team_id'
    cols=text_cols(teams); keys=tuple(x.lower() for x in (needle,)+tuple(alts)); cand=[]
    for row in teams.itertuples(index=False):
        d=row._asdict(); texts=[str(d.get(c) or '').strip() for c in cols]; blob=' | '.join(texts).lower(); score=0
        for k in keys:
            if any(k==t.lower() for t in texts): score=max(score,100)
            elif k in blob: score=max(score,60+len(k))
        if score: cand.append((score,int(d[id_col]),texts))
    if not cand: raise RuntimeError(f"no team match for {needle}")
    cand.sort(key=lambda x:(-x[0],x[1])); top=cand[0]
    return {'team_id':str(top[1]),'texts':top[2],'candidates':cand[:8]}

def resolve_ucl(leagues):
    id_col='id' if 'id' in leagues.columns else 'league_id'; cols=text_cols(leagues); hits=[]
    for row in leagues.itertuples(index=False):
        d=row._asdict(); blob=' | '.join(str(d.get(c) or '') for c in cols).lower()
        if 'champions league' in blob and 'women' not in blob and 'youth' not in blob: hits.append((int(d[id_col]),blob))
    if not hits: raise RuntimeError('UCL not found')
    hits.sort(key=lambda x:len(x[1])); return str(hits[0][0])

def probs(model,raw):
    p=model.predict_proba([r9.feat_k1(raw)])[0]; cls=list(model[-1].classes_); v=np.zeros(3)
    for c,q in zip(cls,p): v[int(c)]=float(q)
    v=np.clip(v,1e-12,None); v/=v.sum(); names=('home','draw','away')
    return {'home':float(v[0]),'draw':float(v[1]),'away':float(v[2]),'top1':names[int(np.argmax(v))]}

def main():
    rows,st,k1=fit_k1_and_state(); tp=download_table('teams'); lp=download_table('leagues')
    teams=pd.read_parquet(tp); leagues=pd.read_parquet(lp)
    vik=resolve_team(teams,'Viking FK',('Viking',)); din=resolve_team(teams,'Dinamo Zagreb',('GNK Dinamo Zagreb','Dinamo'))
    ucl=resolve_ucl(leagues); tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)
    target={'date':TARGET_DAY,'game_id':'WEB_VIKING_DINAMO_20260826','competition_id':ucl,'home_team':vik['team_id'],'away_team':din['team_id'],'home_goals':0,'away_goals':0,'home_xg':0.0,'away_xg':0.0}
    raw=st.pred(target); out={'status':'COMPLETE','model':'R9b K1 pure frozen model','target':{'display':'Viking FK vs Dinamo Zagreb','uefa_day':TARGET_DAY,'beijing_time':'2026-08-27 03:00','competition_id':ucl,'home_team_id':vik['team_id'],'away_team_id':din['team_id']},'governance':{'pure_model':True,'prematch_updates_used':False,'first_leg_used':False,'recent_results_used':False,'injuries_used':False,'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False,'post_match_target_data_used':False},'team_resolution':{'viking':vik,'dinamo':din},'probabilities':probs(k1,raw),'raw':raw}
    OUT.mkdir(parents=True,exist_ok=True); path=OUT/'viking_dinamo_20260826.json'; path.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
