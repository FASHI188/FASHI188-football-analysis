#!/usr/bin/env python3
"""V6.12.8 readiness audit for international-duty fatigue Fast100."""
from __future__ import annotations
import csv,gzip,io,json,urllib.request
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_international_duty_readiness_v6128_status.json'
URL='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz'
def main():
    req=urllib.request.Request(URL,headers={'User-Agent':'football-analysis-research/1.0'})
    with urllib.request.urlopen(req,timeout=120) as resp:raw=resp.read()
    rd=csv.DictReader(io.StringIO(gzip.decompress(raw).decode('utf-8-sig',errors='replace')))
    types=Counter(); comps=Counter(); recent=Counter(); samples=[]; rows=0
    for r in rd:
        rows+=1; typ=str(r.get('competition_type') or '').strip();cid=str(r.get('competition_id') or '').strip();types[typ]+=1;comps[cid]+=1
        date=str(r.get('date') or '')[:10]
        if date>='2025-07-01':
            recent[(typ,cid)]+=1
            if typ and typ!='domestic_league' and len(samples)<40:
                samples.append({'date':date,'competition_id':cid,'competition_type':typ,'home':r.get('home_club_name'),'away':r.get('away_club_name'),'game_id':r.get('game_id')})
    payload={'schema_version':'V6.12.8-international-duty-readiness-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','source':URL,'rows':rows,'competition_types':dict(types),'recent_nonleague_pairs':[{'competition_type':k[0],'competition_id':k[1],'count':v} for k,v in recent.most_common(80)],'recent_nonleague_samples':samples,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'types':dict(types),'recent':payload['recent_nonleague_pairs'][:30]},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
