#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,hashlib,json,math,re
from datetime import datetime,timezone
from pathlib import Path

COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES=('home','draw','away')
GRID=[0,6,12,18,24,30,36,42,48,54,60,66]
FINAL6=set(range(66,72))


def htxt(s):return hashlib.sha256(s.encode()).hexdigest()
def parse_dt(d,t):
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try:return datetime.strptime(f'{d} {t}',fmt)
        except ValueError:pass
    raise ValueError(f'bad datetime {d} {t}')
def valid(v):
    s=v.strip()
    if not s:return False
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
def book_valid(row,m,b,h):return valid(row[m[(b,h,'home')]]) and valid(row[m[(b,h,'draw')]]) and valid(row[m[(b,h,'away')]])
def valid_books(row,m,h):return {b for b in range(1,33) if book_valid(row,m,b,h)}
def hour_has5(row,m,h):
    n=0
    for b in range(1,33):
        if book_valid(row,m,b,h):
            n+=1
            if n>=5:return True
    return False
def strict_common5(row,m):
    common=list(range(1,33))
    for h in range(72):
        common=[b for b in common if book_valid(row,m,b,h)]
        if len(common)<5:return False
    return True
def lane_ok(row,m,lane):
    if lane=='STRICT_COMMON5_ALL72':return strict_common5(row,m)
    cache={}
    def ok(h):
        if h not in cache:cache[h]=hour_has5(row,m,h)
        return cache[h]
    if lane=='PER_HOUR5_ALL72':return all(ok(h) for h in range(72))
    if lane=='PER_HOUR5_AT_LEAST60_PLUS_FINAL6':
        if not all(ok(h) for h in FINAL6):return False
        bad=0
        for h in range(72):
            if not ok(h):
                bad+=1
                if bad>12:return False
        return True
    if lane=='SIX_HOURLY_GRID5':return all(ok(h) for h in GRID)
    raise RuntimeError(lane)
def base3_ok(row,m):return len(set.intersection(*(valid_books(row,m,h) for h in (47,65,70))))>=5
def set_sha(items):return htxt('\n'.join(sorted(x['identity'] for x in items))+'\n')
def load(source_dir,lane):
    base=[];lane_rows=[];seen=set()
    for sanitized in ('odds_series_no_scores.csv.gz','odds_series_b_no_scores.csv.gz'):
        raw=sanitized.replace('_no_scores','')
        with gzip.open(source_dir/sanitized,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);h=next(r);assert h[:3]==['match_id','match_date','match_time'];assert 'score_home' not in h and 'score_away' not in h;m=parse_mapping(h)
            for row in r:
                if len(row)!=len(h) or not row[0].strip():continue
                ident=f'{raw}|{row[0].strip()}';
                if ident in seen:raise RuntimeError(f'duplicate {ident}')
                seen.add(ident);item={'identity':ident,'dt':parse_dt(row[1],row[2])}
                if base3_ok(row,m):base.append(item)
                if lane_ok(row,m,lane):lane_rows.append(item)
    return base,lane_rows
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--source-dir',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args();reg=json.loads(a.registration.read_text());lane=reg['selected_coverage_lane'];base,lrows=load(a.source_dir,lane);start=datetime.fromisoformat(reg['holdout_start']);base_hold=[x for x in base if x['dt']>=start]
    r39c=sorted(base_hold,key=lambda x:htxt(f"{reg['consumed']['r39c_seed']}|{x['identity']}"))[:100];assert set_sha(r39c)==reg['consumed']['r39c_sha256'];cids={x['identity'] for x in r39c};rem=[x for x in base_hold if x['identity'] not in cids]
    r39d=sorted(rem,key=lambda x:htxt(f"{reg['consumed']['r39d_seed']}|{x['identity']}"))[:100];assert set_sha(r39d)==reg['consumed']['r39d_sha256'];dids={x['identity'] for x in r39d};assert not cids&dids
    training=[x for x in lrows if x['dt']<start];hold=[x for x in lrows if x['dt']>=start and x['identity'] not in cids and x['identity'] not in dids];selected=sorted(hold,key=lambda x:htxt(f"{reg['r39e_seed']}|{x['identity']}"))[:reg['r39e_rows']];assert len(selected)==reg['r39e_rows'];eids={x['identity'] for x in selected};assert not(eids&cids or eids&dids);sha=set_sha(selected)
    a.out_dir.mkdir(parents=True,exist_ok=True);out={'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':'LOCKED_R39E_FIXED100_NO_LABELS','selected_coverage_lane':lane,'lane_training_rows':len(training),'lane_holdout_rows_after_consumed_exclusion':len(hold),'r39c_consumed_rows':100,'r39d_consumed_rows':100,'r39e_locked_rows':len(selected),'r39e_identity_set_sha256':sha,'overlap_with_r39c':0,'overlap_with_r39d':0,'r39e_min_datetime':min(x['dt'] for x in selected).isoformat(),'r39e_max_datetime':max(x['dt'] for x in selected).isoformat(),'no_label_audit':{'score_columns_present_in_python_input':False,'score_values_accessed':0,'result_values_accessed':0,'prediction_metrics_computed':0,'model_fit':0,'threshold_selection':0},'hard_limits':reg['hard_limits']};(a.out_dir/'identity_lock_receipt_r39e.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
