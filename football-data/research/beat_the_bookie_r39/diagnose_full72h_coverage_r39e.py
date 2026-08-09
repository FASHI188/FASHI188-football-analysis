#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,json,math,re
from datetime import datetime,timezone
from pathlib import Path

COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES=('home','draw','away')
HOLDOUT_START=datetime.fromisoformat('2016-09-20T14:00:00')


def parse_dt(d,t):
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try:return datetime.strptime(f'{d} {t}',fmt)
        except ValueError:pass
    raise ValueError(f'bad datetime {d} {t}')

def valid(v):
    s=v.strip().casefold()
    if not s or s in {'nan','na','null','none'}:return False
    try:x=float(s)
    except ValueError:return False
    return math.isfinite(x) and x>1.0

def parse_mapping(header):
    m={}
    for i,name in enumerate(header[3:],3):
        g=COL_RE.match(name)
        if g:m[(int(g.group(2)),int(g.group(3)),g.group(1))]=i
    if len(m)!=32*72*3:raise RuntimeError(f'odds mapping {len(m)}')
    return m

def booksets(row,m):
    by=[]
    for h in range(72):
        books=set()
        for b in range(1,33):
            if all(valid(row[m[(b,h,o)]]) for o in OUTCOMES):books.add(b)
        by.append(books)
    return by

def lane_flags(by):
    counts=[len(x) for x in by]
    common=set.intersection(*by) if by else set()
    strict=len(common)>=5
    per_all=all(c>=5 for c in counts)
    per60=sum(c>=5 for c in counts)>=60 and all(c>=5 for c in counts[-6:])
    grid_idx=[0,6,12,18,24,30,36,42,48,54,60,66]
    grid=all(counts[i]>=5 for i in grid_idx)
    return {
      'STRICT_COMMON5_ALL72':strict,
      'PER_HOUR5_ALL72':per_all,
      'PER_HOUR5_AT_LEAST60_PLUS_FINAL6':per60,
      'SIX_HOURLY_GRID5':grid
    },counts,len(common)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--source-dir',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());lane_ids=[x['id'] for x in reg['frozen_coverage_lanes']]
    totals={k:0 for k in lane_ids};holdout={k:0 for k in lane_ids};hour_ge5=[0]*72;common5=0;rows=0;bad=0
    for src in ('odds_series_no_scores.csv.gz','odds_series_b_no_scores.csv.gz'):
        with gzip.open(a.source_dir/src,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);header=next(r)
            assert header[:3]==['match_id','match_date','match_time'];assert 'score_home' not in header and 'score_away' not in header
            m=parse_mapping(header)
            for row in r:
                if len(row)!=len(header) or not row[0].strip():bad+=1;continue
                try:dt=parse_dt(row[1],row[2])
                except ValueError:bad+=1;continue
                flags,counts,common=lane_flags(booksets(row,m));rows+=1
                for i,c in enumerate(counts):hour_ge5[i]+=int(c>=5)
                common5+=int(common>=5)
                for k,v in flags.items():
                    if v:
                        totals[k]+=1
                        if dt>=HOLDOUT_START:holdout[k]+=1
    sel=reg['selection_rule_after_coverage_only'];chosen=None
    for k in sel['preferred_order']:
        if totals[k]>=sel['minimum_total_eligible_rows'] and holdout[k]>=sel['minimum_holdout_pool_rows_after_2016_09_20T14_00_00']:
            chosen=k;break
    status='PASS_R39E_FULL72H_COVERAGE_LANE_SELECTED_NO_LABELS' if chosen else 'STOP_R39E_FULL72H_COVERAGE_NO_LANE'
    out={
      'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,'rows_scanned':rows,'bad_rows':bad,
      'lane_total_eligible':totals,'lane_holdout_eligible':holdout,'selected_lane':chosen,
      'hourly_rows_with_at_least5_books':hour_ge5,'strict_common5_all72_rows':common5,
      'no_label_audit':{'score_columns_present_in_python_input':False,'score_values_accessed':0,'result_values_accessed':0,'prediction_metrics_computed':0,'model_fit':0,'threshold_selection':0,'match_identity_lock':0},
      'hard_limits':reg['hard_limits']
    }
    a.out_dir.mkdir(parents=True,exist_ok=True);(a.out_dir/'coverage_status_r39e.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
