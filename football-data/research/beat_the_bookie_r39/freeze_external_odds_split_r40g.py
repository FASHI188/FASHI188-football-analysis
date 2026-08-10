#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, hashlib, json, math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def hfile(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def dt(v: str):
    s=str(v or '').strip()
    if not s: return None
    for x in (s,s.replace('Z','+00:00')):
        try: return datetime.fromisoformat(x).replace(tzinfo=None)
        except ValueError: pass
    for fmt in (
        '%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M','%Y-%m-%d',
        '%d/%m/%Y %H:%M:%S','%d/%m/%Y %H:%M','%d/%m/%Y',
        '%m/%d/%Y %H:%M:%S','%m/%d/%Y %H:%M','%m/%d/%Y',
    ):
        try: return datetime.strptime(s,fmt)
        except ValueError: pass
    return None


def valid_odd(v: str) -> bool:
    try:
        x=float(str(v).strip())
        return math.isfinite(x) and x>1.0
    except Exception:
        return False


def mid_key(mid: str):
    try: return (0,int(mid))
    except ValueError: return (1,mid)


def digest_rows(rows):
    h=hashlib.sha256()
    for r in rows:
        line=f"{r['match_id']}|{r['kickoff'].isoformat()}|{r['competition']}|{r['home']}|{r['away']}\n"
        h.update(line.encode('utf-8'))
    return h.hexdigest()


def bounds(rows):
    return {
        'count':len(rows),
        'kickoff_min':rows[0]['kickoff'].isoformat() if rows else None,
        'kickoff_max':rows[-1]['kickoff'].isoformat() if rows else None,
        'identity_sha256':digest_rows(rows),
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args(); args.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(args.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_ZERO_LABEL_EXTERNAL_ODDS_CHRONOLOGICAL_SPLIT_FREEZE'
    assert reg['hard_limits']['result_labels_allowed'] is False
    assert reg['hard_limits']['result_file_extraction_allowed'] is False
    assert reg['hard_limits']['model_fit_allowed'] is False
    assert hfile(args.odds_csv)==reg['source_binding']['odds_csv_sha256']

    expected=['match_id','date_start','competition_name','date_created','home_team_name','away_team_name','home_team_odd','away_team_odd','tie_odd']
    identities={}; inconsistent=set()
    with args.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if list(reader.fieldnames or [])!=expected: raise RuntimeError(f'unexpected schema {reader.fieldnames}')
        for r in reader:
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None: continue
            ident=(r['date_start'].strip(),r['competition_name'].strip(),r['home_team_name'].strip(),r['away_team_name'].strip())
            if mid not in identities: identities[mid]=ident
            elif identities[mid]!=ident: inconsistent.add(mid)

    times=defaultdict(set)
    with args.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        for r in reader:
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None or mid in inconsistent: continue
            if not obs<ko: continue
            if not all(valid_odd(r[k]) for k in ('home_team_odd','away_team_odd','tie_odd')): continue
            times[mid].add(obs)

    eligible=[]
    for mid,ident in identities.items():
        if mid in inconsistent: continue
        ko=dt(ident[0]); ts=sorted(times.get(mid,set()))
        if len(ts)<reg['eligibility_contract']['minimum_distinct_strict_prior_observations']: continue
        selected=[]
        for h in reg['eligibility_contract']['cutoffs_hours_before_kickoff']:
            c=[x for x in ts if x<=ko-timedelta(hours=h)]
            if not c: break
            selected.append(c[-1])
        if len(selected)!=3: continue
        if len(set(selected))<reg['eligibility_contract']['minimum_distinct_cutoff_snapshot_times']: continue
        eligible.append({'match_id':mid,'kickoff':ko,'competition':ident[1],'home':ident[2],'away':ident[3]})

    eligible.sort(key=lambda r:(r['kickoff'],mid_key(r['match_id'])))
    n=len(eligible); fit_n=int(n*reg['split_contract']['fit_fraction']); policy_n=int(n*reg['split_contract']['policy_fraction'])
    fit=eligible[:fit_n]; policy=eligible[fit_n:fit_n+policy_n]; blind=eligible[fit_n+policy_n:]
    fit_ids={r['match_id'] for r in fit}; policy_ids={r['match_id'] for r in policy}; blind_ids={r['match_id'] for r in blind}
    overlap=len((fit_ids&policy_ids)|(fit_ids&blind_ids)|(policy_ids&blind_ids))
    gate=reg['pass_gate']
    gates={
        'eligible_matches_exact':n==gate['eligible_matches_exact'],
        'fit_count_exact':len(fit)==gate['fit_count_exact'],
        'policy_count_exact':len(policy)==gate['policy_count_exact'],
        'blind_count_exact':len(blind)==gate['blind_count_exact'],
        'split_overlap_zero':overlap==gate['split_overlap'],
        'result_values_accessed_zero':True,'model_fits_zero':True,'prediction_metrics_zero':True,
    }
    passed=all(gates.values())
    status='PASS_R40G_ZERO_LABEL_EXTERNAL_ODDS_SPLIT_FREEZE' if passed else 'STOP_R40G_EXTERNAL_ODDS_SPLIT_FREEZE'
    out={
        'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
        'source':{'odds_csv_sha256':hfile(args.odds_csv),'identity_inconsistent_matches_excluded':len(inconsistent)},
        'eligible':bounds(eligible),
        'splits':{'fit':bounds(fit),'policy':bounds(policy),'blind':bounds(blind),'overlap':overlap},
        'gates':gates,
        'no_label_audit':{'result_file_extracted':False,'result_values_accessed':0,'model_fits':0,'prediction_metrics':0,'thresholds_selected':0,'fifth_fixed100_accessed':0},
        'next_stage_authorization':'DEVELOPMENT_POLICY_LABEL_PREREGISTRATION_ALLOWED_BLIND_LABELS_STILL_FORBIDDEN' if passed else 'STOP_NO_RESULT_ACCESS','hard_limits':reg['hard_limits']}
    (args.out_dir/'external_odds_split_status_r40g.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
