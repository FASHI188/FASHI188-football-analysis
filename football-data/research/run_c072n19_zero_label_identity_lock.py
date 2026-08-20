#!/usr/bin/env python3
# N19R1 frozen zero-label source plan; this comment also emits PR synchronize after runner-base retarget.
from __future__ import annotations
import hashlib, json, math
from pathlib import Path
from datetime import datetime
import pandas as pd

MIRROR_REPO='MestreAlex/elo-rating'
MIRROR_REV='383d5277fdaed48fd2d909e073e047350e71cb7f'
RAW=f'https://raw.githubusercontent.com/{MIRROR_REPO}/{MIRROR_REV}/data/'
FILES=(
    ('E0','E0_2526.csv','0134017ec2cdec9db8e47e72eabbd74af068a276'),
    ('E1','E1_2526.csv','47b6539b5da4b319e82701d7c5f9bb234f758223'),
    ('D1','D1_2526.csv','9224c84b9f7574461abc48e1119a52704994517d'),
    ('D2','D2_2526.csv','18b5a44ad2fc98b4be9f5884e57d2cdef082cdc0'),
    ('I1','I1_2526.csv','05fde602b37217ddce1da60bb02fb351b246e659'),
    ('I2','I2_2526.csv','e4ac8e20e65af7c06e04a065b6bc0aa526cbfcc3'),
    ('F1','F1_2526.csv','54051914fc311277ab495ec7de950daabfad271b'),
    ('F2','F2_2526.csv','776d5648f1f3c915e34423fcd2a85c5384e5d8cf'),
    ('SP1','SP1_2526.csv','279bd1eee9759f114e89ecc38e32af9ee7c9cdac'),
    ('SP2','SP2_2526.csv','e8258f7c88b1b756b0cc9e728c84f52a83ca4072'),
)
NEED=['Div','Date','HomeTeam','AwayTeam','Avg>2.5','Avg<2.5','AvgC>2.5','AvgC<2.5']
TARGET_N=1000
OUT=Path('football-data/research/_c072n19_zero_label')
MAN=OUT/'c072n19r1_replication1000_identity_market.jsonl'
SUM=OUT/'c072n19r1_zero_label_summary.json'

def odds(x):
    try:
        v=float(x)
        return v if math.isfinite(v) and v>1 else None
    except Exception:return None

def parse_date(x):
    s=str(x).strip()
    for fmt in ('%d/%m/%Y','%d/%m/%y'):
        try:return datetime.strptime(s,fmt).date().isoformat()
        except ValueError:pass
    raise RuntimeError(f'date parse failed: {s}')

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    pool=[]; src=[]
    for code,name,blob_sha in FILES:
        url=RAW+name
        # Zero-label projection: pandas materializes only identity + four market columns.
        # FTHG/FTAG/FTR and every other result/post-match field are excluded by usecols.
        df=pd.read_csv(url,usecols=NEED,encoding='utf-8-sig')
        valid=0
        for pos,r in df.iterrows():
            vals=[odds(r[c]) for c in NEED[4:]]
            if not all(v is not None for v in vals):continue
            home=str(r['HomeTeam']).strip(); away=str(r['AwayTeam']).strip(); div=str(r['Div']).strip(); date=str(r['Date']).strip()
            if not home or not away or not div or not date:continue
            z={'division':div,'source_code':code,'source_file':name,'source_blob_sha':blob_sha,
               'date':date,'date_iso':parse_date(date),'home_team':home,'away_team':away,
               'avg_over25_open':vals[0],'avg_under25_open':vals[1],
               'avg_over25_close':vals[2],'avg_under25_close':vals[3],
               'source_row_index':int(pos)}
            pool.append(z); valid+=1
        src.append({'code':code,'file':name,'blob_sha':blob_sha,'projected_rows':int(len(df)),'valid_market_rows':valid})
    pool.sort(key=lambda z:(z['date_iso'],z['source_code'],z['home_team'],z['away_team'],z['source_row_index']))
    if len(pool)<TARGET_N:raise RuntimeError(f'coverage {len(pool)} < {TARGET_N}')
    sel=pool[:TARGET_N]
    ids=[f"{z['source_code']}|{z['date_iso']}|{z['home_team']}|{z['away_team']}|{z['source_row_index']}" for z in sel]
    if len(ids)!=len(set(ids)):raise RuntimeError('duplicate frozen identity')
    identity_sha=hashlib.sha256(('\n'.join(ids)+'\n').encode()).hexdigest()
    counts={c:0 for c,_,_ in FILES}
    with MAN.open('w',encoding='utf-8',newline='\n') as f:
        for rank,(ident,z) in enumerate(zip(ids,sel),1):
            counts[z['source_code']]+=1
            f.write(json.dumps({'rank':rank,'identity':ident,**z},ensure_ascii=False,sort_keys=True)+'\n')
    s={'project':'football3','experiment':'C072-N19R1','stage':'ZERO_LABEL_IDENTITY_LOCK',
       'classification':'REPLICATION_REPRODUCTION_ONLY','formal_weight':0,
       'mirror_repo':MIRROR_REPO,'mirror_revision':MIRROR_REV,'source_files':src,
       'eligible_market_rows':len(pool),'selected_n':len(sel),'selected_source_counts':counts,
       'identity_sha256':identity_sha,
       'selection_rule':'first 1000 after fixed 10-file zero-label projection and chronological sort',
       'projected_columns':NEED,'forbidden_result_columns_materialized':0,'target_values_materialized':0,'model_fit':0,
       'initial_n19_source_plan_terminal':'STOP_SOURCE_TRANSPORT_BEFORE_LABELS',
       'C070F_confirmation1597_opened':False,'N17_reserve_opened':False,'N18_confirmation150_opened':False,
       'C073_C077_scientific_results_used':False}
    SUM.write_text(json.dumps(s,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(s,indent=2,ensure_ascii=False,sort_keys=True))
if __name__=='__main__':main()
