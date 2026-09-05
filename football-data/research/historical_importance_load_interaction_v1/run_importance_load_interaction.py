from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FD = HERE.parents[1]
sys.path.insert(0, str(FD / 'research' / 'stage6_pre_b_deep_ppda'))
sys.path.insert(0, str(FD / 'research' / 'historical_direction_screen_v1'))
sys.path.insert(0, str(FD / 'research' / 'historical_baseline_residual_v1'))
sys.path.insert(0, str(FD / 'research' / 'historical_score_shape_candidate_v1'))

import common
import run_stage6_pre_b as bmod
import run_baseline_residual_diagnostics as resid
import run_score_shape_candidate as shape

EPS = 1e-15


def clip(x: float, cap: float) -> float:
    return max(-cap, min(cap, float(x)))


def finite(v) -> bool:
    return v is not None and math.isfinite(float(v))


def add_interactions(r: dict) -> dict:
    out = dict(r)

    def mul(a: str, b: str, *, abs_a: bool = False):
        if not finite(r.get(a)) or not finite(r.get(b)):
            return None
        x = float(r[a])
        if abs_a:
            x = abs(x)
        return x * float(r[b])

    out['rest_diff_x_importance_diff'] = mul('rest_diff', 'importance_diff')
    out['abs_rest_diff_x_importance_mean'] = mul('rest_diff', 'importance_mean', abs_a=True)
    out['cong14_diff_x_importance_diff'] = mul('cong14_diff', 'importance_diff')
    out['abs_cong14_diff_x_importance_mean'] = mul('cong14_diff', 'importance_mean', abs_a=True)
    out['rotation_diff_x_importance_diff'] = mul('rotation_diff', 'importance_diff')
    out['abs_rotation_diff_x_importance_mean'] = mul('rotation_diff', 'importance_mean', abs_a=True)
    out['rotation_mean_x_importance_mean'] = mul('rotation_mean', 'importance_mean')
    return out


def fit_importance_base(train: list[dict], c: dict) -> dict:
    keys = list(c['base']['importance_low2_features'])
    ridge = float(c['base']['importance_low2_fixed_ridge'])
    means, sds = shape.fit_scaler(train, keys)
    X = shape.design(train, keys, means, sds)
    y = [int(r['hg'] + r['ag'] <= 2) for r in train]
    off = [resid.logit(r['b_low']) for r in train]
    beta = resid.fit_offset(X, y, off, ridge)
    return {'keys': keys, 'means': means, 'sds': sds, 'beta': beta, 'train_n': len(train)}


def linear_eta(r: dict, p: dict) -> float:
    z = float(p['beta'][0])
    for j, k in enumerate(p['keys'], 1):
        v = r.get(k)
        zz = 0.0 if not finite(v) else (float(v) - float(p['means'][k])) / float(p['sds'][k])
        z += float(p['beta'][j]) * zz
    return z


def apply_importance_base(r: dict, m: list[list[float]], p: dict, cap: float):
    eta = clip(linear_eta(r, p), cap)
    return shape.apply_region_tilt(m, lambda h, a: h + a <= 2, eta)


def complete(r: dict, keys: list[str]) -> bool:
    return all(finite(r.get(k)) for k in keys)


def fit_interaction(train: list[dict], base_mats: dict, keys: list[str], c: dict) -> dict:
    valid = [r for r in train if complete(r, keys)]
    if not valid:
        raise RuntimeError('no complete interaction training rows')
    means, sds = shape.fit_scaler(valid, keys)
    X = shape.design(valid, keys, means, sds)
    y = [int(r['hg'] + r['ag'] <= 2) for r in valid]
    off = [resid.logit(resid.matrix_low(base_mats[r['fixture_id']])) for r in valid]
    beta = resid.fit_offset(X, y, off, float(c['interaction_fit']['fixed_ridge']))
    return {
        'keys': keys,
        'means': means,
        'sds': sds,
        'beta': beta,
        'train_n_total': len(train),
        'train_n_complete': len(valid),
        'train_coverage': len(valid) / len(train),
    }


def apply_interaction(r: dict, m: list[list[float]], p: dict, cap: float):
    if not complete(r, list(p['keys'])):
        return shape.clone(m)
    eta = clip(linear_eta(r, p), cap)
    return shape.apply_region_tilt(m, lambda h, a: h + a <= 2, eta)


def metric_delta(rows: list[dict], base: dict, cand: dict) -> dict:
    bm = shape.metric(rows, base)
    cm = shape.metric(rows, cand)
    return {
        'baseline': bm,
        'candidate': cm,
        'deltas': {
            'exact_score_logloss': cm['exact_score_logloss'] - bm['exact_score_logloss'],
            'total_goals_logloss': cm['total_goals_logloss'] - bm['total_goals_logloss'],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    for x in ('contract', 'v311', 'v31', 'usr1', 'v2', 'xg', 'v1', 'v1_result', 'db', 'xg_identity', 'out'):
        ap.add_argument('--' + x.replace('_', '-'), type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    c = json.loads(a.contract.read_text())
    assert c['status'] == 'FROZEN_BEFORE_INTERACTION_SCREEN'
    assert c['pit_rules']['2023_used'] is False
    assert c['pit_rules']['prospective_1335_touched'] is False

    b = common.build_frozen_baseline(a, 'importance_load_interaction')
    dev0 = [r for r in b['rows'] if r['season'] in (2020, 2021, 2022) and r['fixture_id'] in b['bmap']]
    assert len(dev0) == 5478

    fmap = resid.feature_map(a.db)
    wanted = {r['fixture_id'] for r in dev0}
    snaps, srec = bmod.make_snapshots(a.db, wanted, float(c['base']['stage6_b_half_life']))

    bmats = {}
    rows = []
    for src in dev0:
        fid = src['fixture_id']
        p, _ = bmod.predict(b['bmap'][fid], snaps.get(fid), float(c['base']['stage6_b_coefficient']))
        m = common.region_rescale(b['bmats'][fid], b['bmap'][fid], p)
        bmats[fid] = m
        bp = list(map(float, b['bmap'][fid]))
        side = bp[0] / max(EPS, bp[0] + bp[2])
        feat = dict(fmap[fid])
        feat.update({
            'fixture_id': fid,
            'season': int(src['season']),
            'hg': int(src['home_goals']),
            'ag': int(src['away_goals']),
            'balance_abs': abs(resid.logit(side)),
            'b_low': resid.matrix_low(m),
        })
        rows.append(add_interactions(feat))

    families = {
        name: list(c['interaction_fit'][name])
        for name in c['selection_rule']['candidate_names']
    }
    imp_cap = float(c['base']['importance_low2_max_abs_log_tilt'])
    int_cap = float(c['interaction_fit']['max_abs_incremental_log_tilt'])
    folds = []
    agg = {
        name: {'exact': [], 'total': [], 'coverage': [], 'max_1x2': 0.0, 'max_cell': 0.0}
        for name in families
    }

    for season in c['data_roles']['rolling_tests']:
        season = int(season)
        train = [r for r in rows if r['season'] < season]
        test = [r for r in rows if r['season'] == season]
        imp = fit_importance_base(train, c)
        imp_train = {r['fixture_id']: apply_importance_base(r, bmats[r['fixture_id']], imp, imp_cap) for r in train}
        imp_test = {r['fixture_id']: apply_importance_base(r, bmats[r['fixture_id']], imp, imp_cap) for r in test}
        fold = {
            'season': season,
            'train_n': len(train),
            'test_n': len(test),
            'importance_low2_fold_params': imp,
            'baseline_b_plus_importance_low2': shape.metric(test, imp_test),
            'candidates': {},
        }
        for name, keys in families.items():
            ip = fit_interaction(train, imp_train, keys, c)
            cand = {r['fixture_id']: apply_interaction(r, imp_test[r['fixture_id']], ip, int_cap) for r in test}
            scored = metric_delta(test, imp_test, cand)
            coverage = sum(complete(r, keys) for r in test) / len(test)
            e = shape.max_1x2_error(imp_test, cand)
            cd = shape.max_cell_delta(imp_test, cand)
            rec = {
                'features': keys,
                'fit': ip,
                'test_coverage': coverage,
                **scored,
                'max_1x2_abs_error': e,
                'max_score_cell_abs_delta': cd,
            }
            fold['candidates'][name] = rec
            agg[name]['exact'].append(scored['deltas']['exact_score_logloss'])
            agg[name]['total'].append(scored['deltas']['total_goals_logloss'])
            agg[name]['coverage'].append(coverage)
            agg[name]['max_1x2'] = max(agg[name]['max_1x2'], e)
            agg[name]['max_cell'] = max(agg[name]['max_cell'], cd)
        folds.append(fold)

    gates = c['gates']
    summaries = {}
    material = []
    small = []
    for pos, name in enumerate(c['selection_rule']['candidate_names']):
        z = agg[name]
        mean_ex = sum(z['exact']) / len(z['exact'])
        mean_tg = sum(z['total']) / len(z['total'])
        checks = {
            'exact_each_fold_improves': all(v <= float(gates['exact_score_each_fold_delta_max']) + 1e-15 for v in z['exact']),
            'mean_total_goals_nonworse': mean_tg <= float(gates['mean_total_goals_delta_max']) + 1e-15,
            'single_fold_total_goals_cap': max(z['total']) <= float(gates['max_single_fold_total_goals_degradation']) + 1e-15,
            'coverage': min(z['coverage']) >= float(gates['minimum_feature_coverage']) - 1e-15,
            'one_x_two': z['max_1x2'] <= float(gates['max_1x2_abs_error']) + 1e-15,
            'cell_delta': z['max_cell'] <= float(gates['max_abs_probability_change_per_score_cell']) + 1e-15,
        }
        stable = all(checks.values()) and mean_ex < 0.0
        cls = 'MIXED_OR_NO_INCREMENT'
        if stable and mean_ex <= float(gates['material_mean_exact_score_delta_max']) + 1e-15:
            cls = 'ROBUST_MATERIAL'
            material.append((mean_ex, mean_tg, pos, name))
        elif stable:
            cls = 'ROBUST_SMALL'
            small.append((mean_ex, mean_tg, pos, name))
        summaries[name] = {
            'classification': cls,
            'fold_exact_score_deltas': z['exact'],
            'fold_total_goals_deltas': z['total'],
            'mean_exact_score_logloss_delta': mean_ex,
            'mean_total_goals_logloss_delta': mean_tg,
            'fold_coverage': z['coverage'],
            'max_1x2_abs_error': z['max_1x2'],
            'max_score_cell_abs_delta': z['max_cell'],
            'checks': checks,
        }

    material.sort()
    small.sort()
    selected = material[0][3] if material else (small[0][3] if small else None)
    selected_class = summaries[selected]['classification'] if selected else None
    if selected_class == 'ROBUST_MATERIAL':
        status = c['terminal']['material']
    elif selected_class == 'ROBUST_SMALL':
        status = c['terminal']['small']
    else:
        status = c['terminal']['none']

    out = {
        'schema_version': 'football3-historical-importance-load-interaction-result-v1',
        'status': status,
        'research_only': True,
        'development_n': len(rows),
        'source_max_season_loaded': 2022,
        'historical_confirmation_2023_used': False,
        'later_history_2024_2026_used': False,
        'prospective_1335_data_touched': False,
        'stage6_b_active_n': srec['active'],
        'baseline': 'B_PLUS_ROLLING_TRAIN_ONLY_IMPORTANCE_LOW2',
        'folds': folds,
        'summary': summaries,
        'selected_candidate': selected,
        'selected_classification': selected_class,
        'formal_v2_unchanged': True,
        'frozen_v3_1_1_unchanged': True,
        'stage6_b_unchanged': True,
        'CURRENT_changed': False,
        'production_pointer_changed': False,
        'formal_enablement_changed': False,
        'new_future_queue_created': False,
        'next_step': (
            'FREEZE_SELECTED_INTERACTION_ON_2020_2022_THEN_POSTVIEW_STRESS_TEST_2024_2026'
            if selected_class == 'ROBUST_MATERIAL'
            else 'RETAIN_MECHANISM_ONLY_NO_ADVANCEMENT'
            if selected_class == 'ROBUST_SMALL'
            else 'REJECT_INTERACTION_FAMILY_AND_MOVE_TO_NEXT_ORTHOGONAL_HISTORICAL_DIRECTION'
        ),
    }
    assert out['stage6_b_active_n'] == 5463
    assert out['source_max_season_loaded'] == 2022
    assert out['historical_confirmation_2023_used'] is False
    assert out['prospective_1335_data_touched'] is False
    common.write_json(a.out / 'importance_load_interaction_result.json', out)
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
