#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
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


def build_eligible(path: Path):
    expected=['match_id','date_start','competition_name','date_created','home_team_name','away_team_name','home_team_odd','away_team_odd','tie_odd']
    identities={}; inconsistent=set()
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if list(reader.fieldnames or [])!=expected: raise RuntimeError(f'unexpected odds schema {reader.fieldnames}')
        for r in reader:
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None: continue
            ident=(r['date_start'].strip(),r['competition_name'].strip(),r['home_team_name'].strip(),r['away_team_name'].strip())
            if mid not in identities: identities[mid]=ident
            elif identities[mid]!=ident: inconsistent.add(mid)
    times=defaultdict(set); timestamp_odds=defaultdict(dict); conflicted=set()
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        for r in reader:
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None or mid in inconsistent: continue
            if not obs<ko: continue
            if not all(valid_odd(r[k]) for k in ('home_team_odd','away_team_odd','tie_odd')): continue
            odds=(float(r['home_team_odd']),float(r['tie_odd']),float(r['away_team_odd']))
            times[mid].add(obs)
            prior=timestamp_odds[mid].get(obs)
            if prior is None: timestamp_odds[mid][obs]=odds
            elif prior!=odds: conflicted.add(mid)
    rows=[]
    for mid,ident in identities.items():
        if mid in inconsistent or mid in conflicted: continue
        ko=dt(ident[0]); ts=sorted(times.get(mid,set()))
        if len(ts)<3: continue
        picked=[]
        for hh in (24,6,1):
            c=[x for x in ts if x<=ko-timedelta(hours=hh)]
            if not c: break
            picked.append(c[-1])
        if len(picked)!=3 or len(set(picked))<2: continue
        rows.append({'match_id':mid,'kickoff':ko,'competition':ident[1],'home':ident[2],'away':ident[3]})
    rows.sort(key=lambda r:(r['kickoff'],mid_key(r['match_id'])))
    return rows, {'identity_inconsistent_matches':len(inconsistent),'conflicted_match_ids':len(conflicted)}


def read_result_ids(path: Path):
    counts=Counter(); rows=0
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.reader(f)
        first=next(reader,None)
        if first!=['match_id']: raise RuntimeError(f'sanitized result id header changed: {first}')
        for row in reader:
            if not row: continue
            mid=str(row[0]).strip()
            if not mid: continue
            rows+=1; counts[mid]+=1
    return rows,counts


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True)
    ap.add_argument('--result-header-file',type=Path,required=True)
    ap.add_argument('--result-id-file',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args(); a.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(a.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_RESULT_HEADER_AND_IDENTITY_ONLY_AUDIT'
    assert reg['hard_limits']['score_or_outcome_values_allowed'] is False
    assert reg['hard_limits']['model_fit_allowed'] is False
    assert reg['hard_limits']['prediction_metrics_allowed'] is False
    assert hfile(a.odds_csv)==reg['source_binding']['odds_csv_sha256']

    header_text=a.result_header_file.read_text(encoding='utf-8-sig').strip('\r\n')
    header=next(csv.reader([header_text]))
    eligible,audit=build_eligible(a.odds_csv)
    part=reg['frozen_external_partition']; fit_n=part['fit_count']; policy_n=part['policy_count']; blind_n=part['blind_count']
    fit=eligible[:fit_n]; policy=eligible[fit_n:fit_n+policy_n]; blind=eligible[fit_n+policy_n:]
    identity_hashes={'all':digest_rows(eligible),'fit':digest_rows(fit),'policy':digest_rows(policy),'blind':digest_rows(blind)}
    expected_hashes={'all':part['all_identity_sha256'],'fit':part['fit_identity_sha256'],'policy':part['policy_identity_sha256'],'blind':part['blind_identity_sha256']}

    result_rows,result_counts=read_result_ids(a.result_id_file)
    result_ids=set(result_counts)
    duplicate_ids=sum(1 for v in result_counts.values() if v>1)
    covered_fit=[r for r in fit if r['match_id'] in result_ids]
    covered_policy=[r for r in policy if r['match_id'] in result_ids]
    covered_blind=[r for r in blind if r['match_id'] in result_ids]
    covered_all=covered_fit+covered_policy+covered_blind
    coverage=len(covered_all)/len(eligible) if eligible else 0.0
    covered_hashes={'all':digest_rows(covered_all),'fit':digest_rows(covered_fit),'policy':digest_rows(covered_policy),'blind':digest_rows(covered_blind)}

    cg=reg['coverage_gate']
    gates={
        'frozen_eligible_count_exact':len(eligible)==part['eligible_count'],
        'frozen_split_counts_exact':len(fit)==fit_n and len(policy)==policy_n and len(blind)==blind_n,
        'frozen_identity_hashes_exact':identity_hashes==expected_hashes,
        'header_first_field_match_id':bool(header) and header[0]==cg['header_first_field_must_equal'],
        'eligible_result_id_coverage':coverage>=cg['eligible_result_id_coverage_min'],
        'duplicate_result_match_ids_zero':duplicate_ids==cg['duplicate_result_match_ids_allowed'],
        'score_or_outcome_values_accessed_zero':True,
        'model_fits_zero':True,'prediction_metrics_zero':True,'thresholds_selected_zero':True,
    }
    passed=all(gates.values())
    status='PASS_R40I0_RESULT_HEADER_AND_IDENTITY_AUDIT' if passed else 'STOP_R40I0_RESULT_IDENTITY_AUDIT'
    result={
        'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
        'parent_prediction_hashes':{k:reg['parent_r40h1'][f'prediction_hash_{k}'] for k in ('all','fit','policy','blind')},
        'result_header':{'columns':header,'column_count':len(header),'first_field':header[0] if header else None},
        'result_identity_source':{'sanitized_rows':result_rows,'unique_match_ids':len(result_ids),'duplicate_match_ids':duplicate_ids,'only_first_csv_field_visible_to_research_python':True},
        'frozen_external':{'eligible_count':len(eligible),'identity_hashes':identity_hashes,'source_audit':audit},
        'result_covered_partition':{'coverage_rate':coverage,'count':len(covered_all),'split_counts':{'fit':len(covered_fit),'policy':len(covered_policy),'blind':len(covered_blind)},'missing_counts':{'fit':len(fit)-len(covered_fit),'policy':len(policy)-len(covered_policy),'blind':len(blind)-len(covered_blind)},'identity_hashes':covered_hashes,'no_replacement':True,'no_rebalancing':True},
        'gates':gates,
        'no_label_audit':{'score_or_outcome_values_accessed':0,'model_fits':0,'prediction_metrics':0,'thresholds_selected':0,'internal_fifth_fixed100_accessed':0},
        'next_stage_authorization':'ONE_TIME_EXTERNAL_RESULT_EVALUATION_PREREGISTRATION_ALLOWED' if passed else 'STOP_NO_RESULT_VALUE_ACCESS','hard_limits':reg['hard_limits']
    }
    (a.out_dir/'result_identity_audit_status_r40i0.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
