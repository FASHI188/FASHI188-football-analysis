#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, hashlib, importlib.util, json, math
from collections import Counter, defaultdict
from pathlib import Path


def load_parent(path: Path):
    spec=importlib.util.spec_from_file_location('r40i0_parent',path)
    if spec is None or spec.loader is None: raise RuntimeError('cannot import parent')
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def parse_score(v: str):
    s=str(v or '').strip()
    try:
        x=float(s)
        if not math.isfinite(x) or x<0 or not x.is_integer(): return None
        return int(x)
    except Exception:
        return None


def label(h,a):
    return 'H' if h>a else 'A' if h<a else 'D'


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--parent-code',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True)
    ap.add_argument('--result-id-file',type=Path,required=True)
    ap.add_argument('--clean-fit-results',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args(); a.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(a.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_FIT_ONLY_RESULT_LABEL_SCHEMA_AUDIT'
    assert reg['hard_limits']['policy_result_values_allowed'] is False
    assert reg['hard_limits']['blind_result_values_allowed'] is False
    assert reg['hard_limits']['model_prediction_access_allowed'] is False
    parent=load_parent(a.parent_code)
    assert parent.hfile(a.odds_csv)==reg['source_binding']['odds_csv_sha256']

    eligible,_=parent.build_eligible(a.odds_csv)
    _,result_counts=parent.read_result_ids(a.result_id_file)
    dup_ids={mid for mid,n in result_counts.items() if n>1}
    fit=eligible[:12936]
    clean_fit=[r for r in fit if r['match_id'] not in dup_ids]
    clean_hash=parent.digest_rows(clean_fit)
    expected_header=reg['source_binding']['result_header']

    by_id={}; duplicate_selected=0; bad_identity=0; bad_scores=0; empty_final=0
    token_to_labels=defaultdict(set); label_to_tokens=defaultdict(set); label_counts=Counter(); token_counts=Counter()
    with a.clean_fit_results.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if list(reader.fieldnames or [])!=expected_header: raise RuntimeError(f'unexpected result header {reader.fieldnames}')
        frozen={r['match_id']:r for r in clean_fit}
        for raw in reader:
            mid=str(raw['match_id']).strip()
            if mid not in frozen: raise RuntimeError(f'non-clean-fit result row exposed: {mid}')
            if mid in by_id:
                duplicate_selected+=1; continue
            fr=frozen[mid]
            ident=(raw['date_start'].strip(),raw['competition_name'].strip(),raw['home_team_name'].strip(),raw['away_team_name'].strip())
            expected_ident=(fr['kickoff'].strftime('%Y-%m-%d %H:%M:%S'),fr['competition'],fr['home'],fr['away'])
            # Date strings may omit seconds; compare parsed kickoff instead of raw formatting.
            same=(parent.dt(raw['date_start'])==fr['kickoff'] and ident[1:]==expected_ident[1:])
            if not same: bad_identity+=1
            hs=parse_score(raw['home_team_score']); aas=parse_score(raw['away_team_score'])
            tok=str(raw['final_result'] or '').strip()
            if hs is None or aas is None:
                bad_scores+=1; lab=None
            else:
                lab=label(hs,aas); label_counts[lab]+=1
            if not tok:
                empty_final+=1
            elif lab is not None:
                token_counts[tok]+=1; token_to_labels[tok].add(lab); label_to_tokens[lab].add(tok)
            by_id[mid]={'label':lab,'token':tok}

    tokens=sorted(token_to_labels)
    labels=sorted(label_to_tokens)
    bijection=(len(tokens)==3 and set(labels)=={'A','D','H'} and all(len(token_to_labels[t])==1 for t in tokens) and all(len(label_to_tokens[l])==1 for l in labels))
    inferred_mapping={t:next(iter(token_to_labels[t])) for t in tokens if len(token_to_labels[t])==1}
    pg=reg['pass_gate']
    gates={
        'clean_fit_rows_exact':len(clean_fit)==pg['clean_fit_rows_exact'],
        'clean_fit_identity_sha256_exact':clean_hash==reg['parent_r40i0b']['clean_fit_identity_sha256'],
        'result_join_complete':len(by_id)==len(clean_fit),
        'result_identity_fields_exact':bad_identity==0,
        'scores_valid':bad_scores==0,
        'exactly_three_score_labels_present':set(label_counts)=={'H','D','A'},
        'exactly_three_final_result_tokens_present':len(tokens)==3,
        'final_result_score_label_bijection':bijection,
        'duplicate_clean_fit_result_ids':duplicate_selected==pg['duplicate_clean_fit_result_ids'],
        'policy_result_values_accessed_zero':pg['policy_result_values_accessed']==0,
        'blind_result_values_accessed_zero':pg['blind_result_values_accessed']==0,
        'model_predictions_accessed_zero':pg['model_predictions_accessed']==0,
        'prediction_metrics_zero':pg['prediction_metrics']==0,
    }
    passed=all(gates.values())
    status='PASS_R40I1_FIT_ONLY_LABEL_SCHEMA_AUDIT' if passed else 'STOP_R40I1_LABEL_SCHEMA_AUDIT'
    out={
        'schema_version':reg['schema_version'],'status':status,
        'clean_fit':{'count':len(clean_fit),'identity_sha256':clean_hash,'result_rows_seen':len(by_id),'identity_mismatches':bad_identity,'invalid_score_rows':bad_scores,'empty_final_result_rows':empty_final},
        'source_native_label_schema':{'score_label_counts':dict(label_counts),'final_result_token_counts':dict(token_counts),'inferred_final_result_to_hda':inferred_mapping,'bijection':bijection,'settlement_scope':'SOURCE_NATIVE_FINAL_RESULT_NOT_PROVEN_FORMAL_90MIN'},
        'gates':gates,
        'access_audit':{'clean_fit_result_values_accessed':len(by_id),'policy_result_values_accessed':0,'blind_result_values_accessed':0,'model_predictions_accessed':0,'prediction_metrics':0,'internal_fifth_fixed100_accessed':0},
        'next_stage_authorization':'ONE_TIME_POLICY_EVALUATION_PREREGISTRATION_ALLOWED_BLIND_STILL_UNOPENED' if passed else 'STOP_POLICY_BLIND_UNOPENED','hard_limits':reg['hard_limits']
    }
    (a.out_dir/'fit_label_schema_status_r40i1.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
