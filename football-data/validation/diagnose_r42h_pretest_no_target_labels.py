#!/usr/bin/env python3
"""Engineering-only R42H pre-test diagnostic.

This script intentionally stops before reading target fixed200 outcome/total labels or
computing any target metric. It reconstructs prior exclusions, red/foul coverage, binds
the deterministic sample identity, performs policy-only C selection, fits both models on
historical fit rows, and generates target probability matrices only. The receipt is for
engineering/sample-accounting diagnostics and cannot be used as scientific evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json, select_fixed_identities, split_for_latest_complete
from evaluate_r42g_discipline_referee_direct_total_fixed200 import reproduce_prior2600
from evaluate_r42h_team_red_foul_direct_total_fixed200 import load_team_red_foul_rows, build_team_red_foul_features
from v510_historical_structure_features_r1 import audit_data_identity, build_features, complete_seasons, select_core_features, ResearchError
from v510_historical_structure_model_r1 import align_probability, make_model, select_C

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / 'config' / 'r42h_team_red_foul_direct_total_fixed200.json'
OUT = ROOT / 'manifests' / 'r42h_pretest_no_target_labels_status.json'
TOTAL_CLASSES = list(range(8))


def main() -> None:
    cfg = load_json(CFG)
    base_cfg = load_json(ROOT / str(cfg['base_model_config']))
    raw = pd.read_csv(ROOT / str(cfg['input_ledger']))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    excluded2600, prior_hashes = reproduce_prior2600(raw, seasons, cfg)
    if len(excluded2600) != 2600:
        raise ResearchError(f'prior exclusion count mismatch: {len(excluded2600)}')

    features = add_identity_key(build_features(raw))
    features['split'] = split_for_latest_complete(features, seasons, cfg)
    features['date_norm'] = pd.to_datetime(features['date_key'], errors='raise').dt.date.astype(str)
    competitions = set(features.competition_id.astype(str))
    drows, source_cov = load_team_red_foul_rows(competitions)
    discipline, discipline_audit = build_team_red_foul_features(drows, cfg)
    names = [str(x) for x in cfg['feature_contract']['feature_names']]
    keep = ['competition_id','season','date_norm','home_team','away_team','discipline_team_history_ok'] + names
    merged = features.merge(discipline[keep], on=['competition_id','season','date_norm','home_team','away_team'], how='left', validate='one_to_one')

    target = merged[(merged.split == 'target_pool') & merged[names].notna().all(axis=1) & (merged.discipline_team_history_ok.fillna(0).astype(int) == 1)].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded2600)].copy()
    minimum = int(cfg['coverage_gate']['minimum_fresh_target_rows_after_prior2600_exclusion'])
    if len(fresh) < minimum:
        receipt = {
            'status':'STOP_R42H_PRETEST_COVERAGE_LT200',
            'data_identity':identity,
            'excluded_incomplete_latest_seasons':excluded_latest,
            'prior_exclusion_rows':2600,
            'coverage_rows':int(len(fresh)),
            'sample_bound':False,
            'target_labels_read_for_diagnostic':0,
            'scientific_metrics_computed':0,
        }
        OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(receipt, ensure_ascii=False, indent=2)); return

    selected, sample_sha = select_fixed_identities(fresh, int(cfg['sample_contract']['sample_size']), int(cfg['sample_contract']['seed']))
    sample = fresh[fresh.identity_key.astype(str).isin(set(selected))].sort_values('identity_key').copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded2600:
        raise ResearchError('diagnostic sample identity contract failed')

    fit_rows = merged[merged.split.isin(['train','policy']) & merged[names].notna().all(axis=1) & (merged.discipline_team_history_ok.fillna(0).astype(int) == 1)].copy()
    train = fit_rows[fit_rows.split == 'train'].copy()
    policy = fit_rows[fit_rows.split == 'policy'].copy()
    core = select_core_features(merged)
    selected_C, policy_grid = select_C(train, policy, core, 'total_class', TOTAL_CLASSES, base_cfg)
    challenger_features = core + names
    baseline = make_model(float(selected_C), base_cfg)
    challenger = make_model(float(selected_C), base_cfg)
    baseline.fit(fit_rows[core], fit_rows.total_class)
    challenger.fit(fit_rows[challenger_features], fit_rows.total_class)
    p_base = align_probability(baseline, sample[core], TOTAL_CLASSES)
    p_ch = align_probability(challenger, sample[challenger_features], TOTAL_CLASSES)

    receipt = {
        'status':'PASS_R42H_PRETEST_NO_TARGET_LABELS',
        'data_identity':identity,
        'prior_exclusion_rows':2600,
        'prior_hashes':prior_hashes,
        'coverage_rows':int(len(fresh)),
        'coverage_by_competition':{str(k):int(v) for k,v in fresh.groupby('competition_id').size().sort_index().items()},
        'sample_bound':True,
        'sample_rows':200,
        'sample_seed':int(cfg['sample_contract']['seed']),
        'sample_identity_sha256':sample_sha,
        'sample_overlap_prior2600':0,
        'sample_competitions':{str(k):int(v) for k,v in sample.groupby('competition_id').size().sort_index().items()},
        'baseline_policy_selected_C':float(selected_C),
        'baseline_policy_grid':policy_grid,
        'fit_rows':int(len(fit_rows)),
        'train_rows':int(len(train)),
        'policy_rows':int(len(policy)),
        'baseline_feature_count':int(len(core)),
        'challenger_feature_count':int(len(challenger_features)),
        'baseline_max_solver_iterations':int(np.max(baseline.named_steps['model'].n_iter_)),
        'challenger_max_solver_iterations':int(np.max(challenger.named_steps['model'].n_iter_)),
        'baseline_probability_sum_max_residual':float(np.max(np.abs(p_base.sum(axis=1)-1.0))),
        'challenger_probability_sum_max_residual':float(np.max(np.abs(p_ch.sum(axis=1)-1.0))),
        'target_labels_read_for_diagnostic':0,
        'target_total_class_read_for_diagnostic':0,
        'target_goal_difference_read_for_diagnostic':0,
        'scientific_metrics_computed':0,
        'scientific_verdict':'NONE_ENGINEERING_DIAGNOSTIC_ONLY',
        'source_coverage':source_cov,
        'feature_audit':discipline_audit,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
