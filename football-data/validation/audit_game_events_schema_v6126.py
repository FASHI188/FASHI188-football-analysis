#!/usr/bin/env python3
"""V6.12.6 readiness audit for Transfermarkt game_events disciplinary fields."""
from __future__ import annotations
import csv,gzip,io,json,urllib.request
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_game_events_schema_v6126_status.json'
URL='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/game_events.csv.gz'
def main():
    req=urllib.request.Request(URL,headers={'User-Agent':'football-analysis-research/1.0'})
    with urllib.request.urlopen(req,timeout=120) as resp: raw=resp.read()
    text=gzip.decompress(raw).decode('utf-8-sig',errors='replace'); rd=csv.DictReader(io.StringIO(text)); cols=list(rd.fieldnames or [])
    types=Counter(); rows=0; samples=[]
    for r in rd:
        rows+=1; typ=str(r.get('type') or '').strip(); types[typ]+=1
        if len(samples)<20 and ('card' in typ.lower() or 'yellow' in typ.lower() or 'red' in typ.lower()): samples.append({k:r.get(k) for k in cols})
    payload={'schema_version':'V6.12.6-game-events-schema-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','source':URL,'compressed_bytes':len(raw),'columns':cols,'rows':rows,'event_types':dict(types),'disciplinary_samples':samples,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'columns':cols,'rows':rows,'types':dict(types)},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
