#!/usr/bin/env python3
from __future__ import annotations

import argparse, importlib.util, json
from pathlib import Path


def load_parent(path: Path):
    spec=importlib.util.spec_from_file_location('r40i0_parent',path)
    if spec is None or spec.loader is None: raise RuntimeError('cannot import R40I0 auditor')
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
    assert reg['status']=='PRE_REGISTERED_ZERO_LABEL_DUPLICATE_RESULT_ID_AUDIT'
    assert reg['parent_r40i0']['verdict']=='STOP_R40I0_RESULT_IDENTITY_AUDIT'
    assert reg['parent_r40i0']['verdict_must_not_be_overridden'] is True
    assert reg['hard_limits']['score_or_outcome_values_allowed'] is False
    parent=load_parent(a.parent_code)
    if parent.hfile(a.odds_csv)!=reg['source_binding']['odds_csv_sha256']: raise RuntimeError('odds hash changed')
    eligible,audit=parent.build_eligible(a.odds_csv)
    result_rows,result_counts=parent.read_result_ids(a.result_id_file)
    dup_ids={mid for mid,n in result_counts.items() if n>1}
    eligible_ids={r['match_id'] for r in eligible}
    dup_eligible=dup_ids & eligible_ids
    result_ids=set(result_counts)
    part=reg['frozen_external_partition']; fit_n=part['fit_count']; policy_n=part['policy_count']; blind_n=part['blind_count']
    fit=eligible[:fit_n]; policy=eligible[fit_n:fit_n+policy_n]; blind=eligible[fit_n+policy_n:]
    expected_hashes={'all':part['all_identity_sha256'],'fit':part['fit_identity_sha256'],'policy':part['policy_identity_sha256'],'blind':part['blind_identity_sha256']}
    hashes={'all':parent.digest_rows(eligible),'fit':parent.digest_rows(fit),'policy':parent.digest_rows(policy),'blind':parent.digest_rows(blind)}
    covered=[r for r in eligible if r['match_id'] in result_ids]
    coverage=len(covered)/len(eligible) if eligible else 0.0
    dup_by_block={
        'fit':sum(r['match_id'] in dup_ids for r in fit),
        'policy':sum(r['match_id'] in dup_ids for r in policy),
        'blind':sum(r['match_id'] in dup_ids for r in blind),
    }
    gates={
        'frozen_eligible_count_exact':len(eligible)==part['eligible_count'],
        'frozen_split_counts_exact':len(fit)==fit_n and len(policy)==policy_n and len(blind)==blind_n,
        'frozen_identity_hashes_exact':hashes==expected_hashes,
        'parent_result_rows_exact':result_rows==reg['parent_r40i0']['result_rows'],
        'parent_unique_result_ids_exact':len(result_counts)==reg['parent_r40i0']['unique_result_match_ids'],
        'parent_duplicate_result_ids_exact':len(dup_ids)==reg['parent_r40i0']['duplicate_result_match_ids'],
        'duplicated_result_match_ids_in_frozen_eligible_zero':len(dup_eligible)==reg['audit_contract']['duplicated_result_match_ids_in_frozen_eligible_must_equal'],
        'frozen_eligible_coverage_exact':abs(coverage-reg['audit_contract']['frozen_eligible_coverage_must_equal'])<1e-15,
        'score_or_outcome_values_accessed_zero':True,'model_fits_zero':True,'prediction_metrics_zero':True,'thresholds_selected_zero':True,
    }
    passed=all(gates.values())
    status='PASS_R40I0A_ZERO_LABEL_DUPLICATE_RESULT_ID_AUDIT' if passed else 'STOP_R40I0A_DUPLICATE_RESULT_ID_AUDIT'
    out={
        'schema_version':reg['schema_version'],'status':status,'parent_r40i0_verdict_preserved':'STOP_R40I0_RESULT_IDENTITY_AUDIT',
        'result_identity':{'sanitized_rows':result_rows,'unique_match_ids':len(result_counts),'duplicate_match_ids':len(dup_ids),'duplicated_match_ids_in_frozen_eligible':len(dup_eligible),'duplicated_eligible_by_block':dup_by_block,'frozen_eligible_coverage':coverage},
        'frozen_external':{'eligible_count':len(eligible),'identity_hashes':hashes,'source_audit':audit},
        'gates':gates,
        'no_label_audit':{'score_or_outcome_values_accessed':0,'model_fits':0,'prediction_metrics':0,'thresholds_selected':0,'internal_fifth_fixed100_accessed':0},
        'next_stage_authorization':'ONE_TIME_EXTERNAL_RESULT_EVALUATION_PREREGISTRATION_ALLOWED' if passed else 'STOP_NO_RESULT_VALUE_ACCESS','hard_limits':reg['hard_limits']
    }
    (a.out_dir/'result_duplicate_id_audit_status_r40i0a.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
