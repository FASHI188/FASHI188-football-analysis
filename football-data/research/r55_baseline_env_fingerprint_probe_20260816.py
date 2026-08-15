#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import platform
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import scipy
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

LEAGUES = [
    'England: Premier League',
    'England: Championship',
    'England: League One',
    'England: League Two',
]
EXPECTED_TRAIN_N = 19422
EXPECTED_CLASS_COUNTS = {0:1522,1:3745,2:4914,3:4231,4:2714,5:1421,6:569,7:306}
EXPECTED_DATE_MIN = '2005-01-01'
EXPECTED_DATE_MAX = '2015-05-25'
TRAIN_END = '2015-06-30'


def fodd(v):
    try:
        x=float(v)
        return x if math.isfinite(x) and x>1.0 else None
    except Exception:
        return None


def market_probs(h,d,a):
    h,d,a=fodd(h),fodd(d),fodd(a)
    if None in (h,d,a): return None
    x=np.array([1/h,1/d,1/a],dtype=float)
    return x/x.sum()


def features(q,league):
    qh,qd,qa=map(float,q)
    strength=math.log(qh/qa)
    entropy=-sum(x*math.log(x) for x in (qh,qd,qa))
    pmax=max(qh,qd,qa)
    openness=math.log(math.sqrt(qh*qa)/qd)
    return [strength,qd,entropy,pmax,openness,*[1.0 if league==x else 0.0 for x in LEAGUES]]


def member(zf,basename):
    hits=[n for n in zf.namelist() if Path(n).name==basename]
    if len(hits)!=1: raise SystemExit(f'ARCHIVE_MEMBER_GATE_FAIL {basename}: {hits}')
    return hits[0]


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--archive',required=True); args=ap.parse_args()
    X=[]; y=[]; dates=[]
    with zipfile.ZipFile(args.archive) as zf:
        name=member(zf,'closing_odds.csv.gz')
        with zf.open(name) as raw, gzip.GzipFile(fileobj=raw) as gz:
            text=(line.decode('utf-8') for line in gz)
            rd=csv.DictReader(text)
            for r in rd:
                lg=r['league']; d=r['match_date'][:10]
                if lg not in LEAGUES or not d or d>TRAIN_END: continue
                q=market_probs(r['avg_odds_home_win'],r['avg_odds_draw'],r['avg_odds_away_win'])
                if q is None: continue
                try: hs=int(float(r['home_score'])); aas=int(float(r['away_score']))
                except Exception: continue
                X.append(features(q,lg)); y.append(min(hs+aas,7)); dates.append(d)
    X=np.asarray(X,dtype=float); y=np.asarray(y,dtype=int)
    counts=dict(sorted(Counter(map(int,y)).items()))
    if len(y)!=EXPECTED_TRAIN_N or counts!=EXPECTED_CLASS_COUNTS or min(dates)!=EXPECTED_DATE_MIN or max(dates)!=EXPECTED_DATE_MAX:
        raise SystemExit(f'DATA_FINGERPRINT_FAIL n={len(y)} counts={counts} range={min(dates)}..{max(dates)}')
    scaler=StandardScaler(); Xs=scaler.fit_transform(X)
    clf=LogisticRegression(penalty='l2',C=1.0,solver='lbfgs',max_iter=5000,tol=1e-8,class_weight=None)
    clf.fit(Xs,y)
    out={
        'python':platform.python_version(),
        'numpy':np.__version__,
        'scipy':scipy.__version__,
        'sklearn':sklearn.__version__,
        'train_n':len(y),
        'class_counts':{str(k):int(v) for k,v in counts.items()},
        'date_min':min(dates),'date_max':max(dates),
        'direct_t_iterations':int(np.max(clf.n_iter_)),
        'classes':list(map(int,clf.classes_)),
    }
    print(json.dumps(out,sort_keys=True))

if __name__=='__main__': main()
