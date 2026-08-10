#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, hashlib, json, math
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


def qstats(values):
    if not values: return {'n':0}
    x=sorted(values); n=len(x)
    def q(p): return x[min(n-1,max(0,int(round(p*(n-1)))))]
    return {'n':n,'min':x[0],'p05':q(.05),'p10':q(.10),'p25':q(.25),'p50':q(.50),'p75':q(.75),'p90':q(.90),'p95':q(.95),'max':x[-1],'mean':sum(x)/n}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args(); args.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(args.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_ZERO_LABEL_EXTERNAL_ODDS_QUARANTINE_AUDIT'
    assert reg['parent_r40e']['verdict']=='STOP_R40E_EXTERNAL_ODDS_PIT_COVERAGE'
    assert reg['parent_r40e']['verdict_must_not_be_overridden'] is True
    assert reg['hard_limits']['result_labels_allowed'] is False
    assert reg['hard_limits']['result_file_extraction_allowed'] is False
    assert reg['hard_limits']['model_fit_allowed'] is False
    assert hfile(args.odds_csv)==reg['source_binding']['odds_csv_sha256']

    expected=['match_id','date_start','competition_name','date_created','home_team_name','away_team_name','home_team_odd','away_team_odd','tie_odd']
    identities={}; inconsistent=set(); raw_rows=0; parse_bad=0
    with args.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if list(reader.fieldnames or [])!=expected: raise RuntimeError(f'unexpected schema {reader.fieldnames}')
        for r in reader:
            raw_rows+=1
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None:
                parse_bad+=1; continue
            ident=(r['date_start'].strip(),r['competition_name'].strip(),r['home_team_name'].strip(),r['away_team_name'].strip())
            if mid not in identities: identities[mid]=ident
            elif identities[mid]!=ident: inconsistent.add(mid)

    snapshots=defaultdict(dict)
    dropped={'identity_inconsistent_rows':0,'same_or_post_kickoff_rows':0,'invalid_odds_rows':0,'parse_bad_rows_second_pass':0}
    retained_rows=0
    with args.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        for r in reader:
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None:
                dropped['parse_bad_rows_second_pass']+=1; continue
            if mid in inconsistent:
                dropped['identity_inconsistent_rows']+=1; continue
            if not obs<ko:
                dropped['same_or_post_kickoff_rows']+=1; continue
            if not all(valid_odd(r[k]) for k in ('home_team_odd','away_team_odd','tie_odd')):
                dropped['invalid_odds_rows']+=1; continue
            odds=(float(r['home_team_odd']),float(r['away_team_odd']),float(r['tie_odd']))
            snapshots[mid][obs]=odds
            retained_rows+=1

    ge3=0; all_cutoffs=0; distinct_cutoff_ge2=0; eligible=0
    obs_counts=[]; cutoff_distinct_counts=[]; eligible_kickoffs=[]; by_month=Counter()
    for mid,ident in identities.items():
        if mid in inconsistent: continue
        ko=dt(ident[0]); obsmap=snapshots.get(mid,{})
        times=sorted(obsmap)
        obs_counts.append(len(times))
        if len(times)>=3: ge3+=1
        selected=[]; has_all=True
        for h in (24,6,1):
            cutoff=ko-timedelta(hours=h)
            candidates=[x for x in times if x<=cutoff]
            if not candidates:
                has_all=False; break
            selected.append(candidates[-1])
        if has_all:
            all_cutoffs+=1
            d=len(set(selected)); cutoff_distinct_counts.append(d)
            if d>=2: distinct_cutoff_ge2+=1
            if len(times)>=3 and d>=2:
                eligible+=1; eligible_kickoffs.append(ko)
                by_month[ko.strftime('%Y-%m')]+=1

    gate=reg['pass_gate']
    retained_rows_strict_pre=True
    retained_identity_consistent=all(mid not in inconsistent for mid in snapshots)
    retained_odds_valid=True
    gates={
        'raw_rows_exact':raw_rows==reg['parent_r40e']['raw_rows'],
        'raw_match_ids_exact':len(identities)==reg['parent_r40e']['raw_match_ids'],
        'all_retained_rows_strict_pre_kickoff':retained_rows_strict_pre is gate['all_retained_rows_strict_pre_kickoff'],
        'all_retained_matches_identity_consistent':retained_identity_consistent is gate['all_retained_matches_identity_consistent'],
        'all_retained_rows_complete_valid_odds':retained_odds_valid is gate['all_retained_rows_complete_valid_odds'],
        'clean_matches_with_t24_t6_t1_and_ge3_prior':eligible>=gate['clean_matches_with_t24_t6_t1_and_ge3_prior_min'],
        'clean_matches_with_at_least_2_distinct_cutoff_snapshot_times':distinct_cutoff_ge2>=gate['clean_matches_with_at_least_2_distinct_cutoff_snapshot_times_min'],
        'result_values_accessed_zero':True,'model_fits_zero':True,'prediction_metrics_zero':True,'identity_locks_zero':True,
    }
    passed=all(gates.values())
    status='PASS_R40F_ZERO_LABEL_EXTERNAL_ODDS_QUARANTINE' if passed else 'STOP_R40F_EXTERNAL_ODDS_QUARANTINE_INSUFFICIENT'
    out={
        'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
        'parent_r40e_verdict_preserved':'STOP_R40E_EXTERNAL_ODDS_PIT_COVERAGE',
        'source':{'odds_csv_sha256':hfile(args.odds_csv),'raw_rows':raw_rows,'raw_match_ids':len(identities),'datetime_parse_bad_rows_first_pass':parse_bad},
        'quarantine':{'identity_inconsistent_match_ids_dropped':len(inconsistent),'dropped_rows':dropped,'retained_strict_pre_valid_rows':retained_rows,'retained_match_ids_with_any_snapshot':len(snapshots)},
        'coverage':{'matches_ge3_distinct_strict_prior_observations':ge3,'matches_with_t24_t6_t1_all_available':all_cutoffs,'matches_with_at_least_2_distinct_cutoff_snapshot_times':distinct_cutoff_ge2,'clean_trajectory_eligible_matches':eligible,'strict_prior_observation_count_distribution':qstats(obs_counts),'distinct_cutoff_snapshot_time_distribution_when_all_cutoffs_available':qstats(cutoff_distinct_counts),'eligible_kickoff_min':min(eligible_kickoffs).isoformat() if eligible_kickoffs else None,'eligible_kickoff_max':max(eligible_kickoffs).isoformat() if eligible_kickoffs else None,'eligible_matches_by_month':dict(sorted(by_month.items()))},
        'gates':gates,
        'no_label_audit':{'result_file_extracted':False,'result_values_accessed':0,'model_fits':0,'prediction_metrics':0,'thresholds_selected':0,'identity_locks_created':0,'fifth_fixed100_accessed':0},
        'next_stage_authorization':'ZERO_LABEL_CHRONOLOGICAL_SPLIT_FREEZE_ONLY_RESULTS_STILL_FORBIDDEN' if passed else 'CLOSE_EXTERNAL_SOURCE_NO_RESULTS','hard_limits':reg['hard_limits']}
    (args.out_dir/'external_odds_quarantine_status_r40f.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
