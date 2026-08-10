#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

LABELS={'FTHG','FTAG','FTR','HTHG','HTAG','HTR','Referee'}
ODDS=['AvgH','AvgD','AvgA','AvgCH','AvgCD','AvgCA','Avg>2.5','Avg<2.5','AvgC>2.5','AvgC<2.5','AvgAHH','AvgAHA','AvgCAHH','AvgCAHA']
LINES=['AHh','AHCh']

def htxt(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def set_sha(ids:list[str])->str:return htxt('\n'.join(sorted(ids))+'\n')
def valid_odd(v)->bool:
    try:x=float(str(v).strip())
    except:return False
    return math.isfinite(x) and x>1.0
def valid_line(v)->bool:
    try:x=float(str(v).strip())
    except:return False
    return math.isfinite(x)
def identity(r,fields):return '|'.join(str(r.get(c,'')).strip() for c in fields)
def parse_date(s):
    for f in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
        try:return datetime.strptime(str(s).strip(),f).date()
        except:pass
    raise ValueError(s)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--market-dir',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());ids=reg['identity']['fields'];pre=set(reg['source']['preholdout_seasons']);hold=reg['source']['holdout_season']
    rows=[];bad_headers=[];by_season=Counter();by_div=Counter()
    for p in sorted(a.market_dir.glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
            if hdr & LABELS:bad_headers.append({'file':p.name,'forbidden':sorted(hdr&LABELS)});continue
            needed=set(ids+ODDS+LINES)
            if not needed<=hdr:continue
            for r in rd:
                if r.get('Season') not in pre|{hold}:continue
                if not all(str(r.get(c,'')).strip() for c in ids):continue
                if not all(valid_odd(r.get(c,'')) for c in ODDS):continue
                if not all(valid_line(r.get(c,'')) for c in LINES):continue
                x={'identity':identity(r,ids),'season':r['Season'],'div':r['Div'],'date':r['Date'],'date_obj':parse_date(r['Date'])}
                rows.append(x);by_season[r['Season']]+=1;by_div[r['Div']]+=1
    if bad_headers:raise RuntimeError(f'forbidden label headers present: {bad_headers}')
    pre_rows=[x for x in rows if x['season'] in pre];hold_rows=[x for x in rows if x['season']==hold]
    gate=reg['coverage_gate'];ok=len(pre_rows)>=gate['minimum_complete_preholdout_rows'] and len(hold_rows)>=gate['minimum_complete_holdout_rows']
    fixed=[];sha=None
    if ok:
        seed=reg['identity']['fixed100_seed'];n=reg['identity']['fixed100_rows']
        fixed=sorted(hold_rows,key=lambda x:htxt(f"{seed}|{x['identity']}"))[:n]
        if len(fixed)==n:sha=set_sha([x['identity'] for x in fixed])
        else:ok=False
    status='PASS_R39J_ZERO_LABEL_MARKET_COVERAGE_AND_FIXED100_LOCK' if ok else 'STOP_R39J_MARKET_COVERAGE_INSUFFICIENT'
    out={
      'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
      'complete_total_rows':len(rows),'complete_preholdout_rows':len(pre_rows),'complete_holdout_rows':len(hold_rows),
      'complete_by_season':dict(sorted(by_season.items())),'complete_by_division':dict(sorted(by_div.items())),
      'fixed100_rows':len(fixed),'fixed100_seed':reg['identity']['fixed100_seed'],'fixed100_identity_sha256':sha,
      'fixed100_min_date':str(min((x['date_obj'] for x in fixed),default=None)) if fixed else None,
      'fixed100_max_date':str(max((x['date_obj'] for x in fixed),default=None)) if fixed else None,
      'audit_access':{'score_values':0,'result_labels':0,'model_fit':0,'regime_threshold_selection':0,'holdout_labels':0},
      'prior_research_separation':reg['prior_research_separation'],'hard_limits':reg['hard_limits']
    }
    a.out_dir.mkdir(parents=True,exist_ok=True);(a.out_dir/'source_audit_r39j.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
