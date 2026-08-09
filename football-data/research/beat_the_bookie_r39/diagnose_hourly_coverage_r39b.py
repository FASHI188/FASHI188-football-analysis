#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COL_RE = re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')


def hfile(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):
            h.update(c)
    return h.hexdigest()


def valid(value: str) -> bool:
    text=value.strip().casefold()
    if not text or text in {'nan','na','null','none'}:
        return False
    try:
        x=float(text)
    except ValueError:
        return False
    return math.isfinite(x) and x>1.0


def suffix_for(hours_before: int) -> int:
    return 71-int(hours_before)


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--source-dir',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args()
    reg=json.loads(args.registration.read_text(encoding='utf-8'))
    args.out_dir.mkdir(parents=True,exist_ok=True)

    cutoffs=[int(x) for x in reg['cutoffs_hours_before_kickoff']]
    suffixes={h:suffix_for(h) for h in cutoffs}
    rows_seen=0
    unique_ids=set()
    duplicate_ids=0
    file_rows=Counter()
    snapshot_counts={str(h):Counter() for h in cutoffs}
    joint_counts=Counter()
    per_file_joint={}
    header_audit=[]

    for fname in reg['source_files']:
        path=args.source_dir/fname.replace('.csv.gz','_no_scores.csv.gz')
        if not path.exists():
            raise SystemExit(f'missing sanitized source: {path}')
        local_joint=Counter()
        with gzip.open(path,'rt',encoding='utf-8-sig',errors='strict',newline='') as f:
            reader=csv.reader(f)
            header=next(reader)
            if header[:3] != ['match_id','match_date','match_time']:
                raise SystemExit(f'unexpected sanitized identity header in {fname}: {header[:5]}')
            forbidden={'score_home','score_away','score','detailed_score','home_score','away_score'}
            if forbidden & set(header):
                raise SystemExit(f'forbidden score/result columns survived sanitization: {forbidden & set(header)}')
            mapping={}
            for i,name in enumerate(header[3:],start=3):
                m=COL_RE.match(name)
                if m:
                    outcome,book,suffix=m.group(1),int(m.group(2)),int(m.group(3))
                    mapping[(book,suffix,outcome)]=i
            expected=32*72*3
            header_audit.append({'file':fname,'columns':len(header),'recognized_odds_columns':len(mapping)})
            if len(mapping)!=expected:
                raise SystemExit(f'odds column recognition mismatch {fname}: {len(mapping)} != {expected}')

            for row in reader:
                rows_seen += 1
                file_rows[fname] += 1
                if len(row)!=len(header):
                    continue
                match_id=row[0].strip()
                if not match_id:
                    continue
                if match_id in unique_ids:
                    duplicate_ids += 1
                unique_ids.add(match_id)

                complete_by_cutoff={}
                for h,suf in suffixes.items():
                    complete=[]
                    for book in range(1,33):
                        vals=[row[mapping[(book,suf,o)]] for o in ('home','draw','away')]
                        if all(valid(v) for v in vals):
                            complete.append(book)
                    complete_by_cutoff[h]=set(complete)
                    n=len(complete)
                    for threshold in (1,3,5,10,15,20):
                        if n>=threshold:
                            snapshot_counts[str(h)][f'books_ge_{threshold}'] += 1

                for cfg in reg['priority_joint_configs']:
                    sets=[complete_by_cutoff[int(h)] for h in cfg['cutoffs']]
                    common=set.intersection(*sets) if sets else set()
                    if len(common)>=int(cfg['minimum_common_bookies']):
                        joint_counts[cfg['name']] += 1
                        local_joint[cfg['name']] += 1
        per_file_joint[fname]=dict(local_joint)

    target=int(reg['next_stage_gate']['target_matches'])
    pref=reg['next_stage_gate']['preferred_config']
    minimum=reg['next_stage_gate']['minimum_acceptable_config']
    if joint_counts[pref] >= target:
        status='PASS_R39B_PREFERRED_HOURLY_TRAJECTORY_COVERAGE_NO_LABELS'
    elif joint_counts[minimum] >= target:
        status='PASS_R39B_MINIMUM_HOURLY_TRAJECTORY_COVERAGE_NO_LABELS'
    else:
        status='STOP_R39B_HOURLY_TRAJECTORY_COVERAGE_BELOW_100_NO_LABELS'

    payload={
        'schema_version':reg['schema_version'],
        'generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'status':status,
        'source_files':reg['source_files'],
        'suffix_mapping':reg['source_semantics']['suffix_mapping'],
        'cutoff_suffixes':{f'T-{h}h':s for h,s in suffixes.items()},
        'header_audit':header_audit,
        'coverage_audit':{
            'rows_seen':rows_seen,
            'unique_match_ids':len(unique_ids),
            'duplicate_match_ids_across_sources':duplicate_ids,
            'rows_by_file':dict(file_rows),
            'snapshot_counts':{k:dict(v) for k,v in snapshot_counts.items()},
            'priority_joint_counts':dict(joint_counts),
            'priority_joint_counts_by_file':per_file_joint,
            'target_matches':target,
        },
        'no_label_audit':{
            'original_score_columns_removed_before_python':True,
            'score_columns_present_in_python_input':False,
            'matches_files_opened':0,
            'closing_odds_file_opened':0,
            'score_values_accessed':0,
            'result_values_accessed':0,
            'prediction_metrics_computed':0,
            'match_identities_locked':0,
            'odds_values_accessed_for_coverage_only':True,
        },
        'hard_limits':reg['hard_limits'],
    }
    (args.out_dir/'status.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    with (args.out_dir/'coverage_summary.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['type','cutoff_or_config','criterion','matches'])
        for h,c in snapshot_counts.items():
            for criterion,n in sorted(c.items()): w.writerow(['snapshot',f'T-{h}h',criterion,n])
        for name,n in joint_counts.items(): w.writerow(['joint',name,'common_bookmakers',n])
    manifest=[]
    for p in sorted(args.out_dir.iterdir()):
        if p.is_file() and p.name!='manifest.json': manifest.append({'name':p.name,'bytes':p.stat().st_size,'sha256':hfile(p)})
    (args.out_dir/'manifest.json').write_text(json.dumps({'schema':'r39b-coverage-manifest','files':manifest},indent=2),encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
