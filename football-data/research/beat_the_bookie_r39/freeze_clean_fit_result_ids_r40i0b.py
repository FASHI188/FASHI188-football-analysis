#!/usr/bin/env python3
from __future__ import annotations

import argparse, importlib.util, json
from pathlib import Path


def load_parent(path: Path):
    spec=importlib.util.spec_from_file_location('r40i0_parent',path)
    if spec is None or spec.loader is None: raise RuntimeError('cannot import parent')
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--parent-code',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True)
    ap.add_argument('--result-id-file',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args(); a.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(a.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_ZERO_LABEL_FIT_RESULT_DUPLICATE_QUARANTINE'
    assert reg['parent_r40i0a']['verdict']=='STOP_R40I0A_DUPLICATE_RESULT_ID_AUDIT'
    assert reg['hard_limits']['score_or_outcome_values_allowed'] is False
    parent=load_parent(a.parent_code)
    assert parent.hfile(a.odds_csv)==reg['source_binding']['odds_csv_sha256']

    eligible,audit=parent.build_eligible(a.odds_csv)
    _,counts=parent.read_result_ids(a.result_id_file)
    duplicate_ids={mid for mid,n in counts.items() if n>1}
    part=reg['frozen_partition']; fit_n=part['fit_count']; policy_n=part['policy_count']; blind_n=part['blind_count']
    fit=eligible[:fit_n]; policy=eligible[fit_n:fit_n+policy_n]; blind=eligible[fit_n+policy_n:]
    fit_dups={r['match_id'] for r in fit}&duplicate_ids
    policy_dups={r['match_id'] for r in policy}&duplicate_ids
    blind_dups={r['match_id'] for r in blind}&duplicate_ids
    clean_fit=[r for r in fit if r['match_id'] not in duplicate_ids]
    hashes={
        'fit_original':parent.digest_rows(fit),
        'fit_clean':parent.digest_rows(clean_fit),
        'policy':parent.digest_rows(policy),
        'blind':parent.digest_rows(blind),
    }
    q=reg['quarantine_contract']
    gates={
        'eligible_count_exact':len(eligible)==part['eligible_count'],
        'fit_original_count_exact':len(fit)==fit_n,
        'fit_original_hash_exact':hashes['fit_original']==part['fit_identity_sha256'],
        'policy_hash_exact':hashes['policy']==part['policy_identity_sha256'],
        'blind_hash_exact':hashes['blind']==part['blind_identity_sha256'],
        'fit_duplicate_count_exact':len(fit_dups)==reg['parent_r40i0a']['duplicate_result_match_ids_in_fit'],
        'policy_duplicate_count_zero':len(policy_dups)==0,
        'blind_duplicate_count_zero':len(blind_dups)==0,
        'clean_fit_count_exact':len(clean_fit)==q['expected_clean_fit_count'],
        'policy_count_unchanged':len(policy)==q['policy_count_must_remain'],
        'blind_count_unchanged':len(blind)==q['blind_count_must_remain'],
        'score_or_outcome_values_accessed_zero':True,
        'model_fits_zero':True,'prediction_metrics_zero':True,'thresholds_selected_zero':True,
    }
    passed=all(gates.values())
    status='PASS_R40I0B_ZERO_LABEL_CLEAN_FIT_FREEZE' if passed else 'STOP_R40I0B_CLEAN_FIT_FREEZE'
    out={
        'schema_version':reg['schema_version'],'status':status,
        'parent_r40i0a_verdict_preserved':'STOP_R40I0A_DUPLICATE_RESULT_ID_AUDIT',
        'frozen_external':{'eligible_count':len(eligible),'source_audit':audit},
        'result_duplicate_ids':{'global':len(duplicate_ids),'fit':len(fit_dups),'policy':len(policy_dups),'blind':len(blind_dups)},
        'clean_partition':{'fit_count':len(clean_fit),'fit_identity_sha256':hashes['fit_clean'],'policy_count':len(policy),'policy_identity_sha256':hashes['policy'],'blind_count':len(blind),'blind_identity_sha256':hashes['blind'],'no_replacement':True,'no_rebalancing':True},
        'gates':gates,
        'no_label_audit':{'score_or_outcome_values_accessed':0,'model_fits':0,'prediction_metrics':0,'thresholds_selected':0,'policy_result_values_accessed':0,'blind_result_values_accessed':0,'internal_fifth_fixed100_accessed':0},
        'next_stage_authorization':'FIT_ONLY_LABEL_SCHEMA_AUDIT_ALLOWED_POLICY_BLIND_STILL_UNOPENED' if passed else 'STOP_NO_RESULT_VALUES','hard_limits':reg['hard_limits']
    }
    (a.out_dir/'clean_fit_result_identity_status_r40i0b.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
