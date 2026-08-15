#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import scipy
import sklearn
from scipy.optimize import minimize_scalar
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'football-data' / 'research'
OUT = R / 'r55b_pretarget_alpha_fit_result_20260816.json'

LEAGUES = [
    'England: Premier League',
    'England: Championship',
    'England: League One',
    'England: League Two',
]
EXPECTED_TRAIN_N = 19422
EXPECTED_CLASS_COUNTS = {0:1522,1:3745,2:4914,3:4231,4:2714,5:1421,6:569,7:306}
EXPECTED_TRAIN_DATE_MIN = '2005-01-01'
EXPECTED_TRAIN_DATE_MAX = '2015-05-25'
EXPECTED_DIRECT_T_ITERATIONS = 370
TRAIN_END = '2015-06-30'
EPS = 1e-12


def fodd(v):
    try:
        x = float(v)
        return x if math.isfinite(x) and x > 1.0 else None
    except Exception:
        return None


def market_probs(h, d, a):
    h, d, a = fodd(h), fodd(d), fodd(a)
    if None in (h, d, a):
        return None
    x = np.array([1.0/h, 1.0/d, 1.0/a], dtype=float)
    return x / x.sum()


def features(q, league):
    qh, qd, qa = map(float, q)
    if min(qh, qd, qa) <= 0:
        raise ValueError('nonpositive market probability')
    strength = math.log(qh / qa)
    entropy = -sum(x * math.log(x) for x in (qh, qd, qa))
    pmax = max(qh, qd, qa)
    openness = math.log(math.sqrt(qh * qa) / qd)
    onehot = [1.0 if league == x else 0.0 for x in LEAGUES]
    return [strength, qd, entropy, pmax, openness, *onehot]


def member(zf, basename):
    hits = [n for n in zf.namelist() if Path(n).name == basename]
    if len(hits) != 1:
        raise SystemExit(f'ARCHIVE_MEMBER_GATE_FAIL {basename}: {hits}')
    return hits[0]


def load_training(archive):
    X, y, dates = [], [], []
    with zipfile.ZipFile(archive) as zf:
        name = member(zf, 'closing_odds.csv.gz')
        with zf.open(name) as raw, gzip.GzipFile(fileobj=raw) as gz, io.TextIOWrapper(gz, encoding='utf-8', newline='') as text:
            rd = csv.DictReader(text)
            req = {'league','match_date','home_score','away_score','avg_odds_home_win','avg_odds_draw','avg_odds_away_win'}
            missing = req - set(rd.fieldnames or [])
            if missing:
                raise SystemExit(f'TRAIN_HEADER_FAIL: {sorted(missing)}')
            for r in rd:
                lg = r['league']
                d = r['match_date'][:10]
                if lg not in LEAGUES or not d or d > TRAIN_END:
                    continue
                q = market_probs(r['avg_odds_home_win'], r['avg_odds_draw'], r['avg_odds_away_win'])
                if q is None:
                    continue
                try:
                    hs = int(float(r['home_score']))
                    aas = int(float(r['away_score']))
                except Exception:
                    continue
                X.append(features(q, lg))
                y.append(min(hs + aas, 7))
                dates.append(d)
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=int)
    counts = dict(sorted(Counter(map(int, y)).items()))
    if len(y) != EXPECTED_TRAIN_N:
        raise SystemExit(f'BASELINE_TRAIN_N_FAIL expected={EXPECTED_TRAIN_N} actual={len(y)}')
    if counts != EXPECTED_CLASS_COUNTS:
        raise SystemExit(f'BASELINE_CLASS_COUNTS_FAIL expected={EXPECTED_CLASS_COUNTS} actual={counts}')
    if min(dates) != EXPECTED_TRAIN_DATE_MIN or max(dates) != EXPECTED_TRAIN_DATE_MAX:
        raise SystemExit(f'BASELINE_DATE_RANGE_FAIL expected={EXPECTED_TRAIN_DATE_MIN}..{EXPECTED_TRAIN_DATE_MAX} actual={min(dates)}..{max(dates)}')
    return X, y, min(dates), max(dates), counts


def fit_direct_t(X, y):
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    clf = LogisticRegression(
        penalty='l2', C=1.0, solver='lbfgs', max_iter=5000, tol=1e-8,
        class_weight=None,
    )
    clf.fit(Xs, y)
    if list(map(int, clf.classes_)) != list(range(8)):
        raise SystemExit(f'BASELINE_CLASS_ORDER_FAIL: {clf.classes_.tolist()}')
    nit = int(np.max(clf.n_iter_))
    if nit != EXPECTED_DIRECT_T_ITERATIONS:
        raise SystemExit(f'BASELINE_ITERATION_FINGERPRINT_FAIL expected={EXPECTED_DIRECT_T_ITERATIONS} actual={nit}')
    return scaler, clf


def load_calibration(path):
    obj = json.loads(Path(path).read_text(encoding='utf-8'))
    if obj.get('status') != 'PASS_COMPLETE_INPUT_RECOVERY':
        raise SystemExit(f'INPUT_RECOVERY_NOT_PASS: {obj.get("status")}')
    rows = obj.get('calibration') or []
    if len(rows) != 943:
        raise SystemExit(f'CALIBRATION_N_FAIL expected=943 actual={len(rows)}')
    rows = sorted(rows, key=lambda r: (r['match_datetime'], int(r['match_id'])))
    if rows[-1]['match_datetime'] >= '2016-03-01 00:00:00':
        raise SystemExit('CALIBRATION_TIME_LEAK_FAIL')
    X = np.asarray([features((r['qH'],r['qD'],r['qA']), r['league']) for r in rows], dtype=float)
    y = np.asarray([int(r['actual_t_ge_3']) for r in rows], dtype=int)
    qou = np.asarray([float(r['q_over_2_5']) for r in rows], dtype=float)
    if not np.all((qou > 0) & (qou < 1)):
        raise SystemExit('CALIBRATION_OU_RANGE_FAIL')
    return rows, X, y, qou


def binary_ll(y, p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1-EPS)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y*np.log(p) + (1-y)*np.log(1-p)))


def blend_over(pold, qou, alpha):
    pold = np.clip(np.asarray(pold), EPS, 1-EPS)
    qou = np.clip(np.asarray(qou), EPS, 1-EPS)
    return expit(logit(pold) + float(alpha) * (logit(qou) - logit(pold)))


def fit_alpha(y, pold, qou):
    base_ll = binary_ll(y, pold)
    def objective(a):
        return binary_ll(y, blend_over(pold, qou, a))
    opt = minimize_scalar(objective, bounds=(0.0,1.0), method='bounded', options={'xatol':1e-8})
    if not opt.success:
        raise SystemExit(f'ALPHA_OPT_FAIL: {opt.message}')
    a = float(opt.x)
    fit_ll = float(opt.fun)
    return {
        'alpha': a,
        'baseline_binary_log_loss': base_ll,
        'fitted_binary_log_loss': fit_ll,
        'log_loss_gain_baseline_minus_fitted': base_ll-fit_ll,
        'optimizer_nfev': int(opt.nfev),
        'optimizer_success': bool(opt.success),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--archive', required=True)
    ap.add_argument('--recovery-json', required=True)
    args = ap.parse_args()

    Xtr, ytr, dmin, dmax, counts = load_training(args.archive)
    scaler, clf = fit_direct_t(Xtr, ytr)
    rows, Xcal, ycal, qou = load_calibration(args.recovery_json)
    p8 = clf.predict_proba(scaler.transform(Xcal))
    pold = p8[:,3:].sum(axis=1)
    if np.max(np.abs(p8.sum(axis=1)-1.0)) > 1e-12:
        raise SystemExit('BASELINE_PROBABILITY_SUM_FAIL')

    full = fit_alpha(ycal, pold, qou)
    blocks = []
    for i, idx in enumerate(np.array_split(np.arange(len(rows)), 3), start=1):
        b = fit_alpha(ycal[idx], pold[idx], qou[idx])
        blocks.append({
            'block':i,
            'n':int(len(idx)),
            'start':rows[int(idx[0])]['match_datetime'],
            'end':rows[int(idx[-1])]['match_datetime'],
            'actual_over_rate':float(np.mean(ycal[idx])),
            **b,
        })

    stop = full['alpha'] <= 0.01 and full['log_loss_gain_baseline_minus_fitted'] <= 0.0
    ruling = 'STOP_NO_USABLE_OU_RESIDUAL' if stop else 'ALPHA_FROZEN_READY_FOR_TARGET_APPLICATION'
    payload = {
        'schema':'R55B_PRETARGET_ALPHA_FIT_RESULT_R1',
        'classification':'RESEARCH_ONLY_PRETARGET_ALPHA_FREEZE_FORMAL_WEIGHT_0',
        'formal_weight':0,
        'prereg':'football-data/research/r55b_pretarget_ou_alpha_prereg_20260814.json',
        'input_recovery':'football-data/research/r55b_grid71_input_recovery_20260816.json',
        'target_application_performed':False,
        'target_rows_used_for_alpha':0,
        'runtime':{
            'numpy':np.__version__,
            'scipy':scipy.__version__,
            'sklearn':sklearn.__version__,
        },
        'training':{
            'n':int(len(ytr)),
            'date_min':dmin,
            'date_max':dmax,
            'class_counts':{str(k):int(v) for k,v in counts.items()},
            'direct_t_iterations':int(np.max(clf.n_iter_)),
            'features':['strength','qD','market_entropy','pmax','openness',*['onehot:'+x for x in LEAGUES]],
            'model':'StandardScaler + LogisticRegression L2 C=1 lbfgs max_iter=5000 tol=1e-8 no class_weight',
        },
        'calibration':{
            'n':len(rows),
            'time_window':[rows[0]['match_datetime'],rows[-1]['match_datetime']],
            'actual_over_rate':float(np.mean(ycal)),
            'baseline_mean_over_probability':float(np.mean(pold)),
            'independent_ou_mean_over_probability':float(np.mean(qou)),
            'full_fit':full,
            'chronological_equal_count_blocks':blocks,
        },
        'stop_rule_evaluation':{
            'rule':'if alpha <= 0.01 and calibration LL gain <= 0, STOP and do not manufacture weight',
            'triggered':stop,
        },
        'ruling':ruling,
        'audit':{
            'baseline_fingerprint_gate':True,
            'alpha_bounds':[0.0,1.0],
            'optimizer':'scipy minimize_scalar bounded xatol=1e-8',
            'single_global_alpha':True,
            'per_league_alpha':False,
            'target_tuning':False,
            'target_application':False,
            'target_score_access':0,
            'forced_draw':False,
            'threshold_search':False,
            'topk_override':False,
            'class_weight':False,
            'confirmation_2025_26_access':0,
        },
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True))


if __name__ == '__main__':
    main()
