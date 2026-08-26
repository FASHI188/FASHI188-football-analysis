#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
OUT = ROOT / 'results'
R9B_DIR = ROOT.parent / 'top1_r9b_xg_hf'
if str(R9B_DIR) not in sys.path:
    sys.path.insert(0, str(R9B_DIR))
import run_experiment_r9b as r9b

R9B_MANIFEST = R9B_DIR / 'data' / 'source_manifest_r9b.json'
HASH_KEYS = ('fixtures_sha256', 'match_stats_sha256', 'snapshot_sha256', 'snapshot_rows', 'first_date', 'last_date')


def freeze():
    DATA.mkdir(parents=True, exist_ok=True)
    frozen = json.loads(R9B_MANIFEST.read_text(encoding='utf-8'))
    fresh = r9b.freeze()
    mismatches = {k: {'expected': frozen.get(k), 'fresh': fresh.get(k)} for k in HASH_KEYS if frozen.get(k) != fresh.get(k)}
    if mismatches:
        raise RuntimeError(f'R9b frozen source drift: {json.dumps(mismatches, ensure_ascii=False, sort_keys=True)}')
    m = {
        'schema_version': 'football3-top1-r10-draw-decomposition',
        'status': 'FROZEN_FROM_R9B_EXACT_SNAPSHOT',
        'classification': 'DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION',
        'parent_r9b_manifest_schema': frozen.get('schema_version'),
        'parent_r9b_snapshot_sha256': frozen['snapshot_sha256'],
        'fixtures_sha256': frozen['fixtures_sha256'],
        'match_stats_sha256': frozen['match_stats_sha256'],
        'snapshot_rows': frozen['snapshot_rows'],
        'first_date': frozen['first_date'],
        'last_date': frozen['last_date'],
        'strict_prior_xg_contract': True,
        'same_date_results_and_xg_withheld': True,
        'odds_used': False,
        'market_prices_used': False,
        'manual_match_adjustment': False,
        'change_from_r9b': '1X2 decomposition only; frozen sample, state features and strict-prior xG unchanged',
    }
    (DATA / 'source_manifest_r10.json').write_text(json.dumps(m, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(m, indent=2, ensure_ascii=False))
    return m


def predict_raw(rows):
    st = r9b.S()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row['date']].append(row)
    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x['game_id']):
            p = st.pred(row)
            pred.append({'date': ds, 'y': r9b.actual(row), 'raw': p})
            pending.append((row, p))
        for row, p in pending:
            st.update(row, p)
    return pred


def feat_draw(p):
    ph = max(float(p['p_home']), 1e-12)
    pd = max(float(p['p_draw']), 1e-12)
    pa = max(float(p['p_away']), 1e-12)
    s = ph + pd + pa
    ph, pd, pa = ph / s, pd / s, pa / s
    entropy = -(ph * math.log(ph) + pd * math.log(pd) + pa * math.log(pa))
    goal_gap = abs(p['mu_home'] - p['mu_away'])
    xg_gap = abs(p['xg_mu_home'] - p['xg_mu_away'])
    total = p['mu_total']
    xg_total = p['xg_mu_total']
    return [
        pd,
        max(ph, pa) - pd,
        abs(ph - pa),
        entropy,
        total,
        total * total,
        goal_gap,
        goal_gap * goal_gap,
        xg_total,
        xg_total * xg_total,
        xg_gap,
        xg_gap * xg_gap,
        abs(xg_total - total),
        abs(xg_gap - goal_gap),
        math.log1p(p['home_history']),
        math.log1p(p['away_history']),
        math.log1p(p['comp_history']),
        math.log1p(p['xg_weight_min']),
        pd * total,
        pd * goal_gap,
        pd * xg_total,
        pd * xg_gap,
    ]


def feat_side(p):
    ph = max(float(p['p_home']), 1e-12)
    pa = max(float(p['p_away']), 1e-12)
    goal_diff = p['mu_home'] - p['mu_away']
    xg_diff = p['xg_mu_home'] - p['xg_mu_away']
    return [
        math.log(ph / pa),
        goal_diff,
        xg_diff,
        xg_diff - goal_diff,
        p['mu_total'],
        p['xg_mu_total'],
        p['xg_home_for'] - p['xg_away_for'],
        p['xg_away_against'] - p['xg_home_against'],
        math.log1p(p['home_history']) - math.log1p(p['away_history']),
        math.log1p(p['xg_weight_min']),
    ]


def pos_proba(pipe, X):
    classes = list(pipe.classes_)
    if 1 not in classes:
        raise RuntimeError(f'positive class missing: {classes}')
    return pipe.predict_proba(X)[:, classes.index(1)]


def compose(draw_pipe, side_pipe, rows):
    pd = pos_proba(draw_pipe, [feat_draw(r['raw']) for r in rows])
    ph_nd = pos_proba(side_pipe, [feat_side(r['raw']) for r in rows])
    out = []
    for d, h in zip(pd, ph_nd):
        d = float(np.clip(d, 1e-8, 1 - 1e-8))
        h = float(np.clip(h, 1e-8, 1 - 1e-8))
        out.append(r9b.decorate([(1 - d) * h, d, (1 - d) * (1 - h)]))
    return out


def draw_diag(m):
    picks = int(m['top1_picks']['draw'])
    hits = int(m['top1_hits']['draw'])
    actual = int(m['actuals']['draw'])
    return {
        'top1_draw_picks': picks,
        'top1_draw_hits': hits,
        'top1_draw_precision': hits / picks if picks else 0.0,
        'top1_draw_recall': hits / actual if actual else 0.0,
        'actual_draws': actual,
    }


def delta(a, b):
    return {
        'top1_pp': 100 * (a['top1_accuracy'] - b['top1_accuracy']),
        'logloss': a['logloss'] - b['logloss'],
        'brier': a['brier'] - b['brier'],
        'rps': a['rps'] - b['rps'],
        'draw_top1_picks': a['top1_picks']['draw'] - b['top1_picks']['draw'],
        'draw_top1_hits': a['top1_hits']['draw'] - b['top1_hits']['draw'],
    }


def run():
    rows = r9b.load()
    pred = predict_raw(rows)
    b1 = r9b.boundary(pred, r9b.TARGET_BURN)
    b2 = r9b.boundary(pred, b1 + r9b.TARGET_TRAIN)
    b3 = r9b.boundary(pred, b2 + r9b.TARGET_VAL)
    train = pred[b1:b2]
    val = pred[b2:b3]
    test = pred[b3:]

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y = [r['y'] for r in train]
    k1 = make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=3000, random_state=0))
    k1.fit([r9b.feat_k1(r['raw']) for r in train], y)

    draw_pipe = make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=3000, random_state=0))
    draw_pipe.fit([feat_draw(r['raw']) for r in train], [1 if r['y'] == 1 else 0 for r in train])

    nd = [r for r in train if r['y'] != 1]
    side_pipe = make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=3000, random_state=0))
    side_pipe.fit([feat_side(r['raw']) for r in nd], [1 if r['y'] == 0 else 0 for r in nd])

    for subset in (val, test):
        pk = k1.predict_proba([r9b.feat_k1(r['raw']) for r in subset])
        pj = compose(draw_pipe, side_pipe, subset)
        for r, a, b in zip(subset, pk, pj):
            r['K1'] = r9b.decorate(a)
            r['J1'] = b

    vk = r9b.metrics(val, 'K1')
    vj = r9b.metrics(val, 'J1')
    tk = r9b.metrics(test, 'K1')
    tj = r9b.metrics(test, 'J1')

    out = {
        'schema_version': 'football3-top1-r10-draw-decomposition',
        'status': 'COMPLETE',
        'classification': 'DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION',
        'formal_weight': 0,
        'governance': {
            'snapshot_rows': r9b.N,
            'strict_prior_xg': True,
            'same_date_results_and_xg_withheld': True,
            'burn_in': b1,
            'train': len(train),
            'validation': len(val),
            'untouched_test': len(test),
            'date_safe_split_boundaries': True,
            'coverage_required': 1.0,
            'abstention_used': False,
            'veto_used': False,
            'odds_used': False,
            'market_prices_used': False,
            'manual_match_adjustment': False,
            'hyperparameter_search_used': False,
            'formal_promotion_allowed_from_this_run': False,
        },
        'models': {
            'K1': 'R9b multinomial baseline with strictly-prior rolling xG state',
            'J1': 'two-stage decomposition: P(draw) then P(home|not-draw), recomposed to 1X2',
        },
        'validation': {
            'K1': vk,
            'J1': vj,
            'draw_diag_K1': draw_diag(vk),
            'draw_diag_J1': draw_diag(vj),
            'delta_J1_minus_K1': delta(vj, vk),
        },
        'test': {
            'K1': tk,
            'J1': tj,
            'draw_diag_K1': draw_diag(tk),
            'draw_diag_J1': draw_diag(tj),
            'delta_J1_minus_K1': delta(tj, tk),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'summary_r10.json').write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return out


def verify():
    m = json.loads((DATA / 'source_manifest_r10.json').read_text(encoding='utf-8'))
    o = json.loads((OUT / 'summary_r10.json').read_text(encoding='utf-8'))
    parent = json.loads(R9B_MANIFEST.read_text(encoding='utf-8'))
    assert m['parent_r9b_snapshot_sha256'] == parent['snapshot_sha256']
    assert m['snapshot_rows'] == r9b.N and m['strict_prior_xg_contract']
    g = o['governance']
    assert g['coverage_required'] == 1.0 and not g['abstention_used'] and not g['veto_used']
    assert not g['odds_used'] and not g['market_prices_used'] and not g['manual_match_adjustment']
    assert not g['formal_promotion_allowed_from_this_run']
    print('VERIFY_OK')


if __name__ == '__main__':
    import argparse
    q = argparse.ArgumentParser()
    q.add_argument('mode', choices=['freeze', 'run', 'verify', 'all'])
    a = q.parse_args()
    if a.mode in {'freeze', 'all'}:
        freeze()
    if a.mode in {'run', 'all'}:
        run()
    if a.mode in {'verify', 'all'}:
        verify()
