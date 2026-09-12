from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTRACT = ROOT / 'v3_firstbatch_candidate3_bounded_draw_residual_contract_v1.json'
AUDIT = ROOT / 'v3_firstbatch_candidate3_coverage_audit_v1.json'
EXPECTED_BASE = '40b6f6e1871b90a66f42251310dfda04fc6d47ea'
EXPECTED_PARENT = '8a0da8528b9074e095cab323f825fd53570b5db4'


def load():
    c = json.loads(CONTRACT.read_text(encoding='utf-8'))
    a = json.loads(AUDIT.read_text(encoding='utf-8'))
    return c, a


def validate(c, a):
    assert c['exact_base'] == a['exact_base'] == EXPECTED_BASE
    assert c['second_candidate_parent']['research_head'] == a['parent_candidate_head'] == EXPECTED_PARENT
    assert c['candidate']['single_candidate_only'] is True
    assert c['candidate']['independent_1x2_head_forbidden'] is True
    assert c['candidate']['manual_draw_bonus_forbidden'] is True
    assert c['candidate']['top1_threshold_optimization_forbidden'] is True
    assert c['candidate']['weak_side_rule'].startswith('Every weak-side-win score cell remains')
    assert c['candidate']['total_rule'].startswith('P_candidate(T=t) == P_parent(T=t)')
    assert c['candidate']['gamma_bounds'] == [0.0, 1.0]
    assert c['candidate']['gamma_zero_exact_parent'] is True
    assert c['candidate']['matrix_delta_cap'] == 0.03
    assert c['chapter20_data_gate']['consumed_or_result_viewed_identity_reuse_forbidden'] is True
    assert c['chapter20_data_gate']['candidate3_eligible_set_is_subset_of_candidate2_eligible_set'] is True
    assert c['coverage_decision']['candidate2_strict_historical_usable_n'] == 0
    assert c['coverage_decision']['candidate3_strict_historical_usable_n'] == 0
    assert c['coverage_decision']['labels_opened'] == 0
    assert c['coverage_decision']['training_performed'] is False
    assert c['coverage_decision']['tuning_performed'] is False
    assert c['coverage_decision']['terminal'] == 'STOP_DATA_COVERAGE_PRE_DEVELOPMENT'
    assert c['inactive'] == {'status':'NOT_AVAILABLE','weight':0,'matrix_delta':0,'data_ready':False}
    assert a['parent_coverage_evidence']['strict_usable_n'] == 0
    assert a['result_values_read'] == a['goal_values_read'] == a['target_xg_values_read'] == 0
    assert a['usable_n'] == 0 and a['data_ready'] is False
    assert a['decision'] == 'STOP_DATA_COVERAGE_PRE_DEVELOPMENT'
    assert c['legacy_evidence']['code_reuse_allowed'] is False
    assert c['legacy_evidence']['parameter_reuse_allowed'] is False
    return {'status':'PASS','decision':a['decision'],'usable_n':a['usable_n'],'labels_opened':0}


if __name__ == '__main__':
    print(json.dumps(validate(*load()), sort_keys=True))
