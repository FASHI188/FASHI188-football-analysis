#!/usr/bin/env python3
from __future__ import annotations
import csv,json,math
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_ah_line_distribution_v6165a.json'
COMPS=('ENG_PremierLeague','GER_Bundesliga','ITA_SerieA','FRA_Ligue1','ESP_LaLiga','NED_Eredivisie','POR_PrimeiraLiga','SCO_Premiership')
SEASONS=('2022/23','2023/24','2024/25','2025/26')
def f(v):
    try:x=float(str(v).strip())
    except:return None
    return x if math.isfinite(x) else None

def main():
    byseason={};overall=Counter();usable=Counter()
    for s in SEASONS:
        c=Counter();u=Counter();rows=0
        for cid in COMPS:
            d=ROOT/'processed'/cid
            if not d.exists():continue
            for p in d.glob('*.csv'):
                with p.open('r',encoding='utf-8-sig',newline='') as fh:
                    for r in csv.DictReader(fh):
                        if str(r.get('season') or r.get('Season') or '').strip()!=s:continue
                        line=f(r.get('AHCh'));hh=f(r.get('PCAHH'));aa=f(r.get('PCAHA'))
                        if line is None or hh is None or aa is None or hh<=1 or aa<=1:continue
                        rows+=1;key=f'{line:.2f}';c[key]+=1;overall[key]+=1
                        twice=line*2
                        if abs(twice-round(twice))<1e-9 and abs(line*4-round(line*4))<1e-9:
                            # Half-goal lines are x.5; exclude +/-0.5 because they duplicate 1X2 partitions.
                            if abs(line-round(line))>.49 and abs(line)>=1.49:u[key]+=1;usable[key]+=1
        byseason[s]={'rows_with_pinnacle_closing_ah':rows,'line_counts':dict(c.most_common()),'nonredundant_half_goal_counts':dict(u.most_common()),'nonredundant_half_goal_total':sum(u.values())}
    payload={'schema_version':'V6.16.5a-ah-line-distribution-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','seasons':byseason,'overall_line_counts':dict(overall.most_common()),'overall_nonredundant_half_goal_counts':dict(usable.most_common()),'overall_nonredundant_half_goal_total':sum(usable.values())}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
