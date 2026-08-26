#!/usr/bin/env python3
from __future__ import annotations
import json, urllib.request
from pathlib import Path
import pyarrow.parquet as pq

HERE=Path(__file__).resolve().parent; OUT=HERE/'results'; DATA=HERE/'data'
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
TABLES=['fixtures','match_stats','fixture_lineups','fixture_players','fixture_players_stats_flat','players']

def dl(name):
    DATA.mkdir(parents=True,exist_ok=True); p=DATA/f'{name}.parquet'
    req=urllib.request.Request(f'{HF}/{name}.parquet?download=true',headers={'User-Agent':'football3-stage3-schema/1.0'})
    with urllib.request.urlopen(req,timeout=300) as r,p.open('wb') as f:
        while True:
            b=r.read(1<<20)
            if not b: break
            f.write(b)
    return p

def run():
    o={'schema_version':'football3-batch001-stage3-schema-audit-v1','tables':{}}
    for n in TABLES:
        p=dl(n); pf=pq.ParquetFile(p); s=pf.schema_arrow
        o['tables'][n]={'rows':pf.metadata.num_rows,'columns':[{'name':f.name,'type':str(f.type)} for f in s]}
        p.unlink(missing_ok=True)
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'schema_stage3.json').write_text(json.dumps(o,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':'COMPLETE','tables':{k:len(v['columns']) for k,v in o['tables'].items()}},indent=2))
if __name__=='__main__': run()
