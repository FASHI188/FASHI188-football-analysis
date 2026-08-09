#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,hashlib,json,math,re
from datetime import datetime,timezone
from pathlib import Path

COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES=('home','draw','away')


def htxt(s:str)->str:
    return hashlib.sha256(s.encode()).hexdigest()


def parse_dt(d:str,t:str)->datetime:
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try:return datetime.strptime(f'{d} {t}',fmt)
        except ValueError:pass
    raise ValueError(f'bad datetime {d} {t}')


def valid(v:str)->bool:
    x=v.strip().casefold()
    if not x or x in {'nan','na','null','none'}:return False
    try:f=float(x)
    except ValueError:return False
    return math.isfinite(f) and f>1.0


def suffix(hours:int)->int:
    return 71-hours


def parse_mapping(header:list[str])->dict[tuple[int,int,str],int]:
    m={}
    for i,name in enumerate(header[3:],3):
        g=COL_RE.match(name)
        if g:m[(int(g.group(2)),int(g.group(3)),g.group(1))]=i
    if len(m)!=32*72*3:raise RuntimeError(f'odds mapping {len(m)}')
    return m


def eligible(row:list[str],mapping:dict[tuple[int,int,str],int],cuts:tuple[int,...],minimum:int)->bool:
    complete=[]
    for h in cuts:
        s=suffix(h);books=set()
        for b in range(1,33):
            vals=[row[mapping[(b,s,o)]] for o in OUTCOMES]
            if all(valid(v) for v in vals):books.add(b)
        complete.append(books)
    return len(set.intersection(*complete))>=minimum


def load_rows(source_dir:Path,cuts:tuple[int,...],minimum:int)->list[dict]:
    rows=[];seen=set()
    for source in ('odds_series_no_scores.csv.gz','odds_series_b_no_scores.csv.gz'):
        p=source_dir/source
        with gzip.open(p,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);header=next(r)
            if header[:3]!=['match_id','match_date','match_time']:raise RuntimeError(header[:5])
            if 'score_home' in header or 'score_away' in header:raise RuntimeError('score columns present')
            mapping=parse_mapping(header)
            for row in r:
                if len(row)!=len(header) or not row[0].strip():continue
                if not eligible(row,mapping,cuts,minimum):continue
                identity=f"{source.replace('_no_scores','')}|{row[0].strip()}"
                if identity in seen:raise RuntimeError(f'duplicate identity {identity}')
                seen.add(identity);rows.append({'identity':identity,'dt':parse_dt(row[1],row[2])})
    return rows


def set_sha(rows:list[dict])->str:
    ids=sorted(x['identity'] for x in rows)
    return htxt('\n'.join(ids)+'\n')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--source-dir',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text())
    cuts=tuple(reg['source']['required_cutoffs_hours']);minimum=int(reg['source']['minimum_common_bookmakers'])
    rows=load_rows(a.source_dir,cuts,minimum)
    start=datetime.fromisoformat(reg['holdout']['holdout_start'])
    training=[x for x in rows if x['dt']<start];holdout=[x for x in rows if x['dt']>=start]
    old_seed=int(reg['holdout']['consumed_r39c_seed'])
    old=sorted(holdout,key=lambda x:htxt(f"{old_seed}|{x['identity']}"))[:int(reg['holdout']['consumed_r39c_rows'])]
    old_sha=set_sha(old)
    if old_sha!=reg['holdout']['consumed_r39c_identity_set_sha256']:
        raise RuntimeError(f'R39C identity drift {old_sha}')
    old_ids={x['identity'] for x in old}
    remaining=[x for x in holdout if x['identity'] not in old_ids]
    seed=int(reg['holdout']['r39d_seed']);n=int(reg['holdout']['r39d_rows'])
    selected=sorted(remaining,key=lambda x:htxt(f"{seed}|{x['identity']}"))[:n]
    if len(selected)!=n:raise RuntimeError(f'R39D rows {len(selected)}')
    new_ids={x['identity'] for x in selected}
    if old_ids & new_ids:raise RuntimeError('R39C/R39D overlap')
    new_sha=set_sha(selected)
    a.out_dir.mkdir(parents=True,exist_ok=True)
    receipt={
      'schema_version':reg['schema_version'],
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),
      'status':'LOCKED_R39D_FIXED100_NO_LABELS',
      'eligible_total_rows':len(rows),
      'training_eligible_rows':len(training),
      'holdout_eligible_rows':len(holdout),
      'consumed_r39c_rows':len(old),
      'consumed_r39c_identity_set_sha256':old_sha,
      'remaining_holdout_rows_after_r39c':len(remaining),
      'locked_r39d_rows':len(selected),
      'r39d_identity_set_sha256':new_sha,
      'r39c_r39d_overlap_rows':0,
      'r39d_min_datetime':min(x['dt'] for x in selected).isoformat(),
      'r39d_max_datetime':max(x['dt'] for x in selected).isoformat(),
      'no_label_audit':{
        'score_columns_present_in_python_input':False,
        'score_values_accessed':0,
        'result_values_accessed':0,
        'matches_files_opened':0,
        'prediction_metrics_computed':0,
        'model_fit':0,
        'threshold_selection':0
      },
      'hard_limits':reg['hard_limits']
    }
    (a.out_dir/'identity_lock_receipt_r39d.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
