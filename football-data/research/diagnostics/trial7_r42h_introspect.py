#!/usr/bin/env python3
from pathlib import Path
import urllib.request,hashlib
import pandas as pd
BASE='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main';TMP=Path('/tmp/t7');TMP.mkdir(exist_ok=True)
def dl(n):
 p=TMP/n
 req=urllib.request.Request(f'{BASE}/{n}?download=true',headers={'User-Agent':'football3-trial7'})
 with urllib.request.urlopen(req,timeout=300) as r,p.open('wb') as f:
  while 1:
   b=r.read(1<<20)
   if not b:break
   f.write(b)
 return p
fp=dl('fixtures.parquet');lp=dl('leagues.parquet')
print('LEAGUE_COLS',pd.read_parquet(lp).columns.tolist())
leagues=pd.read_parquet(lp); print('LEAGUES_MATCH')
namecols=[c for c in leagues.columns if 'name' in c.lower()]
for _,r in leagues.iterrows():
 s=' '.join(str(r.get(c,'')) for c in namecols).lower()
 if any(x in s for x in ['europa','la liga','laliga','champions','conference']): print({c:r.get(c) for c in ['id',*namecols] if c in leagues.columns})
fx=pd.read_parquet(fp,columns=['id','date_utc','league_id','home_team_id','away_team_id'])
teamids=[109,110,348,1885,1203,369,771,338,619,743,1182,361,1805,1305]
for tid in teamids:
 g=fx[(fx.home_team_id==tid)|(fx.away_team_id==tid)].copy();g['dt']=pd.to_datetime(g.date_utc,utc=True);g=g[g.dt<'2026-07-05']
 print('TEAM',tid,'RECENT')
 print(g.sort_values('dt').tail(15)[['date_utc','league_id','home_team_id','away_team_id']].to_string(index=False))
 print('COUNTS',g.league_id.value_counts().head(8).to_dict())
