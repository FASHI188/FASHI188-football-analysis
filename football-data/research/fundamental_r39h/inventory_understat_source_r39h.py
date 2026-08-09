#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,sqlite3,zipfile
from datetime import datetime,timezone
from pathlib import Path

IDENTITY_KEYS={'date','datetime','home','away','home_team','away_team','h_team','a_team','team_h','team_a','league','season'}
PERF_KEYS={'xg','xga','npxg','npxga','home_xg','away_xg','h_xg','a_xg','shots','shots_on_target','ppda'}
OUTCOME_KEYS={'goal','goals','result','winner','score','h_goals','a_goals','home_goals','away_goals'}
def norm(s:str)->str:return s.strip().lower().replace(' ','_').replace('-','_')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--zip',required=True);ap.add_argument('--registration',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    reg=json.loads(Path(a.registration).read_text());out=Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);rows=[];db_meta={}
    with zipfile.ZipFile(a.zip) as z:
        for info in sorted(z.infolist(),key=lambda x:x.filename):
            if info.is_dir():continue
            item={'member':info.filename,'bytes':info.file_size,'extension':Path(info.filename).suffix.lower()}
            if item['extension']=='.csv':
                with z.open(info) as raw:
                    text=(line.decode('utf-8-sig','replace') for line in raw);r=csv.reader(text)
                    try:header=next(r)
                    except StopIteration:header=[]
                    n=sum(1 for _ in r)
                cols=[c.strip() for c in header];nc=[norm(c) for c in cols];item['columns']=cols;item['row_count']=n
                item['identity_hits']=[c for c,nm in zip(cols,nc) if nm in IDENTITY_KEYS or any(k in nm for k in ('home_team','away_team','match_date','kickoff'))]
                item['performance_schema_hits']=[c for c,nm in zip(cols,nc) if nm in PERF_KEYS or 'xg' in nm or 'shot' in nm]
                item['outcome_schema_hits']=[c for c,nm in zip(cols,nc) if nm in OUTCOME_KEYS or any(k in nm for k in ('score','result','winner','goal'))]
                item['candidate_match_table']=bool(len(item['identity_hits'])>=3 and any('xg' in norm(c) for c in cols))
            rows.append(item)
        if 'understat.db' in z.namelist():
            db_path=out/'_understat_identity_inspect.db'
            with z.open('understat.db') as src,db_path.open('wb') as dst:
                for chunk in iter(lambda:src.read(1024*1024),b''):dst.write(chunk)
            con=sqlite3.connect(db_path)
            tables=[r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
            for t in tables:
                cols=[r[1] for r in con.execute(f'pragma table_info("{t}")')];nm={norm(c):c for c in cols};entry={'columns':cols}
                if {'league','season','date'}<=set(nm):
                    q=f'SELECT "{nm["league"]}", "{nm["season"]}", COUNT(*), MIN("{nm["date"]}"), MAX("{nm["date"]}") FROM "{t}" GROUP BY 1,2 ORDER BY 1,2'
                    entry['identity_coverage']=[{'league':r[0],'season':r[1],'rows':r[2],'min_date':r[3],'max_date':r[4]} for r in con.execute(q)]
                db_meta[t]=entry
            con.close();db_path.unlink(missing_ok=True)
    candidates=[r for r in rows if r.get('candidate_match_table')]
    result={'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':'PASS_R39H_UNDERSTAT_INVENTORY_MATCH_TABLE_FOUND' if candidates else 'STOP_R39H_UNDERSTAT_INVENTORY_NO_MATCH_TABLE','archive_members':len(rows),'csv_members':sum(r['extension']=='.csv' for r in rows),'candidate_match_tables':[r['member'] for r in candidates],'members':rows,'sqlite_identity_metadata':db_meta,'no_label_audit':{'outcome_values_accessed':0,'xg_values_accessed':0,'model_fit':0,'threshold_selection':0,'holdout_labels_accessed':0},'hard_limits':reg['hard_limits']}
    (out/'understat_inventory_r39h.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'status':result['status'],'candidate_match_tables':result['candidate_match_tables'],'sqlite_tables':list(db_meta)},indent=2))
if __name__=='__main__':main()
