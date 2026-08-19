#!/usr/bin/env python3
import numpy as np
import c074e_scorehistory_movement_directt as m


def fixed_row_metrics(y: np.ndarray, p: np.ndarray):
    n = len(y)
    one = np.eye(m.K)[y]
    ll = -np.log(np.clip(p[np.arange(n), y], 1e-15, 1.0))
    brier = np.square(p - one).sum(axis=1)
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.square(cp - cy).sum(axis=1) / (m.K - 1)
    top1 = (np.argmax(p, axis=1) == y).astype(float)
    top3_idx = np.argpartition(p, -3, axis=1)[:, -3:]
    top3 = np.array([float(y[i] in top3_idx[i]) for i in range(n)])
    return {"ll": ll, "brier": brier, "rps": rps, "top1": top1, "top3": top3}


m.row_metrics = fixed_row_metrics
m.main()
