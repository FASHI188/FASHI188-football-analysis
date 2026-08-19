#!/usr/bin/env python3
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

import c078a_full_support_directt as c078


def finite_diff(fun, theta, eps=1e-6):
    out = np.zeros_like(theta, dtype=float)
    for j in range(len(theta)):
        a = theta.copy(); b = theta.copy()
        a[j] += eps; b[j] -= eps
        out[j] = (fun(a)[0] - fun(b)[0]) / (2 * eps)
    return out


def main() -> int:
    rng = np.random.default_rng(78000)
    X = np.column_stack([np.ones(40), rng.normal(size=(40, 4))])
    y = rng.poisson(2.6, size=40).astype(int)

    beta = np.array([0.15, 0.1, -0.08, 0.03, 0.06], dtype=float)
    _, pg = c078.poisson_objective(beta, X, y)
    pnum = finite_diff(lambda z: c078.poisson_objective(z, X, y), beta)
    assert np.max(np.abs(pg - pnum)) < 1e-5, (pg, pnum)

    theta = np.concatenate([beta, np.array([math.log(0.3)])])
    _, ng = c078.nb2_objective(theta, X, y)
    nnum = finite_diff(lambda z: c078.nb2_objective(z, X, y), theta)
    assert np.max(np.abs(ng - nnum)) < 1e-5, (ng, nnum)

    pres = c078.fit_poisson(X, y)
    assert pres.success, pres.message
    nres = c078.fit_nb2(X, y, pres.x)
    assert nres.success, nres.message
    alpha = math.exp(float(nres.x[-1]))
    assert c078.ALPHA_MIN <= alpha <= c078.ALPHA_MAX

    pdist = c078.distribution_arrays("poisson", pres.x, X)
    ndist = c078.distribution_arrays("nb2", nres.x, X)
    assert float(np.max(pdist["conservation"])) <= 1e-10
    assert float(np.max(ndist["conservation"])) <= 1e-10
    assert np.allclose(c078.collapsed8(pdist).sum(axis=1), 1.0, atol=1e-12)
    assert np.allclose(c078.collapsed8(ndist).sum(axis=1), 1.0, atol=1e-12)

    em = c078.exact_row_metrics(y, ndist)
    assert len(em["ll"]) == len(y)
    assert np.isfinite(em["ll"]).all()
    assert np.isfinite(em["brier"]).all()
    assert np.isfinite(em["rps"]).all()

    # Synthetic high-tail rows verify exact conditional support and survival identities.
    Xh = np.column_stack([np.ones(4), np.zeros((4, 4))])
    betah = np.array([math.log(7.8), 0, 0, 0, 0], dtype=float)
    th = np.concatenate([betah, np.array([math.log(0.2)])])
    dh = c078.distribution_arrays("nb2", th, Xh)
    yh = np.array([7, 8, 9, 10], dtype=int)
    tm = c078.tail_row_metrics(yh, dh)
    assert len(tm["ll"]) == 4
    assert np.isfinite(tm["ll"]).all()
    assert np.all((tm["p8_cond"] >= 0) & (tm["p8_cond"] <= 1))
    assert np.all((tm["p9_cond"] >= 0) & (tm["p9_cond"] <= 1))
    assert np.all(tm["p9_cond"] <= tm["p8_cond"] + 1e-15)

    # Same-date PIT feature test: both fixtures on day 1 must see empty histories.
    raw = pd.DataFrame([
        {"date": pd.Timestamp("2020-01-01"), "Season": "2019-2020", "season_start": 2019, "league_key": "L", "HomeTeam": "A", "AwayTeam": "B", "source_file": "x", "home_key": "L|A", "away_key": "L|B", "FTHG": 2, "FTAG": 1, "T_exact": 3, "movement_logit": 0.1},
        {"date": pd.Timestamp("2020-01-01"), "Season": "2019-2020", "season_start": 2019, "league_key": "L", "HomeTeam": "A", "AwayTeam": "C", "source_file": "x", "home_key": "L|A", "away_key": "L|C", "FTHG": 1, "FTAG": 1, "T_exact": 2, "movement_logit": -0.1},
        {"date": pd.Timestamp("2020-01-02"), "Season": "2019-2020", "season_start": 2019, "league_key": "L", "HomeTeam": "A", "AwayTeam": "B", "source_file": "x", "home_key": "L|A", "away_key": "L|B", "FTHG": 0, "FTAG": 0, "T_exact": 0, "movement_logit": 0.0},
    ])
    ft = c078.build_history_features(raw)
    d1 = ft[ft.date == pd.Timestamp("2020-01-01")].reset_index(drop=True)
    assert len(d1) == 2
    assert np.isnan(d1.loc[0, "home_goals_for_mean"])
    assert np.isnan(d1.loc[1, "home_goals_for_mean"])
    d2 = ft[ft.date == pd.Timestamp("2020-01-02")].iloc[0]
    assert abs(float(d2.home_goals_for_mean) - 1.5) <= 1e-12
    assert abs(float(d2.competition_total_mean) - 2.5) <= 1e-12

    result = {
        "schema_version": "C078A_FULL_SUPPORT_UNIT_TESTS_V1",
        "status": "PASS",
        "tests": {
            "poisson_gradient": "PASS",
            "nb2_gradient_including_dispersion": "PASS",
            "poisson_optimizer": "PASS",
            "nb2_optimizer": "PASS",
            "full_support_conservation": "PASS",
            "collapsed8_conservation": "PASS",
            "exact_proper_score_finite": "PASS",
            "conditional_tail_support": "PASS",
            "tail_survival_monotonicity": "PASS",
            "same_date_predict_before_update": "PASS"
        },
        "C077B_labels_read": False,
        "sealed_pool_opened": False
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
