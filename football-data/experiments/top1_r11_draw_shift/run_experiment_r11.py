#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
OUT = ROOT / 'results'
R10_DIR = ROOT.parent / 'top1_r10_draw_decomp'
if str(R10_DIR) not in sys.path:
    sys.path.insert(0, str(R10_DIR))
import run_experiment_r10 as r10

r9b = r10.r9b
R10_MANIFEST = R10_DIR / 'data' / 'source_manifest_r10.json'
SHIFT_GRID = (0.0, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90)


def freeze():
    DATA.mkdir(parents=True, exist_ok=True)
    r10.freeze()
    parent = json.loads(R10_MANIFEST.read_text(encoding='utf-8'))
    m = {
        'schema_version': 'football3-top1-r11-draw-logit-shift',
        'status': 'FROZEN_FROM_R10_EXACT_R9B_SNAPSHOT',
        'classification': 'DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION',
        'parent_r10_manifest_schema': parent['schema_version'],
        'parent_r9b_snapshot_sha256': parent['parent_r9b_snapshot_sha256'],
        'snapshot_rows': parent['snapshot_rows'],
        'first_date': parent['first_date'],
        'last_date': parent['last_date'],
        'strict_prior_xg_contract': True,
        'odds_used': False,
        'market_prices_used': False,
        'change_from_r10': 'single validation-selected positive logit shift on P(draw) only',
        'shift_grid': list(SHIFT_GRID),
    }
    (DATA / 'source_manifest_r11.json').write_text(json.dumps(m, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(m, indent=2, ensure_ascii=False))
    return m


def shifted(p, shift):
    ph = float(p['p_home'])
    pd = float(np.clip(p['p_draw'], 1e-10, 1 - 1e-10))
    pa = float(p['p_away'])
    logit = math.log(pd / (1 - pd)) + float(shift)
    pd2 = 1.0 / (1.0 + math.exp(-logit))
    nd = max(ph + pa, 1e-12)
    scale = (1 - pd2) / nd
    return r9b.decorate([ph * scale, pd2, pa * scale])


def metric_for(rows, key):
    return r9b.metrics(rows, key)


def make_shift_rows(rows, shift):
    out = []
    for r in rows:
        out.append({'y': r['y'], 'S': shifted(r['J1'], shift)})
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
    pred = r10.predict_raw(rows)
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
    draw_pipe.fit([r10.feat_draw(r['raw']) for r in train], [1 if r['y'] == 1 else 0 for r in train])
    nd = [r for r in train if r['y'] != 1]
    side_pipe = make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=3000, random_state=0))
    side_pipe.fit([r10.feat_side(r['raw']) for r in nd], [1 if r['y'] == 0 else 0 for r in nd])

    for subset in (val, test):
        pk = k1.predict_proba([r9b.feat_k1(r['raw']) for r in subset])
        pj = r10.compose(draw_pipe, side_pipe, subset)
        for r, a, b in zip(subset, pk, pj):
            r['K1'] = r9b.decorate(a)
            r['J1'] = b

    vk = metric_for(val, 'K1')
    vj = metric_for(val, 'J1')
    tk = metric_for(test, 'K1')
    tj = metric_for(test, 'J1')

    grid = []
    best = None
    for shift in SHIFT_GRID:
        sm = metric_for(make_shift_rows(val, shift), 'S')
        row = {
            'shift': shift,
            'top1_accuracy': sm['top1_accuracy'],
            'hits': sm['hits'],
            'logloss': sm['logloss'],
            'brier': sm['brier'],
            'rps': sm['rps'],
            'draw_top1_picks': sm['top1_picks']['draw'],
            'draw_top1_hits': sm['top1_hits']['draw'],
        }
        grid.append(row)
        score = (sm['top1_accuracy'], -sm['logloss'], -shift)
        if best is None or score > best[0]:
            best = (score, shift, sm)

    selected_shift = float(best[1])
    vjs = best[2]
    tjs = metric_for(make_shift_rows(test, selected_shift), 'S')

    out = {
        'schema_version': 'football3-top1-r11-draw-logit-shift',
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
            'test': len(test),
            'coverage_required': 1.0,
            'odds_used': False,
            'market_prices_used': False,
            'manual_match_adjustment': False,
            'hyperparameter_search_used': True,
            'shift_selected_on_validation_only': True,
            'test_used_for_shift_selection': False,
            'formal_promotion_allowed_from_this_run': False,
        },
        'models': {
            'K1': 'R9b multinomial baseline with strict-prior xG',
            'J1': 'R10 two-stage draw decomposition',
            'J1S': 'J1 plus one validation-selected positive logit shift on P(draw)',
        },
        'validation': {
            'K1': vk,
            'J1': vj,
            'shift_grid': grid,
            'selected_shift': selected_shift,
            'J1S': vjs,
            'draw_diag_J1S': draw_diag(vjs),
            'delta_J1S_minus_K1': delta(vjs, vk),
            'delta_J1S_minus_J1': delta(vjs, vj),
        },
        'test': {
            'K1': tk,
            'J1': tj,
            'selected_shift': selected_shift,
            'J1S': tjs,
            'draw_diag_J1S': draw_diag(tjs),
            'delta_J1S_minus_K1': delta(tjs, tk),
            'delta_J1S_minus_J1': delta(tjs, tj),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'summary_r11.json').write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return out


def verify():
    m = json.loads((DATA / 'source_manifest_r11.json').read_text(encoding='utf-8'))
    o = json.loads((OUT / 'summary_r11.json').read_text(encoding='utf-8'))
    assert m['snapshot_rows'] == r9b.N and m['strict_prior_xg_contract']
    g = o['governance']
    assert g['coverage_required'] == 1.0 and not g['odds_used'] and not g['market_prices_used']
    assert g['shift_selected_on_validation_only'] and not g['test_used_for_shift_selection']
    assert o['validation']['selected_shift'] in SHIFT_GRID
    assert o['test']['selected_shift'] == o['validation']['selected_shift']
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
