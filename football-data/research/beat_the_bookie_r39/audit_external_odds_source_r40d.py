#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, hashlib, json, math
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def open_text(path: Path):
    for enc in ('utf-8-sig', 'latin-1'):
        f = path.open('r', encoding=enc, newline='')
        try:
            f.read(8192); f.seek(0)
            return f, enc
        except UnicodeDecodeError:
            f.close()
    raise RuntimeError('cannot decode odds CSV')


def parse_dt(v: str):
    s = str(v or '').strip()
    if not s:
        return None
    for x in (s, s.replace('Z', '+00:00')):
        try: return datetime.fromisoformat(x)
        except ValueError: pass
    for fmt in (
        '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
        '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
        '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y',
    ):
        try: return datetime.strptime(s, fmt)
        except ValueError: pass
    return None


def numeric(v: str):
    try:
        x = float(str(v).strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def is_time_name(name: str) -> bool:
    n = name.casefold()
    return any(t in n for t in ('date','time','timestamp','created','updated','change'))


def is_id_name(name: str) -> bool:
    n = name.casefold().replace(' ', '_')
    return n in {'id','match','match_id','matchid','fixture_id','event_id'} or ('match' in n and 'id' in n)


def is_odds_name(name: str) -> bool:
    n = name.casefold()
    return any(t in n for t in ('odd','home','draw','away'))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True)
    ap.add_argument('--archive-sha256',required=True)
    ap.add_argument('--archive-members-json',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args(); args.out_dir.mkdir(parents=True,exist_ok=True)

    reg=json.loads(args.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_ZERO_LABEL_EXTERNAL_ODDS_SOURCE_AUDIT'
    assert reg['hard_limits']['result_labels_allowed'] is False
    assert reg['hard_limits']['model_fit_allowed'] is False
    assert reg['hard_limits']['fifth_fixed100_authorized'] is False

    members=json.loads(args.archive_members_json.read_text(encoding='utf-8'))
    result_members=[x for x in members if 'result' in x.casefold()]
    odds_members=[x for x in members if 'odd' in x.casefold() and x.casefold().endswith('.csv')]

    f,encoding=open_text(args.odds_csv)
    with f:
        reader=csv.DictReader(f); fields=list(reader.fieldnames or [])
        if not fields: raise RuntimeError('empty odds CSV header')
        time_fields=[k for k in fields if is_time_name(k)]
        id_fields=[k for k in fields if is_id_name(k)]
        odds_name_fields=[k for k in fields if is_odds_name(k)]
        stats={k:{'nonempty':0,'numeric':0,'gt1':0,'datetime':0,'dt_min':None,'dt_max':None} for k in fields}
        unique_ids={k:set() for k in id_fields}
        rows=0; profile_limit=200000
        for row in reader:
            rows+=1
            if rows>profile_limit: continue
            for k in fields:
                v=str(row.get(k,'') or '').strip()
                if not v: continue
                s=stats[k]; s['nonempty']+=1
                x=numeric(v)
                if x is not None:
                    s['numeric']+=1
                    if x>1.0: s['gt1']+=1
                if k in unique_ids and len(unique_ids[k])<100000: unique_ids[k].add(v)
            for k in time_fields:
                v=str(row.get(k,'') or '').strip()
                if not v: continue
                d=parse_dt(v)
                if d is None: continue
                s=stats[k]; s['datetime']+=1; ds=d.isoformat()
                s['dt_min']=ds if s['dt_min'] is None or ds<s['dt_min'] else s['dt_min']
                s['dt_max']=ds if s['dt_max'] is None or ds>s['dt_max'] else s['dt_max']

    profile={}; timestamp_like=[]; odds_like=[]
    for k,s in stats.items():
        non=s['nonempty']; nr=s['numeric']/non if non else 0.0; gr=s['gt1']/s['numeric'] if s['numeric'] else 0.0; dr=s['datetime']/non if non else 0.0
        profile[k]={'nonempty_profile_rows':non,'numeric_rate_nonempty':nr,'numeric_gt1_rate':gr,'datetime_parse_rate_nonempty':dr,'unique_values_sample_capped':len(unique_ids[k]) if k in unique_ids else None,'datetime_min':s['dt_min'],'datetime_max':s['dt_max']}
        if k in time_fields and dr>=0.50: timestamp_like.append(k)
        if k in odds_name_fields and nr>=0.80 and gr>=0.50: odds_like.append(k)

    selected_id_col=max(id_fields,key=lambda k:len(unique_ids[k]),default=None)
    unique_match_estimate=len(unique_ids[selected_id_col]) if selected_id_col else 0
    gate=reg['source_screen']
    gates={
        'odds_rows_minimum':rows>=int(gate['minimum_odds_rows']),
        'unique_match_ids_minimum_in_profile':unique_match_estimate>=int(gate['minimum_unique_match_ids']),
        'timestamp_like_column_present':len(timestamp_like)>=1,
        'three_odds_like_columns_present':len(odds_like)>=3,
        'result_file_values_accessed_zero':True,'model_fits_zero':True,'prediction_metrics_zero':True,'identity_locks_zero':True,
    }
    passed=all(gates.values()); status='PASS_R40D_ZERO_LABEL_EXTERNAL_ODDS_SOURCE_SCREEN' if passed else 'STOP_R40D_EXTERNAL_ODDS_SOURCE_NOT_READY'
    out={
        'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
        'source':{'dataset_slug':reg['source_binding']['dataset_slug'],'archive_sha256':args.archive_sha256,'archive_members':members,'odds_members':odds_members,'result_members_present_but_not_extracted':result_members,'odds_csv_sha256':sha256(args.odds_csv),'odds_csv_bytes':args.odds_csv.stat().st_size,'encoding':encoding,'rows':rows,'profile_rows':min(rows,profile_limit),'fields':fields},
        'schema_profile':profile,'candidate_timestamp_columns':timestamp_like,'candidate_odds_columns':odds_like,'candidate_match_id_columns':id_fields,'selected_match_id_column_for_source_screen':selected_id_col,'unique_match_ids_profile_capped':unique_match_estimate,'gates':gates,
        'no_label_audit':{'result_file_extracted':False,'result_values_accessed':0,'model_fits':0,'prediction_metrics':0,'thresholds_selected':0,'identity_locks_created':0,'fifth_fixed100_accessed':0},
        'next_stage_authorization':'ZERO_LABEL_KICKOFF_IDENTITY_AUDIT_ONLY_FIFTH100_FORBIDDEN' if passed else 'CLOSE_SOURCE_OR_REPAIR_SOURCE_ACCESS_NO_LABELS','hard_limits':reg['hard_limits']}
    (args.out_dir/'external_odds_source_audit_status_r40d.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
