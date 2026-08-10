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


def htext(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def canonical_sha(obj) -> str:
    return htext(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False))


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


def devig(h,d,a):
    inv=[1.0/float(h),1.0/float(d),1.0/float(a)]
    s=sum(inv)
    return [x/s for x in inv]


def clip(p,lo=1e-8,hi=0.99999999):
    return min(hi,max(lo,float(p)))


def logit(p):
    p=clip(p)
    return math.log(p/(1-p))


def sigmoid(x):
    if x>=0:
        return 1.0/(1.0+math.exp(-x))
    e=math.exp(x)
    return e/(1.0+e)


def entropy(p):
    return -sum(float(x)*math.log(max(float(x),1e-15)) for x in p)


def feature(q24,q6,q1):
    d246=q6[1]-q24[1]; d61=q1[1]-q6[1]
    gap24=abs(q24[0]-q24[2]); gap1=abs(q1[0]-q1[2])
    e24=entropy(q24); e1=entropy(q1)
    return [logit(q1[1]),d246,d61,d61/5.0-d246/18.0,gap24,gap1,gap1-gap24,e24,e1,e1-e24]


def threeway(q1,pd):
    pd=clip(pd); h=float(q1[0]); a=float(q1[2]); s=h+a
    if s<=0: raise RuntimeError('non-draw baseline mass nonpositive')
    return [(1-pd)*h/s,pd,(1-pd)*a/s]


def f17(x): return format(float(x),'.17g')


def identity_hash(rows):
    h=hashlib.sha256()
    for r in rows:
        h.update(f"{r['match_id']}|{r['kickoff'].isoformat()}|{r['competition']}|{r['home']}|{r['away']}\n".encode('utf-8'))
    return h.hexdigest()


def feature_hash(rows):
    h=hashlib.sha256()
    for r in rows:
        vals=[r['match_id'],r['kickoff'].isoformat(),r['block'],*(f17(x) for x in r['features'])]
        h.update(('|'.join(vals)+'\n').encode('utf-8'))
    return h.hexdigest()


def prediction_hash(rows):
    h=hashlib.sha256()
    for r in rows:
        vals=[r['match_id'],r['kickoff'].isoformat(),r['block'],
              *(f17(x) for x in r['baseline']),*(f17(x) for x in r['calibration']),*(f17(x) for x in r['trajectory'])]
        h.update(('|'.join(vals)+'\n').encode('utf-8'))
    return h.hexdigest()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args(); a.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(a.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_EXTERNAL_ZERO_LABEL_PREDICTION_FREEZE'
    assert reg['hard_limits']['external_result_values_allowed'] is False
    assert reg['hard_limits']['model_fit_allowed'] is False
    assert reg['hard_limits']['prediction_metrics_allowed'] is False
    assert hfile(a.odds_csv)==reg['source_binding']['odds_csv_sha256']

    model=reg['frozen_internal_model']
    core={k:model[k] for k in ['feature_names','parent_feature_indices_zero_based','internal_training_rows','internal_training_draws','training_mean','training_std','calibration_beta','trajectory_beta','l2_lambda']}
    core_sha=canonical_sha(core)

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

    snapshots=defaultdict(dict); conflicted=set(); conflict_rows=0; identical_dupes=0
    with a.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        for r in reader:
            mid=str(r['match_id']).strip(); ko=dt(r['date_start']); obs=dt(r['date_created'])
            if not mid or ko is None or obs is None or mid in inconsistent: continue
            if not obs<ko: continue
            if not all(valid_odd(r[k]) for k in ('home_team_odd','away_team_odd','tie_odd')): continue
            odds=(float(r['home_team_odd']),float(r['tie_odd']),float(r['away_team_odd']))
            prior=snapshots[mid].get(obs)
            if prior is None: snapshots[mid][obs]=odds
            elif prior==odds: identical_dupes+=1
            else: conflict_rows+=1; conflicted.add(mid)

    rows=[]
    for mid,ident in identities.items():
        if mid in inconsistent or mid in conflicted: continue
        ko=dt(ident[0]); obsmap=snapshots.get(mid,{}); times=sorted(obsmap)
        if len(times)<3: continue
        qs={}; picked=[]; ok=True
        for hh in (24,6,1):
            cutoff=ko-timedelta(hours=hh)
            c=[x for x in times if x<=cutoff]
            if not c: ok=False; break
            t=c[-1]; picked.append(t); qs[hh]=devig(*obsmap[t])
        if not ok or len(set(picked))<2: continue
        rows.append({'match_id':mid,'kickoff':ko,'competition':ident[1],'home':ident[2],'away':ident[3],
                     'q24':qs[24],'q6':qs[6],'q1':qs[1]})
    rows.sort(key=lambda r:(r['kickoff'],mid_key(r['match_id'])))

    part=reg['frozen_external_partition']; fit_n=part['fit_count']; policy_n=part['policy_count']; blind_n=part['blind_count']
    for i,r in enumerate(rows):
        r['block']='fit' if i<fit_n else 'policy' if i<fit_n+policy_n else 'blind'
        r['features']=feature(r['q24'],r['q6'],r['q1'])
        z=[(r['features'][j]-model['training_mean'][j])/model['training_std'][j] for j in range(10)]
        bc=model['calibration_beta']; bt=model['trajectory_beta']
        pd_cal=sigmoid(bc[0]+bc[1]*z[0])
        pd_traj=sigmoid(bt[0]+sum(bt[j+1]*z[j] for j in range(10)))
        r['baseline']=[float(x) for x in r['q1']]
        r['calibration']=threeway(r['q1'],pd_cal)
        r['trajectory']=threeway(r['q1'],pd_traj)

    fit=rows[:fit_n]; policy=rows[fit_n:fit_n+policy_n]; blind=rows[fit_n+policy_n:]
    identity_hashes={'all':identity_hash(rows),'fit':identity_hash(fit),'policy':identity_hash(policy),'blind':identity_hash(blind)}
    expected_hashes={'all':part['all_identity_sha256'],'fit':part['fit_identity_sha256'],'policy':part['policy_identity_sha256'],'blind':part['blind_identity_sha256']}
    feature_hashes={'all':feature_hash(rows),'fit':feature_hash(fit),'policy':feature_hash(policy),'blind':feature_hash(blind)}
    prediction_hashes={'all':prediction_hash(rows),'fit':prediction_hash(fit),'policy':prediction_hash(policy),'blind':prediction_hash(blind)}
    max_prob_residual=0.0
    for r in rows:
        for key in ('baseline','calibration','trajectory'):
            p=r[key]
            if any((not math.isfinite(x)) or x<0 or x>1 for x in p): raise RuntimeError('invalid probability')
            max_prob_residual=max(max_prob_residual,abs(sum(p)-1.0))

    gate=reg['pass_gate']
    conflicted_in_eligible=len({r['match_id'] for r in rows}&conflicted)
    gates={
        'core_model_sha256_exact':core_sha==model['core_model_sha256'],
        'external_eligible_count_exact':len(rows)==gate['external_eligible_count_exact'],
        'external_split_counts_exact':len(fit)==fit_n and len(policy)==policy_n and len(blind)==blind_n,
        'external_identity_hashes_exact':identity_hashes==expected_hashes,
        'conflicted_match_ids_in_eligible_zero':conflicted_in_eligible==gate['conflicted_match_ids_in_eligible'],
        'all_probability_vectors_sum_to_one':max_prob_residual<1e-12 and gate['all_probability_vectors_sum_to_one'],
        'prediction_snapshot_hashes_created':all(len(x)==64 for x in prediction_hashes.values()) and gate['prediction_snapshot_hashes_created'],
        'external_result_values_accessed_zero':gate['external_result_values_accessed']==0,
        'model_fits_zero':gate['model_fits']==0,
        'prediction_metrics_zero':gate['prediction_metrics']==0,
        'thresholds_selected_zero':gate['thresholds_selected']==0,
    }
    passed=all(gates.values())
    status='PASS_R40H1_EXTERNAL_ZERO_LABEL_PREDICTION_FREEZE' if passed else 'STOP_R40H1_EXTERNAL_PREDICTION_FREEZE'
    result={
        'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
        'model':{'core_model_sha256':core_sha,'feature_names':model['feature_names'],'internal_training_rows':model['internal_training_rows'],'internal_training_draws':model['internal_training_draws'],'model_fits_this_stage':0},
        'external':{'eligible_count':len(rows),'split_counts':{'fit':len(fit),'policy':len(policy),'blind':len(blind)},'identity_hashes':identity_hashes,'feature_hashes':feature_hashes,'prediction_hashes':prediction_hashes,'identity_inconsistent_matches_excluded':len(inconsistent),'conflicted_match_ids_excluded':len(conflicted),'conflicting_duplicate_rows':conflict_rows,'identical_duplicate_rows':identical_dupes,'conflicted_match_ids_in_eligible':conflicted_in_eligible,'max_probability_sum_residual':max_prob_residual,'result_file_extracted':False,'result_values_accessed':0},
        'gates':gates,
        'no_label_audit':{'external_result_file_extracted':False,'external_result_values_accessed':0,'model_fits':0,'prediction_metrics':0,'thresholds_selected':0,'internal_fifth_fixed100_accessed':0},
        'next_stage_authorization':'RESULT_HEADER_AND_IDENTITY_AUDIT_ALLOWED_PREDICTIONS_FROZEN' if passed else 'STOP_NO_EXTERNAL_RESULTS','hard_limits':reg['hard_limits']
    }
    (a.out_dir/'external_prediction_freeze_status_r40h1.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
