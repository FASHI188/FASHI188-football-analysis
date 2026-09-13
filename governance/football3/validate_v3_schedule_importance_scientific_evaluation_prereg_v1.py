#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path

SCHEMA_VERSION = "football3-v3-schedule-importance-scientific-evaluation-prereg-v1"
CONTRACT_FILE = "v3_schedule_importance_scientific_evaluation_prereg_contract_v1.json"
EXPECTED_PARENT = {
    "feature_construction_head": "ed165d4ea86e4be468728771ea70027ece696215",
    "run_id": 34768984931,
    "artifact_id": 10321625746,
    "artifact_digest_sha256": "6cd43d1df017ab2a0a1bb698de51e9112530e48ea81a8d5f1c699c012c545b5d",
    "feature_sha256": "c9b14435062f59f1caaf4ffdbf83373d7aa50b858a48057f66b89cc0605ecea4",
    "feature_rows": 30531,
    "feature_receipt_sha256": "7ae768b3f6188a98de678d9dfcf52bb2d43b0ff0fced70359780c8a59382ab8f",
    "head_binding_sha256": "7bddef8f322b1a5fe4298bfae6e9f8c4b0a94b45810980b653cedbace167d0f5",
    "feature_contract_sha256": "e734a90db0e1992407fa156eac1b5e9c7c52fb823437f300d79b7384675bc5fe",
}
EXPECTED_TEST_SEASONS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
FEATURE_FILES = {
    "feature-construction-receipt.json": EXPECTED_PARENT["feature_receipt_sha256"],
    "head-binding.json": EXPECTED_PARENT["head_binding_sha256"],
    "schedule-features.jsonl": EXPECTED_PARENT["feature_sha256"],
    "v3_schedule_importance_feature_construction_contract_v1.json": EXPECTED_PARENT["feature_contract_sha256"],
}

class Stop(RuntimeError): pass

def sha256_file(p: Path):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def load_contract(path: Path):
    c=json.loads(path.read_text(encoding='utf-8'))
    validate_contract(c)
    return c

def validate_contract(c):
    if c.get('schema_version') != SCHEMA_VERSION: raise Stop('STOP_SCHEMA_VERSION')
    if c.get('status') != 'DESIGN_LOCKED': raise Stop('STOP_NOT_DESIGN_LOCKED')
    if c.get('exact_base') != '475dedfd177b02208f550fe97a89bbd1efa52125': raise Stop('STOP_EXACT_BASE')
    if c.get('branch') != 'football3/v3-schedule-importance-scientific-evaluation-prereg-v1': raise Stop('STOP_BRANCH')
    p=c.get('parent',{})
    for k,v in EXPECTED_PARENT.items():
        if p.get(k) != v: raise Stop('STOP_PARENT_BINDING_'+k.upper())
    s=c.get('preregistration_stage',{})
    required_false=['target_labels_opened','training','tuning','standings_pressure_used','rotation_pressure_used','travel_pressure_used','future_matches_used','stage6_1335_used']
    if any(s.get(k) is not False for k in required_false): raise Stop('STOP_PREREG_ZERO_LABEL_BOUNDARY')
    if s.get('formal_weight') != 0 or s.get('matrix_delta') != 0: raise Stop('STOP_PREREG_WEIGHT_BOUNDARY')
    cov=c.get('coverage_decision',{})
    if cov.get('decision') != 'SUFFICIENT_FOR_NARROW_LEAGUE_ONLY_INCREMENTAL_SCREEN_ONLY': raise Stop('STOP_COVERAGE_DECISION')
    if 'total schedule fatigue' not in cov.get('not_sufficient_for',[]): raise Stop('STOP_TOTAL_FATIGUE_CLAIM_GUARD')
    t=c.get('target_definition',{})
    if t.get('primary_target') != 'REGULATION_FULL_TIME_1X2_H_D_A' or t.get('label_values') != ['H','D','A']: raise Stop('STOP_TARGET_DEFINITION')
    if t.get('target_label_opening_authorized_in_this_batch') is not False: raise Stop('STOP_LABEL_AUTHORIZATION')
    folds=c.get('chronological_outer_folds',[])
    if [x.get('test_season') for x in folds] != EXPECTED_TEST_SEASONS: raise Stop('STOP_OUTER_FOLDS')
    if [x.get('fold') for x in folds] != [1,2,3,4,5]: raise Stop('STOP_FOLD_ORDER')
    guards=c.get('split_guards',{})
    for k in ['random_split_forbidden','same_or_future_season_training_for_test_forbidden','future_unplayed_rows_forbidden','2023_24_en_1_backfill_forbidden','post_target_feature_updates_forbidden']:
        if guards.get(k) is not True: raise Stop('STOP_SPLIT_GUARD_'+k.upper())
    b=c.get('baseline_model',{}); m=c.get('candidate_model',{})
    if b.get('id') != 'M0_TEAM_COMPETITION_RIDGE_MULTINOMIAL_V1': raise Stop('STOP_BASELINE_ID')
    if any('schedule' in x.lower() for x in b.get('features',[])): raise Stop('STOP_BASELINE_SCHEDULE_LEAK')
    if m.get('base_model') != b.get('id') or m.get('round_number_used') is not False: raise Stop('STOP_CANDIDATE_BINDING')
    if m.get('feature_selection') is not False or m.get('hyperparameter_search') is not False or m.get('candidate_family_search') is not False: raise Stop('STOP_CANDIDATE_SEARCH')
    met=c.get('metrics',{})
    if met.get('primary') != 'pooled_multiclass_logloss_delta_candidate_minus_baseline': raise Stop('STOP_PRIMARY_METRIC')
    boot=met.get('bootstrap',{})
    if boot != {'unit':'source_path league-season block','paired':True,'replicates':5000,'seed':20260914,'ci_level':0.9,'statistic':'pooled logloss delta'}: raise Stop('STOP_BOOTSTRAP')
    gates=c.get('acceptance_gates',{})
    for k in ['G1_primary_pooled_logloss','G2_primary_bootstrap','G3_fold_direction','G4_fold_worst_case','G5_brier','G6_rps','G7_top1_non_degradation','G8_competition_stability']:
        if k not in gates: raise Stop('STOP_GATESET_'+k)
    if gates.get('all_required') is not True or gates.get('fail_status') != 'SCIENTIFIC_EVALUATION_FAIL_CLOSE_NO_RETUNE': raise Stop('STOP_FAIL_CLOSE_GATE')
    mul=c.get('multiplicity_policy',{})
    if mul.get('primary_hypotheses') != 1 or mul.get('model_comparisons') != 1: raise Stop('STOP_MULTIPLICITY')
    for k in ['no_post_view_feature_selection','no_post_view_threshold_search','no_post_view_hyperparameter_search','no_subgroup_rescue','secondary_metrics_cannot_rescue_primary_failure','diagnostic_subgroups_cannot_change_pass_fail']:
        if mul.get(k) is not True: raise Stop('STOP_MULTIPLICITY_'+k.upper())
    lg=c.get('leakage_guards',{})
    for k in ['feature_sha_must_equal_parent','feature_row_count_must_equal_parent','only_frozen_feature_keys','no_target_or_score_in_features','no_result_derived_standings','no_final_schedule_backfill','no_final_lineup_or_player_minutes','no_future_matches','same_day_history_policy_inherited_strict_prior_date','training_preprocessing_fit_on_outer_train_only','labels_join_only_by_source_path_row_index','duplicate_or_missing_label_join_stop']:
        if lg.get(k) is not True: raise Stop('STOP_LEAKAGE_GUARD_'+k.upper())
    a=c.get('decision_authority',{})
    if a.get('this_batch_may_open_target_labels') is not False or a.get('this_batch_may_train_models') is not False or a.get('this_batch_may_tune_models') is not False: raise Stop('STOP_AUTHORITY')
    ind=c.get('required_independent_acceptance',{})
    if ind.get('dedicated_or_equivalent_execution_required_before_labels') is not True or ind.get('common_repository_boundary_gates_alone_are_not_sufficient') is not True or ind.get('local_gpt_self_validation_alone_is_not_sufficient') is not True or ind.get('target_labels_remain_closed_until_independent_execution_receipt_exists') is not True: raise Stop('STOP_INDEPENDENT_ACCEPTANCE_CONTRACT')

def verify_artifact_dir(d: Path):
    for name,want in FEATURE_FILES.items():
        p=d/name
        if not p.is_file(): raise Stop('STOP_PARENT_FILE_MISSING_'+name.upper().replace('-','_').replace('.','_'))
        if sha256_file(p) != want: raise Stop('STOP_PARENT_FILE_SHA_'+name.upper().replace('-','_').replace('.','_'))
    n=0; allowed={'source_path','row_index','date','round','round_stage','round_number','team1','team2','home_league_rest_days','away_league_rest_days','home_league_congestion_7d','away_league_congestion_7d','home_league_congestion_14d','away_league_congestion_14d','home_league_congestion_21d','away_league_congestion_21d','home_consecutive_away_before','away_consecutive_away_before'}
    with (d/'schedule-features.jsonl').open(encoding='utf-8') as f:
        for line in f:
            r=json.loads(line); n+=1
            if set(r) != allowed: raise Stop('STOP_PARENT_FEATURE_KEYSET')
    if n != EXPECTED_PARENT['feature_rows']: raise Stop('STOP_PARENT_FEATURE_ROWS')
    receipt=json.loads((d/'feature-construction-receipt.json').read_text())
    if receipt.get('decision')!='PASS_SCHEDULE_FEATURE_CONSTRUCTION_PREREG_NEXT_SCIENTIFIC_EVALUATION_PREREG': raise Stop('STOP_PARENT_RECEIPT_DECISION')
    if receipt.get('score_result_goal_values_read')!=0 or receipt.get('future_matches_allowed') is not False or receipt.get('training') is not False or receipt.get('tuning') is not False: raise Stop('STOP_PARENT_BOUNDARY')
    hb=json.loads((d/'head-binding.json').read_text())
    if hb.get('candidate_head') != EXPECTED_PARENT['feature_construction_head']: raise Stop('STOP_PARENT_HEAD_BINDING')
    return {'artifact_verified':True,'feature_rows':n,'feature_sha256':EXPECTED_PARENT['feature_sha256']}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',default=str(Path(__file__).with_name(CONTRACT_FILE))); ap.add_argument('--artifact-dir')
    a=ap.parse_args(); c=load_contract(Path(a.contract)); result={'decision':'PASS_PREREG_CONTRACT_SELF_VALIDATION','schema_version':SCHEMA_VERSION,'target_labels_opened':False,'training':False,'tuning':False,'independent_acceptance':False}
    if a.artifact_dir: result.update(verify_artifact_dir(Path(a.artifact_dir)))
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__': main()
