#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,re
from pathlib import Path

def hfile(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def start(season:str)->int:
    m=re.match(r'(\d{4})',season)
    return int(m.group(1)) if m else 9999

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--reserve',type=Path,required=True)
    ap.add_argument('--spec',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    spec=json.loads(a.spec.read_text(encoding='utf-8'))
    latest={}
    for comp,season,available,quota in spec['strata']:
        if comp not in latest or start(season)>start(latest[comp][0]): latest[comp]=(season,int(quota))
    with a.reserve.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    by={}
    for r in rows: by.setdefault((r['competition_id'],r['season']),[]).append(r)
    selected=[];audit=[]
    for comp in sorted(latest):
        season,quota=latest[comp]
        cand=sorted(by.get((comp,season),[]),key=lambda r:r['selection_hash'])
        if len(cand)<quota: raise ValueError(f'{comp} {season}: need {quota}, found {len(cand)}')
        selected.extend(cand[:quota])
        audit.append({'competition':comp,'season':season,'quota':quota,'reserve_available':len(cand)})
    selected.sort(key=lambda r:r['selection_hash'])
    if len(selected)!=338: raise ValueError(f'expected 338, got {len(selected)}')
    a.out.mkdir(parents=True,exist_ok=True)
    op=a.out/'GOLD1000_R1_confirm_latest_manifest.csv'
    fields=list(rows[0])
    with op.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(selected)
    rec={'schema_version':'GOLD1000-CONFIRM-LATEST-R1','selection_source':'frozen_reserve_manifest','selection_uses_labels':False,'selection_uses_model_outputs':False,'rows':len(selected),'strata':audit,'input_sha256':hfile(a.reserve),'output_sha256':hfile(op)}
    rp=a.out/'GOLD1000_R1_confirm_latest_receipt.json';rp.write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'PASS','rows':len(selected),'output_sha256':hfile(op)},ensure_ascii=False))
    return 0
if __name__=='__main__': raise SystemExit(main())
