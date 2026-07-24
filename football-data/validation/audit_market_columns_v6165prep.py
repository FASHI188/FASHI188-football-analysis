#!/usr/bin/env python3
from __future__ import annotations
import csv,json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_market_column_availability_v6165prep.json'
COMPS=('ENG_PremierLeague','GER_Bundesliga','ITA_SerieA','FRA_Ligue1','ESP_LaLiga','NED_Eredivisie','POR_PrimeiraLiga','SCO_Premiership')
KEYS=('AH','BbAH','P>','P<','B365>','B365<','Avg>','Avg<','BTTS','BTS','GG','NG')

def main():
    out={}
    for cid in COMPS:
        cols=set();files=[]
        d=ROOT/'processed'/cid
        if d.exists():
            for p in sorted(d.glob('*.csv')):
                with p.open('r',encoding='utf-8-sig',newline='') as fh:
                    rd=csv.reader(fh);header=next(rd,[])
                hit=[c for c in header if any(k.lower() in c.lower() for k in KEYS)]
                if hit:files.append({'file':p.name,'columns':hit});cols.update(hit)
        out[cid]={'unique_market_columns':sorted(cols),'files':files}
    payload={'schema_version':'V6.16.5prep-market-column-availability-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','competitions':out}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
