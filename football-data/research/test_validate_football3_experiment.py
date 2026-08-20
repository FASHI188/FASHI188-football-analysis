from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from validate_football3_experiment import PreflightError, validate_contract, validate_runner

ROOT_SHA = 'e3e73c998020beef585cc459a69ea5b73b44ddb3'


def valid_contract() -> dict:
    return {
        'schema_version': 1,
        'project_id': 'football3',
        'scientific_root': {'experiment': 'C072-C', 'sha': ROOT_SHA},
        'branch': 'football3/example-new-pt-hypothesis',
        'scientific_question': {
            'primary_target': 'P(T=0,1,2,3,4,5,6,7+)',
            'question': 'synthetic validation only',
            'direct_draw_optimization': False,
        },
        'prediction_cutoff': {
            'baseline': 'T-15m',
            'candidate': 'T-15m',
            'pit_definition': 'all inputs known by T-15m',
        },
        'baseline': {'description': 'same-cutoff market anchor'},
        'candidate': {
            'description': 'materially new P(T) hypothesis',
            'post_view_neighbor_of_parked_hypothesis': False,
        },
        'data_plan': {
            'identity_lock_before_labels': True,
            'global_consumption_audit': True,
            'target_identity_overlap_with_consumed': 0,
            'evidence_class': 'DEVELOPMENT',
            'random_split': False,
        },
        'oos_design': {'temporal': True, 'shuffle': False},
        'metrics': {
            'proper_scores': ['LogLoss', 'Brier', 'RPS'],
            'top1_primary': False,
            'implementation': 'football3_core',
        },
        'bootstrap': {'paired_match': True, 'resamples': 5000, 'seed': 72001},
        'sample_plan': {
            'confirmation': False,
            'power_or_precision_plan_frozen': False,
            'optional_stopping': False,
        },
        'sealed_boundaries': [
            {'name': 'C070-F Confirmation1597', 'authorized_access_count': 0},
            {'name': 'N17 reserve266', 'authorized_access_count': 0},
            {'name': 'N18C confirmation150', 'authorized_access_count': 0},
        ],
        'method_shopping': {
            'same_labels_rescue_allowed': False,
            'frozen_dimensions': ['candidate', 'baseline', 'folds', 'gates'],
        },
    }


def test_valid_contract_passes():
    validate_contract(valid_contract())


def test_same_cutoff_is_mandatory():
    c = valid_contract()
    c['prediction_cutoff']['baseline'] = 'opening'
    with pytest.raises(PreflightError):
        validate_contract(c)


def test_direct_draw_optimization_is_rejected():
    c = valid_contract()
    c['scientific_question']['direct_draw_optimization'] = True
    with pytest.raises(PreflightError):
        validate_contract(c)


def test_confirmation_requires_power_plan():
    c = valid_contract()
    c['sample_plan']['confirmation'] = True
    with pytest.raises(PreflightError):
        validate_contract(c)
    c['sample_plan']['power_or_precision_plan_frozen'] = True
    validate_contract(c)


def test_consumed_overlap_requires_replication_classification():
    c = valid_contract()
    c['data_plan']['target_identity_overlap_with_consumed'] = 4
    with pytest.raises(PreflightError):
        validate_contract(c)
    c['data_plan']['evidence_class'] = 'REPLICATION'
    validate_contract(c)


def write_runner(tmp_path: Path, body: str) -> Path:
    p = tmp_path / 'runner.py'
    p.write_text(body, encoding='utf-8')
    return p


def test_runner_requires_core(tmp_path):
    p = write_runner(tmp_path, 'x = 1\n')
    with pytest.raises(PreflightError):
        validate_runner(p)


def test_dataframe_T_is_rejected(tmp_path):
    p = write_runner(tmp_path, 'import football3_core\nimport pandas as pd\nx=pd.DataFrame({"T":[1]})\ny=x.T\n')
    with pytest.raises(PreflightError):
        validate_runner(p)


def test_random_split_is_rejected(tmp_path):
    p = write_runner(tmp_path, 'import football3_core\nfrom sklearn.model_selection import train_test_split\ntrain_test_split([1,2],[1,2])\n')
    with pytest.raises(PreflightError):
        validate_runner(p)


def test_clean_runner_passes(tmp_path):
    p = write_runner(tmp_path, 'from football3_core import score_bundle\nTARGET_COL="T"\n')
    validate_runner(p)
