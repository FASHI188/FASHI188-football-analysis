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
    for x in (s, s.replace('Z','+00:00')):
        try:
            return datetime.fromisoformat(x).replace(tzinfo=None)
        except ValueError:
            pass
    for fmt in (
        '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
        '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
        '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y',
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
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
    return {'n':n,'min':x[0],'p01':q(.01),'p05':q(.05),'p10':q(.10),'p25':q(.25),'p50':q(.50),'p75':q(.75),'p90':q(.90),'p95':q(.95),'p99':q(.99),'max':x[-1],'mean':sum(x)/n}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args(); args.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(args.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_ZERO_LABEL_EXTERNAL_ODDS_PIT_COVERAGE'
    assert reg['hard_limits']['result_labels_allowed'] is False
    assert reg['hard_limits']['result_file_extraction_allowed'] is False
    assert reg['hard_limits']['model_fit_allowed'] is False
    assert hfile(args.odds_csv)==reg['parent_source_audit']['odds_csv_sha256']

    expected=['match_id','date_start','competition_name','date_created','home_team_name','away_team_name','home_team_odd','away_team_odd','tie_odd']
    identity={}; inconsistent=set(); pre_times=defaultdict(set); cutoff_ok=defaultdict(lambda:{24:False,6:False,1:False})
    total=0; parse_bad=0; odds_bad=0; strict_pre=0; same_or_post=0; lead_hours=[]
    min_kick=None; max_kick=None; min_obs=None; max_obs=None

    with args.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if list(reader.fieldnames or [])!=expected:
            raise RuntimeError(f'unexpected schema {reader.fieldnames}')
        for r in reader:
            total+=1
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None:
                parse_bad+=1; continue
            if not all(valid_odd(r[k]) for k in ('home_team_odd','away_team_odd','tie_odd')):
                odds_bad+=1
            ident=(r['date_start'].strip(),r['competition_name'].strip(),r['home_team_name'].strip(),r['away_team_name'].strip())
            if mid not in identity: identity[mid]=ident
            elif identity[mid]!=ident: inconsistent.add(mid)
            ks=ko.isoformat(); os=obs.isoformat()
            min_kick=ks if min_kick is None or ks<min_kick else min_kick; max_kick=ks if max_kick is None or ks>max_kick else max_kick
            min_obs=os if min_obs is None or os<min_obs else min_obs; max_obs=os if max_obs is None or os>max_obs else max_obs
            delta=(ko-obs).total_seconds()/3600.0
            lead_hours.append(delta)
            if obs<ko:
                strict_pre+=1; pre_times[mid].add(obs)
                for h in (24,6,1):
                    if obs<=ko-timedelta(hours=h): cutoff_ok[mid][h]=True
            else:
                same_or_post+=1

    unique_matches=len(identity)
    consistent=unique_matches-len(inconsistent)
    pre_counts=[len(pre_times.get(mid,set())) for mid in identity]
    ge2=sum(c>=2 for c in pre_counts)
    cutoff_counts={str(h):sum(cutoff_ok[mid][h] for mid in identity) for h in (24,6,1)}
    all_three=sum(all(cutoff_ok[mid][h] for h in (24,6,1)) for mid in identity)
    valid_time_rows=total-parse_bad
    strict_rate=strict_pre/valid_time_rows if valid_time_rows else 0.0
    consistent_rate=consistent/unique_matches if unique_matches else 0.0
    gate=reg['coverage_gate']
    gates={
        'exact_odds_rows':total==reg['parent_source_audit']['expected_odds_rows'],
        'exact_unique_match_ids':unique_matches==reg['parent_source_audit']['expected_unique_match_ids'],
        'strict_pre_kickoff_row_rate':strict_rate>=gate['strict_pre_kickoff_row_rate_min'],
        'identity_consistent_match_rate':consistent_rate>=gate['identity_consistent_match_rate_min'],
        'matches_with_at_least_2_strict_prior_snapshots':ge2>=gate['matches_with_at_least_2_strict_prior_snapshots_min'],
        'matches_with_t24_t6_t1_all_available':all_three>=gate['matches_with_t24_t6_t1_all_available_min'],
        'result_values_accessed_zero':True,'model_fits_zero':True,'prediction_metrics_zero':True,'fifth100_zero':True,
    }
    passed=all(gates.values()); status='PASS_R40E_ZERO_LABEL_EXTERNAL_ODDS_PIT_COVERAGE' if passed else 'STOP_R40E_EXTERNAL_ODDS_PIT_COVERAGE'
    out={
        'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
        'source':{'odds_csv_sha256':hfile(args.odds_csv),'rows':total,'unique_match_ids':unique_matches,'kickoff_min':min_kick,'kickoff_max':max_kick,'observation_min':min_obs,'observation_max':max_obs},
        'quality':{'datetime_parse_bad_rows':parse_bad,'invalid_odds_triple_rows':odds_bad,'identity_inconsistent_matches':len(inconsistent),'identity_consistent_match_rate':consistent_rate,'strict_pre_kickoff_rows':strict_pre,'same_or_post_kickoff_rows':same_or_post,'strict_pre_kickoff_row_rate':strict_rate},
        'trajectory_coverage':{'matches_with_ge2_distinct_strict_prior_snapshots':ge2,'cutoff_available_matches':cutoff_counts,'matches_with_t24_t6_t1_all_available':all_three,'pre_snapshot_count_distribution':qstats(pre_counts),'observation_lead_hours_distribution_all_parsed_rows':qstats(lead_hours)},
        'gates':gates,
        'no_label_audit':{'result_file_extracted':False,'result_values_accessed':0,'model_fits':0,'prediction_metrics':0,'thresholds_selected':0,'identity_locks_created':0,'fifth_fixed100_accessed':0},
        'next_stage_authorization':'PREHOLDOUT_EXTERNAL_DOMAIN_EXPERIMENT_PREREGISTRATION_ALLOWED_FIFTH100_FORBIDDEN' if passed else 'STOP_NO_LABEL_EXPERIMENT_AUTHORIZATION','hard_limits':reg['hard_limits']}
    (args.out_dir/'external_odds_pit_coverage_status_r40e.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
