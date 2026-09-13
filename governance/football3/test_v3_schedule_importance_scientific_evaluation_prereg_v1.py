#!/usr/bin/env python3
import importlib.util, json, tempfile
from pathlib import Path
H=Path(__file__).resolve().parent
S=importlib.util.spec_from_file_location('v',H/'validate_v3_schedule_importance_scientific_evaluation_prereg_v1.py')
v=importlib.util.module_from_spec(S);S.loader.exec_module(v)
C=json.loads((H/'v3_schedule_importance_scientific_evaluation_prereg_contract_v1.json').read_text())

def t_contract_valid(): v.validate_contract(C)
def t_parent_binding():
 assert C['parent']['feature_sha256']==v.EXPECTED_PARENT['feature_sha256']; assert C['parent']['feature_rows']==30531; assert C['parent']['score_result_goal_values_read']==0
def t_target_hda_only():
 t=C['target_definition']; assert t['primary_target']=='REGULATION_FULL_TIME_1X2_H_D_A'; assert t['label_values']==['H','D','A']; assert t['target_label_opening_authorized_in_this_batch'] is False; assert 'exact_score' in t['forbidden_target_uses']
def t_folds_exact():
 f=C['chronological_outer_folds']; assert [x['test_season'] for x in f]==v.EXPECTED_TEST_SEASONS; assert f[0]['train_seasons_max']=='2019-20' and f[-1]['train_seasons_max']=='2023-24'
def t_no_random_or_backfill():
 g=C['split_guards']; assert g['random_split_forbidden'] and g['same_or_future_season_training_for_test_forbidden'] and g['2023_24_en_1_backfill_forbidden']
def t_baseline_is_schedule_blind():
 b=C['baseline_model']; assert b['id']=='M0_TEAM_COMPETITION_RIDGE_MULTINOMIAL_V1'; assert not any('schedule' in x.lower() for x in b['features']); assert 'round_stage' in b['forbidden_features']
def t_candidate_fixed():
 m=C['candidate_model']; assert len(m['schedule_numeric_features'])==10; assert m['round_number_used'] is False; assert m['feature_selection'] is False and m['hyperparameter_search'] is False and m['candidate_family_search'] is False; assert len(m['interaction_features'])==1 and '10 schedule_numeric_features' in m['interaction_features'][0]
def t_metric_and_bootstrap_fixed():
 m=C['metrics']; assert m['primary']=='pooled_multiclass_logloss_delta_candidate_minus_baseline'; b=m['bootstrap']; assert b['unit']=='source_path league-season block' and b['paired'] and b['replicates']==5000 and b['seed']==20260914 and b['ci_level']==0.9
def t_all_scientific_gates_required():
 g=C['acceptance_gates']; assert g['all_required'] and g['G2_primary_bootstrap']=='paired 90% CI upper bound < 0' and g['G7_top1_non_degradation']=='candidate pooled Top1 hits >= baseline pooled Top1 hits'; assert g['fail_status']=='SCIENTIFIC_EVALUATION_FAIL_CLOSE_NO_RETUNE'
def t_multiplicity_no_rescue():
 m=C['multiplicity_policy']; assert m['primary_hypotheses']==1 and m['model_comparisons']==1; assert m['no_post_view_feature_selection'] and m['no_post_view_threshold_search'] and m['no_post_view_hyperparameter_search'] and m['no_subgroup_rescue'] and m['secondary_metrics_cannot_rescue_primary_failure']
def t_league_only_claim_boundary():
 c=C['coverage_decision']; assert c['decision']=='SUFFICIENT_FOR_NARROW_LEAGUE_ONLY_INCREMENTAL_SCREEN_ONLY'; assert 'total schedule fatigue' in c['not_sufficient_for']; assert 'expected or actual rotation pressure' in c['not_sufficient_for']; assert c['missing_2023_24_england_policy']=='KEEP_EXCLUDED_NO_BACKFILL_NO_IMPUTATION'
def t_zero_label_authority_and_independent_gate():
 s=C['preregistration_stage']; assert s['target_labels_opened'] is False and s['training'] is False and s['tuning'] is False and s['formal_weight']==0 and s['matrix_delta']==0
 a=C['decision_authority']; assert a['this_batch_may_open_target_labels'] is False and a['this_batch_may_train_models'] is False and a['this_batch_may_tune_models'] is False
 i=C['required_independent_acceptance']; assert i['dedicated_or_equivalent_execution_required_before_labels'] and i['common_repository_boundary_gates_alone_are_not_sufficient'] and i['local_gpt_self_validation_alone_is_not_sufficient'] and i['target_labels_remain_closed_until_independent_execution_receipt_exists']
def t_mutations_fail_closed():
 for mutate,err in [
  (lambda c:c['target_definition'].__setitem__('target_label_opening_authorized_in_this_batch',True),'STOP_LABEL_AUTHORIZATION'),
  (lambda c:c['split_guards'].__setitem__('random_split_forbidden',False),'STOP_SPLIT_GUARD_RANDOM_SPLIT_FORBIDDEN'),
  (lambda c:c['candidate_model'].__setitem__('hyperparameter_search',True),'STOP_CANDIDATE_SEARCH')]:
  cc=json.loads(json.dumps(C)); mutate(cc)
  try:v.validate_contract(cc);assert False
  except v.Stop as e: assert str(e)==err
T=[t_contract_valid,t_parent_binding,t_target_hda_only,t_folds_exact,t_no_random_or_backfill,t_baseline_is_schedule_blind,t_candidate_fixed,t_metric_and_bootstrap_fixed,t_all_scientific_gates_required,t_multiplicity_no_rescue,t_league_only_claim_boundary,t_zero_label_authority_and_independent_gate,t_mutations_fail_closed]
if __name__=='__main__':
 for f in T:f();print('PASS',f.__name__)
 print(f'{len(T)}/{len(T)} PASS')
