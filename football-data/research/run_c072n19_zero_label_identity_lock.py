#!/usr/bin/env python3
from __future__ import annotations
import csv, hashlib, io, json, math, urllib.request
from pathlib import Path

SEASON='2526'
BASE='https://www.football-data.co.uk/mmz4281/{season}/{code}.csv'
DIVS=('E1','E2','E3','SC0','SC1','SC2','SC3')
NEED=('Div','Date','HomeTeam','AwayTeam','Avg>2.5','Avg<2.5','AvgC>2.5','AvgC<2.5')
TARGET_N=1000
OUT=Path('football-data/research/_c072n19_zero_label')
MAN=OUT/'c072n19_replication1000_identity_market.jsonl'
SUM=OUT/'c072n19_zero_label_summary.json'

def f(x):
    try:
        v=float(str(x).strip())
        return v if math.isfinite(v) and v>1 else None
    except Exception:return None

def get(code):
    url=BASE.format(season=SEASON,code=code)
    req=urllib.request.Request(url,headers={'User-Agent':'football3-c072n19-zero-label/1.0'})
    with urllib.request.urlopen(req,timeout=90) as r: raw=r.read()
    text=raw.decode('utf-8-sig',errors='replace')
    rd=csv.DictReader(io.StringIO(text))
    if rd.fieldnames is None or any(c not in rd.fieldnames for c in NEED):
        raise RuntimeError(f'missing required columns {code}: {rd.fieldnames}')
    rows=[]
    for i,z in enumerate(rd):
        vals=[f(z[c]) for c in NEED[4:]]
        if not all(v is not None for v in vals): continue
        date=str(z['Date']).strip(); home=str(z['HomeTeam']).strip(); away=str(z['AwayTeam']).strip(); div=str(z['Div']).strip() or code
        if not date or not home or not away: continue
        rows.append({'division':div,'source_code':code,'date':date,'home_team':home,'away_team':away,
                     'avg_over25_open':vals[0],'avg_under25_open':vals[1],
                     'avg_over25_close':vals[2],'avg_under25_close':vals[3],
                     'source_row_index':i,'source_url':url})
    return raw,rows

def parse_date(s):
    from datetime import datetime
    for fmt in ('%d/%m/%Y','%d/%m/%y'):
        try:return datetime.strptime(s,fmt).date().isoformat()
        except ValueError:pass
    raise RuntimeError(f'date parse failed {s}')

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    pool=[]; sources=[]
    for code in DIVS:
        raw,rows=get(code); sources.append({'code':code,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'valid_market_rows':len(rows)})
        for z in rows:z['date_iso']=parse_date(z['date'])
        pool.extend(rows)
    pool.sort(key=lambda z:(z['date_iso'],z['source_code'],z['home_team'],z['away_team'],z['source_row_index']))
    if len(pool)<TARGET_N: raise RuntimeError(f'coverage {len(pool)} < {TARGET_N}')
    sel=pool[:TARGET_N]
    ids=[f"{z['source_code']}|{z['date_iso']}|{z['home_team']}|{z['away_team']}|{z['source_row_index']}" for z in sel]
    if len(ids)!=len(set(ids)): raise RuntimeError('duplicate identity')
    h=hashlib.sha256(('\n'.join(ids)+'\n').encode()).hexdigest()
    with MAN.open('w',encoding='utf-8',newline='\n') as g:
        for rank,(ident,z) in enumerate(zip(ids,sel),1):
            out={'rank':rank,'identity':ident,**z}
            g.write(json.dumps(out,ensure_ascii=False,sort_keys=True)+'\n')
    summary={'project':'football3','experiment':'C072-N19','stage':'ZERO_LABEL_IDENTITY_LOCK','classification':'REPLICATION_REPRODUCTION_ONLY',
             'source':'Football-Data 2025/26 public CSV','source_divisions':list(DIVS),'eligible_market_rows':len(pool),'selected_n':len(sel),
             'selection_rule':'chronological first 1000 after fixed 7-division concatenation; valid identity + Avg O/U2.5 open/close pairs only',
             'identity_sha256':h,'result_columns_requested_or_materialized':0,'target_values_materialized':0,'model_fit':0,
             'C070F_confirmation1597_opened':False,'N17_reserve_opened':False,'N18_confirmation150_opened':False,
             'C073_C077_scientific_results_used':False,'sources':sources}
    SUM.write_text(json.dumps(summary,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2,ensure_ascii=False,sort_keys=True))
if __name__=='__main__':main()
