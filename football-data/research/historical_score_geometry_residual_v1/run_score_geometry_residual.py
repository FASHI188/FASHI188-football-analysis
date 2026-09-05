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
HEADS = ('draw_11', 'home_margin1', 'away_margin1')
REGION = {'draw_11': 1, 'home_margin1': 0, 'away_margin1': 2}


def clone(m):
    return [list(map(float, row)) for row in m]


def expected_total(m):
    return sum(float(v) * (h + a) for h, row in enumerate(m) for a, v in enumerate(row))


def entropy(m):
    return -sum(float(v) * math.log(max(EPS, float(v))) for row in m for v in row)


def top_gap(m):
    p = sorted((float(v) for row in m for v in row), reverse=True)
    return p[0] - p[1]


def class_num(m, head):
    if head == 'draw_11':
        return float(m[1][1]) if len(m) > 1 and len(m[1]) > 1 else 0.0
    if head == 'home_margin1':
        return sum(float(v) for h, row in enumerate(m) for a, v in enumerate(row) if h > a and h - a == 1)
    if head == 'away_margin1':
        return sum(float(v) for h, row in enumerate(m) for a, v in enumerate(row) if a > h and a - h == 1)
    raise KeyError(head)


def conditional_prob(m, head):
    den = shape.integrate(m)[REGION[head]]
    return min(1.0 - 1e-10, max(1e-10, class_num(m, head) / max(EPS, den)))


def target(r, head):
    hg, ag = int(r['hg']), int(r['ag'])
    if head == 'draw_11':
        return int(hg == 1 and ag == 1)
    if head == 'home_margin1':
        return int(hg > ag and hg - ag == 1)
    if head == 'away_margin1':
        return int(ag > hg and ag - hg == 1)
    raise KeyError(head)


def predicate(head):
    if head == 'draw_11':
        return lambda h, a: h == 1 and a == 1
    if head == 'home_margin1':
        return lambda h, a: h > a and h - a == 1
    if head == 'away_margin1':
        return lambda h, a: a > h and a - h == 1
    raise KeyError(head)


def fit_importance(train, c):
    keys = ['importance_diff', 'importance_mean']
    means, sds = shape.fit_scaler(train, keys)
    X = shape.design(train, keys, means, sds)
    y = [int(r['hg'] + r['ag'] <= 2) for r in train]
    off = [resid.logit(r['b_low']) for r in train]
    beta = resid.fit_offset(X, y, off, float(c['base']['importance_fixed_ridge']))
    return {'keys': keys, 'means': means, 'sds': sds, 'beta': beta}


def eta_from_model(r, model, cap):
    z = float(model['beta'][0])
    for j, k in enumerate(model['keys'], 1):
        v = r.get(k)
        zz = 0.0 if v is None else (float(v) - float(model['means'][k])) / float(model['sds'][k])
        z += float(model['beta'][j]) * zz
    return max(-cap, min(cap, z))


def apply_importance(r, m, model, c):
    eta = eta_from_model(r, model, float(c['base']['importance_max_abs_log_tilt']))
    return shape.apply_region_tilt(m, lambda h, a: h + a <= 2, eta)


def enrich(r, m):
    z = dict(r)
    z.update({
        'expected_total': expected_total(m),
        'matrix_entropy': entropy(m),
        'top_gap': top_gap(m),
    })
    return z


def fit_head(train, mats, head, c):
    reg = REGION[head]
    rr = [r for r in train if int(r['y']) == reg]
    keys = list(c['screen']['features'])
    means, sds = shape.fit_scaler(rr, keys)
    X = shape.design(rr, keys, means, sds)
    y = [target(r, head) for r in rr]
    off = [resid.logit(conditional_prob(mats[r['fixture_id']], head)) for r in rr]
    beta = resid.fit_offset(X, y, off, float(c['screen']['fixed_ridge']))
    return {'head': head, 'keys': keys, 'means': means, 'sds': sds, 'beta': beta, 'train_n': len(rr)}


def head_eta(r, model, c):
    return eta_from_model(r, model, float(c['screen']['max_abs_head_log_tilt']))


def apply_heads(r, m, models, heads, c):
    out = clone(m)
    for head in heads:
        out = shape.apply_region_tilt(out, predicate(head), head_eta(r, models[head], c))
    return out


def binary_ll(y, p):
    return -sum(yy * math.log(max(1e-12, pp)) + (1 - yy) * math.log(max(1e-12, 1 - pp)) for yy, pp in zip(y, p)) / len(y)


def local_head_metrics(test, base, cand, head):
    rr = [r for r in test if int(r['y']) == REGION[head]]
    y = [target(r, head) for r in rr]
    pb = [conditional_prob(base[r['fixture_id']], head) for r in rr]
    pc = [conditional_prob(cand[r['fixture_id']], head) for r in rr]
    b = binary_ll(y, pb)
    q = binary_ll(y, pc)
    return {'n': len(rr), 'baseline_logloss': b, 'candidate_logloss': q, 'delta': q - b}


def candidate_heads(name):
    if name == 'draw_geometry':
        return ('draw_11',)
    if name == 'winner_margin_geometry':
        return ('home_margin1', 'away_margin1')
    if name == 'combined_geometry':
        return HEADS
    raise KeyError(name)


def main():
    ap = argparse.ArgumentParser()
    for x in ('contract', 'v311', 'v31', 'usr1', 'v2', 'xg', 'v1', 'v1_result', 'db', 'xg_identity', 'out'):
        ap.add_argument('--' + x.replace('_', '-'), type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    c = json.loads(a.contract.read_text())
    assert c['status'] == 'FROZEN_BEFORE_CONSUMED_HISTORY_GEOMETRY_SCREEN'
    assert c['data_roles']['historical_confirmation_2023'].startswith('CONSUMED_BY_PRIOR')
    assert c['data_roles']['prospective_1335'].startswith('FORBIDDEN')

    b = common.build_frozen_baseline(a, 'geom')
    dev0 = [r for r in b['rows'] if r['season'] in (2020, 2021, 2022) and r['fixture_id'] in b['bmap']]
    assert len(dev0) == 5478
    fmap = resid.feature_map(a.db)
    wanted = {r['fixture_id'] for r in dev0}
    snaps, srec = bmod.make_snapshots(a.db, wanted, float(c['base']['stage6_b_half_life']))

    bmats = {}
    rows = []
    for src in dev0:
        fid = src['fixture_id']
        bp, _ = bmod.predict(b['bmap'][fid], snaps.get(fid), float(c['base']['stage6_b_coefficient']))
        m = common.region_rescale(b['bmats'][fid], b['bmap'][fid], bp)
        bmats[fid] = m
        p0 = list(map(float, b['bmap'][fid]))
        side = p0[0] / max(EPS, p0[0] + p0[2])
        r = dict(fmap[fid])
        hg, ag = int(src['home_goals']), int(src['away_goals'])
        r.update({
            'fixture_id': fid,
            'season': int(src['season']),
            'hg': hg,
            'ag': ag,
            'y': 0 if hg > ag else 1 if hg == ag else 2,
            'balance_abs': abs(resid.logit(side)),
            'b_low': resid.matrix_low(m),
            'b_p11': shape.exact_score_prob(m, 1, 1),
        })
        rows.append(r)

    folds = []
    cand_names = list(c['screen']['candidate_names'])
    agg = {n: {'exact': [], 'total': [], 'max_1x2': 0.0, 'max_cell': 0.0} for n in cand_names}
    head_agg = {h: [] for h in HEADS}

    for season in c['data_roles']['rolling_tests']:
        season = int(season)
        tr0 = [r for r in rows if r['season'] < season]
        te0 = [r for r in rows if r['season'] == season]
        imp = fit_importance(tr0, c)

        fold_base = {}
        tr = []
        te = []
        for r in tr0 + te0:
            fid = r['fixture_id']
            m = apply_importance(r, bmats[fid], imp, c)
            fold_base[fid] = m
            er = enrich(r, m)
            if r['season'] < season:
                tr.append(er)
            else:
                te.append(er)

        models = {h: fit_head(tr, fold_base, h, c) for h in HEADS}
        base_test = {r['fixture_id']: fold_base[r['fixture_id']] for r in te}
        base_metrics = shape.metric(te, base_test)
        rec = {
            'season': season,
            'train_n': len(tr),
            'test_n': len(te),
            'importance_train_n': len(tr0),
            'importance_model': imp,
            'head_models': models,
            'baseline_b_plus_fold_importance': base_metrics,
            'candidates': {},
        }

        combined_cand = {r['fixture_id']: apply_heads(r, fold_base[r['fixture_id']], models, HEADS, c) for r in te}
        for h in HEADS:
            hm = local_head_metrics(te, base_test, combined_cand, h)
            rec.setdefault('local_head_metrics', {})[h] = hm
            head_agg[h].append(hm['delta'])

        for name in cand_names:
            hs = candidate_heads(name)
            cand = {r['fixture_id']: apply_heads(r, fold_base[r['fixture_id']], models, hs, c) for r in te}
            mm = shape.metric(te, cand)
            dx = mm['exact_score_logloss'] - base_metrics['exact_score_logloss']
            dt = mm['total_goals_logloss'] - base_metrics['total_goals_logloss']
            e = shape.max_1x2_error(base_test, cand)
            cd = shape.max_cell_delta(base_test, cand)
            rec['candidates'][name] = {
                'heads': list(hs),
                'metrics': mm,
                'deltas': {'exact_score_logloss': dx, 'total_goals_logloss': dt},
                'max_1x2_abs_error': e,
                'max_score_cell_abs_delta': cd,
            }
            agg[name]['exact'].append(dx)
            agg[name]['total'].append(dt)
            agg[name]['max_1x2'] = max(agg[name]['max_1x2'], e)
            agg[name]['max_cell'] = max(agg[name]['max_cell'], cd)
        folds.append(rec)

    summary = {}
    any_material = False
    any_small = False
    for name in cand_names:
        z = agg[name]
        mean_ex = sum(z['exact']) / len(z['exact'])
        mean_tg = sum(z['total']) / len(z['total'])
        same_direction = all(v < 0.0 for v in z['exact'])
        safety = z['max_1x2'] <= float(c['gates']['max_1x2_abs_error']) and z['max_cell'] <= float(c['gates']['max_abs_probability_change_per_score_cell'])
        if same_direction and safety and mean_ex <= float(c['gates']['material_mean_exact_score_logloss_delta']):
            cls = 'ROBUST_MATERIAL'
            any_material = True
        elif same_direction and safety:
            cls = 'ROBUST_SMALL'
            any_small = True
        else:
            cls = 'MIXED_OR_REJECTED'
        summary[name] = {
            'classification': cls,
            'fold_exact_score_logloss_deltas': z['exact'],
            'mean_exact_score_logloss_delta': mean_ex,
            'fold_total_goals_logloss_deltas': z['total'],
            'mean_total_goals_logloss_delta': mean_tg,
            'max_1x2_abs_error': z['max_1x2'],
            'max_score_cell_abs_delta': z['max_cell'],
        }

    head_summary = {
        h: {
            'fold_conditional_logloss_deltas': head_agg[h],
            'robust_local_improvement': all(v < 0.0 for v in head_agg[h]),
            'mean_conditional_logloss_delta': sum(head_agg[h]) / len(head_agg[h]),
        }
        for h in HEADS
    }

    status = c['terminal']['pass'] if any_material else c['terminal']['small'] if any_small else c['terminal']['none']
    out = {
        'schema_version': 'football3-historical-score-geometry-residual-result-v1',
        'status': status,
        'research_only': True,
        'development_n': len(rows),
        'source_max_season_loaded': 2022,
        'stage6_b_active_n': srec['active'],
        'historical_confirmation_2023_used_for_new_selection': False,
        'postview_2024_2026_loaded': False,
        'prospective_1335_data_touched': False,
        'baseline_protocol': 'ROLLING_PRIOR_SEASON_B_PLUS_REFIT_FROZEN_IMPORTANCE_LOW2_SPEC',
        'head_summary': head_summary,
        'folds': folds,
        'candidate_summary': summary,
        'next_step': 'FREEZE_ONLY_ROBUST_GEOMETRY_FAMILY_ON_CONSUMED_HISTORY' if (any_material or any_small) else 'REJECT_GEOMETRY_FAMILY_AND_RETURN_TO_MECHANISM_RESEARCH',
    }
    assert out['stage6_b_active_n'] == 5463
    assert out['historical_confirmation_2023_used_for_new_selection'] is False
    assert out['postview_2024_2026_loaded'] is False
    assert out['prospective_1335_data_touched'] is False
    common.write_json(a.out / 'score_geometry_residual_result.json', out)
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
