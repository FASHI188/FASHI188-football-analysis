from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
C = ROOT / 'v3_sir_fallback_prereg_contract_v1.json'
I = ROOT / 'v3_sir_fallback_zero_label_inventory_v1.json'


def load(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))


def validate() -> dict:
    c, i = load(C), load(I)
    assert c['schema_version'] == 'football3-v3-sir-fallback-prereg-v1'
    assert c['status'] == 'DESIGN_LOCKED_ZERO_LABEL_MATERIALIZATION_PENDING'
    assert c['exact_base'] == '40b6f6e1871b90a66f42251310dfda04fc6d47ea'
    assert c['branch'] == 'football3/v3-sir-fallback-v1'
    assert c['scope']['route_population'] == 'FROZEN_V1_EXACT_FALLBACK_ONLY'
    assert c['scope']['old_stage6_c_revival'] is False
    assert c['scope']['old_stage6_c_evidence_reused'] is False
    assert c['candidate']['parameter_count'] == 2
    assert c['candidate']['zero_vector_exact_baseline'] is True
    assert c['candidate']['score_support_unchanged'] is True
    assert c['pit_features']['future_schedule_used'] is False
    assert c['pit_features']['cup_or_uefa_schedule_used'] is False
    assert c['pit_features']['final_lineup_used'] is False
    assert c['cohorts']['sealed_confirmation_seasons'] == ['2012/13', '2013/14']
    assert c['cohorts']['sealed_confirmation_theoretical_n'] == 3652
    assert c['confirmation']['classification'] == 'SEALED_HISTORICAL_OOS_CONFIRMATION'
    assert c['confirmation']['prospective_pass_claim_forbidden'] is True
    assert c['inactive'] == {'status': 'NOT_AVAILABLE', 'weight': 0, 'matrix_delta': 0, 'data_ready': False}
    assert all(c['forbidden_changes'].values())
    assert i['audit_mode'] == 'ZERO_LABEL_METADATA_ONLY'
    assert i['result_values_read'] == i['goal_values_read'] == i['target_labels_opened'] == 0
    assert i['confirmation_reservation']['admitted_n'] == 0
    assert i['confirmation_reservation']['status'] == 'RESERVED_NOT_MATERIALIZED'
    assert i['development_reservation']['labels_opened'] is False
    assert i['source_snapshot']['commit'] == c['source']['transport_snapshot_commit']
    assert i['source_snapshot']['tree'] == c['source']['transport_snapshot_tree']
    return {
        'status': 'PASS',
        'research_line': c['research_line'],
        'classification': c['classification'],
        'confirmation_theoretical_n': c['cohorts']['sealed_confirmation_theoretical_n'],
        'labels_opened': 0,
        'training_performed': False,
        'tuning_performed': False,
        'next': i['decision'],
    }

if __name__ == '__main__':
    print(json.dumps(validate(), sort_keys=True))
