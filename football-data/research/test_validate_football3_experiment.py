from __future__ import annotations
from pathlib import Path
import pytest
from validate_football3_experiment import PreflightError, validate_contract, validate_runner
ROOT_SHA='e3e73c998020beef585cc459a69ea5b73b44ddb3'
def valid_contract():
    return {'schema_version':3,'project_id':'football3','scientific_root':{'experiment':'C072-C','sha':ROOT_SHA},'scientific_result_lock':{'C072_N20':'PILOT_NO_SIGNAL_PARK','formal_weight':0},'prediction_cutoff':{'master':'T-15m','baseline':'T-15m','candidate':'T-15m','pit_bound_to_real_source_timestamp':True},'scientific_question':{'primary_target':'P(T=0,1,2,3,4,5,6,7+)','direct_draw_optimization':False},'method_shopping':{'same_viewed_oos_rescue_allowed':False},'data_plan':{'identity_kind':'global_match_identity','identity_lock_before_labels':True,'target_decode_after_identity_guard':True,'identity_count':1000,'ordered_identity_sha256':'1'*64,'global_consumption_fail_closed':True,'fresh_on_unresolved_identity':False},'metrics':{'proper_scores':['LogLoss','Brier','RPS'],'top1_primary':False,'calibration':{'required':True,'metrics':['Top1ECE','ClasswiseECE'],'bins':10}},'candidate_equivalence':{'max_abs_floor':1e-9,'mean_abs_floor':1e-11,'fail_closed':True},'success_gates':{'primary':{'metric':'LogLoss','strict_improvement':True,'iid_bootstrap_ci_high_strictly_below_zero':True,'dependency_bootstrap_ci_high_strictly_below_zero':True},'temporal_consistency':{'minimum_fold_win_fraction':0.60},'domain_consistency':{'minimum_win_fraction':0.60}},'dependency_bootstrap':{'method':'competition_season_cluster','minimum_clusters':8,'resamples':5000},'oos_design':{'temporal':True,'shuffle':False,'temporal_manifest_sha256':'2'*64,'evaluator_binds_identity_fold_date':True},'sealed':{'reader_required':True,'self_report_counts_forbidden':True,'pools':[{'pool_id':'C070-F Confirmation1597','manifest_sha256':'3'*64,'access_authorized':False}]},'confirmation':{'delta_definition':'candidate_loss_minus_baseline_loss','required_direction':'negative','minimum_n':500,'cluster_aware_design_effect':True},'runtime_guards':{'no_real_target_access':True,'no_training':True,'no_scientific_scoring':True,'no_provider_requests':True,'no_secret_access':True}}
def test_valid_contract(): validate_contract(valid_contract())
def test_zero_fold_or_domain_gate_rejected():
    c=valid_contract(); c['success_gates']['temporal_consistency']['minimum_fold_win_fraction']=0
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['success_gates']['domain_consistency']['minimum_win_fraction']=0
    with pytest.raises(PreflightError): validate_contract(c)
def test_non_strict_primary_rejected():
    c=valid_contract(); c['success_gates']['primary']['strict_improvement']=False
    with pytest.raises(PreflightError): validate_contract(c)
def test_source_dependent_identity_rejected():
    c=valid_contract(); c['data_plan']['identity_kind']='source_row_identity'
    with pytest.raises(PreflightError): validate_contract(c)
def test_sealed_self_report_or_authorization_rejected():
    c=valid_contract(); c['sealed']['self_report_counts_forbidden']=False
    with pytest.raises(PreflightError): validate_contract(c)
    c=valid_contract(); c['sealed']['pools'][0]['access_authorized']=True
    with pytest.raises(PreflightError): validate_contract(c)
def test_runner_must_bind_actual_rows(tmp_path:Path):
    p=tmp_path/'r.py'; p.write_text("from football3_core import evaluate_frozen_experiment\nevaluate_frozen_experiment(B,C,Y,identity_sha256=IDS,fold_ids=F,domain_ids=D,contract=CONTRACT)\n",encoding='utf-8')
    with pytest.raises(PreflightError): validate_runner(p)
def test_dummy_temporal_call_not_enough(tmp_path:Path):
    p=tmp_path/'r.py'; p.write_text("from football3_core import evaluate_frozen_experiment, assert_temporal_oos\nassert_temporal_oos(['2020-01-01T00:00:00Z'],['2020-02-01T00:00:00Z'])\nevaluate_frozen_experiment(B,C,Y,identity_sha256=IDS,fold_ids=F,domain_ids=D,contract=CONTRACT)\n",encoding='utf-8')
    with pytest.raises(PreflightError): validate_runner(p)
def test_direct_io_forbidden(tmp_path:Path):
    p=tmp_path/'r.py'; p.write_text("from football3_core import evaluate_frozen_experiment\nopen('sealed.csv')\n",encoding='utf-8')
    with pytest.raises(PreflightError): validate_runner(p)
