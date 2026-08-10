#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
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


def digest_rows(rows) -> str:
    h=hashlib.sha256()
    for r in rows:
        h.update(f"{r['match_id']}|{r['kickoff'].isoformat()}|{r['competition']}|{r['home']}|{r['away']}\n".encode('utf-8'))
    return h.hexdigest()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args(); a.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(a.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_ZERO_LABEL_CONFLICT_QUARANTINE_AUDIT'
    assert reg['parent_r40h']['verdict']=='STOP_R40H_CROSS_SOURCE_FREEZE'
    assert reg['parent_r40h']['verdict_must_not_be_overridden'] is True
    assert reg['hard_limits']['external_result_values_allowed'] is False
    assert reg['hard_limits']['model_fit_allowed'] is False
    assert hfile(a.odds_csv)==reg['source_binding']['odds_csv_sha256']

    expected=['match_id','date_start','competition_name','date_created','home_team_name','away_team_name','home_team_odd','away_team_odd','tie_odd']
    identities={}; inconsistent=set()
    with a.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if list(reader.fieldnames or [])!=expected: raise RuntimeError(f'unexpected schema {reader.fieldnames}')
        for r in reader:
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None: continue
            ident=(r['date_start'].strip(),r['competition_name'].strip(),r['home_team_name'].strip(),r['away_team_name'].strip())
            if mid not in identities: identities[mid]=ident
            elif identities[mid]!=ident: inconsistent.add(mid)

    times=defaultdict(set)
    timestamp_odds=defaultdict(dict)
    conflict_rows=0; conflict_match_ids=set(); identical_duplicate_rows=0
    with a.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        for r in reader:
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None or mid in inconsistent: continue
            if not obs<ko: continue
            if not all(valid_odd(r[k]) for k in ('home_team_odd','away_team_odd','tie_odd')): continue
            odds=(float(r['home_team_odd']),float(r['tie_odd']),float(r['away_team_odd']))
            times[mid].add(obs)
            prior=timestamp_odds[mid].get(obs)
            if prior is None:
                timestamp_odds[mid][obs]=odds
            elif prior==odds:
                identical_duplicate_rows+=1
            else:
                conflict_rows+=1; conflict_match_ids.add(mid)

    original=[]
    for mid,ident in identities.items():
        if mid in inconsistent: continue
        ko=dt(ident[0]); ts=sorted(times.get(mid,set()))
        if len(ts)<3: continue
        selected=[]
        for h in (24,6,1):
            c=[x for x in ts if x<=ko-timedelta(hours=h)]
            if not c: break
            selected.append(c[-1])
        if len(selected)!=3 or len(set(selected))<2: continue
        original.append({'match_id':mid,'kickoff':ko,'competition':ident[1],'home':ident[2],'away':ident[3]})
    original.sort(key=lambda r:(r['kickoff'],mid_key(r['match_id'])))

    p=reg['parent_r40g_partition']; fit_n=p['fit_count']; policy_n=p['policy_count']; blind_n=p['blind_count']
    original_fit=original[:fit_n]; original_policy=original[fit_n:fit_n+policy_n]; original_blind=original[fit_n+policy_n:]
    original_hashes={
        'all':digest_rows(original),'fit':digest_rows(original_fit),'policy':digest_rows(original_policy),'blind':digest_rows(original_blind)
    }
    expected_hashes={
        'all':p['all_identity_sha256'],'fit':p['fit_identity_sha256'],'policy':p['policy_identity_sha256'],'blind':p['blind_identity_sha256']
    }

    original_ids={r['match_id'] for r in original}
    eligible_conflict_ids=conflict_match_ids & original_ids
    revised_fit=[r for r in original_fit if r['match_id'] not in eligible_conflict_ids]
    revised_policy=[r for r in original_policy if r['match_id'] not in eligible_conflict_ids]
    revised_blind=[r for r in original_blind if r['match_id'] not in eligible_conflict_ids]
    revised_all=revised_fit+revised_policy+revised_blind
    removed_by_block={
        'fit':len(original_fit)-len(revised_fit),'policy':len(original_policy)-len(revised_policy),'blind':len(original_blind)-len(revised_blind)
    }
    revised_hashes={
        'all':digest_rows(revised_all),'fit':digest_rows(revised_fit),'policy':digest_rows(revised_policy),'blind':digest_rows(revised_blind)
    }
    gates={
        'original_count_exact':len(original)==p['eligible_count'],
        'original_split_counts_exact':len(original_fit)==fit_n and len(original_policy)==policy_n and len(original_blind)==blind_n,
        'original_r40g_hashes_exact':original_hashes==expected_hashes,
        'observed_conflicting_rows_exact':conflict_rows==reg['pass_gate']['observed_conflicting_rows_exact'],
        'retained_total_min':len(revised_all)>=reg['pass_gate']['retained_total_min'],
        'no_replacement_or_rebalancing':len(revised_all)==len(original)-len(eligible_conflict_ids),
        'result_values_accessed_zero':True,'model_fits_zero':True,'prediction_metrics_zero':True,'thresholds_selected_zero':True,
    }
    passed=all(gates.values())
    status='PASS_R40H0_ZERO_LABEL_CONFLICT_QUARANTINE' if passed else 'STOP_R40H0_CONFLICT_QUARANTINE'
    result={
        'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
        'parent_r40h_verdict_preserved':'STOP_R40H_CROSS_SOURCE_FREEZE',
        'source':{'odds_csv_sha256':hfile(a.odds_csv),'identity_inconsistent_matches':len(inconsistent),'identical_duplicate_timestamp_rows':identical_duplicate_rows,'conflicting_duplicate_timestamp_rows':conflict_rows,'conflicted_match_ids_total':len(conflict_match_ids),'conflicted_match_ids_in_r40g_eligible':len(eligible_conflict_ids)},
        'original_r40g':{'count':len(original),'hashes':original_hashes,'split_counts':{'fit':len(original_fit),'policy':len(original_policy),'blind':len(original_blind)}},
        'revised_no_replacement_partition':{'count':len(revised_all),'split_counts':{'fit':len(revised_fit),'policy':len(revised_policy),'blind':len(revised_blind)},'removed_by_block':removed_by_block,'hashes':revised_hashes,'boundary_rule':'original R40G block membership retained; conflicted matches deleted with no replacement/rebalancing'},
        'gates':gates,
        'no_label_audit':{'external_result_file_extracted':False,'external_result_values_accessed':0,'model_fits':0,'prediction_metrics':0,'thresholds_selected':0,'internal_fifth_fixed100_accessed':0},
        'next_stage_authorization':'R40H1_FREEZE_PREREGISTRATION_ALLOWED_EXTERNAL_RESULTS_STILL_FORBIDDEN' if passed else 'STOP_NO_EXTERNAL_RESULTS','hard_limits':reg['hard_limits']
    }
    (a.out_dir/'external_conflict_quarantine_status_r40h0.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
