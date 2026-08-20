from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest

from football3_core import (
    Football3ContractError,
    MASTER_PREDICTION_CUTOFF,
    SealedPool,
    assert_disjoint_identity_sets,
    assert_exact_one_to_one_join,
    assert_feature_pit,
    assert_master_prediction_cutoff,
    assert_same_prediction_cutoff,
    assert_scoring_identities_match_contract,
    assert_sealed_boundaries,
    assert_temporal_oos,
    classwise_ece,
    collapse_total_goals,
    devig_two_way,
    evaluate_frozen_experiment,
    multiclass_brier,
    multiclass_logloss,
    normalized_rps,
    ordered_identity_sha256,
    ou_tail_k,
    paired_bootstrap_delta_logloss,
    paired_bootstrap_proper_score_deltas,
    required_paired_n_from_observed_delta,
    score_bundle,
    top1_ece,
    topk_accuracy,
    validate_nested_ou_tails,
    validate_probability_matrix,
    validate_target,
)


def _ids(n:int)->list[str]:
    return [hashlib.sha256(f'id-{i}'.encode()).hexdigest() for i in range(n)]


def test_ou_mapping_devig_shape_and_duplicate_line_guards():
    assert [ou_tail_k(x) for x in (0.5,1.5,2.5,3.5,4.5)] == [1,2,3,4,5]
    p=devig_two_way([2.0,1.5],[2.0,3.0]); assert np.allclose(p,[0.5,2/3])
    with pytest.raises(Football3ContractError): devig_two_way([[2.0],[2.1]],[2.0,2.1])
    validate_nested_ou_tails([0.5,1.5,2.5],[0.85,0.60,0.40])
    with pytest.raises(Football3ContractError): validate_nested_ou_tails([0.5,1.5,2.5],[0.50,0.60,0.40])
    with pytest.raises(Football3ContractError): validate_nested_ou_tails([0.5,0.5],[0.7,0.7])


def test_target_probability_and_total_goal_integer_guards():
    assert validate_target([0,1,7]).tolist()==[0,1,7]
    assert collapse_total_goals([0,6,7,10]).tolist()==[0,6,7,7]
    with pytest.raises(Football3ContractError): collapse_total_goals([1.9])
    with pytest.raises(Football3ContractError): collapse_total_goals([np.nan])
    p=validate_probability_matrix([[0.7,0.3,0,0,0,0,0,0]]); assert p.shape==(1,8)
    with pytest.raises(Football3ContractError): validate_target([8])
    with pytest.raises(Football3ContractError): validate_probability_matrix([[0.6,0.5,0,0,0,0,0,0]])
    with pytest.raises(Football3ContractError): validate_probability_matrix(np.empty((0,8)))


def test_metrics_calibration_and_lengths_have_fixed_mapping():
    p=np.array([[0.70,0.20,0.10,0,0,0,0,0],[0.10,0.20,0.70,0,0,0,0,0]])
    y=np.array([0,2])
    assert np.isclose(multiclass_logloss(p,y),-np.log(0.7))
    assert 0<=multiclass_brier(p,y)<=2
    assert 0<=normalized_rps(p,y)<=1
    assert 0<=top1_ece(p,y,5)<=1
    assert 0<=classwise_ece(p,y,5)<=1
    s=score_bundle(p,y,calibration_bins=5)
    assert s['Top1']==1.0 and s['Top3']==1.0
    assert 'Top1ECE' in s and 'ClasswiseECE' in s
    assert s['probability_residual_max']<=1e-15
    with pytest.raises(Football3ContractError): top1_ece(p,[0],5)
    with pytest.raises(Football3ContractError): classwise_ece(p,[0],5)
    with pytest.raises(Football3ContractError): topk_accuracy(p,[0],1)


def test_paired_bootstrap_is_match_paired_seeded_and_multimetric():
    y=np.array([0,1,2,3]*30); b=np.full((len(y),8),0.02); c=np.full((len(y),8),0.02)
    for i,yi in enumerate(y):
        b[i,yi]=0.86; c[i,yi]=0.88; wrong=(yi+1)%8; b[i,wrong]+=1.0-b[i].sum(); c[i,wrong]+=1.0-c[i].sum()
    r1=paired_bootstrap_delta_logloss(b,c,y,n_resamples=500,seed=123); r2=paired_bootstrap_delta_logloss(b,c,y,n_resamples=500,seed=123)
    assert r1==r2 and r1['paired'] is True and r1['delta']<0
    all_scores=paired_bootstrap_proper_score_deltas(b,c,y,n_resamples=500,seed=123)
    assert set(all_scores)=={'LogLoss','Brier','RPS'}
    assert all(v['delta']<0 for v in all_scores.values())
    with pytest.raises(Football3ContractError): paired_bootstrap_delta_logloss(b,c,y,n_resamples=500.0,seed=123)
    with pytest.raises(Football3ContractError): paired_bootstrap_delta_logloss(b,c,y,n_resamples=500,seed=1.2)


def test_power_planning_parameter_guards():
    d=np.linspace(-0.1,0.02,60)
    assert required_paired_n_from_observed_delta(d,alpha=0.10,power=0.8)>=1
    with pytest.raises(Football3ContractError): required_paired_n_from_observed_delta(d,alpha=0)
    with pytest.raises(Football3ContractError): required_paired_n_from_observed_delta(d,power=1.0)
    with pytest.raises(Football3ContractError): required_paired_n_from_observed_delta(d,conservative_multiplier=0.5)


def test_identity_disjoint_exact_join_and_scoring_lock():
    assert_disjoint_identity_sets({'a':{'x','y'},'b':{'z'}})
    with pytest.raises(Football3ContractError): assert_disjoint_identity_sets({'a':{'x'},'b':{'x'}})
    ids=_ids(2); digest=ordered_identity_sha256(ids)
    assert digest==ordered_identity_sha256(ids)
    with pytest.raises(Football3ContractError): ordered_identity_sha256(['not-a-sha'])
    c={'data_plan':{'identity_count':2,'ordered_identity_sha256':digest}}
    assert assert_scoring_identities_match_contract(ids,c,2)==digest
    with pytest.raises(Football3ContractError): assert_scoring_identities_match_contract(list(reversed(ids)),c,2)
    left=pd.DataFrame({'id':['1','2'],'x':[3,4]}); right=pd.DataFrame({'id':['1','2'],'T':[1,2]})
    assert len(assert_exact_one_to_one_join(left,right,keys=['id'],expected_rows=2))==2
    with pytest.raises(Football3ContractError): assert_exact_one_to_one_join(left,right.iloc[:1],keys=['id'],expected_rows=2)
    with pytest.raises(Football3ContractError): assert_exact_one_to_one_join(left,right,keys=[])


def test_temporal_pit_timezone_and_master_cutoff_fail_closed():
    assert_temporal_oos(['2024-01-01T00:00:00Z','2024-02-01T00:00:00Z'],['2024-03-01T00:00:00Z'])
    with pytest.raises(Football3ContractError): assert_temporal_oos(['2024-03-01T00:00:00Z'],['2024-03-01T00:00:00Z'])
    with pytest.raises(Football3ContractError): assert_temporal_oos(['2024-01-01'],['2024-03-01T00:00:00Z'])
    f=pd.DataFrame({'cutoff':['2024-01-02T12:00:00Z'],'odds_ts':['2024-01-02T11:59:00Z']})
    assert_feature_pit(f,cutoff_col='cutoff',feature_timestamp_cols=['odds_ts'])
    f.loc[0,'odds_ts']='2024-01-02T12:01:00Z'
    with pytest.raises(Football3ContractError): assert_feature_pit(f,cutoff_col='cutoff',feature_timestamp_cols=['odds_ts'])
    f.loc[0,'odds_ts']='2024-01-02 11:59:00'
    with pytest.raises(Football3ContractError): assert_feature_pit(f,cutoff_col='cutoff',feature_timestamp_cols=['odds_ts'])
    f.loc[0,'odds_ts']=None
    with pytest.raises(Football3ContractError): assert_feature_pit(f,cutoff_col='cutoff',feature_timestamp_cols=['odds_ts'])
    with pytest.raises(Football3ContractError): assert_feature_pit(pd.DataFrame({'cutoff':['2024-01-02T12:00:00Z']}),cutoff_col='cutoff',feature_timestamp_cols=[])
    assert_same_prediction_cutoff('T-15m',' t-15m ')
    assert_master_prediction_cutoff('T-15m',' t-15m ')
    assert MASTER_PREDICTION_CUTOFF=='T-15m'
    with pytest.raises(Football3ContractError): assert_same_prediction_cutoff('opening','T-15m')
    with pytest.raises(Football3ContractError): assert_master_prediction_cutoff('T-60m')


def _canonical_contract(ids:list[str], minimum_n:int=120):
    return {
      'data_plan':{'identity_count':len(ids),'ordered_identity_sha256':ordered_identity_sha256(ids)},
      'sample_plan':{'development_minimum_n':minimum_n,'confirmation':False},
      'metrics':{'calibration':{'bins':5}},
      'bootstrap':{'resamples':500,'seed':123,'ci':0.90},
      'oos_design':{'minimum_test_rows_per_fold':20},
      'success_gates':{
        'primary':{'delta_max':0.0,'bootstrap_ci_high_max':0.0},
        'secondary_noninferiority':{'Brier_delta_max':0.0,'RPS_delta_max':0.0,'Top1ECE_delta_max':0.0,'ClasswiseECE_delta_max':0.0},
        'temporal_consistency':{'minimum_fold_win_fraction':0.66},
        'domain_consistency':{'minimum_domains':3,'minimum_rows_per_domain':20,'minimum_win_fraction':0.66,'max_domain_logloss_regression':0.0},
      },
    }


def test_canonical_evaluator_applies_frozen_gates_identity_and_sample_plan():
    n=120; ids=_ids(n); y=np.arange(n)%4
    b=np.full((n,8),0.01); c=np.full((n,8),0.01)
    for i,yi in enumerate(y):
        b[i,yi]=0.84; c[i,yi]=0.88
        wb=(yi+1)%8; b[i,wb]+=1.0-b[i].sum(); c[i,wb]+=1.0-c[i].sum()
    folds=np.repeat(['f1','f2','f3'],40); domains=np.repeat(['L1','L2','L3'],40)
    contract=_canonical_contract(ids)
    out=evaluate_frozen_experiment(b,c,y,identity_sha256=ids,fold_ids=folds,domain_ids=domains,contract=contract)
    assert out['terminal']=='PASS' and out['all_gates_pass'] is True
    assert out['scored_identity_sha256']==contract['data_plan']['ordered_identity_sha256']
    assert out['frozen_minimum_n']==120
    assert set(out['bootstrap'])=={'LogLoss','Brier','RPS'}
    bad=_canonical_contract(ids); bad['success_gates']['primary']['delta_max']=-1.0
    out2=evaluate_frozen_experiment(b,c,y,identity_sha256=ids,fold_ids=folds,domain_ids=domains,contract=bad)
    assert out2['terminal']=='PARK' and out2['gate_checks']['primary_delta'] is False
    with pytest.raises(Football3ContractError): evaluate_frozen_experiment(b,c,y,identity_sha256=list(reversed(ids)),fold_ids=folds,domain_ids=domains,contract=contract)
    too_large=_canonical_contract(ids,minimum_n=121)
    with pytest.raises(Football3ContractError): evaluate_frozen_experiment(b,c,y,identity_sha256=ids,fold_ids=folds,domain_ids=domains,contract=too_large)


def test_sealed_pool_guard():
    pools=[SealedPool('C070-F1597'),SealedPool('N17-reserve266')]
    assert_sealed_boundaries({'C070-F1597':0,'N17-reserve266':0},pools)
    with pytest.raises(Football3ContractError): assert_sealed_boundaries({'C070-F1597':1},pools)
