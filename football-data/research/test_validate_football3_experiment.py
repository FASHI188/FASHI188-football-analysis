from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist

import pytest

from validate_football3_experiment import (
    PreflightError,
    validate_contract,
    validate_external_audit_artifacts,
    validate_runner,
    validate_runtime_branch_binding,
)

ROOT_SHA='e3e73c998020beef585cc459a69ea5b73b44ddb3'
DUMMY_SHA='0'*64
REQ_SCOPE=['branches','pull_requests','commits','scientific-hypothesis-history','source-revision-history','market-field-history']


def valid_contract() -> dict:
    return {
      'schema_version':2,'project_id':'football3','scientific_root':{'experiment':'C072-C','sha':ROOT_SHA},'branch':'football3/example-new-pt-hypothesis',
      'scientific_question':{'primary_target':'P(T=0,1,2,3,4,5,6,7+)','question':'synthetic validation only','direct_draw_optimization':False,'materially_new_pt_hypothesis':True},
      'prediction_cutoff':{'master':'T-15m','baseline':'T-15m','candidate':'T-15m','change_requires_new_product_contract':True,'pit_definition':'all timezone-aware inputs known by T-15m'},
      'baseline':{'description':'latest same-cutoff market anchor','market_anchor':True,'same_cutoff':True,'latest_snapshot_at_or_before_cutoff':True,'devigged':True,'representation_frozen_before_labels':True,'representation':'OU market surface frozen before labels','quote_timestamp_column':'quote_ts','market_semantics_zero_label_audit_required':True},
      'candidate':{'description':'materially new P(T) hypothesis','post_view_neighbor_of_parked_hypothesis':False,'feature_columns':['f1','f2'],'feature_timestamp_map':{'f1':'feat_ts','f2':'feat_ts'},'all_model_features_must_be_declared':True},
      'data_plan':{
        'source_revision':'repo@abc123','identity_lock_before_labels':True,'identity_lock_format':'sha256_csv_v1','identity_lock_artifact':'identity.csv','identity_lock_sha256':DUMMY_SHA,'identity_count':2,'ordered_identity_sha256':DUMMY_SHA,'scored_identity_must_equal_lock':True,
        'input_semantics_audit':{'required':True,'artifact':'semantics.json','artifact_sha256':DUMMY_SHA,'connected_audit_status':'VERIFIED_ZERO_LABEL','latest_market_snapshot_verified':True,'devig_representation_verified':True,'all_candidate_features_timestamp_mapped':True},
        'global_consumption_audit':{'required':True,'artifact':'audit.json','artifact_sha256':DUMMY_SHA,'registry_checked':True,'github_history_checked':True,'airtable_history_checked':True,'connected_audit_status':'VERIFIED_ZERO_LABEL','target_identity_overlap_with_consumed':0,'unresolved_historical_identity_gaps':0,'cross_project_duplication':{'same_scientific_hypothesis_previously_viewed':False,'exact_replication_or_reproduction_condition_met':False}},
        'evidence_class':'DEVELOPMENT_FRESH','random_split':False,'target_labels_before_contract':0,
      },
      'oos_design':{'temporal':True,'shuffle':False,'folds':'4 expanding folds','minimum_test_rows_per_fold':100},
      'metrics':{'proper_scores':['LogLoss','Brier','RPS'],'top1_primary':False,'implementation':'football3_core.evaluate_frozen_experiment','calibration':{'required':True,'metrics':['Top1ECE','ClasswiseECE'],'bins':10}},
      'success_gates':{'primary':{'metric':'LogLoss','delta_max':0.0,'bootstrap_ci_high_max':0.0},'secondary_noninferiority':{'Brier_delta_max':0.0,'RPS_delta_max':0.0,'Top1ECE_delta_max':0.0,'ClasswiseECE_delta_max':0.0},'temporal_consistency':{'minimum_fold_win_fraction':0.6},'domain_consistency':{'domain_field':'League','minimum_domains':3,'minimum_rows_per_domain':30,'minimum_win_fraction':0.6,'max_domain_logloss_regression':0.01}},
      'bootstrap':{'paired_match':True,'resamples':5000,'seed':72001,'ci':0.90},
      'sample_plan':{'development_minimum_n':1000,'runtime_minimum_n_enforced':True,'confirmation':False,'power_or_precision_plan_frozen':False,'planning_artifact_schema':None,'optional_stopping':False},
      'sealed_boundaries':[{'name':'C070-F Confirmation1597','authorized_access_count':0},{'name':'N17 reserve266','authorized_access_count':0},{'name':'N18C confirmation150','authorized_access_count':0}],
      'method_shopping':{'same_labels_rescue_allowed':False,'frozen_dimensions':['candidate','baseline','feature timestamp map','folds','gates']},
      'authorization':{'new_target_access_requires_explicit_user_authorization':True},
    }


def test_valid_contract_passes(): validate_contract(valid_contract())


def test_master_cutoff_baseline_and_feature_manifest_are_mandatory():
    c=valid_contract(); c['prediction_cutoff']['candidate']='T-60m'
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['baseline']['market_anchor']=False
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['candidate']['feature_timestamp_map'].pop('f2')
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['baseline']['market_semantics_zero_label_audit_required']=False
    with pytest.raises(PreflightError): validate_contract(c)


def test_direct_draw_optimization_is_rejected():
    c=valid_contract(); c['scientific_question']['direct_draw_optimization']=True
    with pytest.raises(PreflightError): validate_contract(c)


def test_cross_project_same_hypothesis_forces_reuse():
    c=valid_contract(); c['data_plan']['global_consumption_audit']['cross_project_duplication']['same_scientific_hypothesis_previously_viewed']=True
    with pytest.raises(PreflightError): validate_contract(c)
    c['data_plan']['evidence_class']='REPLICATION'; c['scientific_question']['materially_new_pt_hypothesis']=False
    validate_contract(c)


def test_confirmation_requires_numeric_power_plan_and_freshness():
    c=valid_contract(); c['sample_plan']['confirmation']=True; c['data_plan']['evidence_class']='CONFIRMATION_FRESH'
    with pytest.raises(PreflightError): validate_contract(c)
    c['sample_plan'].update({'power_or_precision_plan_frozen':True,'minimum_n':1250,'planned_power':0.8,'alpha':0.10,'planning_basis':'DEVELOPMENT_ONLY','planning_artifact_schema':'football3_paired_power_plan_v1','planning_artifact':'plan.json','planning_artifact_sha256':DUMMY_SHA})
    validate_contract(c)
    c['sample_plan']['planned_power']=1.2
    with pytest.raises(PreflightError): validate_contract(c)


def test_consumed_or_unresolved_history_forces_reuse_classification():
    c=valid_contract(); c['data_plan']['global_consumption_audit']['target_identity_overlap_with_consumed']=4
    with pytest.raises(PreflightError): validate_contract(c)
    c['data_plan']['evidence_class']='REPLICATION'; validate_contract(c)
    c=valid_contract(); c['data_plan']['global_consumption_audit']['unresolved_historical_identity_gaps']=1
    with pytest.raises(PreflightError): validate_contract(c)


def test_nan_inf_and_bad_bootstrap_types_cannot_bypass_gates():
    c=valid_contract(); c['success_gates']['primary']['delta_max']=float('nan')
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['success_gates']['secondary_noninferiority']['RPS_delta_max']=float('inf')
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['bootstrap']['resamples']=5000.0
    with pytest.raises(PreflightError): validate_contract(c)


def test_success_gates_calibration_domain_identity_and_runtime_n_are_mandatory():
    c=valid_contract(); c['metrics']['calibration']['required']=False
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['success_gates']['domain_consistency']['minimum_domains']=1
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['data_plan']['scored_identity_must_equal_lock']=False
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['sample_plan']['runtime_minimum_n_enforced']=False
    with pytest.raises(PreflightError): validate_contract(c)


def _write_bound_artifacts(tmp_path: Path, c: dict):
    ids=[hashlib.sha256(b'a').hexdigest(),hashlib.sha256(b'b').hexdigest()]
    lock=tmp_path/'identity.csv'; lock.write_text('identity_sha256\n'+'\n'.join(ids)+'\n',encoding='utf-8')
    lock_sha=hashlib.sha256(lock.read_bytes()).hexdigest(); ordered=hashlib.sha256(('\n'.join(ids)+'\n').encode()).hexdigest()
    c['data_plan']['identity_lock_sha256']=lock_sha; c['data_plan']['identity_count']=2; c['data_plan']['ordered_identity_sha256']=ordered

    sem={
      'schema_version':1,'project_id':'football3','audited_at':'2026-08-20T12:00:00+08:00','source_revision':'repo@abc123','identity_lock_sha256':lock_sha,'identity_count':2,'ordered_identity_sha256':ordered,'real_target_values_read':0,'connected_audit_status':'VERIFIED_ZERO_LABEL',
      'baseline':{'representation':c['baseline']['representation'],'quote_timestamp_column':'quote_ts','latest_snapshot_at_or_before_cutoff_verified':True,'devig_representation_verified':True,'quote_timestamp_missing_count':0,'post_cutoff_quote_count':0},
      'candidate':{'feature_columns':['f1','f2'],'feature_timestamp_map':{'f1':'feat_ts','f2':'feat_ts'},'all_model_features_timestamp_mapped':True,'feature_timestamp_missing_count':0,'post_cutoff_feature_value_count':0},
    }
    sp=tmp_path/'semantics.json'; sp.write_text(json.dumps(sem,sort_keys=True),encoding='utf-8'); c['data_plan']['input_semantics_audit']['artifact_sha256']=hashlib.sha256(sp.read_bytes()).hexdigest()

    dup={'same_repository_previously_used':True,'same_revision_previously_used':False,'same_season_overlap_count':0,'same_match_identity_overlap_count':0,'same_market_fields_previously_used':True,'same_label_definition_previously_used':True,'same_scientific_hypothesis_previously_viewed':False,'exact_replication_or_reproduction_condition_met':False,'evidence_notes':'zero-label connected history audit'}
    audit={
      'schema_version':1,'project_id':'football3','audited_at':'2026-08-20T12:00:00+08:00','identity_lock_sha256':lock_sha,'identity_count':2,'ordered_identity_sha256':ordered,'source_revision':'repo@abc123','real_target_values_read':0,
      'registry_checked':True,'github_history_checked':True,'airtable_history_checked':True,'connected_audit_status':'VERIFIED_ZERO_LABEL','target_identity_overlap_with_consumed':0,'unresolved_historical_identity_gaps':0,'cross_project_duplication':dup,'evidence_class':c['data_plan']['evidence_class'],
      'github_receipt':{'repository':'FASHI188/FASHI188-football-analysis','checked_at':'2026-08-20T12:00:00+08:00','query_scope':REQ_SCOPE,'result_digest_sha256':'1'*64},
      'airtable_receipt':{'base_id':'appLXF9IBvSCEUjJV','checked_at':'2026-08-20T12:00:00+08:00','tables_checked':['当前状态','维护日志'],'result_digest_sha256':'2'*64},
    }
    ap=tmp_path/'audit.json'; ap.write_text(json.dumps(audit,sort_keys=True,ensure_ascii=False),encoding='utf-8'); c['data_plan']['global_consumption_audit']['artifact_sha256']=hashlib.sha256(ap.read_bytes()).hexdigest()
    return lock,sp,ap,sem,audit


def test_external_artifacts_semantically_bind_identity_market_features_and_history(tmp_path: Path):
    c=valid_contract(); lock,sp,ap,sem,audit=_write_bound_artifacts(tmp_path,c)
    cp=tmp_path/'contract.json'; cp.write_text(json.dumps(c),encoding='utf-8')
    validate_contract(c); validate_external_audit_artifacts(c,cp)
    sem['candidate']['feature_timestamp_map']['f2']='wrong_ts'; sp.write_text(json.dumps(sem,sort_keys=True),encoding='utf-8'); c['data_plan']['input_semantics_audit']['artifact_sha256']=hashlib.sha256(sp.read_bytes()).hexdigest(); cp.write_text(json.dumps(c),encoding='utf-8')
    with pytest.raises(PreflightError): validate_external_audit_artifacts(c,cp)


def test_external_receipt_scope_and_cross_project_counts_fail_closed(tmp_path: Path):
    c=valid_contract(); lock,sp,ap,sem,audit=_write_bound_artifacts(tmp_path,c)
    audit['github_receipt']['query_scope']=['commits']; ap.write_text(json.dumps(audit,sort_keys=True,ensure_ascii=False),encoding='utf-8'); c['data_plan']['global_consumption_audit']['artifact_sha256']=hashlib.sha256(ap.read_bytes()).hexdigest(); cp=tmp_path/'contract.json'; cp.write_text(json.dumps(c),encoding='utf-8')
    with pytest.raises(PreflightError): validate_external_audit_artifacts(c,cp)
    audit['github_receipt']['query_scope']=REQ_SCOPE; audit['cross_project_duplication']['same_match_identity_overlap_count']=1; ap.write_text(json.dumps(audit,sort_keys=True,ensure_ascii=False),encoding='utf-8'); c['data_plan']['global_consumption_audit']['artifact_sha256']=hashlib.sha256(ap.read_bytes()).hexdigest(); cp.write_text(json.dumps(c),encoding='utf-8')
    with pytest.raises(PreflightError): validate_external_audit_artifacts(c,cp)


def test_confirmation_power_artifact_is_recomputed_not_self_reported(tmp_path: Path):
    c=valid_contract(); c['data_plan']['evidence_class']='CONFIRMATION_FRESH'; c['sample_plan'].update({'confirmation':True,'power_or_precision_plan_frozen':True,'planned_power':0.8,'alpha':0.10,'planning_basis':'EXTERNAL_PRIOR','planning_artifact_schema':'football3_paired_power_plan_v1','planning_artifact':'plan.json','planning_artifact_sha256':DUMMY_SHA})
    _write_bound_artifacts(tmp_path,c)
    effect=0.10; sd=0.20; mult=1.25; z_a=NormalDist().inv_cdf(0.95); z_p=NormalDist().inv_cdf(0.8); required=int(math.ceil(((z_a+z_p)*sd/effect)**2*mult))
    c['sample_plan']['minimum_n']=required
    plan={'schema_version':1,'schema':'football3_paired_power_plan_v1','planning_basis':'EXTERNAL_PRIOR','effect_abs':effect,'paired_delta_sd':sd,'alpha':0.10,'planned_power':0.8,'conservative_multiplier':mult,'development_or_prior_n':100,'effect_source':'external prior','required_n':required}
    pp=tmp_path/'plan.json'; pp.write_text(json.dumps(plan,sort_keys=True),encoding='utf-8'); c['sample_plan']['planning_artifact_sha256']=hashlib.sha256(pp.read_bytes()).hexdigest(); cp=tmp_path/'contract.json'; cp.write_text(json.dumps(c),encoding='utf-8')
    validate_contract(c); validate_external_audit_artifacts(c,cp)
    plan['required_n']=required-1; pp.write_text(json.dumps(plan,sort_keys=True),encoding='utf-8'); c['sample_plan']['planning_artifact_sha256']=hashlib.sha256(pp.read_bytes()).hexdigest(); cp.write_text(json.dumps(c),encoding='utf-8')
    with pytest.raises(PreflightError): validate_external_audit_artifacts(c,cp)


def test_artifact_path_traversal_is_rejected(tmp_path: Path):
    c=valid_contract(); c['data_plan']['identity_lock_artifact']='../outside.csv'; cp=tmp_path/'contract.json'; cp.write_text(json.dumps(c),encoding='utf-8')
    with pytest.raises(PreflightError): validate_external_audit_artifacts(c,cp)


def test_runtime_branch_must_match_contract(monkeypatch):
    c=valid_contract(); monkeypatch.setenv('GITHUB_HEAD_REF','football3/example-new-pt-hypothesis'); validate_runtime_branch_binding(c)
    monkeypatch.setenv('GITHUB_HEAD_REF','football3/other')
    with pytest.raises(PreflightError): validate_runtime_branch_binding(c)


def write_runner(tmp_path: Path, body: str) -> Path:
    p=tmp_path/'runner.py'; p.write_text(body,encoding='utf-8'); return p


def _clean_runner_body(extra=''):
    return '''from football3_core import evaluate_frozen_experiment, assert_feature_pit, assert_temporal_oos, assert_master_prediction_cutoff, assert_exact_one_to_one_join, assert_sealed_boundaries\nassert_master_prediction_cutoff("T-15m")\nassert_feature_pit(F,cutoff_col="cutoff",feature_timestamp_cols=["quote_ts","feat_ts"])\nassert_temporal_oos(TR,TE)\nassert_exact_one_to_one_join(L,R,keys=["id"],expected_rows=N)\nassert_sealed_boundaries(COUNTS,POOLS)\nevaluate_frozen_experiment(B,C,Y,identity_sha256=IDS,fold_ids=FOLD,domain_ids=DOM,contract=CONTRACT)\n'''+extra


def test_runner_requires_reachable_core_calls_identity_and_join_guards(tmp_path):
    with pytest.raises(PreflightError): validate_runner(write_runner(tmp_path,'x="football3_core"\n'))
    with pytest.raises(PreflightError): validate_runner(write_runner(tmp_path,'from football3_core import score_bundle\nscore_bundle(P,Y)\n'))
    validate_runner(write_runner(tmp_path,_clean_runner_body()))
    dead='''from football3_core import evaluate_frozen_experiment, assert_feature_pit, assert_temporal_oos, assert_master_prediction_cutoff, assert_exact_one_to_one_join, assert_sealed_boundaries\ndef never_called():\n assert_master_prediction_cutoff("T-15m")\n assert_feature_pit(F,cutoff_col="cutoff",feature_timestamp_cols=["quote_ts","feat_ts"])\n assert_temporal_oos(TR,TE)\n assert_exact_one_to_one_join(L,R,keys=["id"],expected_rows=N)\n assert_sealed_boundaries(COUNTS,POOLS)\n evaluate_frozen_experiment(B,C,Y,identity_sha256=IDS,fold_ids=FOLD,domain_ids=DOM,contract=CONTRACT)\n'''
    with pytest.raises(PreflightError): validate_runner(write_runner(tmp_path,dead))
    no_ids=_clean_runner_body().replace('identity_sha256=IDS,','')
    with pytest.raises(PreflightError): validate_runner(write_runner(tmp_path,no_ids))


def test_dataframe_T_random_split_and_sealed_tokens_are_rejected(tmp_path):
    with pytest.raises(PreflightError): validate_runner(write_runner(tmp_path,_clean_runner_body('import pandas as pd\nx=pd.DataFrame({"T":[1]})\ny=x.T\n')))
    with pytest.raises(PreflightError): validate_runner(write_runner(tmp_path,_clean_runner_body('from sklearn.model_selection import train_test_split\ntrain_test_split([1,2],[1,2])\n')))
    with pytest.raises(PreflightError): validate_runner(write_runner(tmp_path,_clean_runner_body('PATH="foo/confirmation1597.csv"\n')))
