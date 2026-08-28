#!/usr/bin/env python3
from pathlib import Path
import hashlib,urllib.request,re
import pandas as pd
BASE='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
TMP=Path('/tmp/trial7');TMP.mkdir(exist_ok=True)
def dl(name):
 p=TMP/name
 req=urllib.request.Request(f'{BASE}/{name}?download=true',headers={'User-Agent':'football3-trial7'})
 with urllib.request.urlopen(req,timeout=300) as r,p.open('wb') as f:
  while True:
   b=r.read(1<<20)
   if not b:break
   f.write(b)
 return p
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
fp=dl('fixtures.parquet');tp=dl('teams.parquet')
print('FIX_SHA',sha(fp));print('TEAM_SHA',sha(tp))
print('FIX_COLS',pd.read_parquet(fp).columns.tolist())
print('TEAM_COLS',pd.read_parquet(tp).columns.tolist())
teams=pd.read_parquet(tp)
# print plausible rows only; identity lookup, no match results.
namecols=[c for c in teams.columns if 'name' in c.lower()]
print('NAMECOLS',namecols)
need=['celta','osasuna','anderlecht','kairat','ferenc','trabzon','aarhus','agf','benfica','salzburg','mjall','mjäll','omonia','truiden','plzen','plze','zvezda','red star']
for _,r in teams.iterrows():
 s=' | '.join(str(r.get(c,'')) for c in namecols).lower()
 if any(x in s for x in need):
  print('TEAM', {c:r.get(c) for c in ['id',*namecols] if c in teams.columns})
# candidate scheduled fixtures on date ±1, reading only identity/schedule columns.
fxcols=['id','date_utc','league_id','home_team_id','away_team_id']
fx=pd.read_parquet(fp,columns=fxcols)
fx['dt']=pd.to_datetime(fx.date_utc,utc=True)
fx=fx[(fx.dt>='2026-08-27')&(fx.dt<'2026-08-30')]
# build id -> first name
idcol='id'; nc=namecols[0] if namecols else None
mp={int(r[idcol]):str(r[nc]) for _,r in teams.iterrows() if pd.notna(r.get(idcol))} if nc else {}
for r in fx.itertuples(index=False):
 h=mp.get(int(r.home_team_id),str(r.home_team_id));a=mp.get(int(r.away_team_id),str(r.away_team_id))
 s=(h+' '+a).lower()
 if any(x in s for x in need): print('FIX',r.id,r.date_utc,r.league_id,r.home_team_id,h,'vs',r.away_team_id,a)
